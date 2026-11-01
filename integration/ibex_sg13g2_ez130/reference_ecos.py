#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Build a bounded, input-bound reference DRV ECO using public OpenROAD APIs.

This is a planner, not a signoff verdict. It consumes the current run's exact
TT/SS DRV bindings and database, preserves the SDC, and emits a new Tcl script.
All signal wires are discarded after connectivity changes. A complete reroute,
extraction, equivalence and independent physical/timing signoff are mandatory.

Planner: <128 MiB, <=8192 planning rows/repairs, <=1M pin visits. The
external OpenROAD process must run under a separate <=2 GiB/180 s guard.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import resource
import sys


FILES = {"odb", "netlist", "sdc", "cppr", "drv_bindings", "tt_std", "tt_ram",
         "ss_std", "ss_ram"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(path):
    path = path.resolve(strict=True)
    require(path.is_file() and path.stat().st_size <= 256 << 20, "file byte budget")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def read(path):
    require(path.is_file() and path.stat().st_size <= 2 << 20, "JSON byte budget")
    return json.loads(path.read_text())


def quote(value):
    value = str(value)
    require("\x00" not in value and len(value) <= 4096, "Tcl literal budget")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace(
        '$', '\\$').replace('[', '\\[').replace(']', '\\]').replace('\n', '\\n') + '"'


def build(config, output):
    require(set(config) == {"schema", "files", "policy"}
            and config["schema"] == "rusteda.reference-drv-inputs.v1", "config schema")
    require(set(config["files"]) == FILES, "exact current-run input set required")
    files = config["files"]
    for key, record in files.items():
        require(identity(Path(record["path"])) == record, "input changed: " + key)
    policy = config["policy"]
    policy_fields = {"prefix", "buffer", "upsize", "max_fanout_sinks", "max_repairs", "max_pin_visits"}
    require(policy_fields <= set(policy) <= policy_fields | {"fanout_placement", "preserve_placement", "incremental_global_route", "clock_fanout"}, "unknown ECO policy")
    headroom = policy.get("fanout_placement") in {"driver", "cluster"}
    require(policy.get("fanout_placement", "sinks") in {"driver", "cluster", "sinks"}, "invalid ECO placement")
    require(policy["buffer"] == "BUFX8" and policy["upsize"] == {"BUFX1": "BUFX4"},
            "unqualified buffer policy")
    require((policy["max_fanout_sinks"], policy["max_repairs"], policy["max_pin_visits"]) ==
            ((4, 8192, 1000000) if headroom else (8, 1024, 1000000)), "unqualified ECO budgets")
    preserve_placement = policy.get("preserve_placement", False)
    require(type(preserve_placement) is bool and (not preserve_placement or headroom), "invalid placement preservation")
    incremental = policy.get("incremental_global_route", False)
    require(type(incremental) is bool and (not incremental or preserve_placement), "incremental routing requires preserved placement")
    clock_fanout = policy.get('clock_fanout', False)
    require(type(clock_fanout) is bool and (not clock_fanout or headroom and incremental),
            'clock fanout requires explicit preserved-placement incremental policy')
    row_budget = policy["max_repairs"]
    chunk = policy["max_fanout_sinks"]
    prefix = policy["prefix"]
    require(isinstance(prefix, str) and 1 <= len(prefix) <= 64
            and all(c.isascii() and (c.isalnum() or c == '_') for c in prefix),
            "unsafe ECO namespace")
    binding = read(Path(files["drv_bindings"]["path"]))
    grt = binding.get('schema') == 'rusteda.reference-grt-bindings.v1'
    binding_fields = {"schema", "netlist", "sdc", "cppr", "bindings", "parasitics" if grt else "spef"}
    require(binding_fields <= set(binding) <= binding_fields | {"native_extra", "planning", "annotation_scope"}
            and binding['schema'] in {'rusteda.reference-drv-bindings.v1', 'rusteda.reference-grt-bindings.v1'},
            'bound DRV schema required')
    annotation = binding.get('annotation_scope')
    if annotation is not None:
        require(headroom and incremental and set(annotation) == {'status', 'missing_connections', 'evidence'}
                and annotation['status'] == 'incomplete', 'incomplete annotation requires an explicit repair-only scope')
        missing = annotation['missing_connections']
        require(isinstance(missing, list) and 1 <= len(missing) <= 1024
                and all(isinstance(pin, str) and 0 < len(pin) <= 4096 for pin in missing)
                and len(set(missing)) == len(missing),
                'missing connection repair budget')
        require(identity(Path(annotation['evidence']['path'])) == annotation['evidence'],
                'annotation evidence changed')
    if grt:
        require(headroom and binding['parasitics'] == dict(wire_model='global_routing_estimate',
                signoff=False, coverage='reported_violators_only'), 'estimated parasitic scope required')
    require(headroom == ("planning" in binding), "headroom requires explicit bound planning evidence")
    if headroom:
        planning = binding["planning"]
        require(set(planning) == {"mode", "evidence"} and planning["mode"] == "headroom", "planning evidence schema")
        require(1 <= len(planning["evidence"]) <= 8, "planning evidence count")
        for record in planning["evidence"].values():
            require(identity(Path(record["path"])) == record, "planning evidence changed")
    require(binding['netlist'] == files['netlist'] and binding['sdc'] == files['sdc']
            and binding['cppr'] == 'off', 'DRV report source/constraint mismatch')
    if not grt:
        require(binding['spef']['bytes'] <= 128 << 20
                and identity(Path(binding['spef']['path'])) == binding['spef'], 'DRV parasitics changed')
    rows = binding['bindings']
    require(set(rows) == {"tt", "ss"}, "TT and SS bindings required")
    raw = [row for corner in ("tt", "ss") for row in rows[corner]]
    extras = binding.get("native_extra", [])
    require(isinstance(extras, list) and len(extras) <= 1024, "native DRV row budget")
    for row in extras:
        require(row.get("corner") in {"tt", "ss"}, "native DRV corner")
        raw.append({k: v for k, v in row.items() if k != "corner"})
    require(len(raw) <= row_budget, "DRV row budget")
    caps, fans, slew, unique = {}, {}, {}, {}
    for row in raw:
        row_fields = {"kind", "pin", "master", "net", "report_values"}
        require(set(row) in (row_fields, row_fields | {"engine"}), "DRV binding fields")
        require(row.get("engine", "opensta") in {"opensta", "ruststa", "public_odb"}, "DRV binding engine")
        require(row["kind"] in {"max_capacitance", "max_fanout", "max_slew"},
                "unknown DRV kind")
        for field in ("pin", "master", "net"):
            require(isinstance(row[field], str) and 0 < len(row[field]) <= 4096
                    and "\x00" not in row[field] and "unresolved" not in row[field],
                    "unresolved DRV binding")
        require('/' in row["pin"], "instance-pin DRV required")
        key = row["pin"]
        require(key not in unique or all(unique[key][f] == row[f]
                for f in ("pin", "master", "net")), "corner binding mismatch")
        unique[key] = row
        {"max_capacitance": caps, "max_fanout": fans,
         "max_slew": slew}[row["kind"]][row["net"]] = row
    require(len(caps) + len(fans) + len(slew) <= row_budget, "repair source budget")
    require(not clock_fanout or not caps and not slew and annotation is None
            and all(row['kind'] == 'max_fanout' for row in raw),
            'clock policy supports only explicit fanout splitting')
    lines = ["define_corners tt ss", "set_thread_count 2"]
    for corner in ("tt", "ss"):
        for kind in ("std", "ram"):
            lines.append("read_liberty -corner " + corner + " "
                         + quote(files[corner + '_' + kind]["path"]))
    lines += ["read_db " + quote(files["odb"]["path"]),
              "read_sdc " + quote(files["sdc"]["path"]),
              "source " + quote(files["cppr"]["path"]),
              "set block [[[ord::get_db] getChip] getBlock]",
              'if {[$block getDbUnitsPerMicron] != 1000} {error "unexpected DBU"}',
              "set repairs 0", "set visits 0"]
    lines += r'''
proc exact_iterm {name} {
    global block
    set slash [string last "/" $name]
    if {$slash <= 0} {error "instance pin expected"}
    set inst [$block findInst [string range $name 0 [expr {$slash-1}]]]
    if {$inst eq "NULL"} {error "source instance missing"}
    set term [$inst findITerm [string range $name [expr {$slash+1}] end]]
    if {$term eq "NULL"} {error "source terminal missing"}
    return $term
}
proc pin_xy {term} {
    set xy [$term getAvgXY]
    if {[llength $xy] == 3} {
        if {![lindex $xy 0]} {error "terminal has no placed pin geometry"}
        set xy [lrange $xy 1 2]
    }
    if {[llength $xy] != 2} {error "unexpected public pin coordinate result"}
    foreach v $xy {if {![string is integer -strict $v]} {error "non-DBU pin coordinate"}}
    return $xy
}
proc micron_xy {xy} {
    lassign $xy x y
    # Integer DBU through the final conversion; placement uses the 5 nm grid.
    set x [expr {int(($x+2)/5)*5}]; set y [expr {int(($y+2)/5)*5}]
    return [list [format "%.3f" [expr {$x/1000.0}]] [format "%.3f" [expr {$y/1000.0}]]]
}
proc repair_budget {} {
    global repairs
    incr repairs
    if {$repairs > 1024} {error "ECO operation budget"}
}
proc driver_of {pin} {
    global visits
    set net [[exact_iterm $pin] getNet]
    if {$net eq "NULL"} {error "source net missing"}
    set drivers {}
    foreach term [$net getITerms] {
        incr visits
        if {$visits > 1000000} {error "ECO pin visit budget"}
        if {[$term getIoType] eq "OUTPUT"} {lappend drivers $term}
    }
    if {[llength $drivers] != 1} {error "single instance driver required"}
    return [lindex $drivers 0]
}
'''.strip().splitlines()
    # Validate every original report binding before the first mutation.
    if headroom:
        lines += ["proc rusteda_headroom_validate {pin master net} {",
                  "set term [exact_iterm $pin]", "set sta_pin [get_pins $pin]",
                  'if {[llength $sta_pin] != 1} {error "source STA pin missing"}',
                  'if {[get_full_name [get_nets -of_objects $sta_pin]] ne $net} {error "source net differs"}',
                  'if {[[[$term getInst] getMaster] getName] ne $master} {error "source master differs"}',
                  ('if {[[$term getNet] getSigType] ne "CLOCK"} {error "clock-only ECO requires actual CLOCK net"}'
                   if clock_fanout else 'if {[[$term getNet] getSigType] eq "CLOCK"} {error "CLOCK ECO refused"}'),
                  ('if {![llength [get_property $sta_pin clocks]]} {error "clock-only ECO requires STA clock witness"}'
                   if clock_fanout else 'if {[llength [get_property $sta_pin clocks]]} {error "propagated clock ECO refused"}'), "}"]
        for row in sorted(unique.values(), key=lambda row: row["pin"]):
            lines.append("rusteda_headroom_validate " + " ".join(quote(row[k]) for k in ("pin", "master", "net")))
    else:
        for row in sorted(unique.values(), key=lambda row: row["pin"]):
            lines += ["set term [exact_iterm " + quote(row["pin"]) + "]",
                      "set sta_pin [get_pins " + quote(row["pin"]) + "]",
                      'if {[llength $sta_pin] != 1} {error "source STA pin missing"}',
                      'if {[get_full_name [get_nets -of_objects $sta_pin]] ne ' + quote(row["net"])
                      + '} {error ' + quote("source net differs: " + row["pin"]) + '}',
                      'if {[[[$term getInst] getMaster] getName] ne ' + quote(row["master"])
                      + '} {error "source master differs"}']
    if annotation is not None:
        lines += ["set annotation_refresh [list " + " ".join(quote(pin) for pin in annotation['missing_connections']) + "]",
                  "foreach name $annotation_refresh {", "set term [exact_iterm $name]",
                  'if {[$term getIoType] ne "INPUT" || [[$term getMTerm] getName] ne "A"} {error "unsupported missing diode pin"}',
                  'if {[[[$term getInst] getMaster] getName] ni {ANTENNA2 ANTENNA4 ANTENNA8}} {error "missing annotation is not a supported original diode"}',
                  'if {[$term getNet] eq "NULL" || [[$term getNet] getSigType] in {POWER GROUND}} {error "invalid missing diode net"}', "}",
                  "foreach name $annotation_refresh {", "set term [exact_iterm $name]",
                  "set original_net [$term getNet]", "set inst [$term getInst]", "$term disconnect",
                  "odb::dbInst_destroy $inst", "repair_budget",
                  'puts [list RUSTEDA_ANNOTATION_REMOVED $name [$original_net getName]]', "}",
                  "set diode_ids {}", "foreach inst [$block getInsts] {",
                  'if {[regexp {^ANTENNA_([0-9]+)$} [$inst getName] -> id]} {lappend diode_ids $id}', "}",
                  "set diode_ids [lsort -integer $diode_ids]", "set free {}",
                  'for {set i 1} {$i <= [llength $diode_ids]} {incr i} {if {[$block findInst ANTENNA_$i] eq "NULL"} {lappend free $i}}',
                  "set renamed 0", "foreach id [lreverse $diode_ids] {",
                  "if {![llength $free] || $id <= [llength $diode_ids]} {break}",
                  "set target [lindex $free 0]; set free [lrange $free 1 end]",
                  'if {![[$block findInst ANTENNA_$id] rename ANTENNA_$target]} {error "diode rename failed"}',
                  "incr renamed", "}",
                  'if {[llength $free]} {error "diode names not contiguous"}',
                  'puts "RUSTEDA_DIODE_NAMES_COMPACTED $renamed"']
    lines += ["remove_fillers"]
    if incremental:
        lines += ["set_routing_layers -signal Metal2-TopMetal2 -clock Metal2-TopMetal2",
                  "set_global_routing_layer_adjustment * 0.3",
                  "global_route -start_incremental",
                  "write_guides " + quote(output / 'guides-before.guide')]
    if preserve_placement:
        resizable = sorted({row["pin"].rsplit('/', 1)[0] for row in caps.values() if row["master"] in policy["upsize"]})
        lines += ["set resizing [list " + " ".join(quote(name) for name in resizable) + "]",
                  "set frozen_positions {}", "foreach inst [$block getInsts] {",
                  'if {[$inst getPlacementStatus] ne "PLACED" || [lsearch -exact $resizing [$inst getName]] >= 0} {continue}',
                  'if {[llength $frozen_positions] >= 100000} {error "fixed-position budget"}',
                  "set xy [$inst getOrigin]",
                  'if {[llength $xy] != 2} {error "unexpected DBU origin"}',
                  "lappend frozen_positions [list $inst $xy [$inst getOrient]]",
                  '$inst setPlacementStatus LOCKED', "}"]
    extra_fanout_targets = set()
    for index, (net, row) in enumerate(sorted(caps.items())):
        instance = row["pin"].rsplit('/', 1)[0]
        if row["master"] in policy["upsize"]:
            lines += ['replace_cell ' + quote(instance) + ' '
                      + quote(policy["upsize"][row["master"]])]
        else:
            lines += ['set driver [driver_of ' + quote(row["pin"]) + ']',
                      'set location [micron_xy [pin_xy $driver]]',
                      'insert_buffer -buffer_cell BUFX8 -net ' + quote(net)
                      + ' -location $location -buffer_name ' + f'{prefix}_cap_{index}'
                      + ' -net_name ' + f'{prefix}_cap_net_{index}']
        lines += ["repair_budget"]
    for index, net in enumerate(sorted(set(slew) - set(caps))):
        lines += ['set driver [driver_of ' + quote(slew[net]["pin"]) + ']',
                  'set location [micron_xy [pin_xy $driver]]',
                  'insert_buffer -buffer_cell BUFX8 -net ' + quote(net)
                  + ' -location $location -buffer_name ' + f'{prefix}_slew_{index}'
                  + ' -net_name ' + f'{prefix}_slew_net_{index}', 'repair_budget']
    fan_targets = sorted(set(fans) | extra_fanout_targets)
    if headroom and not fan_targets:
        fan_targets = [None]
    for index, net in enumerate(fan_targets):
        source = "{}" if net is None else ("[get_nets -of_objects [get_pins " + quote(fans[net]["pin"]) + "]]" if net in fans else "[get_nets " + quote(net) + "]")
        fan_lines = ["set source " + source,
                  'if {[llength $source] != 1} {error "fanout source missing"}',
                  "set sinks [get_pins -of_objects $source -filter {direction == input}]",
                  "incr visits [llength $sinks]",
                  'if {$visits > 1000000} {error "ECO pin visit budget"}',
                  "set ordered {}",
                  "foreach p $sinks {",
                  "set name [get_full_name $p]",
                  "lassign [pin_xy [exact_iterm $name]] x y",
                  "lappend ordered [list $x $y $name $p]", "}",
                  # Sorting by coordinates groups physically adjacent loads.
                  "set ordered [lsort -integer -index 1 $ordered]",
                  "set ordered [lsort -integer -index 0 $ordered]",
                  "set sinks {}",
                  "foreach row $ordered {lappend sinks [lindex $row 3]}",
                  f'puts "RUSTEDA_FANOUT_SOURCE {index} [llength $sinks]"',
                  'if {[llength $sinks] > 64} {error "fanout hierarchy budget; regenerate a bounded multilevel tree"}',
                  f'if {{[llength $sinks] > {chunk}}} {{',
                  f'for {{set k 0}} {{$k < [llength $sinks]}} {{incr k {chunk}}} {{',
                  f'set group [lrange $sinks $k [expr {{$k+{chunk-1}}}]]',
                  'set sx 0; set sy 0',
                  'foreach p $group {lassign [pin_xy [exact_iterm [get_full_name $p]]] x y; incr sx $x; incr sy $y}',
                  'set n [llength $group]',
                  ('set source_driver [get_pins -of_objects $source -filter {direction == output}]; '
                   'if {[llength $source_driver] != 1} {error "headroom single driver"}; '
                   'set location [micron_xy [pin_xy [exact_iterm [get_full_name [lindex $source_driver 0]]]]]'
                   if headroom and policy['fanout_placement'] == 'driver' else 'set location [micron_xy [list [expr {$sx/$n}] [expr {$sy/$n}]]]'),
                  'insert_buffer -buffer_cell BUFX8 -load_pins $group -location $location'
                  + f' -buffer_name {prefix}_fan_{index}_$k -net_name {prefix}_fan_net_{index}_$k',
                  'repair_budget', '}', '}']
        if headroom:
            if index == 0:
                template = "\n".join(fan_lines[1:]).replace(f"{prefix}_fan_0_", f"{prefix}_fan_${{index}}_").replace(f"{prefix}_fan_net_0_", f"{prefix}_fan_net_${{index}}_").replace("RUSTEDA_FANOUT_SOURCE 0 ", "RUSTEDA_FANOUT_SOURCE $index ")
                lines += ["proc rusteda_headroom_fanout {source index} {", "global visits", template, "}"]
            if net is not None:
                lines.append("rusteda_headroom_fanout " + source + " " + str(index))
        else:
            lines += fan_lines
    if headroom:
        # The public command treats names as prefixes and appends an ID.
        # Query inserted output pins instead of predicting generated names.
        lines += ["set extra_pins {}", "foreach inst [$block getInsts] {", "incr visits",
                  'if {$visits > 1000000} {error "ECO pin visit budget"}',
                  "set name [$inst getName]",
                  'if {[string match ' + quote(prefix + "_cap_*") + ' $name] || [string match '
                  + quote(prefix + "_slew_*") + ' $name]} {',
                  'if {[[$inst getMaster] getName] ne "BUFX8"} {error "inserted master changed"}',
                  'lappend extra_pins "$name/Y"', "}", "}",
                  f"set extra_index {len(fans)}", "foreach pin [lsort $extra_pins] {",
                  'rusteda_headroom_fanout [get_nets -of_objects [get_pins $pin]] $extra_index',
                  "incr extra_index", "}"]
    if clock_fanout:
        lines += ["set clock_new_nets 0", "foreach inst [$block getInsts] {",
            "incr visits", 'if {$visits > 1000000} {error "clock net visit budget"}',
            'if {![string match ' + quote(prefix + '_fan_*') + ' [$inst getName]]} {continue}',
            'if {[[$inst getMaster] getName] ne "BUFX8"} {error "clock buffer master differs"}',
            "foreach term [$inst getITerms] {", 'if {[$term getIoType] ne "OUTPUT"} {continue}',
            'if {[$term getNet] eq "NULL"} {error "new clock output is disconnected"}',
            '[$term getNet] setSigType CLOCK', 'incr clock_new_nets', "}", "}",
            'if {$clock_new_nets > 8192} {error "clock net count budget"}',
            'puts "RUSTEDA_CLOCK_NETS_MARKED $clock_new_nets"']
    lines += r'''
set wire_count 0
foreach net [$block getNets] {
    if {[$net getSigType] in {POWER GROUND}} {continue}
    set wire [$net getWire]
    if {$wire ne "NULL"} {odb::dbWire_destroy $wire; incr wire_count}
}
puts "RUSTEDA_SIGNAL_WIRES_CLEARED $wire_count"
set_placement_padding -global -left 1 -right 1
detailed_placement -max_displacement {1100 400}
check_placement -verbose
'''.strip().splitlines()
    # Insertion does not automatically apply the existing database's global
    # connection rules to new buffers/diodes. Bind logical PG explicitly;
    # physical PDN connectivity still requires final device-level LVS.
    lines += ["global_connect",
              'set pg_nets [dict create POWER [$block findNet VPWR] GROUND [$block findNet VGND]]',
              'dict for {role net} $pg_nets {',
              'if {$net eq "NULL" || [$net getSigType] ne $role} {error "original power/ground net missing or mistyped"}', "}",
              "set pg_verified 0", "foreach inst [$block getInsts] {",
              "foreach term [$inst getITerms] {", "incr visits",
              'if {$visits > 1000000} {error "logical PG pin visit budget"}',
              'set role [[$term getMTerm] getSigType]',
              'if {$role ni {POWER GROUND}} {continue}',
              'if {[$term getNet] ne [dict get $pg_nets $role]} {error "logical PG connection missing or wrong: [$inst getName]/[[$term getMTerm] getName]"}',
              "incr pg_verified", "}", "}",
              'puts "RUSTEDA_LOGICAL_PG_CONNECTED $pg_verified"',
              "write_db " + quote(output / 'eco.odb'),
              "write_verilog " + quote(output / 'eco.v'),
              "write_verilog -include_pwr_gnd " + quote(output / 'eco.pnl.v'),
              "set receipt [open " + quote(output / 'complete.txt') + " w]",
              'puts $receipt "requires_full_revalidation $repairs $visits"',
              "close $receipt", 'puts "RUSTEDA_ECO_COMPLETE $repairs $visits"']
    if preserve_placement:
        # Existing antenna cells can be physically legal without additional
        # soft routing padding. Freeze their coordinates; pad each new cell.
        index = lines.index("set_placement_padding -global -left 1 -right 1")
        lines[index:index+1] = ["set_placement_padding -global -left 0 -right 0",
            "set pad_cells [get_cells -quiet " + quote(prefix + '*') + "]",
            'if {[llength $pad_cells] > 8192} {error "new-cell padding budget"}',
            'if {[llength $pad_cells]} {set_placement_padding -instances $pad_cells -left 1 -right 1}',
            'if {[llength $resizing]} {set_placement_padding -instances [get_cells $resizing] -left 1 -right 1}']
        index = lines.index("check_placement -verbose") + 1
        if incremental:
            post_route = ["global_route -end_incremental",
                "write_guides " + quote(output / 'guides-eco.guide'),
                "repair_antennas ANTENNA8 -iterations 6 -ratio_margin 50",
                # Diode insertion may violate an earlier ECO buffer's soft
                # padding. Legalize with the SAME padding while old instances
                # remain locked, and route every moved connection afterward.
                # Complete foundry antenna/DRC still qualify the final GDS.
                "global_route -start_incremental",
                "set restored_positions 0",
                "foreach row $frozen_positions {", "lassign $row inst xy orient",
                # The antenna inserter may release or move original cells.
                # Restore exact integer origins under the routing observer;
                # new diodes must legalize around these preserved cells.
                'if {[$inst getOrigin] ne $xy || [$inst getOrient] ne $orient} {',
                '$inst setOrient $orient', '$inst setOrigin {*}$xy',
                'incr restored_positions', "}",
                '$inst setPlacementStatus LOCKED', "}",
                'puts "RUSTEDA_ORIGINAL_POSITIONS_RESTORED $restored_positions"',
                "detailed_placement -max_displacement {1100 400}",
                "check_placement -verbose",
                "global_route -end_incremental",
                "write_guides " + quote(output / 'guides-final.guide'),
                "check_placement -verbose"]
            lines[index:index] = post_route
            index += len(post_route)
        lines[index:index] = ["foreach row $frozen_positions {", "lassign $row inst xy orient",
                            'if {[$inst getOrigin] ne $xy || [$inst getOrient] ne $orient} {error "unmodified original instance moved: [$inst getName]"}',
                            '$inst setPlacementStatus PLACED', "}",
                            'puts "RUSTEDA_ORIGINAL_POSITIONS_UNCHANGED [llength $frozen_positions]"']
    if headroom:
        lines = [line.replace('if {$repairs > 1024}', 'if {$repairs > 8192}') for line in lines]
    (output / 'eco.tcl').write_text('if {[catch {\n' + '\n'.join(lines)
        + '\n} message options]} {\n puts stderr $message\n puts stderr [dict get $options -errorinfo]\n exit 2\n}\nexit 0\n')
    result = dict(schema="rusteda.reference-drv-plan.v1", status="requires_full_revalidation",
                files=files, policy=policy, cap_sources=len(caps), fanout_sources=len(fans),
                slew_only_sources=len(set(slew) - set(caps)), report_rows=len(raw),
                script=identity(output / 'eco.tcl'), estimated_external_peak_mib=1536,
                external_memory_max_mib=2048, external_timeout_s=180,
                timing_sources={key: binding[key] for key in ('netlist', 'sdc', 'parasitics' if grt else 'spef', 'cppr')},
                original_constraints_unchanged=True,
                unverified=["equivalence", "physical_power_connectivity", "routing", "RC", "GDS", "DRC", "antenna",
                            "LVS", "setup", "hold", "DRV", "benchmark"])
    if headroom:
        result['planning'] = binding['planning']
    if annotation is not None:
        result['annotation_scope'] = annotation
    if clock_fanout:
        result['clock_eco'] = dict(scope='original_clock_fanout_only', buffer='BUFX8',
            original_max_fanout=10, target_group_size=4, new_output_nets='CLOCK',
            requires_propagated_clock_revalidation=True, requires_setup_hold_skew_revalidation=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--base-rev', required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))
    args.workspace = args.workspace.resolve()
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / 'workspace.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock'
                for p in args.workspace.iterdir()), 'REVISION_CONFLICT')
        result = build(read(args.inputs), args.workspace)
        (args.workspace / 'plan.json').write_text(json.dumps(result, indent=2) + '\n')
        (args.workspace / 'HEAD').write_text('rev1\n')
        print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(status='failed', error=str(error))), file=sys.stderr)
        raise SystemExit(2)

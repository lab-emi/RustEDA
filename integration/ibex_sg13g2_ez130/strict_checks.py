#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reference-specific adapters to the shared strict density and CEC policies.

The adapters retain integer DBU, bounded streaming and the original CEC scope.
No EDA algorithm or proof oracle is introduced. Missing evidence is a failure.
"""
from collections import Counter
import importlib.util
from pathlib import Path
import re


def require(ok, message):
    if not ok:
        raise ValueError(message)


def density_components(path, macro):
    """Read the public DEF component grammar, including optional SOURCE."""
    require(path.stat().st_size <= 128 << 20, 'DEF byte budget')
    names, counts, fixed = set(), Counter(), {}
    top = units = declared = None
    active = seen = False
    statement = ''
    with path.open() as stream:
        while line := stream.readline(65537):
            require(len(line) <= 65536, 'DEF line budget')
            if line.startswith('DESIGN '):
                require(top is None, 'duplicate DESIGN')
                top = line.split()[1]
            if line.startswith('UNITS DISTANCE MICRONS '):
                require(units is None, 'duplicate units')
                units = int(line.split()[3])
            if line.startswith('COMPONENTS '):
                require(not seen, 'duplicate COMPONENTS')
                declared = int(line.split()[1])
                require(0 < declared <= 500000, 'component count budget')
                active = seen = True
                continue
            if line.startswith('END COMPONENTS'):
                require(active and not statement.strip(), 'unfinished component')
                active = False
                break
            if not active:
                continue
            statement += line
            require(len(statement) <= 65536, 'component statement budget')
            if ';' not in line:
                continue
            match = re.fullmatch(r'\s*-\s+(\S+)\s+(\S+)\s+'
                r'(?:\+\s+(?i:SOURCE)\s+(?i:NETLIST|DIST|USER|TIMING)\s+)?'
                r'\+\s+(FIXED|PLACED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+'
                r'(N|S|E|W|FN|FS|FE|FW)\s*;\s*', statement)
            require(match is not None, 'unsupported final DEF component')
            name, master, status, x, y, orientation = match.groups()
            require(name not in names and len(names) < declared, 'duplicate/excess component')
            names.add(name)
            counts[master] += 1
            if master == macro:
                require(status == 'FIXED' and len(fixed) < 2, 'SRAM must remain fixed')
                fixed[name] = dict(master=master, x=int(x), y=int(y), orientation=orientation)
            statement = ''
    require(seen and not active and len(names) == declared, 'component count mismatch')
    require(top == 'ibex_sram_bench' and units == 1000, 'top/units mismatch')
    return units, declared, counts, fixed


def density(qualification):
    q = qualification
    policy = q.files['density_policy']
    original = q.dynamic_record(q.artifacts['def'])
    source = q.files['density_checker']
    # Use the host module's bounded JSON/identity utilities, without accepting
    # alternate density definitions from a run output.
    import json
    require(Path(policy['path']).stat().st_size <= 1 << 20, 'density policy byte budget')
    data = json.loads(Path(policy['path']).read_bytes())
    require(data['max_percent'] == q.contract['common']['final_density_max_percent'] == 70
            and data['evaluation_stage'] == 'after_final_signal_routing_and_all_cts_hold_drv_antenna_ecos',
            'final density policy differs')
    spec = importlib.util.spec_from_file_location('shared_final_density', source['path'])
    require(spec is not None and spec.loader is not None, 'density policy loader')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # LibreLane emits standard SOURCE provenance and may FIX taps. Only actual
    # SRAM masters define the macro denominator; fixed taps remain in occupied
    # standard-cell area. The shared arithmetic below remains byte-identical.
    module.components = lambda path: density_components(path, 'RM_IHPSG13_1P_1024x32_c2_bm_bist')
    physical_path = Path(q.files['physical']['path'])
    require(physical_path.stat().st_size <= 1 << 20, 'physical policy byte budget')
    physical = json.loads(physical_path.read_bytes())
    result = module.utilization(Path(original['path']), Path(q.files['density_lef']['path']),
                                physical, q.contract['common'], 70)
    require(module.identity(Path(source['path'])) == source, 'density policy changed')
    require(result['status'] == 'passed', 'FINAL_DENSITY_LIMIT')
    result.update(complete=True, scope='final_routed_layout', artifacts=q.artifacts,
                  inputs={name: q.files[name] for name in
                          ('density_checker', 'density_policy', 'density_lef', 'physical', 'contract')})
    return result


def equivalence(q, label):
    report = q.reports[label + '_equivalence']
    artifact = q.artifacts['netlist_synth' if label == 'synthesis' else 'netlist_final']
    require(report['status'] == 'proven' and report['complete'] is True
            and report['rtl_sha256'] == q.files['rtl']['sha256']
            and report['netlist_sha256'] == artifact['sha256'], 'stale/incomplete original-RTL event CEC')
    manifest = report['manifest']
    require(manifest['all_rtl_inputs_and_states_quantified'] is True
            and manifest['all_gate_initial_states_covered'] is True
            and manifest['reset_assumptions'] == manifest['case_assumptions'] == manifest['initial_state_choices'] == []
            and manifest['state_pairs'] == manifest['gate_state_elements'] > 0
            and manifest['rtl_unknown_literals'] == 'X/Z-as-zero; distinct from four-state equality',
            'unqualified RTL event/state proof scope')
    q.dynamic_record(report['fresh_original_cut'])
    require(report['abc_verdict'] == 'Networks are equivalent.', 'independent ABC verdict missing')
    return dict(status='passed', complete=True, proof=q.report_records[label + '_equivalence'],
                scope=report['scope'], netlist=artifact)

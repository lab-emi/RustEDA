#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded Ibex ECO flow policy; all netlist edits use the pinned native CLI.

Usage: native_ecos.py {reset,fanout,primary-clock,hold} --workspace NEW
                     --base-rev empty --inputs inputs.json --policy policy.json
                     [--plan-only]

inputs.json schema rusteda.native-eco-inputs.v1 uses file records
{"path": "relative/to/inputs.json", "sha256": "..."}. Required keys:
  netlist, netlist_json (also source_sha256), sdc, io, profiles:{delay,lef,sizing},
  tools:{rustroad,xxhsum,liberty_reader}, and corners:{name:{pin_tables:[
    {path,sha256,library:{path,sha256}}], drv}}. Hold also requires endpoints and
  hold_fix per corner. Ideal hold uses one combined all-corner hold_fix report,
  referenced from both corner records; real hold also accepts per-corner reports.
  Each extracted corner also supplies spef:{path,sha256}. setup_corner is explicit
  in the policy when invoking extracted hold.
  Optional profiles.cell_buses forwards the explicit master bus schema to a
  qualified native CLI that supports --cell-buses.
Reports are complete native JSON envelopes from one compiled STA revision.
Optional timed_netlist/timed_netlist_json bind post-CTS reports when editing
pre-CTS input. Every selected sink's master, net and driver are checked in both.
A fresh source/JSON receipt is required; cached plans are never an input.

io is an explicit projection {sdc_sha256,drivers:{port:{cell,pin}},loads_pf:{port:n}}.
It is checked against literal generated-SDC IO commands AND compiled provenance.
Variables, units overrides and unsupported IO forms fail; this is not an SDC
interpreter. Other constraints remain the native compiler's responsibility.

policy.json schema rusteda.native-eco-policy.v1 contains top, clock_roots,
primary_clock, buffers:[{cell,input,output}], primary_buffer, reset:{net,pin,
leaf,trunk}, delay_cells, target_fanout:8, cap_fraction:0.8, hold_margin_ns:0.025,
setup_margin_ns:0.05, and scope:ideal_diagnostic|spef_rc. Names are explicit;
no library, clock, reset or cell name is inferred. Prefix is optional. Optional
port_diode:{cell,pin} adds one profiled antenna diode to every primary input bit
in the ideal hold stage: a port-driven net has no driver diffusion, so the
foundry deck's no-diode cumulative ratio would otherwise apply to the whole net.

Ideal hold uses original selected-cell table extrema as a provisional estimate,
then requires fresh all-corner timing. Extracted hold invokes existing hold-eco,
retaining its spef_rc/provenance gates. No script writes or patches Verilog.

scope:estimated_rc runs on reports compiled with a placement RC estimate
(estimate_spef.py; the SPEF provenance gate is unchanged). It needs the
timed placement (placement:{def, seed}), the estimator (tools.estimator) and
its policy (rc_policy), and writes seed.json: the timed placement seed plus a
location for every new cell, and weights.json: placement net weights (16.0)
that keep every repair buffer on its short electrical side (the net from a
weak or SRAM driver to its isolation buffer, from a pin buffer to its SRAM
pin) against HPWL-driven detailed moves; the hold stage also carries the
electrical stage's weights (placement.weights). The hold seed also keeps the
timed tree's clock leaf order (clock_sinks) and the regrouped fanout/reset
tree partition (regroup, with each chain's first stage in place of the sink it
now drives), so the final CTS rebuilds the topology and the skew and loads the
hold plan was timed against; the electrical seed drops both and the repair
round clusters afresh. Two operations use it:
  electrical  wire-aware DRV repair. Leaves of native fanout/reset trees
              (regrouped by CTS) are resized level-wide; a violating SRAM
              input pin gets its own buffer at the pin; any other violating
              net is isolated behind a buffer at its driver, sized by the
              estimated load, and nets longer than electrical.max_wire_um
              are first split into geometric clusters, each behind its own
              buffer at the cluster centre (moved to a macro's bottom or top
              edge when it falls inside one); while the driver's tour of those
              buffers is still longer than max_wire_um they are grouped the
              same way behind centre buffers (a bounded trunk tree). The net feeding a regrouped
              tree's top level is sized for a tour of the tree's whole sink
              box (its buffers follow their sinks). Rows within electrical.drv_margin
              of their limit count as violations in the electrical op (the
              next placement moves pins), and rows within the smaller
              electrical.hold_drv_margin in the hold op (the final placement
              still moves them; every hold-round repair is charged to the
              hold paths it speeds up); the estimated rounds report them
              (slack<0.2). Optional electrical.cap_feedback is a hashed file
              record containing at most 256 post-route cap witnesses. The hold
              round verifies all source hashes and current timed net driver,
              sink and master signatures, then uses the observed cap as a load
              floor for repair selection and sizing. Mismatches fail explicitly;
              actual timing remains the unchanged placement estimate and fresh
              extraction is still required for signoff.
              Input ports count as drivers too (overlay16): the STA DRV
              rows omit them, so each nonclock port net's own estimate is
              checked against its driving cell with the same margin.
  hold        the electrical pass on the residual DRV rows, then hold: an
              estimate-violating endpoint group sharing a port or a launch
              register outside the CTS tree (clock_roots other than the
              primary clock), whose skew all its paths share,
              gets one delay chain on the register output its hold paths
              leave through (Q or QB net, all sinks),
              any other endpoint a chain in front of it (ideal-hold rule),
              ending in the SRAM pin buffer at an SRAM input; a launch-chained
              endpoint still gets two guard stages. Endpoints in
              the forward cone of nets the same round's electrical pass
              repairs are charged, per path, half their largest listed arc
              delays as lost hold slack (the report lists endpoints below
              0.5 ns); the hold round repairs only actual violations.
              A tree register
              launches only the delay beyond the endpoint-chain cap. Port diodes
              are seeded at their ports.
              Chains are sized by table minima, charged at table maxima
              against the global worst setup slack.
The receipt records source/tool hashes, budgets, scope and unverified gates.
Planner/apply/report time belongs to the caller's end-to-end measurement.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import fcntl
import gc
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import select
import signal
import subprocess
import sys
import time


class Failure(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(ok, code, text):
    if not ok:
        raise Failure(code, text)


def number(value, *, positive=False):
    require(isinstance(value, (int, float)) and not isinstance(value, bool), 'INPUT', 'numeric value required')
    value = float(value)
    require(math.isfinite(value) and (value > 0 if positive else value >= 0), 'INPUT', 'finite nonnegative value required')
    return value


def sha_fresh(path):
    """SHA-256 of the file's current content, always read in full."""
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fresh_digests(paths):
    """{path: sha_fresh(path)} for every path, each read in full. The files are hashed on the
    CPUs this process may use (at most four threads; hashlib releases the interpreter lock while
    it hashes), so a final check of large tools and libraries costs a fraction of its serial time;
    every byte is still read and hashed."""
    paths = list(dict.fromkeys(paths))
    workers = max(1, min(4, len(os.sched_getaffinity(0)), len(paths)))
    if workers == 1:
        return {path: sha_fresh(Path(path)) for path in paths}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(paths, pool.map(lambda path: sha_fresh(Path(path)), paths)))


def wait_child(proc, seconds):
    """`proc.wait(timeout=seconds)`, woken at the child's exit through a Linux pidfd instead of
    the polling wait (which sleeps up to 50 ms per check); the polling wait elsewhere."""
    try:
        fd = os.pidfd_open(proc.pid)
    except (AttributeError, OSError):
        return proc.wait(timeout=seconds)
    try:
        end = time.monotonic() + seconds
        while proc.poll() is None:
            left = end - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(proc.args, seconds)
            select.select([fd], [], [], left)
        return proc.returncode
    finally:
        os.close(fd)


# Digests of this process, by resolved path: (identity, SHA-256). native_frontend_inputs.py
# shares this dict and DIGEST_FLOW when it loads this module (one memo per process).
DIGEST_MEMO = {}
# The run's flow digest cache (--digest-cache, written by native_frontend_inputs.py): the
# SHA-256 of pinned inputs outside the run tree by identity, or None.
DIGEST_FLOW = None


def file_identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


# File systems whose file status is exact (local ones); only their files are memoized.
LOCAL_FILE_SYSTEMS = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'tmpfs', 'f2fs', 'zfs', 'overlay'}
FILE_SYSTEMS = {}
MOUNTINFO = '/proc/self/mountinfo'


def local_file(st):
    """Whether the file of status ST is on a local file system (LOCAL_FILE_SYSTEMS), by its
    device in MOUNTINFO; False when that cannot be told."""
    if st.st_dev not in FILE_SYSTEMS:
        kind = None
        try:
            dev = f'{os.major(st.st_dev)}:{os.minor(st.st_dev)}'
            with open(MOUNTINFO) as stream:
                for line in stream:
                    left, _, right = line.partition(' - ')
                    if left.split()[2] == dev and right.split():
                        kind = right.split()[0]
                        break
        except (OSError, IndexError):
            kind = None
        FILE_SYSTEMS[st.st_dev] = kind
    return FILE_SYSTEMS[st.st_dev] in LOCAL_FILE_SYSTEMS


def sha(path):
    """SHA-256 of a file, hashed once per file version in this process; a pinned input outside
    the run tree whose identity matches the run's flow digest cache is not hashed again. The
    identity (device, inode, size, mtime_ns, ctime_ns) changes with every write, rename and
    replacement, so a changed file is always hashed again; a file on a file system that is not
    local is hashed every time. The final check that no input changed during planning/apply
    reads every file again (sha_fresh), and the frontend's validate step hashes every entry of
    the flow digest cache again."""
    path = Path(path).resolve(); name = str(path); st = os.stat(path); key = file_identity(st)
    if not local_file(st):
        return sha_fresh(path)
    hit = DIGEST_MEMO.get(name)
    if hit is not None and hit[0] == key:
        return hit[1]
    flow = DIGEST_FLOW
    if flow is not None and not path.is_relative_to(flow['root']):
        entry = flow['entries'].get(name)
        if entry is not None and entry['identity'] == key:
            DIGEST_MEMO[name] = (key, entry['sha256'])
            return entry['sha256']
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        unchanged = file_identity(os.fstat(stream.fileno())) == key
    if unchanged:
        DIGEST_MEMO[name] = (key, digest)
    return digest


def load_digest_cache(path):
    """The flow digest cache of native_frontend_inputs.py at PATH (read only here): used only
    when it is a regular file at <root>/frontend/sha256-cache.json of the root it names."""
    global DIGEST_FLOW
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 << 20:
            return
        data = json.loads(path.read_text())
        root = Path(data['root'])
        if data.get('schema') != 'rusteda.frontend-digests.v1' or not root.is_absolute() or path.resolve() != root/'frontend'/'sha256-cache.json':
            return
        entries = {k: v for k, v in data['entries'].items()
                   if isinstance(v, dict) and set(v) == {'identity', 'sha256'} and isinstance(v['identity'], list)
                   and len(v['identity']) == 5 and all(type(x) is int for x in v['identity'])
                   and isinstance(v['sha256'], str) and re.fullmatch('[0-9a-f]{64}', v['sha256'])}
        DIGEST_FLOW = dict(root=root, entries=entries)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        DIGEST_FLOW = None


def read_json(path, cap):
    require(path.stat().st_size <= cap, 'BUDGET', 'JSON byte budget: '+str(path))
    with path.open() as stream:
        return json.load(stream)


def handle(text):
    require(isinstance(text, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', text), 'HANDLE', 'invalid new ECO handle')
    return text


class Inputs:
    def __init__(self, path, args):
        self.args, self.base = args, path.resolve().parent
        self.manifest = read_json(path, 2 << 20)
        require(self.manifest.get('schema') == 'rusteda.native-eco-inputs.v1', 'SCHEMA', 'input bundle schema')
        self.files, self.cache, self.work = {}, {}, 0
        self.report_identity = None
        self.xxh3_memo = {}
        self.files[str(path.resolve())] = sha(path)

    def tick(self, count=1):
        self.work += count
        require(self.work <= self.args.max_work, 'BUDGET', 'planner work budget exceeded')

    def file(self, record, cap=64 << 20):
        require(isinstance(record, dict) and isinstance(record.get('path'), str), 'INPUT', 'file record required')
        require(len(record['path']) <= 4096 and re.fullmatch('[0-9a-f]{64}', record.get('sha256', '')), 'INPUT', 'path or SHA256 invalid')
        path = (self.base/record['path']).resolve()
        require(path.is_file() and path.stat().st_size <= cap, 'BUDGET', 'missing or oversized input: '+str(path))
        key = str(path)
        if key not in self.files:
            require(len(self.files) < 96, 'BUDGET', 'input file-count budget')
            require(sha(path) == record['sha256'], 'STALE_INPUT', 'SHA256 mismatch: '+key)
            self.files[key] = record['sha256']
        else:
            require(self.files[key] == record['sha256'], 'STALE_INPUT', 'inconsistent file records')
        return path

    def document(self, record, cap=64 << 20):
        path = self.file(record, cap)
        if str(path) not in self.cache:
            self.cache[str(path)] = read_json(path, cap)
        return self.cache[str(path)]

    def tool(self, name):
        path = self.file(self.manifest['tools'][name], 256 << 20)
        if name in ('rustroad', 'xxhsum'):
            with path.open('rb') as stream:
                require(stream.read(4) == b'\x7fELF', 'NATIVE_CLI', 'direct native ELF required for bounded cleanup')
        return path

    def command(self, argv, directory, label):
        self.tick()
        (directory/(label+'-command.json')).write_text(json.dumps(argv, indent=2)+'\n')
        with (directory/(label+'.stdout')).open('wb') as out, (directory/(label+'.stderr')).open('wb') as err:
            proc = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True,
                                    env={**os.environ, 'RAYON_NUM_THREADS': '2'})
            try:
                code = wait_child(proc, self.args.timeout_seconds)
            except BaseException:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise
        require(code == 0, 'NATIVE_CLI', f'{label} exited {code}; see retained stderr')

    def xxh3(self, path):
        # One xxhsum run per file version in this process (by identity, as sha() memoizes).
        st = os.stat(path)
        key = (str(Path(path).resolve()), *file_identity(st)) if local_file(st) else None
        if key in self.xxh3_memo:
            return self.xxh3_memo[key]
        data = subprocess.check_output([str(self.tool('xxhsum')), '-H3', '--', str(path)], timeout=15, text=True)
        match = re.fullmatch(r'XXH3_([0-9a-fA-F]{16})[ \t]+[^\n]+\n?', data)
        require(match is not None, 'HASH_TOOL', 'unexpected XXH3-64 executable output')
        if key is not None and (str(Path(path).resolve()), *file_identity(os.stat(path))) == key:
            self.xxh3_memo[key] = 'xxh3:'+match[1].lower()
        return 'xxh3:'+match[1].lower()

    def report(self, corner, kind, timed, sdc):
        env = self.document(self.manifest['corners'][corner][kind])
        require(env.get('schema') == 'rusteda.v1' and env.get('ok') is True, 'REPORT', 'successful native envelope required')
        require(env.get('rev') and re.fullmatch(r'rev[0-9]{1,10}', env['rev']), 'REPORT', 'immutable revision required')
        scope = env['scope']
        require(corner in scope['corners'] and scope['cppr'] == 'off', 'REPORT_SCOPE', 'missing corner or inconsistent CPPR')
        require(scope['clock_model'] == 'propagated', 'REPORT_SCOPE', 'propagated clock timing required')
        folder = Path(env['workspace']).resolve()
        identity = (str(folder), env['rev'])
        require(self.report_identity in (None, identity), 'STALE_REPORT', 'reports must share one STA workspace revision')
        self.report_identity = identity
        lock = (folder/'workspace.lock').open('rb')
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            meta_path = folder/'revisions'/f"rev{int(env['rev'][3:]):04}"/'meta.json'
            meta = read_json(meta_path, 4 << 20)
            for role, path in [('netlist', timed), ('constraints', sdc)]:
                sources = [x for x in meta['sources'] if x['table'] == role]
                require(len(sources) == 1, 'REPORT_PROVENANCE', 'exactly one source required: '+role)
                require(Path(sources[0]['path']).resolve() == path and sources[0]['hash'] == self.xxh3(path),
                        'STALE_REPORT', 'compiled source does not match current '+role)
            for record in self.manifest['corners'][corner]['pin_tables']:
                source = self.file(record['library'])
                libraries = [x for x in meta['libraries'] if x['corner'] == corner and Path(x['source']).resolve() == source]
                require(len(libraries) == 1, 'REPORT_PROVENANCE', 'pin library missing from compiled corner')
                cached = folder/Path(libraries[0]['compiled']).with_suffix('.lib')
                require(cached.is_file() and sha(cached) == record['library']['sha256'],
                        'STALE_REPORT', 'compiled raw Liberty cache differs from current pin-library source')
                self.files[str(cached)] = record['library']['sha256']
            if scope['wire_model'] == 'spef_rc':
                expected = {self.file(value['spef']) for value in self.manifest['corners'].values()}
                annotated = {Path(x['path']).resolve() for x in meta['sources'] if x['table'] == 'parasitics'}
                require(annotated == expected, 'REPORT_PROVENANCE', 'explicit SPEF sources differ from compiled parasitics')
                for item in meta['sources']:
                    if item['table'] == 'parasitics':
                        require(item['hash'] == self.xxh3(Path(item['path']).resolve()), 'STALE_REPORT', 'SPEF changed after compilation')
            self.files[str(meta_path)] = sha(meta_path)
        finally:
            lock.close()
        section = env['sections'][kind]
        if kind == 'hold_fix':
            require(section['merged_total'] == len(section['merged']) <= self.args.max_rows, 'REPORT_TRUNCATED', 'merged hold rows incomplete')
            require(all(x['listed'] == x['shown'] == len(x['endpoints']) for x in section['corners']), 'REPORT_TRUNCATED', 'corner hold rows incomplete')
        else:
            require(section['shown'] == section['matched'] == len(section['rows']) <= self.args.max_rows,
                    'REPORT_TRUNCATED', kind+' rows incomplete')
            require(section.get('truncated', 0) == 0 and section.get('unevaluated', 0) == 0, 'REPORT_TRUNCATED', 'missing native checks')
            if kind == 'drv':
                require(all(isinstance(r.get('slack'), (int, float)) and math.isfinite(r['slack']) for r in section['rows']),
                        'REPORT', 'nonfinite DRV slack')
                for check, metric in [('max_transition', 'max_transition_violating'), ('max_capacitance', 'max_capacitance_violating'), ('max_fanout', 'max_fanout_violating')]:
                    found = sum(r['kind'] == check and r['slack'] < 0 for r in section['rows'])
                    require(found == env['metrics'][metric], 'REPORT_TRUNCATED', 'DRV rows omit globally counted '+check+' violations')
            elif kind == 'endpoints':
                require(section['matched'] == section['total'], 'REPORT_TRUNCATED', 'all endpoints required for setup windows')
        return env


def pin_views(inputs, corners):
    views = {}
    for corner in corners:
        view = {}
        for record in inputs.manifest['corners'][corner]['pin_tables']:
            inputs.file(record['library'])
            with inputs.file(record, 16 << 20).open() as stream:
                for row in csv.reader(stream, delimiter='\t'):
                    inputs.tick()
                    require(len(row) == 8, 'PIN_TABLE', 'canonical eight-column pin TSV required')
                    cell, pin, direction, cap, limit, fanout, clock, fan_limit = row
                    require((cell, pin) not in view and direction in ('Input', 'Output', 'Inout', 'Internal'), 'PIN_TABLE', 'duplicate or invalid pin')
                    optional = lambda x: None if x == 'none' else number(float(x))
                    require(clock in ('true', 'false'), 'PIN_TABLE', 'clock flag must be explicit')
                    view[cell, pin] = dict(cap=optional(cap), limit=optional(limit), fanout=number(float(fanout)),
                                           clock=clock == 'true', max_fanout=optional(fan_limit), direction=direction)
                    require(len(view) <= inputs.args.max_connections, 'BUDGET', 'pin-table budget')
        views[corner] = view
    memo = {}
    def merged(cell, pin):
        # The merged view of a (cell, pin) is the same every time: computed once, copied out.
        hit = memo.get((cell, pin))
        if hit is None:
            hit = memo[cell, pin] = merge(cell, pin)
        return dict(hit)
    def merge(cell, pin):
        values = [views[c].get((cell, pin)) for c in corners]
        require(all(v is not None for v in values), 'PIN_TABLE', f'pin absent in a corner: {cell}/{pin}')
        require(len({v['direction'] for v in values}) == 1, 'PIN_TABLE', 'mixed pin directions')
        limits = [v['limit'] for v in values if v['limit'] is not None]
        fan_limits = [v['max_fanout'] for v in values if v['max_fanout'] is not None]
        return dict(cap=max(v['cap'] for v in values) if all(v['cap'] is not None for v in values) else None,
                    limit=min(limits) if limits else None, fanout=max(v['fanout'] for v in values),
                    clock=any(v['clock'] for v in values), direction=values[0]['direction'],
                    max_fanout=min(fan_limits) if fan_limits else math.inf)
    return merged


def bit_labels(name, definition):
    bits = definition['bits']
    # offset, upto and is_bus are the same for every bit: read once.
    count, offset, upto = len(bits), definition.get('offset', 0), definition.get('upto', 0)
    bus = definition.get('is_bus', count > 1 or offset != 0 or upto)
    for index, bit in enumerate(bits):
        position = offset + (count-1-index if upto else index)
        yield (f'{name}[{position}]' if bus else name), bit


def io_projection(inputs, sdc):
    io = inputs.document(inputs.manifest['io'], 1 << 20)
    require(io['sdc_sha256'] == sha(sdc), 'STALE_INPUT', 'IO projection belongs to another SDC')
    drivers, loads = {}, {}
    # The qualified input is write_sdc's literal IO syntax. Do not interpret Tcl.
    for line in sdc.read_text().splitlines():
        line = line.strip()
        require(not line.startswith('set_units'), 'SDC_IO_UNSUPPORTED', 'IO policy requires default ns/pF units')
        if line.startswith('set_driving_cell'):
            match = re.fullmatch(r'set_driving_cell -lib_cell (\S+) -pin \{([^{}]+)\} -input_transition_rise ([0-9.eE+-]+) -input_transition_fall ([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]', line)
            require(match is not None, 'SDC_IO_UNSUPPORTED', 'nonliteral driving-cell projection')
            require(float(match[3]) == 0 and float(match[4]) == 0, 'SDC_IO_UNSUPPORTED', 'nonzero virtual-driver input transition')
            require(match[5] not in drivers, 'SDC_IO_UNSUPPORTED', 'duplicate input driver')
            drivers[match[5]] = {'cell': match[1], 'pin': match[2]}
        elif line.startswith('set_load'):
            match = re.fullmatch(r'set_load (?:-pin_load )?([0-9.eE+-]+) \[get_ports \{([^{}]+)\}\]', line)
            require(match is not None, 'SDC_IO_UNSUPPORTED', 'nonliteral output-load projection')
            for port in match[2].split():
                require(port not in loads, 'SDC_IO_UNSUPPORTED', 'duplicate output load')
                loads[port] = number(float(match[1]))
    require(io['drivers'] == drivers and io['loads_pf'] == loads, 'SDC_IO_MISMATCH', 'IO projection differs from original literal SDC')
    return io


class Graph:
    def __init__(self, inputs, document, top, pins, io):
        self.inputs, self.pins = inputs, pins
        require(top in document['modules'], 'NETLIST', 'top module missing')
        self.top, self.modules = document['modules'][top], document['modules']
        self.cells = self.top['cells']
        require(len(self.cells) <= inputs.args.max_cells, 'BUDGET', 'cell budget exceeded')
        self.loads, self.drivers, self.clock_bits, self.pin_bits, self.names = {}, {}, set(), {}, set()
        loads, drivers, clock_bits, pin_bits = self.loads, self.drivers, self.clock_bits, self.pin_bits
        count, max_connections, max_work = 0, inputs.args.max_connections, inputs.args.max_work
        # The pin labels of a port depend only on its cell type: labelled once per (type, port).
        port_labels, checked = {}, {}
        for inst, cell in self.cells.items():
            self.names.add(inst)
            kind = cell['type']
            for port, bits in cell['connections'].items():
                labels = port_labels.get((kind, port))
                if labels is None:
                    definition = self.modules[kind]['ports'][port]
                    labels = port_labels[kind, port] = [pin for pin, _ in bit_labels(port, definition)]
                require(len(bits) == len(labels), 'NETLIST', 'cell port width mismatch')
                # One work tick and one connection per bit, counted per port while both budgets
                # hold for the whole port; otherwise bit by bit, so a budget fails where it did.
                if inputs.work + len(bits) <= max_work and count + len(bits) <= max_connections:
                    inputs.work += len(bits); count += len(bits); per_bit = False
                else:
                    per_bit = True
                direction = None
                for pin, bit in zip(labels, bits):
                    if per_bit:
                        inputs.tick(); count += 1
                        require(count <= max_connections, 'BUDGET', 'connection budget exceeded')
                    if not isinstance(bit, int):
                        continue
                    if direction is None:
                        direction = cell['port_directions'][port]
                    # The pin model and its direction check depend only on (type, pin, direction).
                    model = checked.get((kind, pin, direction))
                    if model is None:
                        model = pins(kind, pin)
                        require(direction.lower() == model['direction'].lower(), 'NETLIST', 'JSON/Liberty direction mismatch')
                        checked[kind, pin, direction] = model
                    row = {'instance': inst, 'pin': pin, 'cell': kind, **model}
                    pin_bits[inst, pin] = bit
                    if direction == 'input':
                        if bit in loads: loads[bit].append(row)
                        else: loads[bit] = [row]
                    elif direction == 'output':
                        require(bit not in drivers, 'NETLIST', 'multiple signal drivers')
                        drivers[bit] = row
                    else: raise Failure('NETLIST', 'inout signal ECO unsupported')
                    if model['clock']: clock_bits.add(bit)
        self.net_bits = {}
        require(len(self.top['netnames']) <= inputs.args.max_connections, 'BUDGET', 'net-name budget exceeded')
        for name, definition in self.top['netnames'].items():
            self.names.add(name)
            for label, bit in bit_labels(name, definition): self.net_bits[label] = bit
        for name, definition in self.top['ports'].items():
            for label, bit in bit_labels(name, definition):
                if not isinstance(bit, int): continue
                if definition['direction'] == 'input':
                    require(label in io['drivers'] and bit not in self.drivers, 'SDC_IO_MISMATCH', 'missing input driving cell')
                    d = io['drivers'][label]; model = pins(d['cell'], d['pin'])
                    require(model['direction'] == 'Output', 'SDC_IO_MISMATCH', 'driving-cell pin is not output')
                    self.drivers[bit] = dict(instance=None, pin=label, cell=d['cell'], **model)
                elif definition['direction'] == 'output':
                    require(label in io['loads_pf'], 'SDC_IO_MISMATCH', 'missing output load')
                    self.loads.setdefault(bit, []).append(dict(instance=None, pin=None, port=label, cell=None,
                        cap=number(io['loads_pf'][label]), fanout=0.0, max_fanout=math.inf, clock=False))
                else: raise Failure('NETLIST', 'top inout unsupported')

    def bit(self, name):
        require(name in self.net_bits and isinstance(self.net_bits[name], int), 'NETLIST', 'unresolved scalar net: '+name)
        return self.net_bits[name]

    def sink_binding(self, row):
        inst, pin = row['endpoint'].rsplit('/', 1)
        require((inst, pin) in self.pin_bits and self.cells[inst]['type'] == row['cell'], 'STALE_REPORT', 'endpoint master/pin mismatch')
        bit = self.pin_bits[inst, pin]
        driver = self.drivers.get(bit)
        require(driver is not None, 'STALE_REPORT', 'endpoint driver missing')
        actual = driver['pin'] if driver['instance'] is None else driver['instance']+'/'+driver['pin']
        # CTS regroup_prefixes may move a sink to another buffer of the same
        # fanout/reset tree level: same source signal, so the pin binding holds.
        level = lambda d: (m := re.fullmatch(r'(native_(?:fanout|reset)_[0-9]+_[0-9]+)_[0-9]+/[A-Za-z0-9_]+', d)) and m[1]
        regrouped = level(actual) is not None and level(actual) == level(row['net_driver'])
        require(regrouped or bit == self.bit(row['net']), 'STALE_REPORT', 'endpoint net mismatch: '+row['endpoint'])
        require(regrouped or actual == row['net_driver'], 'STALE_REPORT', 'endpoint driver mismatch: '+row['endpoint'])
        return inst, pin, bit


class Planner:
    def __init__(self, inputs, policy, graph):
        self.i, self.p, self.g = inputs, policy, graph
        self.ops, self.checks, self.excluded = [], [], []
        self.weights = {}; self.repaired = set()
        self.bus_rewrite = 'cell_buses' in inputs.manifest['profiles']
        self.fanout = int(policy['target_fanout']); self.fraction = number(policy['cap_fraction'], positive=True)
        require(2 <= self.fanout <= 9 and self.fraction <= 1, 'POLICY', 'qualified fanout/cap bounds')
        self.prefix = handle(policy.get('prefix', 'native_eco'))
        self.buffers = {b['cell']: b for b in policy['buffers']}
        require(len(self.buffers) == len(policy['buffers']) <= 32, 'POLICY', 'buffer selection count')
        require(policy['clock_roots'] and policy['primary_clock'] in policy['clock_roots'], 'POLICY', 'primary and gated roots must be explicit')
        self.clocks = {graph.bit(n) for n in policy['clock_roots']} | graph.clock_bits

    @staticmethod
    def prefix_of(operation):
        return 'native_'+operation.replace('-', '_')

    def model(self, cell):
        require(cell in self.buffers, 'POLICY', 'buffer not explicitly selected: '+cell)
        b = self.buffers[cell]; a = self.g.pins(cell, b['input']); z = self.g.pins(cell, b['output'])
        require(a['direction'] == 'Input' and z['direction'] == 'Output' and a['cap'] is not None and z['limit'] is not None,
                'PIN_TABLE', 'selected buffer lacks cap/direction bounds')
        return b, a, z

    def fits(self, group, output):
        self.i.tick(len(group)+1)
        require(all(x['cap'] is not None for x in group), 'PIN_TABLE', 'selected load has no capacitance')
        return (len(group) <= self.fanout and sum(x['fanout'] for x in group) <= min(self.fanout, output['max_fanout'])
                and (output['limit'] is None or sum(x['cap'] for x in group)+(output.get('cap') or 0) <= output['limit']*self.fraction))

    def append(self, op):
        require(len(self.ops) < self.i.args.max_operations, 'BUDGET', 'ECO operation budget exceeded')
        for name in (op['instance'], op['net']):
            handle(name); require(name not in self.g.names, 'HANDLE', 'ECO name collision: '+name); self.g.names.add(name)
        self.ops.append(op)

    def port_diodes(self, geo=None):
        cfg = self.p['port_diode']
        require(isinstance(cfg, dict) and set(cfg) == {'cell', 'pin'}, 'POLICY', 'port diode policy')
        handle(cfg['cell']); handle(cfg['pin'])
        count = 0
        for port, definition in self.g.top['ports'].items():
            if definition['direction'] != 'input':
                continue
            for label, bit in bit_labels(port, definition):
                if not isinstance(bit, int):
                    continue
                name = handle(f'{self.prefix}_port_diode_{count}')
                require(len(self.ops) < self.i.args.max_operations, 'BUDGET', 'ECO operation budget exceeded')
                require(name not in self.g.names, 'HANDLE', 'ECO name collision: '+name); self.g.names.add(name)
                self.ops.append(dict(op='insert_diode', cell=cfg['cell'], instance=name, net=label))
                if geo is not None: geo.place(name, cfg['cell'], geo.pin(None, label))
                count += 1; self.i.tick()
        require(count > 0, 'POLICY', 'port diode policy found no primary input bit')
        self.checks.append(dict(port_diodes=count, cell=cfg['cell']))

    def buffer_group(self, cell, group, name):
        b, a, z = self.model(cell)
        require(self.fits(group, z), 'CAPACITY', 'buffer leaf exceeds corner cap/fanout bound')
        require(self.bus_rewrite or all(x['pin'] is None or '[' not in x['pin'] for x in group),
                'UNSUPPORTED_VECTOR_SINK', 'grouped bus ECO requires explicit cell-bus metadata')
        op = dict(op='buffer_sinks', cell=cell, instance=name, net=name+'_n',
                  sinks=[dict(instance=x['instance'], pin=x['pin']) for x in group if x['instance'] is not None],
                  ports=[x['port'] for x in group if x['instance'] is None])
        self.append(op)
        self.checks.append(dict(instance=name, cell=cell, cap_pf=sum(x['cap'] for x in group)+(z['cap'] or 0),
                                fanout=sum(x['fanout'] for x in group), cap_limit_pf=z['limit']))
        return dict(instance=name, pin=b['input'], cell=cell, **a)

    def tree(self, bit, label, forced=None, pin_filter=None):
        require(bit in self.g.drivers, 'NETLIST', 'repair root has no driver')
        root = self.g.drivers[bit]
        current = sorted(self.g.loads.get(bit, []), key=lambda x: (x['instance'] or '', x['pin'] or x['port']))
        fixed = []
        if pin_filter:
            fixed = [x for x in current if x['pin'] != pin_filter]
            current = [x for x in current if x['pin'] == pin_filter]
        require(current, 'POLICY', 'selected repair has no matching sinks')
        depth = 0
        while not self.fits(fixed+current, root):
            require(depth < self.i.args.max_depth, 'BUDGET', 'buffer tree depth budget')
            candidates = [forced[0 if depth == 0 else 1]] if forced else list(self.buffers)
            following, group = [], []
            def choose(items):
                return next((c for c in candidates if self.fits(items, self.model(c)[2])), None)
            for item in current:
                if group and choose(group+[item]) is None:
                    cell = choose(group); following.append(self.buffer_group(cell, group, f'{label}_{depth}_{len(following)}')); group = []
                group.append(item)
                require(choose(group) is not None, 'CAPACITY', 'single load exceeds every selected buffer')
            if group:
                following.append(self.buffer_group(choose(group), group, f'{label}_{depth}_{len(following)}'))
            require(len(following) < len(current) or depth == 0, 'BUDGET', 'buffer tree fails to contract')
            current = following; depth += 1
        self.checks.append(dict(root=label, depth=depth, fanout=sum(x['fanout'] for x in fixed+current),
                                cap_pf=sum(x['cap'] for x in fixed+current)+(root['cap'] or 0), cap_limit_pf=root['limit']))

    def reset(self):
        cfg = self.p['reset']; bit = self.g.bit(cfg['net'])
        require(bit not in self.clocks, 'POLICY', 'reset root is a clock net')
        self.tree(bit, self.prefix, (cfg['leaf'], cfg['trunk']), cfg['pin'])

    def fanout_repair(self, reports):
        requested = sorted({r['net'] for report in reports for r in report['sections']['drv']['rows']
                            if r['kind'] in ('max_fanout', 'max_capacitance') and r['slack'] < 0})
        require(len(requested) <= self.i.args.max_cells, 'BUDGET', 'reported repair root budget')
        seen = set()
        for index, name in enumerate(requested):
            bit = self.g.bit(name)
            if bit in seen or bit in self.clocks:
                self.excluded.append({'net': name, 'reason': 'alias' if bit in seen else 'explicit clock/CTS scope'}); continue
            seen.add(bit); self.tree(bit, f'{self.prefix}_{index}')

    def primary(self):
        bit = self.g.bit(self.p['primary_clock']); cell = self.p['primary_buffer']
        group = self.g.loads.get(bit, [])
        require(all(x['cap'] is not None for x in group), 'PIN_TABLE', 'primary clock load lacks capacitance')
        count = math.ceil(len(group)/self.fanout)
        if count <= 1 and self.fits(group, self.g.drivers[bit]): return
        require(1 <= count <= self.i.args.max_operations, 'BUDGET', 'primary-clock branch budget')
        groups = [[] for _ in range(count)]; output = self.model(cell)[2]
        for item in sorted(group, key=lambda x: (-x['cap'], x['instance'] or '', x['pin'] or '')):
            possible = [j for j, g in enumerate(groups) if self.fits(g+[item], output)]
            require(possible, 'CAPACITY', 'primary clock split exceeds selected buffer')
            at = min(possible, key=lambda j: (sum(x['cap'] for x in groups[j]), len(groups[j]), j)); groups[at].append(item)
        roots = [self.buffer_group(cell, g, f'{self.prefix}_{j}') for j, g in enumerate(groups)]
        require(self.fits(roots, self.g.drivers[bit]), 'CAPACITY', 'primary source capacity exceeded')


# Wire load allowance of one chain stage: stages are placed at the same pin.
CHAIN_WIRE_PF = 0.01


def chain_setup(bounds, corners, late, stages, heavy_last):
    """Upper bound (ns) on the setup cost of `stages` delay stages on one path:
    each drives a light load, except a launch chain's last stage, which drives
    the original sinks and is bounded by the whole table."""
    return late*max(stages*bounds[c]['chain']+(bounds[c]['max']-bounds[c]['chain'] if heavy_last and stages else 0.0) for c in corners)


def delay_bounds(inputs, policy, corners):
    path = inputs.tool('liberty_reader')
    spec = importlib.util.spec_from_file_location('rusteda_native_eco_liberty', path)
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    names = set(policy['delay_cells']); require(1 <= len(names) <= 16, 'POLICY', 'delay cell selection budget')
    output = {name: {} for name in names}
    for corner in corners:
        libraries = {inputs.file(x['library']) for x in inputs.manifest['corners'][corner]['pin_tables']}
        for source in sorted(libraries):
            lib = reader.Library(source, wanted=names)
            for name in names & lib.cells.keys():
                require(corner not in output[name], 'LIBERTY', 'duplicate selected delay master')
                cell = lib.cells[name]; pins = cell.sub('pin')
                a = [p for p in pins if p.attr('direction') == 'input']; z = [p for p in pins if p.attr('direction') == 'output']
                require(len(a) == len(z) == 1 and z[0].attr('function').strip().strip('()').strip() == a[0].name,
                        'LIBERTY', 'delay cell is not a single-input identity')
                arcs = z[0].sub('timing')
                require(len(arcs) == 1 and arcs[0].attr('related_pin') == a[0].name and arcs[0].attr('timing_sense') == 'positive_unate', 'LIBERTY', 'delay arc identity mismatch')
                tables = [reader.Table(arcs[0].sub(k)[0], lib) for k in ('cell_rise', 'cell_fall')]
                # A chain stage that drives only the next stage (or one endpoint
                # pin) sees at most its own input pin plus CHAIN_WIRE_PF. Delay
                # rises with load, so the table column at the first load index
                # at or above that bound, maximized over every input slew row,
                # bounds the stage. Without such a column it is the table max.
                light = float(a[0].attr('capacitance'))*lib.cap_unit_pf()+CHAIN_WIRE_PF
                def light_max(t):
                    require(t.variables == ['input_net_transition', 'total_output_net_capacitance'], 'LIBERTY', 'delay table axes')
                    column = next((j for j, v in enumerate(t.index_2) if v*lib.cap_unit_pf() >= light), None)
                    return max(map(max, t.values)) if column is None else max(row[column] for row in t.values)
                output[name][corner] = dict(min=min(min(map(min, t.values)) for t in tables)*lib.time_unit_ns(),
                    max=max(max(map(max, t.values)) for t in tables)*lib.time_unit_ns(),
                    chain=max(light_max(t) for t in tables)*lib.time_unit_ns(), area=number(float(cell.attr('area'))))
    require(all(set(v) == set(corners) for v in output.values()), 'LIBERTY', 'missing delay model in a corner')
    return output


def ideal_hold(planner, reports, setups, corners):
    inputs, policy = planner.i, planner.p
    require(policy['scope'] == 'ideal_diagnostic', 'REPORT_SCOPE', 'ideal hold requires diagnostic policy')
    bounds = delay_bounds(inputs, policy, corners)
    hold = reports[0]['sections']['hold_fix']; rows = hold['merged']
    require(all(x['sections']['hold_fix'] == hold for x in reports), 'REPORT', 'inconsistent merged hold reports')
    factors = reports[0]['scope'].get('global_timing_derate', [1., 1.])
    require(all(x['scope'].get('global_timing_derate', [1., 1.]) == factors for x in reports), 'REPORT_SCOPE', 'inconsistent derates')
    early, late = map(lambda v: number(v, positive=True), factors)
    target = number(policy['hold_margin_ns']); setup_margin = number(policy['setup_margin_ns'])
    require(hold['target_margin_ns'] >= target, 'REPORT', 'hold report target insufficient')
    setup_views = {c: {r['endpoint']: r for r in x['sections']['endpoints']['rows']} for c, x in zip(corners, setups)}
    details = {}
    for co in hold['corners']:
        for detail in co['endpoints']:
            inputs.tick()
            details.setdefault(detail['endpoint'], detail)
    for index, row in enumerate(rows):
        inputs.tick(); slacks = row['hold_slack_by_corner_ns']
        require(set(slacks) == set(corners) and all(v is not None and math.isfinite(v) for v in slacks.values()), 'REPORT', 'missing corner hold slack')
        if min(slacks.values()) >= target: continue
        detail = details.get(row['endpoint'])
        require(detail is not None, 'REPORT', 'merged hold endpoint lacks structural binding')
        inst, pin, bit = planner.g.sink_binding(detail)
        require(bit not in planner.clocks and (planner.bus_rewrite or '[' not in pin),
                'POLICY', 'clock or bus sink lacks an explicit supported delay point')
        values = [setup_views[c].get(row['endpoint'], {}).get('setup_slack_ns') for c in corners]
        require(all(x is not None and math.isfinite(x) for x in values), 'REPORT', 'missing all-corner setup bound')
        setup = min(values)
        candidates = []
        for cell, corner in bounds.items():
            for count in range(1, inputs.args.max_depth+1):
                inputs.tick()
                if all(count*early*corner[c]['min'] >= target-slacks[c] for c in corners):
                    after = setup-chain_setup(corner, corners, late, count, False)
                    if after >= setup_margin: candidates.append((count*max(v['area'] for v in corner.values()), count, cell, after))
                    break
        require(candidates, 'HOLD_INFEASIBLE', 'no bounded all-corner delay chain: '+row['endpoint'])
        _, count, cell, after = min(candidates)
        for level in range(count):
            name = f'{planner.prefix}_{index:04}_{level}'
            planner.append(dict(op='insert_buffer', cell=cell, instance=name, net=name+'_n', sink=dict(instance=inst, pin=pin)))
        planner.checks.append(dict(endpoint=row['endpoint'], count=count, cell=cell, conservative_setup_ns=after))
    return {'basis': 'original selected-cell table extrema; characterization-domain estimate only',
            'target_estimate_ns': target, 'bounds': bounds, 'unverified': ['actual post-edit timing', 'fresh CTS', 'RC', 'DRC', 'LVS', 'CEC']}


HEAVY = 16000  # rustroad MAX_NET_WEIGHT (milli-units)


class Geometry:
    """Pin locations (um) of the timed placement and the seed of the next one."""
    def __init__(self, inputs):
        self.i = inputs; place = inputs.manifest['placement']
        path = inputs.file(inputs.manifest['tools']['estimator'], 1 << 20)
        spec = importlib.util.spec_from_file_location('rusteda_native_eco_estimate', path)
        self.est = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.est)
        self.rc = inputs.document(inputs.manifest['rc_policy'], 1 << 20)
        require(self.rc.get('schema') == 'rusteda.rc-estimate-policy.v1', 'SCHEMA', 'RC estimate policy schema')
        self.macros = self.est.read_lef(inputs.file(inputs.manifest['profiles']['lef'], 64 << 20))
        limits = self.rc['limits']
        self.comps, self.ports, connections, self.scale, _ = self.est.read_def(inputs.file(place['def'], 256 << 20),
                                                                   int(limits['max_nets']), int(limits['max_pins_per_net']))
        self.connections = dict(connections)
        self.blockages = self.est.blockages_for(self.rc, inputs.file(inputs.manifest['profiles']['lef'], 64 << 20),
                                                inputs.file(place['def'], 256 << 20), self.comps, self.scale)
        self.base = inputs.document(place['seed'], 64 << 20)
        require(self.base.get('schema') == 'rustroad.placement-seed.v1', 'SCHEMA', 'placement seed schema')
        self.dbu = float(self.base['source']['units_per_micron'])
        self.new = {}

    def pin(self, instance, pin):
        """Location of an instance pin, or of a top-level port when instance is None."""
        if instance is None:
            port = self.ports.get(pin)
            require(port is not None and port[3] is not None, 'PLACEMENT', 'unplaced port '+str(pin))
            x, y = port[3]
            if port[2]:
                x += (port[2][0]+port[2][2])/2; y += (port[2][1]+port[2][3])/2
            return x/self.scale, y/self.scale
        if instance in self.new:
            return self.new[instance][1]
        require(instance in self.comps, 'PLACEMENT', 'unplaced instance '+instance)
        master, ox, oy, orient = self.comps[instance]
        w, h, pins = self.macros[master]
        require(pin in pins, 'PLACEMENT', f'pin without LEF geometry {master}/{pin}')
        dx, dy = self.est.ORIENT[orient](pins[pin][0], pins[pin][1], w, h)
        return ox/self.scale+dx, oy/self.scale+dy

    def place(self, instance, cell, at):
        """Seed a new cell centred on at (um); legalization snaps it to a row."""
        require(cell in self.macros and instance not in self.new, 'PLACEMENT', 'new cell geometry')
        self.new[instance] = (cell, at)

    # A centre point inside a placed CLASS BLOCK macro moves this far beyond
    # its bottom or top edge (rows run along both).
    OUTSIDE_UM = 2.0

    def outside(self, at, points):
        """A centre (um) of points moved out of any macro containing it.

        The centre of points on opposite sides of an SRAM lies inside it; the
        legalizer then puts the cell at an arbitrary far row (fe16l: a BUFX3
        seeded at (392, 596) inside imem was legalized at (1072, 407) and
        drove 0.356 pF). The bottom- or top-edge projection with the shorter
        estimated tree to points is used instead, and the cell is sized there.
        """
        if self.blockages is None: return at
        x, y = at
        for lx, ly, ux, uy in self.blockages.rects:
            if lx < x < ux and ly < y < uy:
                options = [(x, ly-self.OUTSIDE_UM), (x, uy+self.OUTSIDE_UM)]
                return min(options, key=lambda q: (self.wire_pf([q]+list(points))[1], q[1]))
        return at

    def wire_pf(self, points):
        """Estimated wire capacitance of a net over points (um), as the estimator models it."""
        if len(points) < 2: return 0.0
        self.i.tick(len(points)**2)
        length = self.est.tree_length(points, self.est.mst(points), self.blockages)
        return length*float(self.rc['detour'])*float(self.rc['c_pf_per_um']), length*float(self.rc['detour'])

    def write_seed(self, planner, path, clock_order):
        cells = dict(self.base['cells'])
        for name, (cell, (x, y)) in self.new.items():
            w, h, _ = self.macros[cell]
            require(name not in cells, 'PLACEMENT', 'new cell already seeded: '+name)
            cells[name] = [round((x-w/2)*self.dbu), round((y-h/2)*self.dbu)]
        seed = dict(schema='rustroad.placement-seed.v1', source={**self.base['source'], 'eco_cells': len(self.new)}, cells=cells)
        sinks = self.base.get('clock_sinks', [])
        require(isinstance(sinks, list) and all(isinstance(s, str) for s in sinks) and len(set(sinks)) == len(sinks), 'SCHEMA', 'seed clock sinks')
        if clock_order and sinks: seed['clock_sinks'] = sinks
        regroup = self.base.get('regroup', {})
        require(isinstance(regroup, dict) and all(isinstance(v, list) and all(isinstance(s, list) and len(s) == 2 for s in v) for v in regroup.values()),
                'SCHEMA', 'seed regroup partition')
        if clock_order and regroup:
            # The first chain stage inserted in front of a regrouped sink pin
            # is that tree net's sink now (later stages sit behind it).
            first = {}
            for op in planner.ops:
                if op['op'] == 'insert_buffer':
                    inputs = [n for n, pin in self.macros[op['cell']][2].items() if pin[2] == 'INPUT']
                    require(len(inputs) == 1, 'PLACEMENT', 'chain cell input pin: '+op['cell'])
                    first.setdefault((op['sink']['instance'], op['sink']['pin']), [op['instance'], inputs[0]])
            seed['regroup'] = {b: [first.get(tuple(s), s) for s in sinks] for b, sinks in sorted(regroup.items())}
        path.write_text(json.dumps(seed, indent=1)+'\n')
        return dict(new_cells=len(self.new), seeded_cells=len(cells), clock_sinks=len(seed.get('clock_sinks', [])),
                    regrouped_buffers=len(seed.get('regroup', {})))


def electrical(planner, reports, geo, hold_round=False):
    """Wire-aware DRV repair on estimated-RC reports (see module docstring)."""
    cfg = planner.p['electrical']; g = planner.g
    require(set(cfg) == {'driver_cells', 'sram_buffer', 'sram_clock_buffer', 'sram_masters', 'load_fraction', 'max_wire_um', 'antenna_cells',
                         'drv_margin', 'hold_drv_margin'} | ({'cap_feedback', 'clock_balance'} & set(cfg)),
            'POLICY', 'electrical policy fields')
    fraction = number(cfg['load_fraction'], positive=True); require(fraction <= 1, 'POLICY', 'load fraction')
    margin = number(cfg['hold_drv_margin' if hold_round else 'drv_margin'])
    require(0 <= number(cfg['hold_drv_margin']) <= number(cfg['drv_margin']) < 0.5, 'POLICY', 'DRV margins')
    max_wire = number(cfg['max_wire_um'], positive=True)
    drivers = []
    for cell in cfg['driver_cells']:
        _, a, z = planner.model(cell); drivers.append((z['limit'], cell, a['cap']))
    drivers.sort()
    def choose(load):
        cell = next((c for limit, c, _ in drivers if limit*fraction >= load), None)
        require(cell is not None, 'CAPACITY', f'estimated load {load:.3f} pF exceeds every selected driver')
        return cell
    # Optional post-route feedback is a bounded, provenance-bound repair input.
    # Apply it only after the unchanged electrical round, so the witnessed
    # repaired placement can be checked against current driver/sink identities.
    # It never changes SPEF, STA results, or the final signoff acceptance limits.
    feedback = {}
    if hold_round and 'cap_feedback' in cfg:
        evidence = planner.i.document(cfg['cap_feedback'], 1 << 20)
        require(evidence.get('schema') == 'rusteda.cap-feedback.v1'
                and evidence.get('application') == 'hold'
                and evidence.get('scope') == 'repair_selection_only_not_signoff',
                'CAP_FEEDBACK', 'post-route feedback schema/application')
        utilization = number(evidence['selection_utilization'], positive=True)
        require(0.5 <= utilization < 1, 'CAP_FEEDBACK', 'cap feedback selection utilization')
        sources = evidence['source_files']
        require(set(sources) == {'drv', 'spef', 'timing_inputs', 'repair_def', 'final_def', 'generator', 'drv_ss', 'corner_coverage'},
                'CAP_FEEDBACK', 'cap feedback source roles')
        for record in sources.values(): planner.i.file(record, 128 << 20)
        rows = evidence['rows']
        require(isinstance(rows, list) and 0 < len(rows) <= 256, 'BUDGET', 'cap feedback row count')
        for row in rows:
            planner.i.tick()
            name, label = row['net'], row['driver']
            require(name not in feedback and name in geo.connections, 'CAP_FEEDBACK', 'duplicate/missing feedback net '+name)
            conns = geo.connections[name]
            signature = sorted([inst, pin, geo.comps[inst][0] if inst != 'PIN' else 'PORT'] for inst, pin in conns)
            digest = hashlib.sha256(json.dumps(signature, separators=(',', ':')).encode()).hexdigest()
            require(len(conns) == row['connection_count'] and digest == row['connections_sha256'],
                    'CAP_FEEDBACK_IDENTITY', 'driver/sink/master topology differs from extracted witness: '+name)
            inst, pin = label.rsplit('/', 1)
            require((inst, pin) in map(tuple, conns) and geo.comps[inst][0] == row['driver_cell']
                    and geo.macros[row['driver_cell']][2][pin][2] == 'OUTPUT',
                    'CAP_FEEDBACK_IDENTITY', 'extracted driver identity differs: '+label)
            load, limit = number(row['observed_load_pf'], positive=True), number(row['observed_limit_pf'], positive=True)
            require(load > utilization*limit and row['corner'] in planner.i.manifest['corners'],
                    'CAP_FEEDBACK', 'row outside declared feedback selection: '+name)
            feedback[name] = row
    nets, feedback_seen = {}, set()
    for env in reports:
        for r in env['sections']['drv']['rows']:
            # The next placement moves pins: a transition or load within the
            # margin of its limit is repaired like a violation.
            near = r['kind'] in ('max_capacitance', 'max_transition') and r['value'] > r['limit']*(1-margin)
            witness = feedback.get(r['net']) if r['kind'] == 'max_capacitance' else None
            if witness is not None:
                require(r['pin'] == witness['driver'], 'CAP_FEEDBACK_IDENTITY', 'current cap report driver differs: '+r['net'])
                feedback_seen.add(r['net'])
            if r['slack'] >= 0 and not near and witness is None: continue
            info = nets.setdefault(r['net'], dict(load=0.0, slew_pins=set(), driver_slew=False))
            if r['kind'] == 'max_capacitance':
                info['load'] = max(info['load'], r['value'], witness['observed_load_pf'] if witness else 0.0)
            elif r['kind'] == 'max_transition': info['slew_pins'].add(r['pin'])
            planner.i.tick()
    require(feedback_seen == set(feedback), 'CAP_FEEDBACK_IDENTITY', 'current cap report omits feedback nets')
    # The STA DRV rows omit input-port driving cells (run14: rst_ni 0.403 pF on a
    # 0.4 pF BUFX4, seen only at signoff). Check every nonclock port net with the
    # same estimate and margin; a near-limit one is isolated at its port below.
    for label, bit in sorted((n, b) for n, b in g.net_bits.items() if isinstance(b, int)):
        d = g.drivers.get(bit)
        if d is None or d['instance'] is not None or d['pin'] != label or bit in planner.clocks or not d['limit']: continue
        loads = g.loads.get(bit, [])
        if not loads: continue
        planner.i.tick(len(loads))
        wire, length = geo.wire_pf([geo.pin(None, label)]+[geo.pin(x['instance'], x.get('pin') if x['instance'] else x['port']) for x in loads])
        load = wire+sum(x['cap'] for x in loads)
        if load <= d['limit']*(1-margin): continue
        info = nets.setdefault(label, dict(load=0.0, slew_pins=set(), driver_slew=False)); info['load'] = max(info['load'], load)
        planner.checks.append(dict(net=label, action='port_drv', driver_cell=d['cell'], estimated_load_pf=round(load, 6),
                                   limit_pf=d['limit'], margin=margin, estimated_wire_um=round(length, 1)))
    planner.feedback_nets = set(feedback)
    if feedback:
        planner.checks.append(dict(action='cap_feedback', policy=cfg['cap_feedback'], nets=len(feedback),
                                   basis='observed extracted load floor; checked driver/sink/master identity'))
    trees, count = {}, 0
    for name in sorted(nets):
        info = nets[name]
        if name not in g.net_bits or not isinstance(g.net_bits[name], int):
            planner.excluded.append({'net': name, 'reason': 'not an editable net of the pre-CTS netlist'}); continue
        bit = g.bit(name)
        d = g.drivers.get(bit); loads = g.loads.get(bit, [])
        if bit in planner.clocks:
            # Only a primary-clock ECO branch (never the CTS tree) may buffer its
            # violating SRAM clock pins, each at the pin.
            fixed = []
            if d is not None and re.fullmatch(planner.prefix_of('primary-clock')+r'_[0-9]+', d['instance'] or ''):
                for x in loads:
                    if x['instance'] and x['cell'] in cfg['sram_masters'] and x['instance']+'/'+x['pin'] in info['slew_pins']:
                        inst = f'{planner.prefix}_{count}'; count += 1
                        planner.append(dict(op='buffer_sinks', cell=cfg['sram_clock_buffer'], instance=inst, net=inst+'_n',
                                            sinks=[dict(instance=x['instance'], pin=x['pin'])], ports=[]))
                        geo.place(inst, cfg['sram_clock_buffer'], geo.pin(x['instance'], x['pin'])); fixed.append(x['pin']); planner.weights[inst+'_n'] = HEAVY
            if fixed:
                planner.checks.append(dict(net=name, action='sram_clock_pin_buffer', pins=fixed, cell=cfg['sram_clock_buffer']))
            else:
                planner.excluded.append({'net': name, 'reason': 'explicit clock/CTS scope'})
            continue
        require(d is not None and loads, 'NETLIST', 'violating net lacks driver or load: '+name)
        label = d['pin'] if d['instance'] is None else d['instance']+'/'+d['pin']
        tree = re.fullmatch(r'(native_(?:fanout|reset)_[0-9]+)_([0-9]+)_[0-9]+', d['instance'] or '')
        here = [geo.pin(d['instance'], d['pin'])]+[geo.pin(x['instance'], x.get('pin') if x['instance'] else x['port']) for x in loads]
        wire, length = geo.wire_pf(here)
        load = max(info['load'], wire+sum(x['cap'] for x in loads))
        if tree:
            key = (tree[1], int(tree[2])); trees[key] = max(trees.get(key, 0.0), load); continue
        moved, added = set(), []
        for x in loads:
            pin = x['instance'] and x['instance']+'/'+x['pin']
            if x['instance'] and x['cell'] in cfg['sram_masters'] and pin in info['slew_pins']:
                inst = f'{planner.prefix}_{count}'; count += 1; b, a, _ = planner.model(cfg['sram_buffer'])
                planner.append(dict(op='buffer_sinks', cell=cfg['sram_buffer'], instance=inst, net=inst+'_n',
                                    sinks=[dict(instance=x['instance'], pin=x['pin'])], ports=[]))
                geo.place(inst, cfg['sram_buffer'], geo.pin(x['instance'], x['pin'])); moved.add(pin); planner.weights[inst+'_n'] = HEAVY; planner.repaired.add(name)
                # The new pin buffer is now a load of this net, at the SRAM pin.
                added.append(dict(instance=inst, pin=b['input'], cell=cfg['sram_buffer'], cap=a['cap'], port=None))
                planner.checks.append(dict(net=name, action='sram_pin_buffer', pin=pin, cell=cfg['sram_buffer']))
        rest = [x for x in loads if not (x['instance'] and x['instance']+'/'+x['pin'] in moved) and x['cell'] not in cfg['antenna_cells']]+added
        other_slew = bool(info['slew_pins'] - moved)
        if added:
            pts = [here[0]]+[geo.pin(x['instance'], x.get('pin') if x['instance'] else x['port']) for x in rest]
            wire, length = geo.wire_pf(pts); load = max(load, wire+sum(x['cap'] for x in rest))
            # Isolate the driver too when the remaining net (to the pin buffers) exceeds its own limit.
            other_slew = other_slew or load > (d['limit'] or math.inf)*fraction
        if not rest or not (info['load'] > 0 or other_slew): continue
        groups = [rest]
        if length > max_wire and len(rest) > 1:
            # Recursive bisection along the longer side until each cluster's own wire fits.
            groups, done = [rest], []
            while groups:
                part = groups.pop(); planner.i.tick(len(part))
                pts = [geo.pin(x['instance'], x.get('pin') if x['instance'] else x['port']) for x in part]
                if len(part) == 1 or geo.wire_pf(pts)[1] <= max_wire/2: done.append(part); continue
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; axis = 0 if max(xs)-min(xs) >= max(ys)-min(ys) else 1
                order = sorted(range(len(part)), key=lambda k: (pts[k][axis], k)); half = len(part)//2
                groups += [[part[k] for k in order[:half]], [part[k] for k in order[half:]]]
            require(len(done) <= 64, 'BUDGET', 'cluster count of one net')
            groups = sorted(done, key=lambda part: min((x['instance'] or '', x.get('pin') or x.get('port')) for x in part))
        roots = []
        for part in groups:
            pts = [geo.pin(x['instance'], x.get('pin') if x['instance'] else x['port']) for x in part]
            if len(groups) == 1:
                at = here[0]
            else:
                at = geo.outside((sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts)), pts)
            cw, _ = geo.wire_pf([at]+pts)
            part_load = cw+sum(x['cap'] for x in part)
            if len(groups) == 1 and name in feedback:
                part_load = max(part_load, feedback[name]['observed_load_pf'])
            cell = choose(part_load)
            inst = f'{planner.prefix}_{count}'; count += 1
            planner.append(dict(op='buffer_sinks', cell=cell, instance=inst, net=inst+'_n',
                                sinks=[dict(instance=x['instance'], pin=x['pin']) for x in part if x['instance']],
                                ports=[x['port'] for x in part if x['instance'] is None]))
            geo.place(inst, cell, at); roots.append((inst, cell, at))
        clustered, leaves = len(roots) > 1, len(roots)
        # The trunk over the cluster buffers obeys the same wire bound: while
        # the driver's tour of them is too long, bisect them again and drive
        # each group from a buffer at its centre (one group when bisection
        # leaves them all apart), so the count strictly falls.
        while len(roots) > 1 and geo.wire_pf([here[0]]+[at for _, _, at in roots])[1] > max_wire:
            parts, done = [roots], []
            while parts:
                part = parts.pop(); planner.i.tick(len(part))
                pts = [at for _, _, at in part]
                if len(part) == 1 or geo.wire_pf(pts)[1] <= max_wire/2: done.append(part); continue
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; axis = 0 if max(xs)-min(xs) >= max(ys)-min(ys) else 1
                order = sorted(range(len(part)), key=lambda k: (pts[k][axis], k)); half = len(part)//2
                parts += [[part[k] for k in order[:half]], [part[k] for k in order[half:]]]
            if len(done) >= len(roots): done = [roots]
            level = []
            for part in sorted(done, key=lambda part: min(r for r, _, _ in part)):
                if len(part) == 1: level.append(part[0]); continue
                pts = [at for _, _, at in part]; at = geo.outside((sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts)), pts)
                cw, _ = geo.wire_pf([at]+pts); cell = choose(cw+sum(planner.model(c)[1]['cap'] for _, c, _ in part))
                inst = f'{planner.prefix}_{count}'; count += 1
                planner.append(dict(op='buffer_sinks', cell=cell, instance=inst, net=inst+'_n',
                                    sinks=[dict(instance=r, pin=planner.buffers[c]['input']) for r, c, _ in part], ports=[]))
                geo.place(inst, cell, at); level.append((inst, cell, at))
            roots = level
        if clustered:
            pts = [at for _, _, at in roots]; cw, _ = geo.wire_pf([here[0]]+pts)
            cell = choose(cw+sum(planner.model(c)[1]['cap'] for _, c, _ in roots)); inst = f'{planner.prefix}_{count}'; count += 1
            planner.append(dict(op='buffer_sinks', cell=cell, instance=inst, net=inst+'_n',
                                sinks=[dict(instance=r, pin=planner.buffers[c]['input']) for r, c, _ in roots], ports=[]))
            geo.place(inst, cell, here[0])
        planner.weights[name] = HEAVY; planner.repaired.add(name)
        planner.checks.append(dict(net=name, action='cluster' if clustered else 'isolate', driver=label,
                                   estimated_load_pf=round(load, 6), estimated_wire_um=round(length, 1), clusters=leaves,
                                   trunk_buffers=len(roots) if clustered else 0))
    # A regrouped tree's buffers follow their sinks, which later ECOs and CTS
    # move: the net feeding a tree's top level is sized for a tour of the
    # whole tree's sink box (1.5 x half-perimeter, driver included), not for
    # today's buffer positions.
    names = {bit: n for n, bit in g.net_bits.items() if isinstance(bit, int)}
    members = {}
    for inst in g.cells:
        m = re.fullmatch(r'(native_(?:fanout|reset)_[0-9]+)_([0-9]+)_[0-9]+', inst)
        if m: members.setdefault(m[1], []).append((int(m[2]), inst))
    for tree, levels in sorted(members.items()):
        top = max(d for d, _ in levels)
        heads = [i for d, i in levels if d == top]
        bits = {g.pin_bits.get((i, planner.buffers[g.cells[i]['type']]['input'])) for i in heads if g.cells[i]['type'] in planner.buffers}
        if len(bits) != 1 or None in bits: continue
        bit = bits.pop(); d = g.drivers.get(bit); name = names.get(bit)
        if d is None or name is None or bit in planner.clocks or name in nets: continue
        leaves = [x for depth, i in levels if depth == 0
                  for x in g.loads.get(g.pin_bits.get((i, planner.buffers[g.cells[i]['type']]['output'])), [])]
        pts = [geo.pin(d['instance'], d['pin'])]+[geo.pin(x['instance'], x.get('pin') if x['instance'] else x['port']) for x in leaves]
        planner.i.tick(len(pts))
        span = max(p[0] for p in pts)-min(p[0] for p in pts)+max(p[1] for p in pts)-min(p[1] for p in pts)
        loads = g.loads.get(bit, [])
        load = 1.5*span*float(geo.rc['detour'])*float(geo.rc['c_pf_per_um'])+sum(x['cap'] for x in loads)
        if load <= (d['limit'] or math.inf)*fraction: continue
        cell = choose(load); inst = f'{planner.prefix}_{count}'; count += 1
        planner.append(dict(op='buffer_sinks', cell=cell, instance=inst, net=inst+'_n',
                            sinks=[dict(instance=x['instance'], pin=x['pin']) for x in loads if x['instance']],
                            ports=[x['port'] for x in loads if x['instance'] is None]))
        geo.place(inst, cell, pts[0]); planner.weights[name] = HEAVY; planner.repaired.add(name)
        planner.checks.append(dict(net=name, action='tree_feed', tree=tree, driver=d['pin'] if d['instance'] is None else d['instance']+'/'+d['pin'],
                                   bound_load_pf=round(load, 6), sink_box_um=round(span, 1), cell=cell))
    level_members = {}
    for i in g.cells:
        parts = i.rsplit('_', 2)
        if len(parts) == 3 and parts[1].isascii() and parts[1].isdigit() and parts[2].isascii() and parts[2].isdigit():
            level_members.setdefault((parts[0], parts[1]), []).append(i)
    for (tree, depth), load in sorted(trees.items()):
        cell = choose(load); members = sorted(level_members.get((tree, str(depth)), []))
        require(members, 'NETLIST', 'tree level has no buffers: '+tree)
        planner.repaired.update(inst+'_n' for inst in members)
        for inst in members:
            current = g.cells[inst]['type']
            if planner.model(current)[2]['limit'] < planner.model(cell)[2]['limit']:
                require(len(planner.ops) < planner.i.args.max_operations, 'BUDGET', 'ECO operation budget exceeded')
                planner.ops.append(dict(op='resize_cell', instance=inst, cell=cell))
        planner.checks.append(dict(tree=tree, depth=depth, action='resize_level', cell=cell, estimated_load_pf=round(load, 6), buffers=len(members)))
    return dict(electrical_nets=len(nets), resized_levels=len(trees), cap_feedback_nets=len(feedback), basis='placement RC estimate; re-time after placement')


def balance_stage_ns(inputs, cell, corners, wire_pf):
    """Typical delay (ns) of one stage of a chain of `cell`, per corner: each
    stage drives the next stage's input plus `wire_pf`, with the input slew
    the stage itself produces at that load (fixed point of the transition
    tables), worse of rise and fall. A model, not a bound: the next round's
    estimated-RC STA times the chain and the hold round repairs the rest."""
    spec = importlib.util.spec_from_file_location('rusteda_native_balance_liberty', inputs.tool('liberty_reader'))
    reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    out = {}
    for corner in corners:
        for source in sorted({inputs.file(x['library']) for x in inputs.manifest['corners'][corner]['pin_tables']}):
            lib = reader.Library(source, wanted={cell})
            if cell not in lib.cells: continue
            require(corner not in out, 'LIBERTY', 'duplicate clock balance master')
            pins = lib.cells[cell].sub('pin')
            a = [x for x in pins if x.attr('direction') == 'input']; z = [x for x in pins if x.attr('direction') == 'output']
            require(len(a) == len(z) == 1 and z[0].attr('function').strip().strip('()').strip() == a[0].name, 'LIBERTY', 'clock balance cell is not a single-input identity')
            arc = z[0].sub('timing'); require(len(arc) == 1 and arc[0].attr('timing_sense') == 'positive_unate', 'LIBERTY', 'clock balance arc')
            tables = {k: reader.Table(arc[0].sub(k)[0], lib) for k in ('cell_rise', 'cell_fall', 'rise_transition', 'fall_transition')}
            tu, cu = lib.time_unit_ns(), lib.cap_unit_pf()
            load = (float(a[0].attr('capacitance'))*cu+wire_pf)/cu; slew = tables['rise_transition'].index_1[0]
            for _ in range(8):
                slew = max(tables[k].lookup(slew, load) for k in ('rise_transition', 'fall_transition'))
            out[corner] = max(tables[k].lookup(slew, load) for k in ('cell_rise', 'cell_fall'))*tu
    require(set(out) == set(corners) and all(v > 0 for v in out.values()), 'LIBERTY', 'missing clock balance model in a corner')
    return out


def clock_balance(planner, holds, setups, corners, geo):
    """Delay the clocks of ungated launch flip-flops towards the gated tree (overlay14).

    run13: every hold violator launched from r1..r4, clocked straight off the
    primary-clock branches (~0.6-1.0 ns), into registers of the gated tree
    (~3.0 TT / 3.9 SS ns): 4 launch chains, 756 endpoint chains and 1,682
    DLY1X2. Here each primary branch's flip-flops that launch a listed hold
    deficit get one shared chain of `cell` on their clock pins. Latches
    (the clock-gate enable latch), the clock gate itself, SRAM clock
    branches and flip-flops without a deficit stay as they are. A chain is
    as long as the group's worst launch deficit plus `margin_ns`, and no
    longer than the smallest hold slack of the group's own data/async pins
    less the hold target and `margin_ns` (r5/D had 0.60 ns), in every corner.
    """
    cfg = planner.p['electrical']['clock_balance']; g = planner.g
    require(set(cfg) == {'cell', 'max_stages', 'margin_ns', 'wire_pf'}, 'POLICY', 'clock balance fields')
    cell = cfg['cell']; require(cell in planner.p['delay_cells'] and cell in geo.macros, 'POLICY', 'clock balance cell must be a selected delay cell')
    cap = int(cfg['max_stages']); require(1 <= cap <= 64, 'POLICY', 'clock balance stage cap')
    margin = number(cfg['margin_ns']); wire = number(cfg['wire_pf']); target = number(planner.p['hold_margin_ns'])
    require(0 <= margin < 1 and 0 <= wire < 0.05, 'POLICY', 'clock balance margin/wire bounds')
    hold = holds[0]['sections']['hold_fix']
    require(all(x['sections']['hold_fix'] == hold for x in holds), 'REPORT', 'inconsistent merged hold reports')
    stage = balance_stage_ns(planner.i, cell, corners, wire)
    need = {}
    for co in hold['corners']:
        for d in co['endpoints']:
            planner.i.tick()
            st = d.get('path', {}).get('stages', [])
            if st and st[0].get('arc') == 'launch' and target-d['hold_slack_ns'] > 0:
                key = (d['startpoint'], co['corner']); need[key] = max(need.get(key, 0.0), target-d['hold_slack_ns'])
    allow, kinds = {}, {}
    for env, c in zip(setups, corners):
        for r in env['sections']['endpoints']['rows']:
            if '/' not in r['endpoint']: continue
            inst = r['endpoint'].rsplit('/', 1)[0]; kinds.setdefault(inst, set()).add(r['kind'])
            if r.get('hold_slack_ns') is not None:
                allow[inst, c] = min(allow.get((inst, c), math.inf), r['hold_slack_ns']-target)
    names = {bit: n for n, bit in g.net_bits.items() if isinstance(bit, int)}
    branch = re.compile(re.escape(planner.prefix_of('primary-clock'))+r'_[0-9]+')
    done, count = [], 0
    for bit, d in sorted(g.drivers.items()):
        if not (d['instance'] and branch.fullmatch(d['instance'])): continue
        regs = [x for x in g.loads.get(bit, []) if x['instance'] and x['clock']
                and any((x['instance']+'/'+x['pin'], c) in need for c in corners)
                and kinds.get(x['instance'], set()) <= {'flip_flop', 'async_recovery_removal'}]
        if not regs: continue
        want = {c: max(need.get((x['instance']+'/'+x['pin'], c), 0.0) for x in regs)+margin for c in corners}
        room = {c: min(allow.get((x['instance'], c), math.inf) for x in regs)-margin for c in corners}
        n = min(cap, max(math.ceil(want[c]/stage[c]) for c in corners), min(math.floor(room[c]/stage[c]) for c in corners))
        record = dict(action='clock_balance', net=names.get(bit), registers=sorted(x['instance']+'/'+x['pin'] for x in regs),
                      cell=cell, stages=max(n, 0), stage_ns={c: round(v, 4) for c, v in stage.items()},
                      deficit_ns={c: round(want[c]-margin, 4) for c in corners}, room_ns={c: round(room[c]+margin, 4) for c in corners})
        if n < 1:
            planner.excluded.append({**record, 'reason': 'own hold slack leaves no room for one stage'}); continue
        pts = [geo.pin(x['instance'], x['pin']) for x in regs]
        at = (sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts))
        for level in range(n):
            name = f'{planner.prefix}_clkbal_{count}_{level}'
            planner.append(dict(op='buffer_sinks', cell=cell, instance=name, net=name+'_n',
                                sinks=[dict(instance=x['instance'], pin=x['pin']) for x in regs], ports=[]))
            geo.place(name, cell, at)
        count += 1; planner.checks.append(record); done.append(record)
    return dict(groups=len(done), stages=sum(r['stages'] for r in done), registers=sum(len(r['registers']) for r in done), stage_ns=stage)


def estimated_hold(planner, reports, setups, corners, drvs, geo):
    """Hold repair on estimated-RC reports: shared launch chains, then endpoint chains."""
    inputs, policy, g = planner.i, planner.p, planner.g
    bounds = delay_bounds(inputs, policy, corners)
    hold = reports[0]['sections']['hold_fix']; rows = hold['merged']
    require(all(x['sections']['hold_fix'] == hold for x in reports), 'REPORT', 'inconsistent merged hold reports')
    factors = reports[0]['scope'].get('global_timing_derate', [1., 1.])
    early, late = map(lambda v: number(v, positive=True), factors)
    target = number(policy['hold_margin_ns']); setup_margin = number(policy['setup_margin_ns'])
    require(hold['target_margin_ns'] >= target, 'REPORT', 'hold report target insufficient')
    cell = sorted(bounds)[0]; b = bounds[cell]
    worst_setup = min(min(r['setup_slack_ns'] for r in x['sections']['endpoints']['rows'] if r.get('setup_slack_ns') is not None) for x in setups)
    require(math.isfinite(worst_setup), 'REPORT', 'global setup bound')
    details, delta = {}, {}
    for co in hold['corners']:
        for detail in co['endpoints']:
            inputs.tick(); details.setdefault(detail['endpoint'], detail); on = {}
            for s in detail.get('path', {}).get('stages', []):
                if s.get('net') in planner.repaired: on[s['net']] = on.get(s['net'], 0.0)+max(0.0, s['incr_ns'])
            # A resized or isolated driver kept over half its arc delay at these
            # loads (fe10g: native_fanout_126 0.409 -> 0.228, 0.384 -> 0.265 ns).
            for net, v in on.items(): delta[net, co['corner']] = max(delta.get((net, co['corner']), 0.0), 0.5*v)
    # This round's electrical repairs (isolated, buffered or resized drivers)
    # can only shorten the arcs on their nets, and any path through them may
    # become an endpoint's worst. A repaired net may lose half its largest
    # listed arc delay (else the largest such loss); each endpoint is
    # charged the largest such sum along one combinational path (longest-path
    # label correction over the repaired nets' forward cones, work-bounded).
    ends = {r['endpoint'] for r in setups[0]['sections']['endpoints']['rows']}
    sequential = {e.rsplit('/', 1)[0] for e in ends if '/' in e}
    outs, faster, work = {}, {}, 0
    for bit, d in g.drivers.items():
        if d['instance'] is not None: outs.setdefault(d['instance'], []).append(bit)
    fallback = {c: max([v for (_, k), v in delta.items() if k == c], default=0.0) for c in corners}
    # overlay13: a post-route cap-feedback net is charged only its own listed
    # half-arc. Its repair is sized for an observed routed load, but this round
    # times the estimated one; the global fallback (the largest half-arc of any
    # repaired net, 0.23/0.35 ns on run13's repair placement) summed along
    # shared cones turned 29 feedback nets into 1,071 extra chain stages
    # (fe18a), and that placement no longer routed. This is not a bound: a
    # feedback net on no listed worst path can still make a lesser path an
    # endpoint's worst. The final estimated-RC STA and the extracted signoff
    # check every endpoint.
    feedback_nets = set(getattr(planner, 'feedback_nets', ()))
    for c in corners:
        weight = {g.bit(n): delta.get((n, c), 0.0 if n in feedback_nets else fallback[c]) for n in sorted(planner.repaired)
                  if n in g.net_bits and isinstance(g.net_bits[n], int)}
        acc = dict(weight); stack = sorted(acc)
        while stack:
            bit = stack.pop(); v = acc[bit]
            for x in g.loads.get(bit, []):
                work += 1; require(work <= inputs.args.max_work, 'BUDGET', 'repaired-net cone walk')
                label = x['port'] if x['instance'] is None else x['instance']+'/'+x['pin']
                if label in ends: faster[label, c] = max(faster.get((label, c), 0.0), v)
                if x['instance'] is None or x['instance'] in sequential or x['clock']: continue
                for out in outs.get(x['instance'], []):
                    if v+weight.get(out, 0.0) > acc.get(out, -1.0)+1e-9: acc[out] = v+weight.get(out, 0.0); stack.append(out)
    need, groups = {}, {}
    for row in rows:
        slacks = row['hold_slack_by_corner_ns']
        require(set(slacks) == set(corners) and all(v is not None and math.isfinite(v) for v in slacks.values()), 'REPORT', 'missing corner hold slack')
        slacks = {c: v-faster.get((row['endpoint'], c), 0.0) for c, v in slacks.items()}
        if min(slacks.values()) >= target: continue
        need[row['endpoint']] = slacks
        # A register launches through one output (Q or QB): group by that pin.
        detail = details[row['endpoint']]; start = detail['startpoint']; path = detail.get('path', {}).get('stages', [])
        via = path[1]['pin'] if '/' in start and len(path) > 1 and path[1].get('arc') == 'cell' else None
        groups.setdefault((start, via), []).append(row['endpoint'])
    def stages(slacks_list):
        n = max(math.ceil(max(target-s[c] for c in corners)/(early*b[c]['min'])) for s in slacks_list for c in corners)
        return max(1, n)
    count, launched, credit = 0, set(), {}
    limit, depth = int(policy.get('launch_chain_max', 32)), inputs.args.max_depth
    tree = {g.bit(n) for n in policy['clock_roots'] if n != policy['primary_clock']}
    ecfg = policy['electrical']; antenna = set(ecfg['antenna_cells'])
    for (start, via), ends in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1] or '')):
        # A shared launch point, or one endpoint beyond the endpoint-chain cap,
        # gets one chain on the launch output (register Q or input port net).
        over = [e for e in ends if stages([need[e]]) > depth]
        if len(ends) < 2 and not over: continue
        if '/' in start:
            inst, clock_pin = start.rsplit('/', 1)
            outs = [(p, bit) for (i, p), bit in g.pin_bits.items() if i == inst and bit in g.drivers and g.drivers[bit]['instance'] == inst]
            if via is not None: outs = [(p, bit) for p, bit in outs if inst+'/'+p == via]
            if len(outs) != 1: continue
            out_pin, bit = outs[0]; at = geo.pin(inst, out_pin)
            inside = g.pin_bits.get((inst, clock_pin)) in tree
        else:
            inside = False
            if start not in g.net_bits or not isinstance(g.net_bits[start], int): continue
            bit = g.bit(start); d = g.drivers.get(bit)
            if d is None or d['instance'] is not None: continue
            at = geo.pin(None, start)
        if bit in planner.clocks: continue
        if inside:
            # Registers on one branch of the tree share its skew: a launch chain
            # would only expose the branch's next launch point. Endpoint chains
            # take the delay; this chain only the part beyond their cap.
            if not over: continue
            ends = over; n = max(stages([need[e]]) for e in ends)-depth
        else:
            n = stages([need[e] for e in ends])
        if n > limit or worst_setup-chain_setup(b, corners, late, n, True) < setup_margin: continue
        sinks = [x for x in g.loads.get(bit, []) if x['instance'] and x['cell'] not in antenna]
        ports = [x['port'] for x in g.loads.get(bit, []) if x['instance'] is None]
        if not sinks and not ports: continue
        # Each later stage moves the same sinks and ports, now on the previous stage's net.
        for level in range(n):
            name = f'{planner.prefix}_launch_{count}_{level}'
            planner.append(dict(op='buffer_sinks', cell=cell, instance=name, net=name+'_n',
                                sinks=[dict(instance=x['instance'], pin=x['pin']) for x in sinks], ports=ports))
            geo.place(name, cell, at)
        count += 1
        if inside: credit.update((e, n) for e in ends)
        else: launched.update(ends)
        planner.checks.append(dict(startpoint=start, launch_pin=via, endpoints=len(ends), stages=n, cell=cell, inside_tree=inside))
    for index, (end, slacks) in enumerate(sorted(need.items())):
        detail = details[end]; inst, pin, bit = g.sink_binding(detail)
        require(bit not in planner.clocks and (planner.bus_rewrite or '[' not in pin), 'POLICY', 'clock or bus sink lacks a delay point')
        # A launch chain delays only its own launch point: two stages at the
        # pin guard the endpoint's other, unlisted launch points (one stage
        # nets ~0: its slow edge raises the pin's hold check, fe10l).
        n = 2 if end in launched else stages([slacks])-credit.get(end, 0)
        require(n <= depth and worst_setup-chain_setup(b, corners, late, n+credit.get(end, 0), end in credit) >= setup_margin,
                'HOLD_INFEASIBLE', f'no bounded all-corner delay chain: {end} (effective slack {min(slacks.values()):.3f} ns, '
                f'repair charge {max(faster.get((end, c), 0.0) for c in corners):.3f} ns)')
        at = geo.pin(inst, pin)
        for level in range(n):
            name = f'{planner.prefix}_{index:04}_{level}'
            planner.append(dict(op='insert_buffer', cell=cell, instance=name, net=name+'_n', sink=dict(instance=inst, pin=pin)))
            geo.place(name, cell, at)
        if g.cells.get(inst, {}).get('type') in ecfg['sram_masters']:
            # A delay cell's slow edge would violate the SRAM input transition.
            name = f'{planner.prefix}_{index:04}_pin'
            planner.append(dict(op='insert_buffer', cell=ecfg['sram_buffer'], instance=name, net=name+'_n', sink=dict(instance=inst, pin=pin)))
            geo.place(name, ecfg['sram_buffer'], at); planner.weights[name+'_n'] = HEAVY
        planner.checks.append(dict(endpoint=end, stages=n, cell=cell))
    return {'basis': 'placement RC estimate; table extrema; re-time after placement', 'violating': len(need),
            'charged_repairs': sum(1 for v in faster.values() if v > 0), 'report_margin_ns': hold['target_margin_ns'],
            'launch_groups': count, 'worst_setup_ns': worst_setup, 'bounds': bounds}


def main(argv=None, reuse=None):
    """The ECO stage for the command line `argv` (default: sys.argv[1:]).

    `reuse` is set when native_frontend_inputs.py bundles and plans in one process: the bundle's
    `Inputs` read the same inputs.json, so its decoded documents are taken instead of decoding
    those files again (each file is still checked against its record), and its graph (built from the same
    netlist JSON, pin tables and IO projection, with the same budgets) instead of building it
    again; the work units that graph cost are charged as if it was built here. The XXH3 checks
    run as in a process of their own, and every input is hashed again in full at the end.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('operation', choices=['reset', 'fanout', 'primary-clock', 'hold', 'electrical'])
    parser.add_argument('--workspace', type=Path, required=True); parser.add_argument('--base-rev', required=True)
    parser.add_argument('--inputs', type=Path, required=True); parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--digest-cache', type=Path, help='the run\'s flow digest cache (frontend/sha256-cache.json), read only')
    for name, default in [('memory-mib', 2048), ('max-operations', 4096), ('max-depth', 8), ('max-cells', 100000),
                          ('max-connections', 500000), ('max-rows', 200000), ('max-work', 10000000),
                          ('max-combinations', 200000), ('max-hold-chain', 4), ('max-total-hold-chain', 8),
                          ('timeout-seconds', 180)]:
        parser.add_argument('--'+name, type=int, default=default)
    args = parser.parse_args(argv); started = time.monotonic()
    # A planner run builds large, acyclic JSON and graph structures that live to the end: the
    # cyclic collector would traverse them again and again and free nothing (the peak RSS is the
    # same), so it runs only every 100,000 allocations and almost never on the old generations.
    gc.set_threshold(100_000, 50, 100)
    if args.digest_cache is not None: load_digest_cache(args.digest_cache)
    require(all(0 < getattr(args, n) <= cap for n, cap in [('memory_mib', 4096), ('max_operations', 4096), ('max_depth', 8),
            ('max_cells', 100000), ('max_connections', 500000), ('max_rows', 200000), ('max_work', 100000000),
            ('max_combinations', 200000), ('max_hold_chain', 8), ('max_total_hold_chain', 8),
            ('timeout_seconds', 600)]), 'BUDGET', 'invalid policy budget')
    limit = args.memory_mib << 20; soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (min(limit, soft) if soft > 0 else limit, hard))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Failure('TIMEOUT', 'planner wall budget')))
    signal.alarm(args.timeout_seconds)
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace/'workspace.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc: raise Failure('WORKSPACE_LOCKED', 'ECO workspace has another writer') from exc
        require(args.base_rev == 'empty' and not any(p.name != 'workspace.lock' for p in args.workspace.iterdir()), 'REVISION_CONFLICT', 'ECO stage requires a fresh empty workspace')
        staging = args.workspace/'.pending'; staging.mkdir()
        inputs = Inputs(args.inputs, args); policy = read_json(args.policy, 1 << 20)
        if reuse is not None:
            require(reuse['inputs'].manifest == inputs.manifest and reuse['inputs'].base == inputs.base,
                    'INPUT', 'reused bundle state belongs to another input bundle')
            inputs.cache.update(reuse['inputs'].cache)
        require(policy.get('schema') == 'rusteda.native-eco-policy.v1', 'SCHEMA', 'policy schema')
        known = {'schema', 'top', 'clock_roots', 'primary_clock', 'buffers', 'primary_buffer', 'reset', 'delay_cells',
                 'target_fanout', 'cap_fraction', 'hold_margin_ns', 'setup_margin_ns', 'scope', 'prefix', 'setup_corner',
                 'port_diode', 'electrical', 'launch_chain_max'}
        require(set(policy) <= known, 'POLICY', 'unknown policy field')
        inputs.files[str(args.policy.resolve())] = sha(args.policy)
        bundle = inputs.manifest; corners = list(bundle['corners'])
        require(len(corners) == 2 and len(set(corners)) == 2, 'POLICY', 'exactly two explicit common PVT corners required')
        source = inputs.file(bundle['netlist']); timed = inputs.file(bundle.get('timed_netlist', bundle['netlist']))
        source_json = bundle['netlist_json']; require(source_json['source_sha256'] == sha(source), 'STALE_INPUT', 'structural JSON source mismatch')
        sdc = inputs.file(bundle['sdc'], 2 << 20)
        # Estimate before decoding the potentially large graph and timing reports.
        records = []
        def gather(value):
            if isinstance(value, dict):
                if 'path' in value and 'sha256' in value: records.append(value)
                for child in value.values(): gather(child)
            elif isinstance(value, list):
                for child in value: gather(child)
        gather(bundle)
        decoded_files = {str(inputs.file(r, 256 << 20)): inputs.file(r, 256 << 20).stat().st_size
                         for r in records if Path(r['path']).suffix in ('.json', '.lib', '.tsv')}
        decoded = sum(decoded_files.values())
        estimate = decoded*8+args.max_connections*128+args.max_operations*1024+(64 << 20)
        require(estimate <= limit, 'BUDGET', f'upfront memory estimate {estimate} exceeds {limit}')
        available = next((int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')), limit)
        require(available >= estimate, 'BUDGET', 'insufficient host memory for policy estimate')
        io = io_projection(inputs, sdc); pins = pin_views(inputs, corners)
        document = inputs.document(source_json)
        budgets = ('max_work', 'max_cells', 'max_connections', 'max_rows')
        if (reuse is not None and reuse['top'] == policy['top'] and reuse['graph'].modules is document['modules']
                and reuse['work'][0] == inputs.work and all(getattr(reuse['inputs'].args, k) == getattr(args, k) for k in budgets)):
            # The bundle built this graph from the same decoded netlist JSON, pin tables and IO
            # projection, with the same budgets, after the same work: charge its work as Graph does.
            graph = reuse['graph']; graph.inputs, graph.pins = inputs, pins
            inputs.work += reuse['work'][1] - reuse['work'][0]
        else:
            graph = Graph(inputs, document, policy['top'], pins, io)
        reports = [inputs.report(c, 'drv', timed, sdc) for c in corners]
        require(all(policy['primary_clock'] in x['completeness']['clock_domains'] for x in reports), 'REPORT_SCOPE', 'primary clock differs from compiled reports')
        scope = {x['scope']['wire_model'] for x in reports}
        require(scope in ({'ideal'}, {'spef_rc'}), 'REPORT_SCOPE', 'mixed or unsupported wire models')
        expected = 'ideal_diagnostic' if scope == {'ideal'} else 'spef_rc'
        if expected == 'spef_rc' and policy['scope'] == 'estimated_rc':
            require('placement' in bundle and 'rc_policy' in bundle, 'REPORT_SCOPE', 'estimated RC scope needs its placement and estimate policy')
            expected = 'estimated_rc'
        require(policy['scope'] == expected, 'REPORT_SCOPE', 'policy does not match actual analysis scope')
        require((args.operation == 'electrical') <= (expected == 'estimated_rc'), 'REPORT_SCOPE', 'electrical repair needs estimated RC reports')
        planner = Planner(inputs, policy, graph); extra = {}
        if args.operation == 'reset': planner.reset()
        elif args.operation == 'fanout': planner.fanout_repair(reports)
        elif args.operation == 'primary-clock': planner.primary()
        elif expected == 'estimated_rc':
            geometry = Geometry(inputs)
            balance = None
            if args.operation == 'electrical' and 'clock_balance' in policy['electrical']:
                holds = [inputs.report(c, 'hold_fix', timed, sdc) for c in corners]
                setups = [inputs.report(c, 'endpoints', timed, sdc) for c in corners]
                balance = clock_balance(planner, holds, setups, corners, geometry)
            # The hold round keeps a smaller margin: every repair may speed up
            # hold paths, which the hold plan then charges.
            extra = electrical(planner, reports, geometry, hold_round=args.operation == 'hold')
            if balance is not None: extra['clock_balance'] = balance
            if args.operation == 'hold':
                holds = [inputs.report(c, 'hold_fix', timed, sdc) for c in corners]
                setups = [inputs.report(c, 'endpoints', timed, sdc) for c in corners]
                extra['hold'] = estimated_hold(planner, holds, setups, corners, reports, geometry)
                if 'port_diode' in policy: planner.port_diodes(geometry)
            extra['seed'] = geometry.write_seed(planner, staging/'seed.json', clock_order=args.operation == 'hold')
            weights = {}
            if 'weights' in bundle['placement']:
                prior = inputs.document(bundle['placement']['weights'], 64 << 20)
                require(prior.get('schema') == 'rustroad.net-weights.v1', 'SCHEMA', 'prior net weights schema')
                weights.update(prior['weights'])
            weights.update(planner.weights)
            (staging/'weights.json').write_text(json.dumps(dict(schema='rustroad.net-weights.v1', source=dict(
                operation=args.operation, rule='repair buffers stay on their short electrical side', carried=len(weights)-len(planner.weights)),
                weights=dict(sorted(weights.items()))), indent=1)+'\n')
            extra['weights'] = dict(total=len(weights), new=len(planner.weights))
        elif expected == 'ideal_diagnostic':
            holds = [inputs.report(c, 'hold_fix', timed, sdc) for c in corners]
            setups = [inputs.report(c, 'endpoints', timed, sdc) for c in corners]
            extra = ideal_hold(planner, holds, setups, corners)
            if 'port_diode' in policy: planner.port_diodes()
        else:
            require('port_diode' not in policy, 'POLICY', 'port diodes are applied in the ideal hold stage only')
            require(not args.plan_only, 'POLICY', 'extracted hold uses native hold-eco planning/apply together')
            for c in corners:
                inputs.report(c, 'hold_fix', timed, sdc); inputs.report(c, 'endpoints', timed, sdc)
            cmd = [str(inputs.tool('rustroad')), 'hold-eco', '--netlist', str(source), '--timed-netlist', str(timed), '--timed-netlist-sha256', sha(timed),
                   '--delay-cells', str(inputs.file(bundle['profiles']['delay'])), '--cell-lef', str(inputs.file(bundle['profiles']['lef'])),
                   '--target-margin-ns', str(policy['hold_margin_ns']), '--setup-margin-ns', str(policy['setup_margin_ns']),
                   '--setup-corner', policy['setup_corner'], '--max-chain', str(args.max_hold_chain), '--max-total-chain', str(args.max_total_hold_chain),
                   '--max-sinks', str(args.max_operations), '--max-eco-operations', str(args.max_operations), '--max-combinations', str(args.max_combinations),
                   '--prefix', planner.prefix+'_', '--out', str(staging/'netlist.v'), '--plan-out', str(staging/'hold-native.json')]
            for c in corners:
                cmd += ['--hold-corner', c, '--hold-report', str(inputs.file(bundle['corners'][c]['hold_fix'])), '--setup-report', str(inputs.file(bundle['corners'][c]['endpoints']))]
            if 'cell_buses' in bundle['profiles']:
                cmd += ['--cell-buses', str(inputs.file(bundle['profiles']['cell_buses'], 2 << 20))]
            inputs.command(cmd, staging, 'hold-native')
            native = read_json(staging/'hold-native.json', 64 << 20)
            require(native['status'] in ('planned', 'closed') and not native.get('infeasible'), 'HOLD_INFEASIBLE', 'native hold planner did not close every requested endpoint')
            planner.ops = native['eco']['operations']; extra = {'native_hold_gate': 'spef_rc retained', 'native_plan': native}
        plan = dict(schema='rustroad.eco-plan.v1', module=policy['top'], operations=planner.ops)
        (staging/'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
        if not args.plan_only and not (staging/'netlist.v').exists():
            cmd = [str(inputs.tool('rustroad')), 'apply-eco', '--netlist', str(source), '--plan', str(staging/'plan.json'),
                '--delay-cells', str(inputs.file(bundle['profiles']['delay'])), '--cell-lef', str(inputs.file(bundle['profiles']['lef'])),
                '--sizing-profile', str(inputs.file(bundle['profiles']['sizing'])), '--max-eco-operations', str(args.max_operations),
                '--max-input-mib', '16', '--eco-memory-mib', str(args.memory_mib), '--out', str(staging/'netlist.v')]
            if 'cell_buses' in bundle['profiles']:
                cmd += ['--cell-buses', str(inputs.file(bundle['profiles']['cell_buses'], 2 << 20))]
            inputs.command(cmd, staging, 'apply-native')
        fresh = fresh_digests(inputs.files)
        require(all(fresh[p] == h for p, h in inputs.files.items()), 'STALE_INPUT', 'an input changed during planning/apply')
        receipt = dict(schema='rusteda.native-eco-receipt.v1', rev='rev1', operation=args.operation,
            planner_sha256=sha_fresh(Path(__file__)),
            status='planned_unverified' if args.plan_only else 'applied_requires_retiming', scope=expected,
            corner_scopes={c: r['scope'] for c, r in zip(corners, reports)}, inputs=inputs.files,
            operations=len(planner.ops), checked=planner.checks, excluded=planner.excluded, extra=extra,
            budgets={k: v for k, v in vars(args).items() if k.startswith('max_') or k in ('memory_mib', 'timeout_seconds')},
            memory_estimate_bytes=estimate, work_units=inputs.work, elapsed_seconds=time.monotonic()-started,
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, plan_sha256=sha_fresh(staging/'plan.json'),
            netlist_sha256=sha_fresh(staging/'netlist.v') if (staging/'netlist.v').exists() else None,
            signoff={'CEC': 'unverified', 'DRC': 'unverified', 'LVS': 'unverified', 'post_edit_timing': 'unverified'})
        (staging/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
        for path in staging.iterdir(): os.replace(path, args.workspace/path.name)
        staging.rmdir()
        head = args.workspace/'.HEAD.pending'; head.write_text('rev1\n'); os.replace(head, args.workspace/'HEAD')
        signal.alarm(0)
        print(json.dumps({k: receipt[k] for k in ('schema', 'rev', 'status', 'scope', 'operations', 'plan_sha256', 'netlist_sha256', 'elapsed_seconds', 'peak_rss_kib')}))


if __name__ == '__main__':
    try: main()
    except (Failure, OSError, ValueError, KeyError, TypeError, MemoryError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'error', 'code': getattr(error, 'code', 'INPUT'), 'message': str(error)}), file=sys.stderr)
        raise SystemExit(2) from error

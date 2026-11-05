#!/usr/bin/env python3
"""Native post-route electrical repair: equal-width resize plus ECO route.

The frontend closes DRV on placement-estimated RC; routed wires can still push
a few nets past max_capacitance/max_transition (run13: 15 TT nets on native
SPEF). This runner times a routed layout on native nominal SPEF with RustSTA
(TT and SS, propagated clocks), upsizes each offending driver to an
equal-width member of its Liberty sizing family (same pins, directions and
output functions in both corners, same LEF width and height, no
sequential/bus groups, no dont_use), applies the resizes to the pre-CTS
netlist and replays the frontend's final import/place/CTS recipe argument for
argument (byte-identical placed DEF for identical inputs), then routes with
the screened route as ECO prior: nets whose pins did not change keep their
geometry.

Two input protocols:

* v2 (screen after extraction, rusteda.native-postroute-inputs.v2): the
  producer has already run antenna repair, its verifier and the one full
  native extraction on the trial route (frontend final workspace after
  be_route). The runner binds that SPEF to the workspace path, its HEAD
  revision (design.json and routes.rbin SHA-256, which must match the antenna
  lineage's final design file) and the SHA-256 of the exact extraction argv,
  and times it; it never extracts the trial again. Without any resize the
  final workspace is the screened trial, hard-linked file for file (RustROAD
  writes every revision table with create_new and replaces HEAD and receipts
  by rename, so a link is never edited in place; HEAD and the lock are fresh
  files), and the final SPEF path is a hard link of the trial SPEF. With
  resizes, each ECO rebuild is antenna-repaired with the same pinned runner
  and inputs (only the workspace differs), independently verified when the
  recipe verifies, extracted with the same argv template and screened again,
  at most max_rounds times; the last screened workspace and its SPEF are then
  linked the same way. Every screened revision, lineage, verification and
  extraction is recorded in result.json.
* v1 (the earlier order): the runner extracts the pre-antenna trial itself
  (antenna repair follows, and be_extract extracts the final layout again)
  and copies the trial file for file when no resize is needed.

Downstream steps therefore always read one final workspace.

Rows at or above near_limit_ratio of their limit are repaired when an
equal-width upsize exists (native and OpenRCX extraction differ per net);
violations (slack < 0) without one are recorded as residual and left to the
signoff timing gate. Setup/hold are not repaired here; their state after the
last round is recorded. Nothing here is signoff.

Memory/work: one child at a time, except that the v2 screen overlaps the
netlist export with input hashing and runs its four read-only report queries
together (route with prior about 1 GiB measured on the full design and bounded
by its track memory_mib; native extraction 0.85 GiB; apply-eco 2 GiB budget,
the full netlist estimate is 546 MiB; RustSTA --max-state-gib 2; a report
query well below 1 GiB). Python state: the flat netlist (about 16k
instances), two Liberty cell tables and at most max_rows DRV rows per corner,
below 256 MiB. Rounds, resizes and rows are capped by the policy.

usage: native_postroute.py --inputs INPUTS.json --workspace OUT --base-rev empty
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import stat
import types
import time

SCHEMA = 'rusteda.native-postroute-inputs.v1'
SCREEN_SCHEMA = 'rusteda.native-postroute-inputs.v2'
CORNERS = ('tt', 'ss')
MAX_ROUNDS, MAX_RESIZES, MAX_ROWS = 3, 1024, 100000
DRV_KINDS = {'max_capacitance', 'max_transition'}


class Failure(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Failure(message)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def identity(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size)


def read(path, cap=64 << 20):
    path = Path(path)
    require(path.stat().st_size <= cap, 'input byte budget: ' + str(path))
    with path.open('rb') as stream:
        raw = stream.read(cap + 1)
    require(len(raw) <= cap, 'input byte budget: ' + str(path))
    return json.loads(raw)


def pinned_protocol(item):
    """Bootstrap only verified bounded source bytes, with no second import read."""
    require(isinstance(item, dict) and set(item) == {'path', 'sha256', 'bytes'}
            and isinstance(item['path'], str) and Path(item['path']).is_absolute()
            and type(item['bytes']) is int and 0 <= item['bytes'] <= 1 << 20
            and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
            'foundry protocol source pin schema')
    path = Path(item['path'])
    require('..' not in path.parts, 'foundry protocol source traversal')
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        require(not stat.S_ISLNK(current.lstat().st_mode), 'foundry protocol source symlink')
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size == item['bytes'], 'foundry protocol source byte budget')
    with path.open('rb') as stream:
        raw = stream.read(item['bytes'] + 1)
    require(len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256']
            and tuple(getattr(path.stat(), key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns'))
            == tuple(getattr(before, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')),
            'foundry protocol source changed')
    module = types.ModuleType('foundry_pinned_protocol')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def save(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(tmp, path)


def head(workspace):
    p = Path(workspace) / 'HEAD'
    return p.read_text().strip() if p.exists() else 'rev0'


def base(name):
    """Netlist/STA names without the Verilog escape."""
    return name[1:].rstrip() if name.startswith('\\') else name


def liberty(path):
    """Line-based Liberty reader: cell -> area, dont_use, sequential/bus flag, pins."""
    cells, stack, cell = {}, [], None
    with open(path, errors='replace') as stream:
        for line in stream:
            text = line.split('/*')[0].strip()
            opens, closes = text.count('{'), text.count('}')
            if opens:
                header = text.split('{')[0].strip()
                kind = header.split('(')[0].strip()
                arg = header[header.find('(') + 1:header.rfind(')')].strip().strip('"') if '(' in header else ''
                if len(stack) == 1 and kind == 'cell':
                    cell = cells.setdefault(arg, dict(area=None, dont_use=False, unsupported=False, pins={}))
                elif len(stack) == 2 and cell is not None:
                    if kind in ('ff', 'latch', 'statetable', 'bus', 'bundle', 'test_cell', 'ff_bank', 'latch_bank'):
                        cell['unsupported'] = True
                    elif kind == 'pin':
                        cell['pins'][arg] = {}
                stack.append((kind, arg))
                stack.extend([('', '')] * (opens - 1))
            elif ':' in text and text.endswith(';') and stack:
                key, value = (s.strip() for s in text[:-1].split(':', 1))
                value = value.strip('"')
                if len(stack) == 2 and cell is not None:
                    if key == 'area':
                        cell['area'] = float(value)
                    elif key == 'dont_use':
                        cell['dont_use'] = value == 'true'
                    elif key in ('clock_gating_integrated_cell', 'is_level_shifter', 'is_isolation_cell'):
                        cell['unsupported'] = True
                elif len(stack) == 3 and stack[-1][0] == 'pin' and cell is not None:
                    if key in ('direction', 'function', 'max_capacitance', 'max_transition'):
                        cell['pins'][stack[-1][1]][key] = value
            for _ in range(closes):
                require(stack, 'unbalanced Liberty braces: ' + str(path))
                stack.pop()
            if len(stack) < 2:
                cell = None
            require(len(stack) <= 16, 'Liberty nesting budget: ' + str(path))
    require(cells and not stack, 'Liberty without cells or unbalanced: ' + str(path))
    return cells


def lef_sizes(path):
    sizes = {}
    text = Path(path).read_text()
    for m in re.finditer(r'(?ms)^MACRO\s+(\S+)\s*$(.*?)^END\s+\1\s*$', text):
        s = re.search(r'\bSIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;', m[2])
        if s:
            sizes[m[1]] = (round(float(s[1]) * 1000), round(float(s[2]) * 1000))
    return sizes


def families(libs, sizes):
    """Equal-footprint sizing classes from both corners' Liberty."""
    def key(name):
        rows = []
        for corner in CORNERS:
            c = libs[corner].get(name)
            if c is None or c['unsupported'] or c['dont_use'] or not c['pins']:
                return None
            pins = tuple(sorted((p, v.get('direction')) for p, v in c['pins'].items()))
            if any(d not in ('input', 'output') for _, d in pins):
                return None
            funcs = tuple(sorted((p, re.sub(r'\s+', '', v.get('function', ''))) for p, v in c['pins'].items()
                                 if v.get('direction') == 'output'))
            if not funcs or any(not f for _, f in funcs):
                return None
            rows.append((pins, funcs))
        return rows[0] if rows[0] == rows[1] and name in sizes else None

    groups = {}
    for name in sorted(libs['tt']):
        k = key(name)
        if k is not None:
            groups.setdefault((k, sizes[name]), []).append(name)
    out, member = [], {}
    for (k, size), names in sorted(groups.items(), key=lambda g: g[1][0]):
        if len(names) < 2:
            continue
        def maxcap(n):
            values = [float(v['max_capacitance']) for co in CORNERS for v in libs[co][n]['pins'].values()
                      if v.get('direction') == 'output' and 'max_capacitance' in v]
            return min(values) if values else None
        cells = sorted(names, key=lambda n: (maxcap(n) or 0.0, n))
        fid = 'EQW_' + cells[0]
        out.append(dict(id=fid, order='max_capacitance_pf (min over TT/SS), equal LEF width/height',
                        pins=dict(k[0]), functions=dict(k[1]), sequential=[],
                        cells=[dict(name=n, width_dbu=size[0], area_um2=libs['tt'][n]['area'] or 0.0,
                                    max_capacitance_pf=maxcap(n)) for n in cells]))
        for n in cells:
            member[n] = out[-1]
    return out, member


def netlist_instances(path):
    """Flat canonical export: instance -> (master, {pin: net})."""
    insts = {}
    for m in re.finditer(r'(?m)^[ \t]+([A-Za-z_][\w$]*)[ \t]+(\\\S+ |[A-Za-z_][\w$]*)[ \t]*\((.*)\);[ \t]*$',
                         Path(path).read_text()):
        conns = {}
        for c in re.finditer(r'\.([A-Za-z_][\w$]*)\(\s*(\\\S+ |[^(),\s]+)?\s*\)', m[3]):
            if c[2]:
                conns[c[1]] = base(c[2])
        insts[base(m[2])] = (m[1], conns)
    require(insts, 'no instances parsed: ' + str(path))
    return insts


class Runner:
    def __init__(self, inputs_path, out, allow_reuse=False):
        self.c = read(inputs_path, 4 << 20)
        c = self.c
        self.reuse = c.get('schema') == 'rusteda.native-postroute-reuse-inputs.v1'
        self.screen = c.get('schema') == SCREEN_SCHEMA
        require(not self.reuse or allow_reuse, 'explicit --foundry-first required')
        require(not (self.screen and allow_reuse), 'foundry-first capture requires the v1 order')
        expected = {'schema', 'top', 'trial', 'final_workspace', 'prects_netlist',
                    'recipe', 'profiles', 'timing', 'tools', 'policy', 'limits'}
        if self.screen:
            require(set(c) == expected | {'final_spef', 'screen', 'antenna'}
                    and set(c['tools']) == {'rustroad', 'rusteda', 'ruststa', 'python'}, 'post-route input schema')
        else:
            require(c.get('schema') == ('rusteda.native-postroute-reuse-inputs.v1' if self.reuse else SCHEMA)
                    and set(c) == expected | ({'foundry_first'} if self.reuse else set()), 'post-route input schema')
        p = c['policy']
        require(0.5 <= p['near_limit_ratio'] <= 1 and 0.3 <= p['target_ratio'] < p['near_limit_ratio']
                and 1 <= p['max_rounds'] <= MAX_ROUNDS and 0 <= p['max_resizes'] <= MAX_RESIZES, 'post-route policy budget')
        for key in ('import', 'place', 'cts', 'route', 'extract'):
            require(isinstance(c['recipe'][key], list) and 1 <= len(c['recipe'][key]) <= 128
                    and all(isinstance(a, str) for a in c['recipe'][key]), 'recipe ' + key)
        require(c['recipe']['import'].count('@NETLIST@') == 1 and c['recipe']['extract'].count('@SPEF@') == 1
                and '--prior-workspace' not in c['recipe']['route'], 'recipe markers')
        for rec in [*c['tools'].values(), *c['timing'].values()]:
            require(identity(rec['path']) == rec, 'input changed: ' + rec['path'])
        self.inputs = identity(inputs_path)
        self.out = out
        self.started = time.monotonic()
        self.commands = []
        self.final = Path(c['final_workspace'])
        require(not self.final.exists() and self.final.parent == out.resolve(), 'final workspace must be fresh, inside the runner')
        self.extract_inputs = []
        if self.screen:
            self.screen_inputs()
        if self.reuse:
            pins = c['foundry_first']
            require(set(pins) == {'protocol', 'policy', 'cell_gds', 'layer_map', 'drc_profile', 'cell_lef', 'run_root'}, 'foundry-first capture source pins')
            self.protocol = pinned_protocol(pins['protocol'])
            for role, item in pins.items():
                if role != 'run_root':
                    self.protocol.record(item, 64 << 20 if role == 'cell_gds' else 16 << 20 if role == 'cell_lef' else 2 << 20)
            self.protocol.validate_policy(self.protocol.read(pins['policy']['path']))
            effective = self.out / 'effective-inputs'
            effective.mkdir()
            args = c['recipe']['extract']
            require(args.count('--rc-profile') == 1, 'one effective extraction profile required')
            index = args.index('--rc-profile') + 1
            self.frozen_rc = self.protocol.freeze_file(args[index], effective / 'rc-profile.json', self.out, 16 << 20)
            args[index] = self.frozen_rc['effective']['path']
            self.frozen_exports = {
                role: self.protocol.freeze_file(self.protocol.record(pins[role], 64 << 20),
                    effective / filename, self.out, 64 << 20)
                for role, filename in [('cell_gds', 'cells.gds'), ('layer_map', 'layer-map.json'), ('cell_lef', 'cells.lef')]}
            self.frozen_foundry = self.protocol.freeze_profile(pins['drc_profile'], effective / 'foundry', self.out)


    def screen_inputs(self):
        """v2: the screened trial evidence, rendered by the producer as file identities."""
        c = self.c
        trial = Path(c['trial']['workspace'])
        require(trial.is_absolute() and c['trial']['revision'] == 'rev4', 'trial route binding')
        self.final_spef = Path(c['final_spef'])
        require(self.final_spef.is_absolute() and self.final_spef.parent == self.out.resolve().parent
                and not self.final_spef.exists() and not self.final_spef.is_symlink(),
                'final SPEF must be a fresh path beside the runner workspace')
        require(set(c['screen']) == {'spef', 'extract_report', 'extract_command'}
                and set(c['antenna']) == {'inputs', 'lineage', 'runner', 'verifier', 'verifier_inputs',
                                          'verification'}, 'post-route screen input schema')
        verified = c['antenna']['verifier'] is not None
        require(all((c['antenna'][key] is not None) == verified
                    for key in ('verifier', 'verifier_inputs', 'verification')),
                'antenna verifier, its inputs and its result go together')
        for item in [*c['screen'].values(), *(v for v in c['antenna'].values() if v is not None)]:
            self.record(item)

    def record(self, item, cap=1 << 40):
        """A file identity rendered earlier in this run; the bytes must be unchanged."""
        require(isinstance(item, dict) and set(item) == {'path', 'sha256', 'bytes'}
                and type(item['bytes']) is int and 0 <= item['bytes'] <= cap
                and Path(item['path']).is_absolute(), 'file identity schema')
        require(identity(item['path']) == item, 'input changed: ' + item['path'])
        return Path(item['path'])

    def remaining(self):
        left = self.c['limits']['deadline_seconds'] - (time.monotonic() - self.started)
        require(left > 30, 'post-route deadline')
        return left

    def run(self, name, argv, cap=256 << 20):
        argv = [str(a) for a in argv]
        log = self.out / 'logs'
        log.mkdir(exist_ok=True)
        tick = time.monotonic()
        with (log / (name + '.stdout')).open('xb') as o, (log / (name + '.stderr')).open('xb') as e:
            code = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=o, stderr=e, timeout=self.remaining(),
                                  env=dict(os.environ, RAYON_NUM_THREADS=str(self.c['limits']['threads']))).returncode
        row = dict(name=name, argv=argv, exit_code=code, wall_seconds=round(time.monotonic() - tick, 3),
                   stdout=identity(log / (name + '.stdout')), stderr=identity(log / (name + '.stderr')))
        self.commands.append(row)
        require(row['stdout']['bytes'] <= cap and row['stderr']['bytes'] <= 64 << 20, 'command output budget: ' + name)
        if code != 0:
            raise Failure(f'{name} exited {code}: ' + (log / (name + '.stderr')).read_text()[-2000:])
        return log / (name + '.stdout')

    def road(self, name, workspace, args, cap=256 << 20):
        argv = [self.c['tools']['rustroad']['path'], '--workspace', workspace, *args]
        if not getattr(self, 'reuse', False) or 'extract' not in args:
            return self.run(name, argv, cap)
        protocol = self.protocol
        ran = False
        try:
            with protocol.shared(workspace) as locked:
                before = protocol.snapshot_locked(locked)
                environment = protocol.extraction_environment(self.c['limits']['threads'])
                protocol.extraction_request([str(a) for a in argv], self.c['tools']['rustroad'])
                argument_profile = protocol.argument_profile([str(a) for a in argv], self.c['tools']['rustroad'])
                protocol.immutable(self.frozen_rc['effective'], self.out, 16 << 20)
                output = self.run(name, argv, cap)
                ran = True
                require(protocol.snapshot_locked(locked) == before, 'extraction changed full revision inputs')
                raw = protocol.read(output)
                require(raw['schema'] == 'rustroad.extract.v1' and raw['status'] == 'ok'
                        and raw['workspace_revision'] == before['revision']
                        and raw['rc_profile'] == self.frozen_rc['effective'], 'actual extraction report binding')
                spef = protocol.record(raw['output'], 256 << 20)
                require(str(spef) == str(Path(args[args.index('--out') + 1]).resolve()), 'actual SPEF output path')
                capture = dict(schema='rusteda.actual-full-extract-inputs.v1', status='captured',
                    source_workspace=str(locked), snapshot=before, tool=self.c['tools']['rustroad'],
                    profile=self.frozen_rc['effective'], environment=environment, argument_profile=argument_profile,
                    command=self.commands[-1], raw_report=protocol.identity(output, 2 << 20), output=raw['output'])
        except protocol.Miss as error:
            require(not ran, 'extraction snapshot changed to unsupported inputs')
            output = self.run(name, argv, cap)
            capture = dict(schema='rusteda.full-extract-inputs-miss.v1', status='cache_miss',
                           reason=str(error), command=self.commands[-1])
        require(len(self.extract_inputs) < 4, 'extraction capture count budget')
        path = self.out / (name + '-full-inputs.json')
        protocol.write_new(path, capture)
        self.extract_inputs.append(protocol.identity(path, 2 << 20))
        return output

    def spawn(self, name, argv):
        """Start one bounded child; finish() records its row (same fields as run())."""
        argv = [str(a) for a in argv]
        log = self.out / 'logs'
        log.mkdir(exist_ok=True)
        with (log / (name + '.stdout')).open('xb') as o, (log / (name + '.stderr')).open('xb') as e:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=o, stderr=e,
                                       env=dict(os.environ, RAYON_NUM_THREADS=str(self.c['limits']['threads'])))
        return dict(name=name, argv=argv, process=process, started=time.monotonic(), wall=None)

    @staticmethod
    def abandon(jobs):
        for job in jobs:
            if job['process'].poll() is None:
                job['process'].kill()
            job['process'].wait(timeout=10)

    def finish(self, jobs, cap=256 << 20):
        """Wait for every job (polling, so each wall is its own), then record rows in job order."""
        try:
            while any(job['wall'] is None for job in jobs):
                self.remaining()
                for job in jobs:
                    if job['wall'] is None and job['process'].poll() is not None:
                        job['wall'] = round(time.monotonic() - job['started'], 3)
                time.sleep(.005)
        except BaseException:
            self.abandon(jobs)
            raise
        log, outputs = self.out / 'logs', {}
        for job in jobs:
            name = job['name']
            row = dict(name=name, argv=job['argv'], exit_code=job['process'].returncode, wall_seconds=job['wall'],
                       stdout=identity(log / (name + '.stdout')), stderr=identity(log / (name + '.stderr')))
            self.commands.append(row)
            require(row['stdout']['bytes'] <= cap and row['stderr']['bytes'] <= 64 << 20, 'command output budget: ' + name)
            if row['exit_code'] != 0:
                raise Failure(f"{name} exited {row['exit_code']}: " + (log / (name + '.stderr')).read_text()[-2000:])
            outputs[name] = log / (name + '.stdout')
        return outputs

    def run_together(self, queries):
        jobs = []
        try:
            for name, argv in queries:
                jobs.append(self.spawn(name, argv))
        except BaseException:
            self.abandon(jobs)
            raise
        return self.finish(jobs)

    def physical_models(self, netlist, root):
        lef = Path(self.c['profiles']['cell_lef']).read_text()
        logical = Path(netlist).read_text()
        names = []
        for match in re.finditer(r'(?ms)^MACRO\s+(\S+)\s*$(.*?)^END\s+\1\s*$', lef):
            name, body = match[1], match[2]
            pins = list(re.finditer(r'(?ms)^\s*PIN\s+(\S+)\s*$(.*?)^\s*END\s+\1\s*$', body))
            if not pins or any(not re.search(r'\bUSE\s+(POWER|GROUND)\s*;', p[2]) for p in pins):
                continue
            if any(name in self.libs[co] for co in CORNERS):
                continue
            for inst in re.finditer(r'(?m)^[ \t]*' + re.escape(name) + r'[ \t]+\\?[^\s(]+[ \t]*\(([^;]*)\);', logical):
                require(not inst[1].strip(), 'connected physical-only cell: ' + name)
            names.append(name)
        require(len(names) <= 256, 'physical-only cell budget')
        libs = {}
        for co in CORNERS:
            libs[co] = root / (co + '-physical.lib')
            libs[co].write_text('library(physical_' + co + ') { time_unit : "1ns"; capacitive_load_unit(1,pf);\n'
                                + ''.join(' cell(' + n + ') { area : 0; }\n' for n in names) + '}\n')
        return libs, names

    def check(self, tag, workspace):
        """v1: native extraction plus RustSTA on the routed HEAD of workspace."""
        root = self.out / tag
        root.mkdir()
        spef = root / 'native.nom.spef'
        extract = [spef if a == '@SPEF@' else a for a in self.c['recipe']['extract']]
        self.road(tag + '-extract', workspace, extract, 1 << 20)
        netlist = self.road(tag + '-netlist', workspace, ['export-verilog'])
        summary, corners = self.sta(tag, root, spef, netlist)
        return summary, corners, netlist

    def sta(self, tag, root, spef, netlist, together=False):
        """RustSTA TT/SS (propagated clocks, SPEF RC) on one netlist and SPEF.

        together runs the four read-only report queries of rev2 at once (each
        takes a shared workspace lock); their rows are recorded in the fixed
        sequential order either way."""
        stubs, names = self.physical_models(netlist, root)
        sta = root / 'sta'
        t, th = self.c['tools'], str(self.c['limits']['threads'])
        self.run(tag + '-sta-init', [t['rusteda']['path'], 'init', '-w', sta, '--format', 'json', '--threads', th])
        argv = [t['rusteda']['path'], 'import', '-w', sta, '--base-rev', 'rev0', '--threads', th, '--format', 'json',
                '--max-spef-mib', '128', '--max-rc-name-mib', '128', '--max-rc-nodes', '2000000',
                '--max-rc-elements', '4000000', '--max-rc-nets', '100000']
        for co in CORNERS:
            for p in (self.c['timing']['std_' + co]['path'], self.c['timing']['ram_' + co]['path'], stubs[co]):
                argv += ['--lib', p, '--corner', co]
            argv += ['--spef', spef, '--spef-corner', co]
        self.run(tag + '-sta-import', argv + ['--netlist', netlist, '--sdc', self.c['timing']['sdc']['path']])
        self.run(tag + '-sta-run', [t['ruststa']['path'], 'run', '-w', sta, '--base-rev', 'rev1', '--threads', th,
                 '--backend', 'cpu', '--precision', 'f64', '--max-state-gib', '2', '--max-paths', '32',
                 '--max-limit-rows', str(MAX_ROWS), '--require-wire-model', 'spef_rc', '--format', 'json'])
        queries = []
        for co in CORNERS:
            queries.append((f'{tag}-{co}-summary', [t['ruststa']['path'], 'report', '-w', sta, '--rev', 'rev2',
                            '--corner', co, '--threads', th, '--section', 'summary', '--format', 'json']))
            queries.append((f'{tag}-{co}-drv', [t['ruststa']['path'], 'report', '-w', sta, '--rev', 'rev2',
                            '--corner', co, '--threads', th, '--section', 'drv', '--filter', 'slack<0.2',
                            '--limit', str(MAX_ROWS), '--format', 'json']))
        if together:
            outputs = self.run_together(queries)
        else:
            outputs = {name: self.run(name, argv) for name, argv in queries}
        corners = {}
        for co in CORNERS:
            summary = read(outputs[f'{tag}-{co}-summary'])
            drv = read(outputs[f'{tag}-{co}-drv'])
            require(drv['scope']['wire_model'] == 'spef_rc' and drv['scope']['clock_model'] == 'propagated'
                    and drv['scope']['corner'] == co, 'post-route STA scope')
            rows = drv['sections']['drv']
            require(rows['shown'] == rows['matched'] == len(rows['rows']) <= MAX_ROWS and not rows.get('truncated', 0)
                    and not rows.get('unevaluated', 0), 'post-route DRV report truncated/unevaluated')
            m = {**drv.get('metrics', {}), **summary.get('metrics', {})}
            corners[co] = dict(rows=rows['rows'], metrics={k: m.get(k) for k in (
                'worst_setup_slack_ns', 'worst_hold_slack_ns', 'setup_violating_endpoints', 'hold_violating_endpoints',
                'max_transition_violating', 'max_capacitance_violating', 'max_fanout_violating')})
        return dict(spef=identity(spef), netlist=identity(netlist), physical_only=names,
                    corners={co: v['metrics'] for co, v in corners.items()}), corners

    def plan(self, corners, netlist, resized):
        """Equal-width upsizes for rows at/over near_limit_ratio; residual rows that have none."""
        p = self.c['policy']
        insts = netlist_instances(netlist)
        prects = netlist_instances(self.c['prects_netlist'])
        driver = {}
        for name, (master, conns) in insts.items():
            for pin, net in conns.items():
                d = (self.libs['tt'].get(master, {}).get('pins', {}).get(pin, {}).get('direction'))
                if d == 'output':
                    driver.setdefault(net, (name, pin))
        need = {}
        for co, data in corners.items():
            for r in data['rows']:
                if r['kind'] not in DRV_KINDS or not r['limit'] or r['limit'] <= 0:
                    continue
                ratio = r['value'] / r['limit']
                if ratio < p['near_limit_ratio'] and r['slack'] >= 0:
                    continue
                net = base(r['net'])
                at = driver.get(net)
                key = at[0] if at else 'net:' + net
                cur = need.setdefault(key, dict(net=net, driver=at and '/'.join(at), violating=False, rows=[]))
                cur['violating'] |= r['slack'] < 0
                cur['rows'].append(dict(corner=co, kind=r['kind'], pin=r['pin'], value=r['value'], limit=r['limit'],
                                        slack=r['slack'], ratio=round(ratio, 4)))
        ops, residual, skipped = [], [], []
        for key, item in sorted(need.items()):
            inst = key if not key.startswith('net:') else None
            master = insts.get(inst, (None,))[0] if inst else None
            current = resized.get(inst, master)
            fam = self.member.get(current) if current else None
            reason = None
            if inst is None:
                reason = 'net driven by a port, macro or undriven'
            elif inst not in prects:
                reason = 'driver is not in the pre-CTS netlist (clock tree)'
            elif fam is None:
                reason = f'{current} has no equal-width sizing family'
            choice = None
            if reason is None:
                cells = fam['cells']
                now = next(c for c in cells if c['name'] == current)
                cap_need = max((r['value'] for r in item['rows'] if r['kind'] == 'max_capacitance'), default=0.0)
                slew = any(r['kind'] == 'max_transition' for r in item['rows'])
                stronger = [c for c in cells if (c['max_capacitance_pf'] or 0) > (now['max_capacitance_pf'] or 0)]
                if slew:
                    choice = stronger[-1] if stronger else None
                else:
                    fit = [c for c in stronger if c['max_capacitance_pf'] * p['target_ratio'] >= cap_need]
                    choice = fit[0] if fit else (stronger[-1] if stronger and stronger[-1]['max_capacitance_pf'] > cap_need else None)
                if choice is None:
                    reason = f'no stronger equal-width member of {fam["id"]} than {current}'
            entry = dict(item, instance=inst, master=current)
            if choice is not None:
                ops.append(dict(entry, cell=choice['name'], family=fam['id']))
            elif item['violating']:
                residual.append(dict(entry, reason=reason))
            else:
                skipped.append(dict(entry, reason=reason))
        require(len(resized) + len(ops) <= p['max_resizes'], 'post-route resize budget')
        return ops, residual, skipped

    def build(self, tag, netlist, prior):
        ws = self.out / tag
        require(not ws.exists(), 'fresh rebuild workspace')
        recipe = self.c['recipe']
        stages = {}
        for stage, args in (('import', [str(netlist) if a == '@NETLIST@' else a for a in recipe['import']]),
                            ('place', recipe['place']), ('cts', recipe['cts']),
                            ('route', recipe['route'] + ['--prior-workspace', prior[0], '--prior-rev', prior[1]])):
            stages[stage] = read(self.road(f'{tag}-{stage}', ws, args))
        revs = [stages[s]['revision'] for s in ('import', 'place', 'cts', 'route')]
        require(revs == ['rev1', 'rev2', 'rev3', 'rev4'] and head(ws) == 'rev4', 'rebuild revision chain')
        eco = stages['route'].get('eco_prior')
        require(eco and eco['workspace'] == str(prior[0]) and eco['revision'] == prior[1], 'route did not use its prior')
        mine = self.road(f'{tag}-def', ws, ['export-def'])
        theirs = self.road(f'{tag}-prior-def', prior[0], ['export-def'])
        return ws, dict(workspace=str(ws), revisions=revs, eco_prior=eco,
                        stage_stdout={s: identity(self.out / 'logs' / f'{tag}-{s}.stdout') for s in stages},
                        def_identical_to_prior=sha(mine) == sha(theirs), def_sha256=sha(mine))

    def prepare(self):
        """Equal-width sizing families from both corners' Liberty and the cell LEF."""
        c = self.c
        self.libs = {co: liberty(c['timing']['std_' + co]['path']) for co in CORNERS}
        sizes = lef_sizes(c['profiles']['cell_lef'])
        fams, self.member = families(self.libs, sizes)
        profile = dict(schema='rustroad.sizing-profile.v1', pdk='ihp-sg13g2', library=c['top'] + '-equal-width',
                       generator='native_postroute.py',
                       sources=[dict(role='liberty_' + co, path=c['timing']['std_' + co]['path'], sha256=c['timing']['std_' + co]['sha256'],
                                     bytes=c['timing']['std_' + co]['bytes']) for co in CORNERS]
                       + [dict(role='cell_lef', **identity(c['profiles']['cell_lef']))], dbu_per_um=1000,
                       rule='equal LEF width/height, identical pins/directions/output functions in TT and SS; '
                            'no sequential, bus, bundle, clock-gating or dont_use cell', families=fams, excluded=[])
        sizing = self.out / 'equal-width-sizing.json'
        save(sizing, profile)
        return fams, sizing

    def rebuild(self, index, ops, resized, sizing, prior, row):
        """Apply the cumulative resizes to the pre-CTS netlist and rebuild with prior as ECO route prior."""
        c = self.c
        for op in ops:
            resized[op['instance']] = op['cell']
        plan = self.out / f'plan{index}.json'
        save(plan, dict(schema='rustroad.eco-plan.v1', module=c['top'],
                        operations=[dict(op='resize_cell', instance=i, cell=resized[i]) for i in sorted(resized)]))
        source = self.out / f'netlist{index}.v'
        args = ['apply-eco', '--netlist', c['prects_netlist'], '--plan', plan, '--delay-cells', c['profiles']['delay'],
                '--cell-lef', c['profiles']['cell_lef'], '--sizing-profile', sizing, '--max-eco-operations',
                str(len(resized)), '--max-input-mib', '16', '--eco-memory-mib', '2048', '--out', source]
        if c['profiles'].get('cell_buses'):
            args += ['--cell-buses', c['profiles']['cell_buses']]
        self.run(f'apply{index}', [c['tools']['rustroad']['path'], *args])
        ws, row['build'] = self.build(f'build{index}', source, prior)
        row['netlist'] = identity(source)
        return ws

    @staticmethod
    def status(resized, residual):
        return 'residual_violations' if residual else ('repaired' if resized else 'clean')

    def resources(self):
        return dict(wall_seconds=round(time.monotonic() - self.started, 3),
                    peak_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)

    def execute(self):
        if self.screen:
            return self.execute_screen()
        c, p = self.c, self.c['policy']
        fams, sizing = self.prepare()
        trial = (Path(c['trial']['workspace']), c['trial']['revision'])
        require(head(trial[0]) == trial[1], 'trial workspace HEAD differs')
        resized, rounds, prior, status = {}, [], trial, None
        for index in range(p['max_rounds'] + 1):
            summary, corners, netlist = self.check(f'check{index}', prior[0])
            ops, residual, skipped = self.plan(corners, netlist, resized)
            row = dict(check=summary, workspace=str(prior[0]), revision=prior[1], resizes=ops,
                       residual=residual, near_limit_without_upsize=skipped)
            rounds.append(row)
            if not ops:
                status = self.status(resized, residual)
                break
            if index == p['max_rounds']:
                status = 'round_budget_exhausted'
                break
            prior = (self.rebuild(index, ops, resized, sizing, prior, row), 'rev4')
        if prior[0] == trial[0]:
            # No resize: the final workspace is the trial, copied file for file.
            shutil.copytree(trial[0], self.final, symlinks=False)
            files = sorted(f.relative_to(trial[0]) for f in trial[0].rglob('*') if f.is_file())
            require(files == sorted(f.relative_to(self.final) for f in self.final.rglob('*') if f.is_file())
                    and all(sha(trial[0] / f) == sha(self.final / f) for f in files), 'trial copy differs')
            copied = len(files)
        else:
            os.rename(prior[0], self.final)
            copied = 0
        require(head(self.final) == 'rev4', 'final workspace HEAD')
        last = rounds[-1]['check']['corners']
        result = dict(schema='rusteda.native-postroute-result.v1', status=status, top=c['top'],
                      input_manifest=self.inputs, policy_source=identity(__file__), tools=c['tools'],
                      trial=dict(workspace=str(trial[0]), revision=trial[1]),
                      final_workspace=str(self.final), final_revision='rev4', resized=resized,
                      final_source='trial_copy' if copied else 'eco_rebuild', copied_files=copied,
                      sizing_profile=identity(sizing), equal_width_families=len(fams), rounds=rounds,
                      last_check_corners=last, commands=self.commands,
                      scope='native nominal SPEF, RustSTA TT/SS propagated; repair selection only, not signoff',
                      unverified=['signoff_timing', 'openrcx_timing', 'drc', 'lvs', 'antenna'],
                      resources=self.resources())
        if self.reuse:
            result.update(schema='rusteda.native-postroute-reuse-result.v1',
                          extract_inputs=self.extract_inputs, effective_rc=self.frozen_rc,
                          effective_exports=self.frozen_exports, effective_foundry=self.frozen_foundry,
                          capture_policy=self.c['foundry_first'])
        save(self.out / 'result.json', result)
        summary = {k: result[k] for k in ('schema', 'status', 'final_workspace', 'final_revision', 'resized')}
        if self.reuse:
            summary.update(input_manifest=self.inputs, result=self.protocol.identity(self.out / 'result.json', 2 << 20))
        print(json.dumps(summary))

    # ---- v2: screen the SPEF of the step that extracted the antenna-repaired route ----

    def extract_argv(self, workspace, spef):
        return [self.c['tools']['rustroad']['path'], '--workspace', str(workspace),
                *[str(spef) if a == '@SPEF@' else a for a in self.c['recipe']['extract']]]

    def screened(self, source, workspace, lineage, antenna, spef, report_path, argv):
        """Bind one extraction to (workspace path, HEAD, design/routes bytes, exact argv)."""
        revision = head(workspace)
        require(lineage['workspace'] == str(workspace) and lineage['initial_revision'] == 'rev4'
                and lineage['final_revision'] == revision and lineage['tool'] == self.c['tools']['rustroad'],
                'antenna lineage does not end at the screened HEAD')
        files = {name: identity(workspace / 'revisions' / revision / name) for name in ('design.json', 'routes.rbin')}
        require(lineage['final']['revision'] == revision and lineage['final']['design_file'] == files['design.json'],
                'screened revision differs from the antenna lineage final design')
        expected = self.extract_argv(workspace, spef['path'])
        require(argv == expected, 'extraction argv differs from the recipe template')
        report = read(report_path, 2 << 20)
        profile = expected[expected.index('--rc-profile') + 1]
        require(report['schema'] == 'rustroad.extract.v1' and report['status'] == 'ok'
                and report['workspace_revision'] == revision and report['output'] == spef
                and report['rc_profile'] == identity(profile),
                'extraction report does not bind its SPEF to the screened HEAD')
        require(head(workspace) == revision, 'screened HEAD moved')
        return dict(source=source, workspace=str(workspace), revision=revision, revision_files=files,
                    antenna=antenna, extraction=dict(
                        argv_sha256=hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
                        report=identity(report_path), spef=spef))

    def screen_trial(self, trial):
        """The trial as the producer left it: antenna-repaired (and verified), extracted by be_extract."""
        c, a = self.c, self.c['antenna']
        lineage = read(self.record(a['lineage']))
        verification = None
        if a['verification'] is not None:
            verification = read(self.record(a['verification']))
            require(verification['workspace'] == str(trial) and verification['final_revision'] == head(trial)
                    and verification['lineage'] == a['lineage'], 'antenna verification does not bind the trial HEAD')
        command = read(self.record(c['screen']['extract_command']))
        argv = command['argv']
        # The producer runs every command under GNU time: time -v -o FILE argv...
        require(isinstance(argv, list) and len(argv) > 4 and argv[1:3] == ['-v', '-o'], 'extraction command record')
        return self.screened('trial', trial, lineage, dict(inputs=a['inputs'], lineage=a['lineage'],
                             verification=a['verification']), self.c['screen']['spef'],
                             self.record(c['screen']['extract_report']), argv[4:])

    def antenna(self, index, workspace):
        """Antenna-repair (and verify) an ECO rebuild with the producer's own pinned runner and inputs."""
        c, a = self.c, self.c['antenna']
        trial, python = c['trial']['workspace'], c['tools']['python']['path']
        template = read(self.record(a['inputs']))
        require(template['design_workspace'] == trial and template['initial_revision'] == 'rev4'
                and template['tool'] == c['tools']['rustroad'], 'antenna input template differs')
        require(head(workspace) == 'rev4', 'rebuild HEAD before antenna repair')
        inputs = self.out / f'antenna{index}-inputs.json'
        save(inputs, dict(template, design_workspace=str(workspace)))
        work = self.out / f'antenna{index}'
        self.record(a['runner'])
        self.run(f'antenna{index}', [python, a['runner']['path'], '--inputs', inputs, '--workspace', work,
                                      '--base-rev', 'empty'], 16 << 20)
        lineage_path = work / 'lineage.json'
        lineage = read(lineage_path)
        ids = dict(inputs=identity(inputs), lineage=identity(lineage_path), verification=None)
        if a['verifier'] is not None:
            template = read(self.record(a['verifier_inputs']))
            require(template['design_workspace'] == trial and template['lineage'] == a['lineage']
                    and template.get('controller_inputs', a['inputs']) == a['inputs'],
                    'antenna verifier input template differs')
            config = dict(template, lineage=ids['lineage'], design_workspace=str(workspace))
            if 'controller_inputs' in template:
                config['controller_inputs'] = ids['inputs']
            path = self.out / f'antenna{index}-verification-inputs.json'
            save(path, config)
            review = self.out / f'antenna{index}-verification'
            self.record(a['verifier'])
            self.run(f'antenna{index}-verify', [python, a['verifier']['path'], '--inputs', path, '--workspace',
                                                review, '--base-rev', 'empty'], 16 << 20)
            verification = read(review / 'result.json')
            require(verification['workspace'] == str(workspace) and verification['final_revision'] == head(workspace)
                    and verification['lineage'] == ids['lineage'], 'rebuild antenna verification differs')
            ids.update(verification=identity(review / 'result.json'))
        return lineage, ids

    def screen_rebuild(self, index, workspace):
        lineage, ids = self.antenna(index, workspace)
        root = self.out / f'check{index}'
        root.mkdir()
        spef = root / 'native.nom.spef'
        args = self.extract_argv(workspace, spef)[3:]
        report = self.road(f'check{index}-extract', workspace, args, 1 << 20)
        return self.screened('eco_rebuild', workspace, lineage, ids, identity(spef), report,
                             self.commands[-1]['argv'])

    def link_workspace(self, source, revision):
        """The final workspace: every file of source hard-linked, fresh HEAD and lock."""
        target = self.final
        require(head(source) == revision, 'screened HEAD moved before linking')
        linked = []
        with (source / 'workspace.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)   # as a RustROAD reader: no commit meanwhile
            require(head(source) == revision, 'screened HEAD moved before linking')
            target.mkdir()
            for folder, dirs, names in os.walk(source):
                dirs.sort()
                here = Path(folder)
                for name in dirs:
                    require(not (here / name).is_symlink(), 'workspace directory symlink')
                    (target / (here / name).relative_to(source)).mkdir()
                for name in sorted(names):
                    path = here / name
                    relative = path.relative_to(source)
                    if relative in (Path('HEAD'), Path('workspace.lock')):
                        continue
                    require(stat.S_ISREG(path.lstat().st_mode), 'workspace entry is not a regular file: ' + str(relative))
                    os.link(path, target / relative)
                    linked.append(relative)
            (target / 'workspace.lock').touch()
            (target / 'HEAD').write_text(revision + '\n')
            same = all(os.stat(source / f)[1:3] == os.stat(target / f)[1:3] for f in linked)
            require(same and head(target) == head(source) == revision, 'final workspace links differ')
        return len(linked)

    def execute_screen(self):
        c, p = self.c, self.c['policy']
        trial = Path(c['trial']['workspace'])
        require(trial.resolve(strict=True) == trial, 'trial workspace path must be canonical')
        # The round-0 netlist export starts first; it overlaps input hashing and Liberty parsing.
        job = self.spawn('check0-netlist', [c['tools']['rustroad']['path'], '--workspace', trial, 'export-verilog'])
        try:
            current = self.screen_trial(trial)
            fams, sizing = self.prepare()
        except BaseException:
            self.abandon([job])
            raise
        netlist = self.finish([job])['check0-netlist']
        require(head(trial) == current['revision'], 'trial HEAD moved during the netlist export')
        screens, resized, rounds, status = [current], {}, [], None
        for index in range(p['max_rounds'] + 1):
            tag = f'check{index}'
            root = self.out / tag
            if index:
                netlist = self.road(tag + '-netlist', Path(current['workspace']), ['export-verilog'])
            else:
                root.mkdir()
            summary, corners = self.sta(tag, root, Path(current['extraction']['spef']['path']), netlist,
                                        together=True)
            ops, residual, skipped = self.plan(corners, netlist, resized)
            row = dict(check=summary, workspace=current['workspace'], revision=current['revision'],
                       source=current['source'], resizes=ops, residual=residual, near_limit_without_upsize=skipped)
            rounds.append(row)
            if not ops:
                status = self.status(resized, residual)
                break
            if index == p['max_rounds']:
                status = 'round_budget_exhausted'
                break
            prior = (Path(current['workspace']), current['revision'])
            workspace = self.rebuild(index, ops, resized, sizing, prior, row)
            current = self.screen_rebuild(index + 1, workspace)
            screens.append(current)
        last = screens[len(rounds) - 1]
        require(last['workspace'] == rounds[-1]['workspace'], 'last screen differs from the last round')
        linked = self.link_workspace(Path(last['workspace']), last['revision'])
        spef = Path(last['extraction']['spef']['path'])
        require(identity(spef) == last['extraction']['spef'], 'screened SPEF changed')
        os.link(spef, self.final_spef)
        require(os.stat(spef)[1:3] == os.stat(self.final_spef)[1:3], 'final SPEF link differs')
        result = dict(schema='rusteda.native-postroute-result.v2', status=status, top=c['top'],
                      input_manifest=self.inputs, policy_source=identity(__file__), tools=c['tools'],
                      order='screen_after_extract',
                      trial=dict(workspace=str(trial), revision=c['trial']['revision']),
                      screens=screens, final_workspace=str(self.final), final_revision=last['revision'],
                      resized=resized, final_source='trial_link' if last['source'] == 'trial' else 'eco_rebuild',
                      final_link=dict(method='hard_link', source=last['workspace'], linked_files=linked,
                                      fresh=['HEAD', 'workspace.lock']),
                      final_spef=identity(self.final_spef), final_extraction=last['extraction'],
                      final_antenna=last['antenna'],
                      sizing_profile=identity(sizing), equal_width_families=len(fams), rounds=rounds,
                      last_check_corners=rounds[-1]['check']['corners'], commands=self.commands,
                      scope='native nominal SPEF, RustSTA TT/SS propagated; repair selection only, not signoff',
                      unverified=['signoff_timing', 'openrcx_timing', 'drc', 'lvs', 'antenna'],
                      resources=self.resources())
        save(self.out / 'result.json', result)
        print(json.dumps({k: result[k] for k in ('schema', 'status', 'final_workspace', 'final_revision', 'resized')}))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--inputs', required=True, type=Path)
    ap.add_argument('--workspace', required=True, type=Path)
    ap.add_argument('--base-rev', required=True)
    ap.add_argument('--foundry-first', action='store_true', help='explicit new full-input proof protocol')
    a = ap.parse_args()
    if a.foundry_first:
        probe = read(a.inputs, 4 << 20)
        require(probe.get('schema') == 'rusteda.native-postroute-reuse-inputs.v1', 'new post-route schema required')
        pin = probe['foundry_first']['protocol']
        module = pinned_protocol(pin)
        module.real_path(a.inputs)
        a.workspace = module.private_path(a.workspace, probe['foundry_first']['run_root'], create_parents=True)
    require(a.base_rev == 'empty' and not a.workspace.exists(), 'post-route workspace must be fresh (--base-rev empty)')
    a.workspace.mkdir(parents=True)
    try:
        Runner(a.inputs, a.workspace.resolve(), allow_reuse=a.foundry_first).execute()
    except (Failure, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as e:
        save(a.workspace / 'result.json', dict(schema='rusteda.native-postroute-result.v1', status='error',
             error=f'{type(e).__name__}: {e}'))
        print(json.dumps(dict(status='error', error=str(e)[:2000])), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

import sys, json, shutil, difflib
from pathlib import Path
sys.path.insert(0, '/home/user/RustEDA/.work/kit-runs/int2')
import ab
out = Path('/home/user/RustEDA/.work/kit-runs/int2/idcheck'); shutil.rmtree(out, ignore_errors=True); out.mkdir()
res = {}
for who, o, b in (('base', ab.BASE_OUT, ab.BASE_BIN), ('cand', ab.CAND_OUT, ab.CAND_STA), ('base-on-int2', ab.CAND_OUT, ab.BASE_BIN)):
    work = out / 'run'
    shutil.rmtree(work, ignore_errors=True)
    ab.copy_timing(o, work)
    steps = ab.timing_steps(o, b, work, 4)
    ab.run_steps(steps, work)
    shutil.copytree(work / 'logs', out / f'logs-{who}')
    res[who] = ab.report_hashes(work)
    shutil.rmtree(work)
for a, b in (('base', 'cand'), ('base-on-int2', 'cand')):
    d = [k for k in res[a] if res[a][k] != res[b][k]]
    print(a, 'vs', b, 'differ:', d)
    for k in d:
        x = (out / f'logs-{a}' / k).read_text().splitlines(); y = (out / f'logs-{b}' / k).read_text().splitlines()
        print('\n'.join(list(difflib.unified_diff(x, y, lineterm='', n=0))[:12]))

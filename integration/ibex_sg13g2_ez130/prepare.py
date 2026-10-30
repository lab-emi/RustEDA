# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import argparse
import fcntl

MACRO = 'RM_IHPSG13_1P_1024x32_c2_bm_bist'

def hashfile(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def prepare_rtl():
    repo = IBEX
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip() != '38c070939183cc10940b66c8e9e04eeca6b65470':
        raise ValueError('Ibex source revision mismatch')
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():
        raise ValueError('Ibex source must be clean')
    prim = repo/'vendor/lowrisc_ip/ip/prim/rtl'
    generic = repo/'vendor/lowrisc_ip/ip/prim_generic/rtl'
    dest = W/'rtl'; dest.mkdir(exist_ok=True)
    sources = [prim/(n+'.sv') for n in ('prim_cipher_pkg','prim_secded_pkg','prim_mubi_pkg','prim_util_pkg','prim_ram_1p_pkg')]
    sources += [repo/'rtl/ibex_pkg.sv']
    for name in ('buf','clock_gating','flop'):
        source = generic/f'prim_generic_{name}.sv'
        bound = dest/f'prim_{name}.sv'
        bound.write_text(source.read_text().replace(f'module prim_generic_{name}',f'module prim_{name}'))
        sources.append(bound)
    sources.append(prim/'prim_lfsr.sv')
    exclude = {'ibex_pkg.sv','ibex_tracer_pkg.sv','ibex_tracer.sv','ibex_core_tracing.sv','ibex_top_tracing.sv'}
    sources += sorted(p for p in (repo/'rtl').glob('*.sv') if p.name not in exclude)
    includes = [repo/'rtl',prim,repo/'vendor/lowrisc_ip/dv/sv/dv_utils']
    # A declaration preserves the macro during language conversion, without a behavioral RAM.
    vendor = (IHP/f'libs.ref/sg13g2_sram/verilog/{MACRO}.v').read_text()
    start = vendor.index('module '+MACRO)
    end = vendor.index('`ifdef FUNCTIONAL')
    bb = dest/'sram_bb.v'
    bb.write_text('// IHP macro interface; Apache-2.0. No implementation substituted.\n(* blackbox *)\n'+vendor[start:end]+'endmodule\n')
    sources += [bb,W/'ibex_sram_bench.sv']
    cmd = [SV2V,'-DSYNTHESIS','--top=ibex_sram_bench',*[f'-I{p}' for p in includes],*map(str,sources),'+RTS','-N2','-M1536m','-RTS']
    (W/'rtl-command.json').write_text(json.dumps(cmd,indent=2))
    with (dest/'design.v').open('w') as out, (W/'sv2v.log').open('w') as err:
        subprocess.run([TIME,'-v','-o',str(W/'sv2v-time.txt'),*cmd],stdout=out,stderr=err,check=True,timeout=180)
    (W/'rtl-inputs.json').write_text(json.dumps([{'path':str(p),'sha256':hashfile(p)} for p in sources],indent=2))

def prepare_pdk():
    pdk = W/'pdk/ihp-sg13g2'
    (pdk/'libs.ref').mkdir(parents=True,exist_ok=True)
    (pdk/'libs.tech/librelane/ez130_8t').mkdir(parents=True,exist_ok=True)
    for p in (IHP/'libs.ref').iterdir():
        dst=pdk/'libs.ref'/p.name
        if not dst.exists(): dst.symlink_to(p,target_is_directory=p.is_dir())
    dst=pdk/'libs.ref/ez130_8t'
    if not dst.exists(): dst.symlink_to(EZ,target_is_directory=True)
    for p in (IHP/'libs.tech').iterdir():
        if p.name == 'librelane': continue
        dst=pdk/'libs.tech'/p.name
        if not dst.exists(): dst.symlink_to(p,target_is_directory=p.is_dir())
    dst=pdk/'libs.tech/librelane/openrcx'
    if not dst.exists(): dst.symlink_to(IHP/'libs.tech/librelane/openrcx',target_is_directory=True)
    for name in ('sg13g2_io', 'sg13g2_stdcell'):
        dst=pdk/'libs.tech/librelane'/name
        if not dst.exists(): dst.symlink_to(IHP/'libs.tech/librelane'/name,target_is_directory=True)
    config=(IHP/'libs.tech/librelane/config.tcl').read_text()
    config='# Derived from IHP SG13G2 v0.3.0 public PDK configuration, Apache-2.0.\n'+config
    config=config.replace('sg13g2_stdcell','ez130_8t')
    for a,b in [('typ_1p20V_25C','tt_1p20v_25c'),('fast_1p32V_m40C','ff_1p32v_m40c'),('slow_1p08V_125C','ss_1p08v_125c')]:
        config=config.replace('ez130_8t_'+a+'.lib','ez130_8t_'+b+'.lib')
    config=config.replace('/lef/sg13g2_tech.lef','/lef/ez130_sg13g2_tech.lef')
    config='\n'.join(line for line in config.splitlines() if not line.startswith('set ::env(CELL_SPICE_MODELS)'))+'\n'
    config += '\nset ::env(FP_TAPCELL_DIST) 25\n'
    (pdk/'libs.tech/librelane/config.tcl').write_text(config)
    # Exclusions are physical-only and tristate cells; timed functional views stay unmodified.
    scl=pdk/'libs.tech/librelane/ez130_8t'
    (scl/'config.tcl').write_text('''# EZ130 8T v1.1 interface derived from vendor LEF/Liberty.
set ::env(PLACE_SITE) CoreSite
set ::env(PLACE_SITE_WIDTH) 0.42
set ::env(PLACE_SITE_HEIGHT) 3.36
set ::env(WELLTAP_CELL) WELLTAP
set ::env(SYNTH_DRIVING_CELL) BUFX4
set ::env(SYNTH_DRIVING_CELL_PIN) Y
set ::env(OUTPUT_CAP_LOAD) 6.0
set ::env(SYNTH_MIN_BUF_PORT) "BUFX1 A Y"
set ::env(SYNTH_TIEHI_PORT) "TIEHI Y"
set ::env(SYNTH_TIELO_PORT) "TIELO Y"
set ::env(FILL_CELLS) "FILLER1 FILLER2 FILLER4 FILLER8 FILLER16"
set ::env(DECAP_CELLS) "DECAP4 DECAP8 DECAP16 DECAP32"
set ::env(DIODE_CELL) "ANTENNA2/A"
set ::env(GPL_CELL_PADDING) 0
set ::env(DPL_CELL_PADDING) 0
set ::env(CELL_PAD_EXCLUDE) "FILLER* DECAP* WELLTAP"
set ::env(PDN_RAIL_WIDTH) 0.32
set ::env(CTS_ROOT_BUFFER) BUFX16
set ::env(CTS_CLK_BUFFERS) "BUFX12 BUFX8 BUFX4 BUFX2"
set ::env(MAX_FANOUT_CONSTRAINT) 10
set ::env(CLOCK_UNCERTAINTY_CONSTRAINT) 0.05
set ::env(CLOCK_TRANSITION_CONSTRAINT) 0.15
set ::env(TIME_DERATING_CONSTRAINT) 0
set ::env(IO_DELAY_CONSTRAINT) 10
set ::env(TRISTATE_CELLS) "TBUFX* TINVX*"
set ::env(HEURISTIC_ANTENNA_THRESHOLD) 90
''')
    for name in ['synth_exclude.cells','pnr_exclude.cells']:
        (scl/name).write_text('TBUFX*\nTINVX*\n')
    tech=(EZ/'lef/ez130_sg13g2_tech.lef').read_text()
    tracks=[]
    for m in re.finditer(r'^LAYER (\w+)\n(.*?)^END \1',tech,re.M|re.S):
        if not re.search(r'\bTYPE\s+ROUTING\b',m[2]): continue
        pitch=re.search(r'\bPITCH\s+([\d.]+)',m[2])[1]
        offset=re.search(r'\bOFFSET\s+([\d.]+)',m[2])
        offset=offset[1] if offset else str(float(pitch)/2)
        tracks += [f'{m[1]} X {offset} {pitch}',f'{m[1]} Y {offset} {pitch}']
    (scl/'tracks.info').write_text('\n'.join(tracks)+'\n')
    shutil.copyfile(R/'modules/RustSys/verification/fixtures/synthetic/lec/latches-ez130.v',scl/'latch_map.v')
    with (scl/'config.tcl').open('a') as f:
        f.write(f'set ::env(SYNTH_LATCH_MAP) "{scl}/latch_map.v"\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Prepare fresh external benchmark inputs, not a native design database.')
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--base-rev', required=True)
    parser.add_argument('--ibex-source', type=Path, required=True)
    parser.add_argument('--ihp-root', type=Path, default=Path.home()/'pdks/ihp-open-pdk/ihp-sg13g2')
    parser.add_argument('--ez130-root', type=Path, default=Path.home()/'pdks/ez130_8t_v1p1')
    parser.add_argument('--sv2v', default='sv2v')
    parser.add_argument('--gnu-time', required=True)
    args = parser.parse_args()
    W = args.workspace.resolve()
    if args.base_rev != 'empty' or (W.exists() and any(W.iterdir())):
        raise SystemExit('REVISION_CONFLICT: preparation requires --base-rev empty and an empty directory')
    HERE = Path(__file__).resolve().parent
    R = HERE.parents[1]
    IBEX = args.ibex_source.resolve()
    IHP = args.ihp_root.resolve()
    EZ = args.ez130_root.resolve()
    TIME = str(Path(args.gnu_time).resolve())
    SV2V = shutil.which(args.sv2v)
    if not SV2V or not Path(TIME).is_file():
        raise SystemExit('required sv2v or GNU time executable is unavailable')
    if hashfile(EZ/'lib/ez130_8t_tt_1p20v_25c.lib') != '847cb30b38f15ef0b9c4a735cfaf96c1782c013b1952261b25e5e38cd3ae072c':
        raise SystemExit('EZ130 v1.1 TT Liberty identity mismatch')
    if subprocess.check_output(['git','-C',str(IHP),'rev-parse','HEAD'],text=True).strip() != '5cccb161f7492697cfa52eb14dc03beb00bdca9e':
        raise SystemExit('IHP v0.3.0 identity mismatch')
    contract_path = HERE/'contract.json'
    contract = json.loads(contract_path.read_text())
    if len(contract['files']) > 64:
        raise SystemExit('collateral file budget exceeded')
    for name, expected in contract['files'].items():
        source, relative = name.split('/', 1)
        parts = Path(relative).parts
        if (source not in ('ihp', 'ez130') or not parts or Path(relative).is_absolute()
                or any(p in ('.', '..') for p in parts)):
            raise SystemExit('invalid collateral path in the pinned contract')
        path = (IHP if source == 'ihp' else EZ)/relative
        if not path.is_file() or hashfile(path) != expected:
            raise SystemExit(f'pinned collateral identity mismatch: {name}')
    if hashfile(HERE/'constraints.sdc') != contract['common']['sdc_sha256']:
        raise SystemExit('pinned SDC identity mismatch')
    W.mkdir(parents=True,exist_ok=True)
    workspace_lock = (W/'workspace.lock').open('a+')
    fcntl.flock(workspace_lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    if any(p.name != 'workspace.lock' for p in W.iterdir()):
        raise SystemExit('REVISION_CONFLICT: preparation directory changed while acquiring the lock')
    for name in ['ibex_sram_bench.sv','tb.sv','pdn.tcl','constraints.sdc','macro_contract.py','contract.json','cppr_off.tcl','pnr.sdc']:
        shutil.copyfile(HERE/name,W/name)
    prepare_rtl()
    prepare_pdk()
    (W/'config.json').write_text((HERE/'config.json.in').read_text().replace('@WORKSPACE@',str(W)))
    version = subprocess.check_output([SV2V,'--version'],text=True,timeout=10).strip()
    (W/'preparation.json').write_text(json.dumps({'status':'prepared_not_qualified',
        'sv2v':{'path':SV2V,'version':version,'sha256':hashfile(Path(SV2V).resolve())},
        'contract_sha256':hashfile(contract_path),
        'config_sha256':hashfile(W/'config.json'), 'rtl_sha256':hashfile(W/'rtl/design.v')},indent=2))
    print(json.dumps({'status':'prepared_not_qualified','workspace':str(W)}))


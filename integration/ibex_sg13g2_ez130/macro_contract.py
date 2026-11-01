# SPDX-License-Identifier: Apache-2.0
"""Check the public Yosys JSON cell connectivity without trusting synthesis exit status."""
import json
import argparse
from pathlib import Path
import re

NAME='RM_IHPSG13_1P_1024x32_c2_bm_bist'
WIDTHS={'A_CLK':1,'A_MEN':1,'A_WEN':1,'A_REN':1,'A_ADDR':10,'A_DIN':32,
        'A_DLY':1,'A_DOUT':32,'A_BM':32,'A_BIST_CLK':1,'A_BIST_EN':1,
        'A_BIST_MEN':1,'A_BIST_WEN':1,'A_BIST_REN':1,'A_BIST_ADDR':10,
        'A_BIST_DIN':32,'A_BIST_BM':32}

def check(path, max_cells=100000, max_hops=64):
    if not 1 <= max_cells <= 100000 or not 1 <= max_hops <= 1024:
        raise ValueError('max_cells must be 1..100000 and max_hops 1..1024')
    if path.stat().st_size > 64*1024*1024:
        return {'status':'failed','errors':['JSON_INPUT_BUDGET']}
    data=json.loads(path.read_text())
    top=data['modules']['ibex_sram_bench']
    if len(top['cells']) > max_cells:
        return {'status':'failed','errors':['CELL_COUNT_BUDGET']}
    macros={n:c for n,c in top['cells'].items() if c['type']==NAME}
    errors=[]
    # Resolve only vendor-defined constant and scalar buffer/inverter cells.
    # This is a bounded structural check, never a simulation/formal oracle.
    drivers={}
    for c in top['cells'].values():
        con=c['connections']; kind=c['type']
        if kind in ('TIEHI','TIELO') and len(con.get('Y',[]))==1:
            drivers[con['Y'][0]]=('1' if kind=='TIEHI' else '0',False)
        elif re.fullmatch(r'(BUF|INV)X[0-9P]+',kind) and len(con.get('Y',[]))==len(con.get('A',[]))==1:
            drivers[con['Y'][0]]=(con['A'][0],kind.startswith('INV'))
    def root(bit):
        seen=set(); inverted=False
        for _ in range(max_hops):
            if bit in ('0','1'):
                return str(int(bit)^int(inverted))
            if bit not in drivers:
                return (bit,inverted)
            if bit in seen:
                raise ValueError('cycle in scalar clock/constant path')
            seen.add(bit)
            bit,inv=drivers[bit];inverted ^= inv
        raise ValueError(f'{max_hops}-hop scalar path budget exhausted')
    if set(macros)!={'u_imem','u_dmem'}:
        errors.append(f'MACRO_INSTANCE_MISMATCH: {sorted(macros)}')
    outputs=[]
    for n,c in macros.items():
        con=c['connections']
        for pin,width in WIDTHS.items():
            if len(con.get(pin,[]))!=width:
                errors.append(f'{n}.{pin}: width != {width}')
        for pin in WIDTHS:
            if pin.startswith('A_BIST_') and [root(b) for b in con.get(pin,[])]!=['0']*WIDTHS[pin]:
                errors.append(f'{n}.{pin}: BIST must be inactive')
        if [root(b) for b in con.get('A_DLY',[])]!=['1']:
            errors.append(f'{n}.A_DLY: expected vendor delay setting 1')
        out=con.get('A_DOUT',[])
        if len(set(out))!=32 or not all(isinstance(b,int) for b in out):
            errors.append(f'{n}.A_DOUT: collapsed or constant output')
        outputs.append(set(out))
        if [root(b) for b in con.get('A_CLK',[])]!=[root(b) for b in top['ports']['clk_i']['bits']]:
            errors.append(f'{n}.A_CLK: unexpected clock')
        bm=con.get('A_BM',[])
        if len(bm)==32 and any(len({root(b) for b in bm[i:i+8]})!=1 for i in range(0,32,8)):
            errors.append(f'{n}.A_BM: byte mask not replicated by lane')
        if any(p in con for p in ('VDD!','VDDARRAY!','VSS!')):
            for pin,net in [('VDD!','VPWR'),('VDDARRAY!','VPWR'),('VSS!','VGND')]:
                expected=top['ports'].get(net,{}).get('bits')
                if expected is None or con.get(pin)!=expected:
                    errors.append(f'{n}.{pin}: does not connect to {net}')
    if len(outputs)==2 and outputs[0]&outputs[1]:
        errors.append('SRAM outputs shorted between banks')
    return {'status':'failed' if errors else 'passed','macro_type':NAME,
            'instances':sorted(macros),'macro_count':len(macros),'errors':errors,
            'scope':'structural macro presence, widths, clocks, disabled BIST, byte masks; not equivalence'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('netlist_json',type=Path)
    parser.add_argument('--max-cells',type=int,default=100000)
    parser.add_argument('--max-hops',type=int,default=64)
    args=parser.parse_args()
    try:
        result=check(args.netlist_json,args.max_cells,args.max_hops)
    except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError) as error:
        result={'status':'failed','errors':[f'INPUT_REJECTED: {error}']}
    print(json.dumps(result,indent=2))
    raise SystemExit(result['status']!='passed')

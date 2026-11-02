#!/usr/bin/env python3
"""Rebuild an SG13G2 EZ130 8T v1.1 tech LEF from the public SG13CMOS5L retarget.

Reverses the six documented OpenBLAU edits (doc/sg13cmos5l_adaptation.md) using the
IHP SG13G2 stdcell tech LEF (commit 3b59002e, 2025-06-20) for the removed layer/rule
text. The result is functionally an SG13G2 stack; it is NOT byte-identical to the
pinned ETH archive member (sha256 375ae19d...), which is not reachable from this host.
"""
import re, sys, hashlib
src, ihp, out = sys.argv[1:4]
t = open(src).read(); p = open(ihp).read()
def block(text, start, end):
    i = text.index(start); j = text.index(end, i) + len(end)
    return text[i:j]
# 1. drop the OpenBLAU header paragraph
t = re.sub(r"# This file was modified in 2026 for OpenBLAU.*?one track per standard-cell row\n#\n", "", t, flags=re.S)
# 2. Via4 + Metal5 after Metal4 (Metal5 gets EZ130's MINWIDTH like every other metal)
via4 = block(p, "LAYER Via4\n", "END Via4\n")
m5 = block(p, "LAYER Metal5\n", "END Metal5\n").replace("  WIDTH\t\t0.20 ;\n", "  WIDTH\t\t0.20 ;\n  MINWIDTH\t0.20 ;\n", 1)
t = t.replace("END Metal4\n", "END Metal4\n\n" + via4 + "\n" + m5, 1)
# 3. TopMetal1 back to VERTICAL with the original 0.0 offset
tm1 = block(t, "LAYER TopMetal1\n", "END TopMetal1\n")
tm1n = tm1.replace("DIRECTION\tHORIZONTAL", "DIRECTION\tVERTICAL").replace("OFFSET\t1.68", "OFFSET\t0.0")
t = t.replace(tm1, tm1n)
# 4. TopVia2 + TopMetal2 after TopMetal1
tv2 = block(p, "LAYER TopVia2\n", "END TopVia2\n")
tm2 = block(p, "LAYER TopMetal2\n", "END TopMetal2\n").replace("  WIDTH\t\t2 ;\n", "  WIDTH\t\t2 ;\n  MINWIDTH\t2 ;\n", 1)
t = t.replace(tm1n, tm1n + "\n" + tv2 + "\n" + tm2, 1)
# 5. Via4_* fixed vias: EZ130's own Via3 section with layers shifted up by one
v3 = t[t.index("######### Via 3 Definitions"):t.index("##############\n\n##############\n\n##############\n\n##############\n\n##############\n\n##############\n\n########################################\nVia TopVia1EWNS")]
v4 = v3.replace("Metal4", "Metal5").replace("Metal3", "Metal4").replace("Via3", "Via4").replace("Via 3 Definitions", "Via 4 Definitions")
t = t.replace(v3, v3 + v4, 1)
# 6. TopVia1EWNS lands on Metal5 again; add TopVia2EWNS
tv1 = block(t, "Via TopVia1EWNS DEFAULT\n", "END TopVia1EWNS\n")
t = t.replace(tv1, tv1.replace("LAYER Metal4", "LAYER Metal5") + "\n" + block(p, "Via TopVia2EWNS DEFAULT\n", "END TopVia2EWNS\n"), 1)
# 7. via4Array, viagen56 (Metal5; keep the TV1.c-legal 0.1 lower enclosure) and viagen67
via4a = block(p, "ViaRULE via4Array GENERATE\n", "END via4Array\n")
t = t.replace("END via3Array\n", "END via3Array\n\n" + via4a, 1)
top1 = block(t, "ViaRULE viaTop1Array GENERATE\n", "END viaTop1Array\n")
t = t.replace(top1, top1.replace("viaTop1Array", "viagen56").replace("LAYER Metal4", "LAYER Metal5") + "\n" + block(p, "ViaRULE viagen67 GENERATE\n", "END viagen67\n"), 1)
open(out, "w").write(t)
print(hashlib.sha256(t.encode()).hexdigest(), out)

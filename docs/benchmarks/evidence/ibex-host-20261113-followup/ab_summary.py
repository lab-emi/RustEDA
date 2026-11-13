#!/usr/bin/env python3
"""Summary of ab.py's runs.jsonl: medians and ranges per section, contender and thread count (warm-up rep 0 excluded),
and the identity of every candidate output with the baseline's."""
import json, statistics as st, sys
from collections import defaultdict

rows = [json.loads(l) for l in open(sys.argv[1])]
g = defaultdict(list)
for r in rows:
    if r["rep"] == 0:
        continue
    g[(r["section"], r["threads"], r["who"])].append(r)
out = {}
for (sec, t, who), v in sorted(g.items()):
    def stat(k):
        x = [r[k] for r in v]
        return dict(median=round(st.median(x), 3), min=min(x), max=max(x))
    key = f"{sec}/t{t}/{who}"
    out[key] = dict(n=len(v), failed=sum(r["exit_code"] != 0 for r in v), wall_seconds=stat("wall_seconds"),
                    cpu_seconds=stat("cpu_seconds"), peak_rss_kib=stat("peak_rss_kib"))
    if sec == "extraction" and who != "openrcx":
        out[key]["spef_sha256"] = sorted({r["spef_sha256"] for r in v})
    if sec == "timing" and who != "opensta":
        out[key]["report_sets"] = len({json.dumps(r["report_sha256"], sort_keys=True) for r in v})
ident = {}
for t in sorted({r["threads"] for r in rows}):
    b = [r for r in rows if r["section"] == "timing" and r["who"] == "base" and r["threads"] == t]
    c = [r for r in rows if r["section"] == "timing" and r["who"] == "cand" and r["threads"] == t]
    if b and c:
        hb, hc = b[0]["report_sha256"], c[0]["report_sha256"]
        ident[f"timing/t{t}"] = dict(files=len(hb), differ=sorted(k for k in set(hb) | set(hc) if hb.get(k) != hc.get(k)))
out["identity"] = ident
print(json.dumps(out, indent=1))

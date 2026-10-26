#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Verify source-bound public claims, brand assets and built local links.

This checks documentation, not EDA correctness. Bounds: 2500 HTML pages,
256 MiB total HTML, 8 MiB per page; pages are processed one at a time.
"""
import gzip
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT/'.work/docs-site'


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ('href', 'src', 'srcset'):
            if key in attrs:
                self.links.append(attrs[key])


def main():
    summary = json.loads((ROOT/'docs/benchmarks/openc910-sky130.json').read_text())
    source = ROOT/summary['source']
    for filename, digest in summary['sources_sha256'].items():
        assert hashlib.sha256((source/filename).read_bytes()).hexdigest() == digest, filename
    measurements = json.loads(gzip.decompress((source/'measurements.json.gz').read_bytes()))
    row = next(r for r in measurements['rows'] if r['id']=='openc910' and r['library']=='sky130hd_tt')
    for tool in ('rustsys', 'yosys'):
        for key in ('median_wall_s', 'peak_rss_kib', 'netlist_sha256'):
            assert summary[tool][key] == row[tool][key], (tool,key)
        assert summary[tool]['area_um2'] == row[tool]['qor']['area_um2']
        certificates = summary['quality'][tool]
        assert len(certificates) == 1 and certificates[0]['netlist_sha256'] == summary[tool]['netlist_sha256']
        assert certificates[0].get('verdict') == 'proven', certificates[0].keys()
    ratios = { 'speedup': summary['yosys']['median_wall_s']/summary['rustsys']['median_wall_s'],
               'area': summary['yosys']['area_um2']/summary['rustsys']['area_um2'],
               'rss': summary['yosys']['peak_rss_kib']/summary['rustsys']['peak_rss_kib'] }
    for key,value in ratios.items(): assert math.isclose(value,summary['ratios'][key],rel_tol=1e-12)
    assert summary['full_rtl_to_gds']['status']=='unverified'
    assert summary['full_rtl_to_gds']['speedup'] is None
    readme=(ROOT/'README.md').read_text()
    for value in ratios.values(): assert f'{value:.2f}×' in readme
    assert '10.90%' in readme and 'Full RTL-to-GDS speedup: unverified' in readme

    products=[('RustEDA',ROOT)]+[(name,ROOT/'modules'/name) for name in ('RustSys','RustSTA','RustROAD','RustEDA-Common')]
    logos=set()
    for name,repo in products:
        slug=name.lower();assets=repo/'docs/assets'
        logo=(assets/f'{slug}-logo.svg').read_bytes();logos.add(hashlib.sha256(logo).hexdigest())
        root=ET.fromstring(logo);assert root.attrib['viewBox']=='0 0 256 256'
        ET.parse(assets/f'{slug}-banner.svg')
        gif=(assets/f'{slug}-banner.gif').read_bytes()
        # Engine gitlinks retain their own published artwork revisions. The older
        # qualified pins use a 1200x360 canvas; the integrated product uses 1200x400.
        allowed_heights = (400,) if name == 'RustEDA' else (360, 400)
        assert gif[:3]==b'GIF' and int.from_bytes(gif[6:8],'little')==1200
        assert int.from_bytes(gif[8:10],'little') in allowed_heights
        assert len(gif)<750000, (name,'banner exceeds byte budget')
        rm_text = (repo/'README.md').read_text()
        assert 'prefers-reduced-motion: reduce' in rm_text
        if name == 'RustEDA':
            assert 'height="360"' not in rm_text and 'height="400"' not in rm_text, (name,'hardcoded banner height distorts GitHub README aspect ratio')
        assert 'AGENTS.md' in (repo/'.gitignore').read_text().splitlines()
        tracked=subprocess.check_output(['git','-C',str(repo),'ls-files','AGENTS.md','HANDOFF.md'],text=True)
        assert not tracked, (name,'private instructions remain tracked')
    assert len(logos)==5

    html=sorted(SITE.rglob('*.html'));assert 7<=len(html)<=2500
    assert sum(p.stat().st_size for p in html)<256*1024*1024
    errors=[]
    for p in html:
        assert p.stat().st_size<8*1024*1024
        parser=Links();parser.feed(p.read_text())
        for raw in parser.links:
            url=urlsplit(raw)
            if url.scheme or url.netloc or not url.path:continue
            if url.path.startswith('/RustEDA/'):
                target=SITE/unquote(url.path[len('/RustEDA/'):])
            elif url.path.startswith('/'):
                continue
            else:target=p.parent/unquote(url.path)
            if not target.is_file() and not (target/'index.html').is_file():
                errors.append(f'{p.relative_to(SITE)} -> {raw}')
    assert not errors, '\n'.join(errors[:30])
    print(json.dumps({'status':'passed','html_pages':len(html),'brand_families':5,'benchmark_ratios':ratios,'eda_validation':'not_run'},indent=2))


if __name__=='__main__':main()

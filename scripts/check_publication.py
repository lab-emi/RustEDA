#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Verify brand assets and built local links.

This checks documentation, not EDA correctness. Bounds: 2500 HTML pages,
256 MiB total HTML, 8 MiB per page; pages are processed one at a time.
"""
import hashlib
from html.parser import HTMLParser
import json
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
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'img':
            self.images.append(attrs)
        for key in ('href', 'src', 'srcset'):
            if key in attrs:
                self.links.append(attrs[key])


def check_responsive_artwork(readme, slug):
    parser = Links()
    parser.feed(readme)
    for suffix in ('banner.gif', 'wordmark.svg'):
        images = [attrs for attrs in parser.images
                  if urlsplit(attrs.get('src', '')).path.endswith(f'{slug}-{suffix}')]
        assert len(images) == 1, (slug, suffix, 'expected one README image')
        attrs = images[0]
        assert attrs.get('alt', '').strip(), (slug, suffix, 'missing alternative text')
        assert 'height' not in attrs, (slug, suffix, 'fixed height distorts responsive artwork')
        assert attrs.get('width') == ('1200' if suffix == 'banner.gif' else '360'), (slug, suffix, 'unexpected display width')


def check_current_benchmark():
    """Check publication identity without asserting EDA correctness."""
    receipt_path = ROOT/'docs/benchmarks/ibex-sg13g2-ez130.json'
    assert receipt_path.stat().st_size <= 65536, 'benchmark receipt exceeds byte cap'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['design']['family'] == 'Ibex'
    assert receipt['design']['sram_instances_required'] == 2
    assert receipt['technology']['process'] == 'IHP SG13G2'
    assert receipt['technology']['standard_cell_library'].startswith('EZ130')
    if receipt['status'] == 'pending':
        assert receipt['qualification_status'] == 'unverified'
        for key, value in receipt['metrics'].items():
            assert value is None or value in ('unchecked', 'unverified'), (key, value)
    else:
        # A comparative ratio needs two completed, eligible flows; a failed flow
        # keeps its measured values but never an end-to-end time.
        rows = receipt['implementations']
        assert sorted(rows) == sorted(receipt['comparison']['contenders']) == sorted(receipt['metrics'])
        for name, row in rows.items():
            if row['status'] == 'completed':
                assert row['eligible'] is True and row['gates_passed'] == row['required_gate_count'], name
                assert len(row['gates']) == row['required_gate_count'], name
                assert all(verdict in ('passed', 'proven') for verdict in row['gates'].values()), name
            else:
                assert row['eligible'] is False, name
                assert receipt['metrics'][name]['end_to_end_seconds'] is None, name
        if not all(row['status'] == 'completed' for row in rows.values()):
            for key, value in receipt['ratios'].items():
                assert key == 'eligibility' or value is None, (key, value)
            assert all(row['comparative_speedup'] is None for row in receipt['metrics'].values())
    retired_markers = ('<!-- open-hardware-benchmark:start -->',
                       '<!-- benchmark-16-core:start -->',
                       '<!-- benchmark-full-matrix:start -->',
                       '<!-- gf180-audit:start -->',
                       '<!-- rustspice-ngspice-benchmark:start -->')
    for repo in (ROOT, ROOT/'modules/RustSys', ROOT/'modules/RustSpice'):
        for path in (repo/'README.md', repo/'docs/index.md'):
            text = path.read_text()
            assert all(marker not in text for marker in retired_markers), path


def check_development_results():
    """Verify retained measurement files and publication scope, without running EDA."""
    path = ROOT/'docs/benchmarks/ibex-sg13g2-ez130-development.json'
    assert path.stat().st_size <= 65536, 'development receipt exceeds byte cap'
    receipt = json.loads(path.read_text())
    assert receipt['schema'] == 'rusteda.native-development-results.v1'
    assert receipt['comparative_speedup'] is None
    assert receipt['comparative_memory_ratio'] is None
    inventory = receipt['evidence_inventory']
    assert 1 <= len(inventory) <= 128, 'evidence file budget'
    total = 0
    for record in inventory:
        source = (ROOT/'docs'/record['path']).resolve()
        assert source.is_relative_to(ROOT/'docs/benchmarks/evidence')
        assert source.stat().st_size == record['bytes'] <= 2*1024*1024
        total += record['bytes']
        assert total <= 8*1024*1024, 'evidence byte budget'
        assert hashlib.sha256(source.read_bytes()).hexdigest() == record['sha256'], source
    rows = receipt['measurements']
    assert len(rows) == 2
    for row in rows:
        summary = json.loads((ROOT/'docs'/row['summary']['path']).read_text())
        metrics = json.loads((ROOT/'docs'/row['metrics']['path']).read_text())
        steps = json.loads((ROOT/'docs'/row['receipts']['path']).read_text())
        assert summary['status'] == row['status'] == 'completed'
        assert not summary['failed_items'] and not summary['unverified_items']
        assert len(summary['gates']) == len(row['gates']) == row['required_gate_count'] == 16
        assert len(steps) == row['steps_completed'] == 129
        assert all(s.get('exit_code', 0) == 0 and not s.get('capture_error') for s in steps)
        assert metrics['elapsed_seconds'] == row['elapsed_seconds'] > 0
        assert metrics['peak_rss_kib'] == row['peak_reaped_process_rss_kib'] > 0
        assert metrics['cgroup'] == row['cgroup']
    for name in ('gds', 'def', 'netlist_final', 'netlist_powered_final', 'spef_native_nom'):
        assert rows[0]['artifacts'][name] == rows[1]['artifacts'][name], name


def check_host_benchmarks():
    """Host reproduction receipts: identity, evidence hashes and the ratio rule."""
    for path in sorted((ROOT/'docs/benchmarks').glob('ibex-sg13g2-ez130-host-*.json')):
        assert path.stat().st_size <= 131072, (path, 'host receipt exceeds byte cap')
        receipt = json.loads(path.read_text())
        assert receipt['schema'] == 'rusteda.ibex-host-benchmark.v1', path
        assert receipt['design']['family'] == 'Ibex' and receipt['design']['sram_instances_required'] == 2
        assert receipt['technology']['process'] == 'IHP SG13G2'
        assert receipt['technology']['standard_cell_library'].startswith('EZ130')
        assert sorted(receipt['implementations']) == ['LibreLane', 'RustEDA']
        for name, row in receipt['implementations'].items():
            assert row['end_to_end_seconds'] > 0, name
        complete = receipt['status'] == 'completed'
        assert complete == all(all(gates.values()) for gates in receipt['gate_pass'].values()), path
        if not complete:
            # Text fields (eligibility, the end-to-end convention) describe the ratios; every ratio stays null.
            assert all(value is None for key, value in receipt['ratios'].items()
                       if key not in ('eligibility', 'end_to_end_convention')), path
        inventory = receipt['evidence_inventory']
        assert 1 <= len(inventory) <= 128, 'host evidence file budget'
        total = 0
        for record in inventory:
            source = (ROOT/'docs'/record['path']).resolve()
            assert source.is_relative_to(ROOT/'docs/benchmarks/evidence'), source
            assert source.stat().st_size == record['bytes'] <= 2*1024*1024, source
            total += record['bytes']
            assert total <= 8*1024*1024, 'host evidence byte budget'
            assert hashlib.sha256(source.read_bytes()).hexdigest() == record['sha256'], source


def main():
    check_current_benchmark()
    check_development_results()
    check_host_benchmarks()
    products=[('RustEDA',ROOT)]+[(name,ROOT/'modules'/name) for name in ('RustSys','RustSTA','RustROAD','RustSpice','RustEDA-Common')]
    logos=set()
    for name,repo in products:
        slug=name.lower();assets=repo/'docs/assets'
        logo=(assets/f'{slug}-logo.svg').read_bytes();logos.add(hashlib.sha256(logo).hexdigest())
        root=ET.fromstring(logo);assert root.attrib['viewBox']=='0 0 256 256'
        poster=ET.parse(assets/f'{slug}-banner.svg').getroot()
        gif=(assets/f'{slug}-banner.gif').read_bytes()
        # Engine gitlinks retain their own published artwork revisions. The older
        # qualified pins use a 1200x360 canvas; the integrated product uses 1200x400.
        allowed_heights = (400,) if name == 'RustEDA' else (360, 400)
        assert gif[:3]==b'GIF' and int.from_bytes(gif[6:8],'little')==1200
        assert int.from_bytes(gif[8:10],'little') in allowed_heights
        assert len(gif)<750000, (name,'banner exceeds byte budget')
        dimensions=(int.from_bytes(gif[6:8],'little'),int.from_bytes(gif[8:10],'little'))
        assert poster.attrib['viewBox']==f'0 0 {dimensions[0]} {dimensions[1]}', (name,'GIF and static poster ratios differ')
        rm_text = (repo/'README.md').read_text()
        assert 'prefers-reduced-motion: reduce' in rm_text
        if name in ('RustEDA', 'RustSys', 'RustSTA'):
            check_responsive_artwork(rm_text, slug)
            assert dimensions==(1200,400), (name,'expected 3:1 Signal Forge banner')
        assert 'AGENTS.md' in (repo/'.gitignore').read_text().splitlines()
        tracked=subprocess.check_output(['git','-C',str(repo),'ls-files','AGENTS.md','HANDOFF.md'],text=True)
        assert not tracked, (name,'private instructions remain tracked')
    assert len(logos)==6

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
    print(json.dumps({'status':'passed','html_pages':len(html),'brand_families':6,'eda_validation':'not_run'},indent=2))


if __name__=='__main__':main()

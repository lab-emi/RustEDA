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


def main():
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

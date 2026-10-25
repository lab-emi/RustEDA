#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Render the RustEDA family from one vector geometry and one palette.

Run from the superproject with Pillow installed. No network or EDA tools are used.
Seven products are rendered serially; at most 25 1200x360 frames are retained.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
INK, PANEL, LINE = "#101923", "#162431", "#304451"
PAPER, MUTED, COPPER, MINT = "#F4F1E9", "#A4B6BF", "#EF8B52", "#57D6BE"
PRODUCTS = [
    ("RustEDA", "rusteda", "Chip-design infrastructure for recursive self-improvement", "RTL → GDS → feedback → better RTL"),
    ("RustSys", "rustsys", "Synthesis for the next design iteration", "RTL → mapped netlist"),
    ("RustSTA", "ruststa", "Timing feedback for the next design iteration", "Netlist + constraints → timing"),
    ("RustROAD", "rustroad", "Physical design for the next design iteration", "Netlist → placement → routing → GDS"),
    ("RustLane", "rustlane", "Reproducible execution of the design loop", "Plan → execute → verify → resume"),
    ("RustEDA-Studio", "rusteda-studio", "A shared view of the design loop", "Inspect → select → act → inspect"),
    ("RustEDA-Common", "rusteda-common", "The foundation beneath the design loop", "Models · formats · workspaces · compute"),
]
GLYPHS = {
    "rusteda": [[(84,100),(128,76),(172,100),(172,154),(128,180),(84,154),(84,100)],[(84,100),(128,126),(172,100)],[(128,126),(128,180)]],
    "rustsys": [[(84,96),(124,96),(151,106),(164,128),(151,150),(124,160),(84,160),(84,96)],[(64,111),(84,111)],[(64,145),(84,145)],[(164,128),(192,128)]],
    "ruststa": [[(70,174),(70,84)],[(70,174),(188,174)],[(84,152),(111,152),(111,124),(141,124),(141,92),(184,92)]],
    "rustroad": [[(75,86),(111,86),(111,131),(169,131),(169,173)],[(75,173),(111,173),(111,152),(146,152),(146,86),(181,86)]],
    "rustlane": [[(71,105),(111,105),(111,151),(151,151),(151,105),(187,105)],[(187,140),(187,186),(71,186),(71,143)]],
    "rusteda-studio": [[(72,83),(184,83),(184,173),(72,173),(72,83)],[(72,109),(184,109)],[(105,110),(105,173)],[(120,151),(139,131),(156,145),(173,121)]],
    "rusteda-common": [[(76,102),(128,75),(180,102),(128,129),(76,102)],[(76,127),(128,154),(180,127)],[(76,153),(128,180),(180,153)]],
}


def chip_svg(slug: str) -> str:
    paths = "".join(f'<polyline points="{" ".join(f"{x},{y}" for x,y in p)}"/>' for p in GLYPHS[slug])
    return f'''<rect width="256" height="256" rx="54" fill="{INK}"/>
<g fill="none" stroke-width="9" stroke-linecap="round" stroke-linejoin="round">
<path d="M83 48H48V83M48 173V208H83" stroke="{COPPER}"/>
<path d="M173 48H208V83M208 173V208H173" stroke="{MINT}"/>
<g stroke="{PAPER}">{paths}</g></g>'''


def svg(body: str, width: int, height: int, title: str, desc: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc"><title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>{body}</svg>\n'


def font(size: int, bold: bool = False):
    path = Path('/usr/share/fonts/truetype/dejavu') / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')
    return ImageFont.truetype(str(path), size)


def chip_draw(draw, slug, x, y, scale=1.0):
    def coords(p): return [(x+a*scale,y+b*scale) for a,b in p]
    draw.rounded_rectangle((x,y,x+256*scale,y+256*scale),radius=54*scale,fill=INK)
    for points,color in [([(83,48),(48,48),(48,83)],COPPER), ([(48,173),(48,208),(83,208)],COPPER), ([(173,48),(208,48),(208,83)],MINT), ([(208,173),(208,208),(173,208)],MINT)]:
        draw.line(coords(points), fill=color, width=max(1,round(9*scale)), joint='curve')
    for points in GLYPHS[slug]: draw.line(coords(points),fill=PAPER,width=max(1,round(9*scale)),joint='curve')


ROUTE = [(874,105),(1050,105),(1108,164),(1108,246),(1050,304),(874,304),(824,254),(824,163),(874,105)]


def banner(name, slug, subtitle, flow):
    display = name.replace('RustEDA-', 'RustEDA ')
    size = 65 if len(display) < 14 else 53
    route = ' '.join(f'{x},{y}' for x,y in ROUTE)
    body = f'''<rect width="1200" height="360" rx="20" fill="{INK}"/>
<g stroke="{LINE}" opacity=".25">{''.join(f'<path d="M{x} 24V336"/>' for x in range(24,1200,32))}{''.join(f'<path d="M24 {y}H1176"/>' for y in range(24,360,32))}</g>
<text x="52" y="61" fill="{MINT}" font-family="DejaVu Sans,sans-serif" font-size="13" letter-spacing="4">RUSTEDA / DESIGN. MEASURE. IMPROVE.</text>
<text x="49" y="163" fill="{PAPER}" font-family="DejaVu Sans,sans-serif" font-size="{size}" font-weight="700">{escape(display)}</text>
<text x="53" y="214" fill="{MUTED}" font-family="DejaVu Sans,sans-serif" font-size="18">{escape(subtitle)}</text>
<path d="M53 254H105" stroke="{COPPER}" stroke-width="3"/>
<text x="53" y="302" fill="{PAPER}" font-family="DejaVu Sans,sans-serif" font-size="16">{escape(flow)}</text>
<polyline points="{route}" fill="none" stroke="{LINE}" stroke-width="3"/>
<g transform="translate(850 100) scale(.8)">{chip_svg(slug)}</g>
<circle cx="874" cy="105" r="6" fill="{MINT}"/>
<path d="M866 98L874 105L865 112" fill="none" stroke="{MINT}" stroke-width="3"/>'''
    static = svg(body,1200,360,display,subtitle+'. '+flow)
    base=Image.new('RGB',(1200,360),INK); d=ImageDraw.Draw(base)
    for x in range(24,1200,32): d.line([(x,24),(x,336)],fill='#182733')
    for y in range(24,360,32): d.line([(24,y),(1176,y)],fill='#182733')
    d.text((52,46),'RUSTEDA / DESIGN. MEASURE. IMPROVE.',font=font(13),fill=MINT)
    d.text((49,101),display,font=font(size,True),fill=PAPER)
    d.text((53,197),subtitle,font=font(18),fill=MUTED)
    d.line([(53,254),(105,254)],fill=COPPER,width=3)
    d.text((53,284),flow,font=font(16),fill=PAPER)
    d.line(ROUTE,fill=LINE,width=3,joint='curve');chip_draw(d,slug,850,100,.8)
    segments=[math.dist(a,b) for a,b in zip(ROUTE,ROUTE[1:])];total=sum(segments)
    frames=[]
    for i in range(24):
        frame=base.copy(); draw=ImageDraw.Draw(frame); distance=total*i/24
        for (a,b),length in zip(zip(ROUTE,ROUTE[1:]),segments):
            if distance<=length:
                t=distance/length;cx=a[0]+(b[0]-a[0])*t;cy=a[1]+(b[1]-a[1])*t;break
            distance-=length
        draw.ellipse((cx-10,cy-10,cx+10,cy+10),fill='#234A48')
        draw.ellipse((cx-5,cy-5,cx+5,cy+5),fill=MINT)
        frames.append(frame.quantize(colors=64))
    return static,frames


def main():
    manifest=[]
    for name,slug,subtitle,flow in PRODUCTS:
        dest=(ROOT if name=='RustEDA' else ROOT/'modules'/name)/'docs/assets'
        dest.mkdir(parents=True,exist_ok=True)
        (dest/f'{slug}-logo.svg').write_text(svg(chip_svg(slug),256,256,name,subtitle))
        (dest/'rusteda-logo.svg').write_text(svg(chip_svg('rusteda'),256,256,'RustEDA','The RustEDA design feedback loop'))
        (dest/'favicon.svg').write_text(svg(chip_svg(slug),256,256,name,subtitle))
        word=f'<rect width="440" height="64" rx="14" fill="{INK}"/><g transform="scale(.25)">{chip_svg(slug)}</g><text x="80" y="44" font-family="DejaVu Sans,sans-serif" font-size="32" font-weight="700" fill="{PAPER}">{escape(name.replace("RustEDA-","RustEDA "))}</text>'
        (dest/f'{slug}-wordmark.svg').write_text(svg(word,440,64,name,subtitle))
        static,frames=banner(name,slug,subtitle,flow)
        (dest/f'{slug}-banner.svg').write_text(static)
        frames[0].save(dest/f'{slug}-banner.gif',save_all=True,append_images=frames[1:],duration=125,loop=0,optimize=True,disposal=2)
        manifest.append({'name':name,'slug':slug,'banner_bytes':(dest/f'{slug}-banner.gif').stat().st_size})
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()

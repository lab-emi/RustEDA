#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Render the unified RustEDA Signal Forge visual identity and animated GIF banners.

All seven repositories (RustEDA + six modules) share the Signal Forge chip/terminal
frame, dark-ink palette, 256x256 mark, 760x176 wordmark, and 1200x400 three-card
animated pipeline banner (84 frames at 100 ms/frame).

Upfront memory estimate:
- Canvas: 1200 x 400 = 480,000 pixels per frame.
- 84 indexed 8-bit frames per product: ~40.3 MiB.
- Products are rendered serially; including rasterizer child and Pillow buffers,
  peak RSS stays well below 256 MiB (guarded under 1 GiB / 180 s).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
INK, PANEL, BORDER = "#101923", "#17242F", "#304451"
COPPER, TEAL, PAPER, MUTED = "#EF8B52", "#57D6BE", "#F4F1E9", "#A1B0BA"
WIDTH, HEIGHT, FRAMES, FRAME_MS = 1200, 400, 84, 100
MAX_PIXELS = 1200 * 400
MAX_FRAMES = 96
MAX_GIF_BYTES = 750_000

PRODUCTS = [
    ("RustEDA", "rusteda", "RustEDA", "From RTL to verified silicon."),
    ("RustSys", "rustsys", "RustSys", "From Verilog to standard cells."),
    ("RustSTA", "ruststa", "RustSTA", "From netlists to timing insight."),
    ("RustROAD", "rustroad", "RustROAD", "From netlists to routed layout."),
    ("RustEDA-Common", "rusteda-common", "RustEDA Common", "Shared IRs, formats, and compute."),
]


def text(x: float, y: float, value: str, size: int = 16, fill: str = PAPER,
         weight: int = 400, mono: bool = False, **attrs: object) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    family = "DejaVu Sans Mono,monospace" if mono else "DejaVu Sans,sans-serif"
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-family="{family}" xml:space="preserve" '
            f'font-size="{size}" font-weight="{weight}" {extra}>{html.escape(value)}</text>')


def svg(width: int, height: int, body: str, title: str, description: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{html.escape(title)}</title>'
            f'<desc id="desc">{html.escape(description)}</desc>{body}</svg>\n')


def mark(slug: str) -> str:
    """Shared Signal Forge chip/terminal frame surrounding a tool-specific signal glyph."""
    body = f'<rect width="256" height="256" rx="54" fill="{INK}"/>'
    body += (f'<g fill="none" stroke-width="12" stroke-linecap="round" stroke-linejoin="round">'
             f'<path d="M79 50H50V79M50 177V206H79" stroke="{COPPER}"/>'
             f'<path d="M177 50H206V79M206 177V206H177" stroke="{TEAL}"/>')
    if slug == "rustsys":
        body += (f'<path d="M69 108H106M69 148H106" stroke="{COPPER}"/>'
                 f'<path d="M108 89H130C180 89 180 167 130 167H108Z" stroke="{PAPER}"/>'
                 f'<path d="M168 128H189" stroke="{TEAL}"/></g>'
                 f'<circle cx="69" cy="108" r="7" fill="{COPPER}"/>'
                 f'<circle cx="189" cy="128" r="7" fill="{TEAL}"/>')
    elif slug == "ruststa":
        body += (f'<path d="M69 128H83" stroke="{COPPER}"/>'
                 f'<circle cx="128" cy="128" r="43" stroke="{PAPER}" stroke-width="8"/>'
                 f'<path d="M128 128V99" stroke="{COPPER}" stroke-width="9"/>'
                 f'<path d="M128 128L151 141M173 128H189" stroke="{TEAL}" stroke-width="9"/></g>'
                 f'<circle cx="128" cy="128" r="6" fill="{PAPER}"/>')
    elif slug == "rustroad":
        body += (f'<path d="M69 104H90" stroke="{COPPER}" stroke-width="10"/>'
                 f'<rect x="90" y="84" width="36" height="40" rx="6" stroke="{PAPER}" stroke-width="8"/>'
                 f'<rect x="130" y="132" width="36" height="40" rx="6" stroke="{PAPER}" stroke-width="8"/>'
                 f'<path d="M126 104H148V132" stroke="{TEAL}" stroke-width="9"/>'
                 f'<path d="M108 124V152H130" stroke="{COPPER}" stroke-width="9"/>'
                 f'<path d="M166 152H189" stroke="{TEAL}" stroke-width="10"/></g>'
                 f'<circle cx="69" cy="104" r="6.5" fill="{COPPER}"/>'
                 f'<circle cx="148" cy="104" r="5.5" fill="{PAPER}"/>'
                 f'<circle cx="108" cy="152" r="5.5" fill="{PAPER}"/>'
                 f'<circle cx="189" cy="152" r="6.5" fill="{TEAL}"/>')
    elif slug == "rustlane":
        body += (f'<path d="M90 82H166M90 174H166" stroke="{PAPER}" stroke-width="8"/>'
                 f'<path d="M67 128H94L128 102M94 128L128 154" stroke="{COPPER}" stroke-width="9"/>'
                 f'<path d="M128 102L162 128H189M128 154L162 128" stroke="{TEAL}" stroke-width="9"/></g>'
                 f'<circle cx="67" cy="128" r="6.5" fill="{COPPER}"/>'
                 f'<circle cx="94" cy="128" r="6.5" fill="{PAPER}"/>'
                 f'<circle cx="128" cy="102" r="6.5" fill="{PAPER}"/>'
                 f'<circle cx="128" cy="154" r="6.5" fill="{PAPER}"/>'
                 f'<circle cx="162" cy="128" r="6.5" fill="{PAPER}"/>'
                 f'<circle cx="189" cy="128" r="6.5" fill="{TEAL}"/>')
    elif slug == "rusteda-studio":
        body += (f'<path d="M67 128H82" stroke="{COPPER}" stroke-width="10"/>'
                 f'<rect x="82" y="84" width="92" height="88" rx="10" stroke="{PAPER}" stroke-width="8"/>'
                 f'<path d="M82 108H174M110 108V172" stroke="{PAPER}" stroke-width="6"/>'
                 f'<path d="M123 152L141 126L158 140" stroke="{TEAL}" stroke-width="8"/>'
                 f'<path d="M174 128H189" stroke="{TEAL}" stroke-width="10"/></g>'
                 f'<circle cx="67" cy="128" r="6.5" fill="{COPPER}"/>'
                 f'<circle cx="123" cy="152" r="4.5" fill="{COPPER}"/>'
                 f'<circle cx="141" cy="126" r="4.5" fill="{PAPER}"/>'
                 f'<circle cx="158" cy="140" r="4.5" fill="{TEAL}"/>'
                 f'<circle cx="189" cy="128" r="6.5" fill="{TEAL}"/>')
    elif slug == "rusteda-common":
        body += (f'<path d="M67 128H79" stroke="{COPPER}" stroke-width="10"/>'
                 f'<path d="M177 128H189" stroke="{TEAL}" stroke-width="10"/>'
                 f'<path d="M88 104L128 82L168 104L128 126Z" stroke="{PAPER}" stroke-width="8"/>'
                 f'<path d="M88 128L128 150L168 128" stroke="{COPPER}" stroke-width="8"/>'
                 f'<path d="M88 152L128 174L168 152" stroke="{TEAL}" stroke-width="8"/></g>'
                 f'<circle cx="67" cy="128" r="6.5" fill="{COPPER}"/>'
                 f'<circle cx="189" cy="128" r="6.5" fill="{TEAL}"/>')
    else:
        body += (f'<path d="M69 102H104L152 154H188" stroke="{COPPER}"/>'
                 f'<path d="M69 154H104L152 102H188" stroke="{TEAL}"/></g>'
                 f'<circle cx="128" cy="128" r="12" fill="{PAPER}"/>')
    return body


def wordmark(slug: str, display: str, subtitle: str) -> str:
    size = 58 if len(display) <= 10 else 48
    body = f'<rect width="760" height="176" rx="28" fill="{INK}"/>'
    body += f'<g transform="translate(20 20) scale(.53125)">{mark(slug)}</g>'
    body += text(180, 89, display, size, weight=700)
    body += text(184, 127, subtitle, 19, MUTED)
    return svg(760, 176, body, f"{display} wordmark", subtitle)


def wire(path: str, color: str = BORDER, width: float = 2) -> str:
    return (f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def pulse(a: tuple[float, float], b: tuple[float, float], progress: float, color: str = TEAL) -> str:
    if not 0 <= progress <= 1:
        return ""
    x, y = a[0] + (b[0] - a[0]) * progress, a[1] + (b[1] - a[1]) * progress
    return (f'<circle cx="{x:.2f}" cy="{y:.2f}" r="9" fill="{color}" opacity=".14"/>'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="{color}"/>')


def base_canvas(slug: str, display: str, right_top: str, right_sub: str,
                cards: list[tuple[int, int, str, str]]) -> str:
    body = f'<rect width="{WIDTH}" height="{HEIGHT}" rx="24" fill="{INK}"/>'
    body += '<g opacity=".15">'
    for x in range(24, WIDTH, 24):
        for y in range(96, 336, 24):
            body += f'<circle cx="{x}" cy="{y}" r=".75" fill="{MUTED}"/>'
    body += '</g>'
    body += f'<g transform="translate(32 20) scale(.21)">{mark(slug)}</g>'
    body += text(98, 57, display, 29, weight=700)
    body += text(1164, 43, right_top, 13, MUTED, weight=700, text_anchor="end", letter_spacing="1")
    body += text(1164, 66, right_sub, 14, TEAL, text_anchor="end")
    for x, w, number, label in cards:
        body += f'<rect x="{x}" y="100" width="{w}" height="218" rx="14" fill="{PANEL}" stroke="{BORDER}"/>'
        body += text(x + 18, 126, number, 11, COPPER, mono=True)
        body += text(x + 49, 126, label, 12, MUTED, weight=700, letter_spacing=".8")
    return body


def cli_bar(cmd: str, tag: str) -> str:
    body = f'<rect x="32" y="336" width="1136" height="40" rx="9" fill="#0B1219" stroke="{BORDER}"/>'
    body += text(48, 361, "$", 14, TEAL, mono=True)
    body += text(68, 361, cmd, 13, PAPER, mono=True)
    body += text(1151, 361, tag, 12, COPPER, mono=True, text_anchor="end")
    return body


def stage_arrows(t: float, pairs: list[tuple[int, int, str, float]]) -> str:
    body = ""
    for start, end, color, offset in pairs:
        body += wire(f"M{start} 207H{end - 8}", color)
        body += wire(f"M{end - 14} 201L{end - 6} 207L{end - 14} 213", color)
        body += pulse((start, 207), (end - 10, 207), ((t - offset) * 3.5) % 1, color)
    return body


def banner_rustsys(frame: int) -> str:
    """Original RustSys 8.4s loop: read RTL, optimize AIG cone, map gates, propagate pulses."""
    t = frame / FRAMES
    phase = min(3, int(t * 4))
    active_labels = ["READ RTL", "OPTIMIZE", "MAP CELLS", "WRITE NETLIST"]
    cards = [(32, 326, "01", "RTL / VERILOG"),
             (438, 278, "02", "SYNTHESIZE"),
             (796, 372, "03", "GATE-LEVEL NETLIST")]
    body = base_canvas("rustsys", "RustSys", "VERILOG  →  GATE-LEVEL NETLIST", "CLI first. Ready for agents.", cards)
    code = [("module enabled_and (", PAPER),
            ("  input clk, en,", MUTED),
            ("  input [7:0] a, b,", MUTED),
            ("  output reg [7:0] q", MUTED),
            (");", PAPER),
            ("  always @(posedge clk)", COPPER),
            ("    if (en) q <= a & b;", TEAL),
            ("endmodule", PAPER)]
    revealed = min(len(code), 1 + int(t * 33))
    for i, (line, color) in enumerate(code):
        opacity = "1" if i < revealed else ".18"
        body += text(49, 153 + i * 20, line, 13, color, mono=True, opacity=opacity)
    if t < .21:
        row = max(0, revealed - 1)
        body += f'<rect x="{51 + min(30, len(code[row][0])) * 7.83}" y="{142 + row * 20}" width="6" height="14" fill="{TEAL}"/>'
    body += stage_arrows(t, [(367, 428, COPPER, .05), (725, 786, TEAL, .33)])

    nodes = [(469, 174), (469, 236), (508, 148), (508, 205),
             (508, 269), (555, 174), (555, 236), (605, 205), (654, 205)]
    edges = [(0, 3), (1, 3), (2, 5), (3, 5), (3, 6), (4, 6), (5, 7), (6, 7), (7, 8)]
    energized = t >= .22
    for i, (a, b) in enumerate(edges):
        xa, ya = nodes[a]
        xb, yb = nodes[b]
        c = TEAL if (energized and (i + frame // 4) % 3 != 0) else BORDER
        body += wire(f"M{xa} {ya}L{xb} {yb}", c)
    redundant_opacity = .17 if t >= .38 else .8
    for i, (x, y) in enumerate(nodes):
        fade = redundant_opacity if i in (2, 4) else 1
        fill = TEAL if energized and (i + frame // 3) % 4 == 0 else PANEL
        body += f'<circle cx="{x}" cy="{y}" r="7" fill="{fill}" stroke="{TEAL if energized else BORDER}" stroke-width="2" opacity="{fade}"/>'
    body += text(577, 296, active_labels[phase], 11, TEAL, weight=700, text_anchor="middle", letter_spacing="1.5")

    active = t >= .51
    body += text(815, 170, "a", 11, MUTED, mono=True)
    body += text(815, 213, "b", 11, MUTED, mono=True)
    body += text(815, 145, "en", 10, MUTED, mono=True)
    body += text(815, 264, "clk", 11, MUTED, mono=True)
    body += wire("M840 166H865M840 208H865", COPPER if active else BORDER)
    body += (f'<path d="M866 153H884C915 153 915 221 884 221H866Z" '
             f'fill="{INK}" stroke="{COPPER if active else MUTED}" stroke-width="2"/>')
    body += text(879, 193, "&", 17, PAPER, mono=True)
    body += text(884, 242, "AND × 8", 10, MUTED, mono=True, text_anchor="middle")
    body += wire("M908 187H922", TEAL if active else BORDER)
    body += f'<path d="M922 151L947 163V220L922 232Z" fill="{INK}" stroke="{TEAL if active else MUTED}" stroke-width="2"/>'
    body += text(930, 199, "M", 10, MUTED, mono=True)
    body += wire("M840 141H934V149", TEAL if active else BORDER)
    body += wire("M947 193H990M840 259H976V231H990", TEAL if active else BORDER)
    body += f'<rect x="991" y="170" width="72" height="76" rx="5" fill="{INK}" stroke="{TEAL if active else MUTED}" stroke-width="2"/>'
    body += text(1001, 199, "D", 12, PAPER, mono=True)
    body += text(1041, 199, "Q", 12, PAPER, mono=True)
    body += f'<path d="M992 223L1002 231L992 239" fill="none" stroke="{MUTED}" stroke-width="1.5"/>'
    body += text(1027, 158, "DFF × 8", 10, MUTED, mono=True, text_anchor="middle")
    body += wire("M1063 194H1138M1110 194V282H912V213H922", TEAL if active else BORDER)
    body += f'<circle cx="1110" cy="194" r="3" fill="{TEAL if active else BORDER}"/>'
    body += text(1143, 199, "q", 11, TEAL, mono=True)
    body += text(980, 304, "Liberty cell library", 10, MUTED, text_anchor="middle")
    if active:
        body += pulse((1065, 194), (1137, 194), ((t - .51) * 6) % 1, TEAL)
        body += pulse((841, 259), (974, 259), ((t - .51) * 6) % 1, COPPER)
        if (frame // 3) % 2 == 0:
            body += f'<rect x="1005" y="215" width="41" height="4" rx="2" fill="{TEAL}" opacity=".7"/>'
    body += cli_bar("rustsys synth --top enabled_and --lib cells.lib --out gates.v enabled_and.v", "RTL → gates")
    return svg(WIDTH, HEIGHT, body, "RustSys: Verilog to gate-level netlist",
               "A clock-enabled eight-bit AND in Verilog maps to an AND, feedback multiplexer and flip-flop bank. "
               "Animated pulses describe data flow; this is an illustration, not a run or proof result.")


def banner_ruststa(frame: int) -> str:
    """Original RustSTA 8.4s loop: design inputs, reconvergent timing DAG, path1 report."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    cards = [(32, 286, "01", "DESIGN INPUTS"),
             (396, 410, "02", "PROPAGATE TIMING"),
             (884, 284, "03", "INSPECT THE PATH")]
    body = base_canvas("ruststa", "RustSTA", "NETLIST  →  TIMING  →  CRITICAL PATH", "CLI first. Ready for agents.", cards)
    for i, (name, label) in enumerate([("gates.v", "gate-level netlist"),
                                       ("cells.lib", "Liberty timing tables"),
                                       ("design.sdc", "clocks + constraints")]):
        y = 158 + 47 * i
        color = COPPER if i == stage or stage >= 1 else MUTED
        body += wire(f"M52 {y-10}H65L72 {y-3}V{y+12}H52Z", color)
        body += text(85, y, name, 16, PAPER, mono=True)
        body += text(85, y + 18, label, 11, MUTED)
    body += text(51, 298, "Explicit files. Reproducible revisions.", 11, TEAL)
    body += stage_arrows(t, [(329, 385, COPPER, 0.0), (817, 873, TEAL, 0.25)])

    critical = [((461, 206), (516, 167)), ((516, 167), (604, 206)),
                ((604, 206), (691, 167)), ((691, 167), (745, 206))]
    other = [((461, 206), (516, 254)), ((516, 254), (604, 206)),
             ((604, 206), (691, 254)), ((691, 254), (745, 206))]
    active = t >= .25
    for a, b in other:
        body += wire(f"M{a[0]} {a[1]}L{b[0]} {b[1]}")
    for i, (a, b) in enumerate(critical):
        color = TEAL if active else BORDER
        body += wire(f"M{a[0]} {a[1]}L{b[0]} {b[1]}", color, 3)
        if active:
            body += pulse(a, b, ((t - .25) * 4 - i * .23) % 1)
    for x, y in [(516, 167), (516, 254), (604, 206), (691, 167), (691, 254)]:
        color = TEAL if active and y != 254 else BORDER
        body += f'<circle cx="{x}" cy="{y}" r="8" fill="{INK}" stroke="{color}" stroke-width="2"/>'
    for x, name in [(425, "r0/Q"), (745, "r1/D")]:
        body += f'<rect x="{x}" y="189" width="36" height="34" rx="4" fill="{INK}" stroke="{PAPER}"/>'
        body += wire(f"M{x} 213L{x+6} 217L{x} 221", MUTED, 1)
        body += text(x + 18, 242, name, 11, MUTED, mono=True, text_anchor="middle")
    body += text(443, 171, "launch", 10, COPPER, text_anchor="middle")
    body += text(763, 171, "capture", 10, TEAL, text_anchor="middle")
    body += wire("M428 282H461V272H498V282H536V272H574V282H612V272H650V282H688V272H772", COPPER)
    body += text(601, 302, ["READ INPUTS", "ARRIVAL → REQUIRED", "RANK BOUNDED PATHS", "TRACE THE CRITICAL PATH"][stage],
                 11, TEAL, weight=700, text_anchor="middle", letter_spacing="1")

    body += text(903, 162, "path1", 23, TEAL, weight=700, mono=True)
    body += text(1149, 161, "setup", 12, COPPER, mono=True, text_anchor="end")
    for i, (key, value) in enumerate([("corner", "default"), ("wire model", "ideal"),
                                      ("from", "r0/Q"), ("to", "r1/D")]):
        y = 190 + i * 24
        body += text(904, y, key, 12, MUTED, mono=True)
        body += text(1148, y, value, 13, PAPER, mono=True, text_anchor="end")
    body += text(904, 298, "Trace → inspect → iterate", 12, TEAL)
    body += cli_bar("ruststa run --workspace ./ws --base-rev rev1 --max-paths 5 --format json", "netlist → insight")
    return svg(WIDTH, HEIGHT, body, "RustSTA: from netlists to timing insight",
               "Netlist, Liberty and SDC inputs feed a reconvergent timing graph. Signals move along a "
               "highlighted launch-to-capture path, then a revision-scoped path report identifies its endpoints.")


def banner_rusteda(frame: int) -> str:
    """RustEDA 8.4s loop: RTL proposal, native engine pipeline + checkpoints, structured feedback."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    cards = [(32, 296, "01", "PROPOSE & IMPORT"),
             (402, 404, "02", "IMPLEMENT & CHECK LOOP"),
             (880, 288, "03", "MEASURE & IMPROVE")]
    body = base_canvas("rusteda", "RustEDA", "RTL  →  GDS & TIMING  →  VERIFIED FEEDBACK",
                       "Fast native engines for the chip-design loop.", cards)

    code = [("module counter (", PAPER),
            ("  input clk, rst_n, en,", MUTED),
            ("  output reg [7:0] q", MUTED),
            (");", PAPER),
            ("  // pipeline carry stage", COPPER),
            ("  always @(posedge clk)", COPPER),
            ("    if (en) q <= q_next;", TEAL),
            ("endmodule", PAPER)]
    revealed = min(len(code), 2 + int(t * 28))
    for i, (line, color) in enumerate(code):
        op = "1" if i < revealed else ".22"
        body += text(48, 152 + i * 18, line, 12, color, mono=True, opacity=op)
    body += text(48, 300, "workspace.lock · rev0 → rev1", 11, TEAL, mono=True)
    body += stage_arrows(t, [(338, 392, COPPER, 0.05), (816, 870, TEAL, 0.30)])

    boxes = [
        (418, "RustSys", "rev1", "RTL → cells", "CEC: proven", stage >= 0),
        (548, "RustROAD", "rev2", "Place · CTS", "Route · GDS", stage >= 1),
        (678, "RustSTA", "rev3", "SPEF timing", "path1 · slack", stage >= 2),
    ]
    for bx, btitle, brev, sub1, sub2, act in boxes:
        stroke = TEAL if act else BORDER
        body += f'<rect x="{bx}" y="146" width="110" height="72" rx="8" fill="{INK}" stroke="{stroke}" stroke-width="2"/>'
        body += text(bx + 10, 168, btitle, 13, PAPER, weight=700)
        body += text(bx + 10, 188, sub1, 10, MUTED, mono=True)
        body += text(bx + 10, 205, sub2, 10, TEAL if act else MUTED, mono=True)
    for sx, ex, col in [(528, 548, COPPER), (658, 678, TEAL)]:
        body += wire(f"M{sx} 182H{ex}", col, 2)
        body += pulse((sx, 182), (ex, 182), (t * 4) % 1, col)

    fb_active = stage >= 2
    fb_color = COPPER if stage == 3 else (TEAL if fb_active else BORDER)
    body += wire("M733 218V250H473V218", fb_color, 2)
    body += wire("M468 225L473 218L478 225", fb_color, 2)
    if fb_active:
        body += pulse((733, 250), (473, 250), ((t - .35) * 3) % 1, COPPER)
    for rx, rlabel in [(473, "rev1"), (603, "rev2"), (733, "rev3")]:
        body += f'<circle cx="{rx}" cy="250" r="4.5" fill="{INK}" stroke="{fb_color}" stroke-width="2"/>'
        body += text(rx, 266, rlabel, 9, COPPER if fb_active else MUTED, mono=True, text_anchor="middle")
    body += text(603, 281, "RustEDA checkpoints · workbench selection", 10, MUTED, text_anchor="middle")
    body += text(604, 302, ["SYNTHESIZE & PROVE LEC", "PLACE, CTS & ROUTE LAYOUT", "EXTRACT RC & TIME PATHS", "FEEDBACK TO NEXT ITERATION"][stage],
                 11, TEAL, weight=700, text_anchor="middle", letter_spacing="1")

    body += text(898, 160, "rusteda.v1", 21, TEAL, weight=700, mono=True)
    body += text(1150, 159, "rev3", 12, COPPER, mono=True, text_anchor="end")
    for i, (k, v, c) in enumerate([
        ("lec verdict", "equivalent", TEAL),
        ("worst path", "path1 (r0 → r1)", PAPER),
        ("focus handle", "sel1 @ rev2", COPPER),
        ("next action", "guide RTL edit", TEAL),
    ]):
        y = 188 + i * 24
        body += text(898, y, k, 12, MUTED, mono=True)
        body += text(1150, y, v, 12, c, mono=True, text_anchor="end")
    body += text(898, 298, "Design → measure → improve", 12, TEAL)
    body += cli_bar("./dev flow --workspace .work/counter run --base-rev rev0 && ./dev flow --workspace .work/counter report",
                    "RTL → GDS → feedback")
    return svg(WIDTH, HEIGHT, body, "RustEDA: chip-design feedback loop",
               "An RTL change flows through RustSys, RustROAD and RustSTA in a revisioned workspace to guide the next iteration.")


def banner_rustroad(frame: int) -> str:
    """RustROAD 8.4s loop: mapped netlist & LEF inputs, row placement + CTS + routing, DEF/SPEF/GDS."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    cards = [(32, 286, "01", "PHYSICAL INPUTS"),
             (396, 410, "02", "PLACE · CLOCK TREE · ROUTE"),
             (884, 284, "03", "PHYSICAL ARTIFACTS")]
    body = base_canvas("rustroad", "RustROAD", "NETLIST + LEF  →  PLACE · CTS · ROUTE  →  GDS",
                       "CLI first. Ready for agents.", cards)

    for i, (name, label) in enumerate([("mapped.v", "synthesized standard cells"),
                                       ("tech + cells.lef", "layers, vias & cell abstracts"),
                                       ("floorplan.json", "die area, N/FS rows & taps")]):
        y = 158 + 47 * i
        color = COPPER if i == stage or stage >= 1 else MUTED
        body += wire(f"M52 {y-10}H65L72 {y-3}V{y+12}H52Z", color)
        body += text(85, y, name, 15, PAPER, mono=True)
        body += text(85, y + 18, label, 11, MUTED)
    body += text(51, 298, "Integer DBU geometry. Revision guarded.", 11, TEAL)
    body += stage_arrows(t, [(329, 385, COPPER, 0.05), (817, 873, TEAL, 0.30)])

    body += f'<rect x="420" y="142" width="362" height="136" rx="6" fill="#0D161F" stroke="{BORDER}"/>'
    for ry in (150, 196, 242):
        body += wire(f"M426 {ry}H776", "#233542", 1)
        body += wire(f"M426 {ry + 26}H776", "#233542", 1)
        body += f'<rect x="430" y="{ry+2}" width="26" height="22" rx="3" fill="{INK}" stroke="{BORDER}"/>'
        body += text(443, ry + 16, "TAP", 8, MUTED, mono=True, text_anchor="middle")
        body += f'<rect x="746" y="{ry+2}" width="26" height="22" rx="3" fill="{INK}" stroke="{BORDER}"/>'
        body += text(759, ry + 16, "TAP", 8, MUTED, mono=True, text_anchor="middle")

    cts_active = t >= .22
    cts_col = COPPER if cts_active else BORDER
    body += wire("M420 186H573V198", cts_col, 2)
    body += wire("M573 220V232H491V174", cts_col, 2)
    body += wire("M573 232H689V244", cts_col, 2)
    if cts_active:
        body += pulse((420, 186), (573, 186), ((t - .22) * 4) % 1, COPPER)
        body += pulse((573, 232), (689, 232), ((t - .22) * 4 - .3) % 1, COPPER)

    route_active = t >= .45
    r_col = TEAL if route_active else BORDER
    body += wire("M514 163H536M580 163H598M642 163H654V255H666M712 255H732V186H782", r_col, 2.4)

    cells = [
        (468, 152, 46, "ff0", PAPER),
        (536, 152, 44, "u1", PAPER),
        (598, 152, 44, "u2", PAPER),
        (548, 198, 50, "buf_ck", COPPER if stage >= 1 else MUTED),
        (612, 198, 34, "u3", PAPER),
        (502, 244, 44, "u4", PAPER),
        (666, 244, 46, "ff1", PAPER),
    ]
    for cx, cy, cw, cname, ccol in cells:
        body += f'<rect x="{cx}" y="{cy}" width="{cw}" height="22" rx="3" fill="{INK}" stroke="{ccol}" stroke-width="1.6"/>'
        body += text(cx + cw / 2, cy + 15, cname, 10, PAPER, mono=True, text_anchor="middle")

    for vx, vy in [(654, 163), (654, 255), (732, 255), (732, 186)]:
        body += f'<circle cx="{vx}" cy="{vy}" r="3.2" fill="{PAPER if route_active else BORDER}"/>'
    if route_active:
        body += pulse((654, 163), (654, 255), ((t - .45) * 4) % 1, TEAL)
        body += pulse((732, 255), (732, 186), ((t - .45) * 4 - .4) % 1, TEAL)

    body += text(601, 302, ["PLACE ROWS & WELL TAPS", "SYNTHESIZE CLOCK TREE", "ROUTE METAL TRACKS & VIAS", "EXTRACT SPEF & STREAM GDS"][stage],
                 11, TEAL, weight=700, text_anchor="middle", letter_spacing="1")

    body += text(903, 162, "chip.gds", 22, TEAL, weight=700, mono=True)
    body += text(1149, 161, "rev5", 12, COPPER, mono=True, text_anchor="end")
    for i, (k, v) in enumerate([("layout", "routed.def"),
                                ("parasitics", "routed_max.spef"),
                                ("checks", "DEF · tap · antenna"),
                                ("streamout", "DBU exact GDSII")]):
        y = 190 + i * 24
        body += text(904, y, k, 12, MUTED, mono=True)
        body += text(1148, y, v, 12, PAPER, mono=True, text_anchor="end")
    body += text(904, 298, "Place → CTS → route → GDS", 12, TEAL)
    body += cli_bar("rustroad route --workspace ./ws --base-rev rev3 --scope all --config sky130-routing.json", "netlist → GDS")
    return svg(WIDTH, HEIGHT, body, "RustROAD: physical implementation from netlist to GDS",
               "Synthesized cells are placed into rows with well taps, buffered with a clock tree, and routed onto metal tracks.")


def banner_common(frame: int) -> str:
    """RustEDA Common 8.4s loop: formats, generational arena IR & mmap store, SoA/CSR compute."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    cards = [(32, 286, "01", "RUSTEDA-FORMATS"),
             (396, 410, "02", "ARENA IR & MMAP STORE"),
             (884, 284, "03", "COMPUTE & CLI CONTRACT")]
    body = base_canvas("rusteda-common", "RustEDA Common", "FORMATS  →  TYPED ARENAS & MMAP  →  COMPUTE & CLI",
                       "The shared Rust foundation.", cards)

    for i, (name, label) in enumerate([(".v / .sv / .aig", "RTL, netlists & logic graphs"),
                                       (".lib (NLDM 1D–3D)", "Sky130 & GF180 cell tables"),
                                       (".sdc + .spef.gz", "clocks, exceptions & RC trees")]):
        y = 158 + 47 * i
        color = COPPER if i == stage or stage >= 1 else MUTED
        body += wire(f"M52 {y-10}H65L72 {y-3}V{y+12}H52Z", color)
        body += text(85, y, name, 15, PAPER, mono=True)
        body += text(85, y + 18, label, 11, MUTED)
    body += text(51, 298, "Explicit diagnostics. Zero silent ignores.", 11, TEAL)
    body += stage_arrows(t, [(329, 385, COPPER, 0.05), (817, 873, TEAL, 0.30)])

    c1_stroke = COPPER if stage == 1 else (TEAL if stage >= 2 else BORDER)
    body += f'<rect x="416" y="144" width="370" height="56" rx="8" fill="#0D161F" stroke="{c1_stroke}" stroke-width="1.8"/>'
    body += text(428, 164, "rusteda-core · Generational Arena IR", 12, PAPER, weight=700, mono=True)
    pills = [
        (428, 54, "InstId"),
        (490, 54, "NetId"),
        (552, 54, "PinId"),
        (614, 56, "CellId"),
        (678, 96, "SymbolInterner"),
    ]
    for px, pw, pill in pills:
        body += f'<rect x="{px}" y="173" width="{pw}" height="20" rx="4" fill="{INK}" stroke="{BORDER}"/>'
        body += text(px + pw / 2, 187, pill, 9, TEAL if stage >= 1 else MUTED, mono=True, text_anchor="middle")

    for ax in (520, 680):
        body += wire(f"M{ax} 200V222", TEAL if stage >= 2 else BORDER, 2)
        if stage >= 1:
            body += pulse((ax, 200), (ax, 222), (t * 4) % 1, COPPER if stage == 1 else TEAL)

    c2_stroke = TEAL if stage >= 2 else BORDER
    body += f'<rect x="416" y="222" width="370" height="56" rx="8" fill="#0D161F" stroke="{c2_stroke}" stroke-width="1.8"/>'
    body += text(428, 243, "rusteda-workspace · Daemonless Mmap Store", 12, PAPER, weight=700, mono=True)
    body += text(428, 264, "workspace.lock · --base-rev · netlist.rbin · sta.rbin", 10, TEAL if stage >= 2 else MUTED, mono=True)

    body += text(601, 302, ["PARSE STANDARD FORMATS", "INTERN INTO TYPED ARENAS", "MMAP REVISIONED SIDE-TABLES", "LOWER TO CONTIGUOUS SOA / CSR"][stage],
                 11, TEAL, weight=700, text_anchor="middle", letter_spacing="1")

    body += text(903, 162, "SoA / CSR", 21, TEAL, weight=700, mono=True)
    body += text(1149, 161, "rusteda.v1", 12, COPPER, mono=True, text_anchor="end")
    for i, (k, v, c) in enumerate([("cpu_ref", "Rayon deterministic f64", PAPER),
                                   ("gpu", "Native CUDA kernels", TEAL),
                                   ("cli envelope", "JSON + coded errors", PAPER),
                                   ("handles", "rev1, inst42, path1", COPPER)]):
        y = 190 + i * 24
        body += text(904, y, k, 12, MUTED, mono=True)
        body += text(1148, y, v, 12, c, mono=True, text_anchor="end")
    body += text(904, 298, "Shared by RustSys, RustSTA & Studio", 12, TEAL)
    body += cli_bar("rusteda -w ./ws --format json import --base-rev rev0 --lib sky130.lib --rtl top.sv --top top", "core · mmap · compute")
    return svg(WIDTH, HEIGHT, body, "RustEDA Common: shared models, formats, workspaces and compute",
               "Standard EDA formats parse into generational arenas, memory-mapped workspace revisions and contiguous compute views.")


BANNERS = {
    "rusteda": banner_rusteda,
    "rustsys": banner_rustsys,
    "ruststa": banner_ruststa,
    "rustroad": banner_rustroad,
    "rusteda-common": banner_common,
}


def raster(source: str, width: int = WIDTH, height: int = HEIGHT) -> Image.Image:
    """Rasterize SVG geometry to an RGB PIL Image using resvg, rsvg-convert, or cairosvg."""
    if shutil.which("resvg"):
        png = subprocess.run(
            ["resvg", "-w", str(width), "-h", str(height), "-", "-c"],
            input=source.encode(), capture_output=True, check=True, timeout=15,
        ).stdout
    elif shutil.which("rsvg-convert"):
        png = subprocess.run(
            ["rsvg-convert", "-w", str(width), "-h", str(height)],
            input=source.encode(), capture_output=True, check=True, timeout=15,
        ).stdout
    else:
        import cairosvg
        png = cairosvg.svg2png(bytestring=source.encode(), output_width=width, output_height=height)
    with Image.open(io.BytesIO(png)) as img:
        return img.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true", help="Write SVG marks, wordmarks and static banners only")
    args = parser.parse_args()
    if WIDTH * HEIGHT > MAX_PIXELS or not 1 <= FRAMES <= MAX_FRAMES:
        raise SystemExit("ASSET_BUDGET_EXCEEDED: configured dimensions or frames exceed hard caps")

    qa = ROOT / ".work" / "brand"
    qa.mkdir(parents=True, exist_ok=True)
    suite_logo_svg = svg(256, 256, mark("rusteda"), "RustEDA signal-forge mark",
                         "A shared terminal and chip frame surrounding crossing copper and teal signals.")
    suite_wordmark_svg = wordmark("rusteda", "RustEDA", "From RTL to verified silicon.")

    manifest = []
    for name, slug, display, subtitle in PRODUCTS:
        dest = (ROOT if name == "RustEDA" else ROOT / "modules" / name) / "docs" / "assets"
        dest.mkdir(parents=True, exist_ok=True)
        logo_svg = svg(256, 256, mark(slug), f"{display} mark", subtitle)
        wmark_svg = wordmark(slug, display, subtitle)
        banner_fn = BANNERS[slug]
        poster_svg = banner_fn(65)

        (dest / f"{slug}-logo.svg").write_text(logo_svg)
        (dest / "rusteda-logo.svg").write_text(suite_logo_svg)
        (dest / "favicon.svg").write_text(logo_svg)
        (dest / f"{slug}-wordmark.svg").write_text(wmark_svg)
        if slug != "rusteda":
            (dest / "rusteda-wordmark.svg").write_text(suite_wordmark_svg)
        (dest / f"{slug}-banner.svg").write_text(poster_svg)

        if args.static_only:
            manifest.append({"name": name, "slug": slug, "static_only": True})
            continue

        palette = raster(poster_svg).quantize(colors=96, method=Image.Quantize.MEDIANCUT)
        frames = []
        samples = []
        for frame in range(FRAMES):
            img = raster(banner_fn(frame))
            frames.append(img.quantize(palette=palette, dither=Image.Dither.NONE))
            if frame in (0, 21, 42, 65):
                samples.append(img.resize((600, 200)))

        gif_path = dest / f"{slug}-banner.gif"
        frames[0].save(
            gif_path, save_all=True, append_images=frames[1:],
            duration=FRAME_MS, loop=0, optimize=True, disposal=1,
        )
        gif_bytes = gif_path.stat().st_size
        if gif_bytes > MAX_GIF_BYTES:
            raise SystemExit(f"ASSET_BUDGET_EXCEEDED: {gif_path} ({gif_bytes} bytes) exceeds {MAX_GIF_BYTES}")

        contact = Image.new("RGB", (1200, 400), INK)
        for idx, sample in enumerate(samples):
            contact.paste(sample, ((idx % 2) * 600, (idx // 2) * 200))
        contact.save(qa / f"{slug}-contact-sheet.png")
        raster(poster_svg).save(qa / f"{slug}-banner-poster.png")

        manifest.append({
            "name": name,
            "slug": slug,
            "width": WIDTH,
            "height": HEIGHT,
            "frames": FRAMES,
            "banner_bytes": gif_bytes,
            "banner_sha256": hashlib.sha256(gif_path.read_bytes()).hexdigest(),
        })
        del frames, samples

    (qa / "assets-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

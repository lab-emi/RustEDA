#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Render the unified RustEDA Signal Forge v2 visual identity and animated GIF banners.

All six repositories (RustEDA + five engine/foundation modules) plus RustEDA Studio
share the Signal Forge v2 precision IC die frame, deep obsidian-slate palette,
256x256 emblem, 760x176 two-tone wordmark, and 1200x400 three-card animated pipeline
banner (84 frames at 100 ms/frame).

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
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

# Signal Forge v2 palette
INK = "#0B121B"
INK_HI = "#121E2C"
PANEL = "#121D2A"
PANEL_WELL = "#080F17"
BORDER = "#273C4F"
BORDER_SOFT = "#1C2D3D"
COPPER = "#F08A4B"
COPPER_SOFT = "#3A241A"
TEAL = "#4ADBC0"
TEAL_SOFT = "#133231"
PAPER = "#F5F2EA"
MUTED = "#9EB1C0"
DIM = "#627788"

SANS = "'Fira Sans','Inter','SF Pro Display','Segoe UI','DejaVu Sans',sans-serif"
MONO = "'Hack','JetBrains Mono','Adwaita Mono','DejaVu Sans Mono',monospace"

WIDTH, HEIGHT, FRAMES, FRAME_MS = 1200, 400, 84, 100
MAX_PIXELS = 1200 * 400
MAX_FRAMES = 96
MAX_GIF_BYTES = 750_000

# (name, slug, display, subtitle, badge, default_dest)
PRODUCTS = [
    ("RustEDA", "rusteda", "RustEDA", "AI-native RTL-to-GDS in Rust", "RTL → GDS", ROOT / "docs/assets"),
    ("RustSys", "rustsys", "RustSys", "Bounded synthesis & formal LEC", "SYNTHESIS", ROOT / "modules/RustSys/docs/assets"),
    ("RustSTA", "ruststa", "RustSTA", "Multi-corner static timing", "TIMING", ROOT / "modules/RustSTA/docs/assets"),
    ("RustROAD", "rustroad", "RustROAD", "Place, CTS, route & GDSII", "PHYSICAL", ROOT / "modules/RustROAD/docs/assets"),
    ("RustSpice", "rustspice", "RustSpice", "Differentiable SPICE & adjoints", "SPICE + AD", ROOT / "modules/RustSpice/docs/assets"),
    ("RustEDA-Common", "rusteda-common", "RustEDA Common", "Typed arenas, formats & compute", "FOUNDATION", ROOT / "modules/RustEDA-Common/docs/assets"),
    ("RustEDA-Studio", "rusteda-studio", "RustEDA Studio", "Revisioned layout & timing view", "WORKBENCH", ROOT / "apps/docs/assets"),
]


def text(x: float, y: float, value: str, size: float = 16, fill: str = PAPER,
         weight: int = 400, mono: bool = False, **attrs: object) -> str:
    extra = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    family = MONO if mono else SANS
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-family="{family}" xml:space="preserve" '
            f'font-size="{size}" font-weight="{weight}" {extra}>{html.escape(value)}</text>')


def brand_title(x: float, y: float, display: str, size: float, weight: int = 700,
                letter_spacing: str = "-0.8") -> str:
    """Two-tone product name: Rust in warm copper, engine suffix in crisp paper, qualifier in mint."""
    if display.startswith("Rust"):
        rest = display[4:]
        if " " in rest:
            stem, qual = rest.split(" ", 1)
            inner = (f'<tspan fill="{COPPER}">Rust</tspan>'
                     f'<tspan fill="{PAPER}">{html.escape(stem)}</tspan>'
                     f'<tspan fill="{TEAL}" font-weight="600"> {html.escape(qual)}</tspan>')
        else:
            inner = (f'<tspan fill="{COPPER}">Rust</tspan>'
                     f'<tspan fill="{PAPER}">{html.escape(rest)}</tspan>')
    else:
        inner = html.escape(display)
    return (f'<text x="{x}" y="{y}" font-family="{SANS}" xml:space="preserve" '
            f'font-size="{size}" font-weight="{weight}" letter-spacing="{letter_spacing}">{inner}</text>')


def svg(width: int, height: int, body: str, title: str, description: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{html.escape(title)}</title>'
            f'<desc id="desc">{html.escape(description)}</desc>{body}</svg>\n')


def mark(slug: str, uid: str = "m") -> str:
    """Signal Forge v2 precision IC die bezel surrounding a tool-specific glyph."""
    bg_id = f"{uid}-bg"
    die_id = f"{uid}-die"
    body = (
        f'<defs>'
        f'<linearGradient id="{bg_id}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="#152333"/>'
        f'<stop offset="100%" stop-color="#091018"/>'
        f'</linearGradient>'
        f'<linearGradient id="{die_id}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="#111D2B"/>'
        f'<stop offset="100%" stop-color="#0A121C"/>'
        f'</linearGradient>'
        f'</defs>'
        # Outer silicon package with crisp bezel for both light & dark themes
        f'<rect x="2" y="2" width="252" height="252" rx="52" fill="url(#{bg_id})" stroke="#2C445B" stroke-width="4"/>'
        # Package input leads (copper left) and output leads (mint right)
        f'<g stroke-linecap="round" stroke-width="11">'
        f'<line x1="20" y1="98" x2="46" y2="98" stroke="{COPPER}"/>'
        f'<line x1="20" y1="158" x2="46" y2="158" stroke="{COPPER}"/>'
        f'<line x1="210" y1="98" x2="236" y2="98" stroke="{TEAL}"/>'
        f'<line x1="210" y1="158" x2="236" y2="158" stroke="{TEAL}"/>'
        f'</g>'
        # Precision chamfered Pin-1 IC die frame
        f'<path d="M72 46 H194 A16 16 0 0 1 210 62 V194 A16 16 0 0 1 194 210 '
        f'H62 A16 16 0 0 1 46 194 V72 Z" fill="url(#{die_id})" stroke="#314B63" stroke-width="4.5"/>'
        # Pin-1 orientation marker dot
        f'<circle cx="67" cy="67" r="4.5" fill="{COPPER}" opacity="0.9"/>'
    )

    if slug == "rustsys":
        # Logic gate + formal equivalence proof check
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M64 104 H92 M64 152 H92" stroke="{COPPER}" stroke-width="10"/>'
            f'<path d="M94 82 H126 C164 82 178 104 178 128 C178 152 164 174 126 174 H94 Z" '
            f'fill="#162637" stroke="{PAPER}" stroke-width="10"/>'
            f'<path d="M112 129 L125 142 L150 114" stroke="{TEAL}" stroke-width="10"/>'
            f'<path d="M178 128 H194" stroke="{TEAL}" stroke-width="10"/>'
            f'</g>'
            f'<circle cx="64" cy="104" r="6.5" fill="{COPPER}"/>'
            f'<circle cx="64" cy="152" r="6.5" fill="{COPPER}"/>'
            f'<circle cx="194" cy="128" r="6.5" fill="{TEAL}"/>'
        )
    elif slug == "ruststa":
        # Clock launch edge -> critical-path slew ramp -> capture deadline & slack bracket
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M64 166 H82 V92" stroke="{COPPER}" stroke-width="9"/>'
            f'<path d="M74 108 L82 92 L90 108" stroke="{COPPER}" stroke-width="7"/>'
            f'<path d="M92 162 H112 L140 96 H166" stroke="{PAPER}" stroke-width="11"/>'
            f'<line x1="184" y1="84" x2="184" y2="172" stroke="{TEAL}" stroke-width="8" stroke-dasharray="12 10"/>'
            f'<path d="M166 130 H182" stroke="{TEAL}" stroke-width="8"/>'
            f'</g>'
            f'<circle cx="112" cy="162" r="7" fill="{COPPER}"/>'
            f'<circle cx="140" cy="96" r="7" fill="{PAPER}"/>'
            f'<circle cx="166" cy="96" r="8" fill="{TEAL}"/>'
        )
    elif slug == "rustroad":
        # Multi-layer Manhattan metal tracks & crisp square silicon vias
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            # Horizontal copper metal layer (M1/M3)
            f'<path d="M66 96 H152 M104 160 H190" stroke="{COPPER}" stroke-width="11"/>'
            # Vertical mint metal layer (M2/M4)
            f'<path d="M104 72 V160 M152 96 V184" stroke="{TEAL}" stroke-width="11"/>'
            # Connecting bus
            f'<path d="M104 128 H152" stroke="{PAPER}" stroke-width="7" opacity="0.85"/>'
            f'</g>'
            # Precision vias at layer intersections
            f'<rect x="94" y="86" width="20" height="20" rx="4" fill="{PAPER}"/>'
            f'<rect x="99" y="91" width="10" height="10" rx="2" fill="{INK}"/>'
            f'<rect x="142" y="86" width="20" height="20" rx="4" fill="{PAPER}"/>'
            f'<rect x="147" y="91" width="10" height="10" rx="2" fill="{INK}"/>'
            f'<rect x="94" y="150" width="20" height="20" rx="4" fill="{PAPER}"/>'
            f'<rect x="99" y="155" width="10" height="10" rx="2" fill="{INK}"/>'
            f'<rect x="142" y="150" width="20" height="20" rx="4" fill="{PAPER}"/>'
            f'<rect x="147" y="155" width="10" height="10" rx="2" fill="{INK}"/>'
        )
    elif slug == "rustspice":
        # Copper step input -> damped transient response curve + mint adjoint slope tangent
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            f'<line x1="64" y1="162" x2="192" y2="162" stroke="#284056" stroke-width="4"/>'
            # Step input
            f'<path d="M66 162 H92 V94 H108" stroke="{COPPER}" stroke-width="8" opacity="0.9"/>'
            # Damped transient curve
            f'<path d="M92 162 C108 160 116 82 138 86 C154 89 162 120 176 112 C182 109 187 108 192 108" '
            f'stroke="{PAPER}" stroke-width="10"/>'
            # Adjoint sensitivity tangent
            f'<line x1="94" y1="144" x2="144" y2="86" stroke="{TEAL}" stroke-width="7"/>'
            f'</g>'
            f'<circle cx="119" cy="115" r="8.5" fill="{TEAL}" stroke="{INK}" stroke-width="3"/>'
            f'<circle cx="192" cy="108" r="6" fill="{PAPER}"/>'
        )
    elif slug == "rusteda-common":
        # Three precision isometric silicon stack planes + central bus spine
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            # Bottom compute plane (mint)
            f'<path d="M76 154 L128 182 L180 154" stroke="{TEAL}" stroke-width="10"/>'
            # Middle mmap workspace plane (copper)
            f'<path d="M76 128 L128 156 L180 128" stroke="{COPPER}" stroke-width="10"/>'
            # Top typed arena plane (warm paper)
            f'<path d="M76 102 L128 74 L180 102 L128 130 Z" fill="#162637" stroke="{PAPER}" stroke-width="9"/>'
            f'</g>'
            f'<circle cx="128" cy="102" r="7" fill="{TEAL}"/>'
        )
    elif slug == "rusteda-studio":
        # Split die inspection viewport with highlighted critical path & crosshair
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            f'<rect x="70" y="76" width="116" height="104" rx="12" fill="#132233" stroke="{PAPER}" stroke-width="8"/>'
            f'<line x1="70" y1="104" x2="186" y2="104" stroke="{PAPER}" stroke-width="6"/>'
            f'<line x1="106" y1="104" x2="106" y2="180" stroke="{BORDER}" stroke-width="5"/>'
            f'<path d="M120 156 L142 126 L168 142" stroke="{TEAL}" stroke-width="9"/>'
            f'<line x1="82" y1="124" x2="96" y2="124" stroke="{COPPER}" stroke-width="6"/>'
            f'<line x1="82" y1="142" x2="96" y2="142" stroke="{MUTED}" stroke-width="6"/>'
            f'<line x1="82" y1="160" x2="96" y2="160" stroke="{MUTED}" stroke-width="6"/>'
            f'</g>'
            f'<circle cx="120" cy="156" r="6" fill="{COPPER}"/>'
            f'<circle cx="142" cy="126" r="6" fill="{PAPER}"/>'
            f'<circle cx="168" cy="142" r="6.5" fill="{TEAL}"/>'
        )
    else:
        # RustEDA Suite: Closed-Loop Silicon Forge (copper forward + mint feedback + forged die core)
        body += (
            f'<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
            # Top forward copper arc (RTL -> silicon)
            f'<path d="M68 112 V88 A14 14 0 0 1 82 74 H174 A14 14 0 0 1 188 88 V104" stroke="{COPPER}" stroke-width="11"/>'
            f'<path d="M174 92 L188 108 L201 92" stroke="{COPPER}" stroke-width="9"/>'
            # Bottom verified feedback mint arc (silicon -> next iteration)
            f'<path d="M188 144 V168 A14 14 0 0 1 174 182 H82 A14 14 0 0 1 68 168 V152" stroke="{TEAL}" stroke-width="11"/>'
            f'<path d="M55 164 L68 148 L82 164" stroke="{TEAL}" stroke-width="9"/>'
            # Central forged silicon diamond core
            f'<path d="M128 96 L160 128 L128 160 L96 128 Z" fill="#17293B" stroke="{PAPER}" stroke-width="9"/>'
            f'</g>'
            f'<circle cx="128" cy="128" r="9.5" fill="{TEAL}"/>'
        )
    return body


def wordmark(slug: str, display: str, subtitle: str, badge: str) -> str:
    """Crisp 760x176 two-tone wordmark with hardware bezel, legible subtitle & right category badge."""
    size = 56 if len(display) <= 10 else 43
    uid = f"wm-{slug}"
    body = (
        f'<defs>'
        f'<linearGradient id="{uid}-bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="#132130"/>'
        f'<stop offset="100%" stop-color="#091018"/>'
        f'</linearGradient>'
        f'<linearGradient id="{uid}-accent" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{COPPER}"/>'
        f'<stop offset="100%" stop-color="{TEAL}"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<rect x="1.5" y="1.5" width="757" height="173" rx="28" fill="url(#{uid}-bg)" stroke="#2A4055" stroke-width="3"/>'
        f'<g transform="translate(22 22) scale(.515625)">{mark(slug, uid=uid + "-m")}</g>'
    )
    body += brand_title(178, 88, display, size, weight=700, letter_spacing="-1.0")
    body += text(180, 128, subtitle, 21, "#B8CAD8", weight=500)
    # Bottom accent trace connecting mark to right hardware bus
    body += f'<rect x="180" y="148" width="548" height="3" rx="1.5" fill="url(#{uid}-accent)" opacity="0.85"/>'
    # Right-side precision hardware badge + pin bus (eliminates empty right void)
    badge_w = max(112, len(badge) * 10 + 30)
    bx = 728 - badge_w
    body += f'<rect x="{bx}" y="30" width="{badge_w}" height="32" rx="8" fill="#132435" stroke="#2D475F" stroke-width="1.5"/>'
    body += text(bx + badge_w / 2, 51, badge, 13, TEAL, weight=700, mono=True, text_anchor="middle", letter_spacing="1.2")
    # Subtle 3-pin bus motif on right edge
    body += (
        f'<g stroke-linecap="round" stroke-width="2.5">'
        f'<line x1="684" y1="92" x2="728" y2="92" stroke="{COPPER}" opacity="0.7"/>'
        f'<line x1="664" y1="108" x2="728" y2="108" stroke="{BORDER}" opacity="0.9"/>'
        f'<line x1="696" y1="124" x2="728" y2="124" stroke="{TEAL}" opacity="0.8"/>'
        f'</g>'
        f'<circle cx="684" cy="92" r="3.5" fill="{COPPER}"/>'
        f'<circle cx="664" cy="108" r="3" fill="{MUTED}"/>'
        f'<circle cx="696" cy="124" r="3.5" fill="{TEAL}"/>'
    )
    return svg(760, 176, body, f"{display} wordmark", subtitle)


def wire(path: str, color: str = BORDER, width: float = 2.2) -> str:
    return (f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def pulse(a: tuple[float, float], b: tuple[float, float], progress: float, color: str = TEAL) -> str:
    if not 0 <= progress <= 1:
        return ""
    x, y = a[0] + (b[0] - a[0]) * progress, a[1] + (b[1] - a[1]) * progress
    return (f'<circle cx="{x:.2f}" cy="{y:.2f}" r="10" fill="{color}" opacity=".18"/>'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.2" fill="{color}"/>')


def base_canvas(slug: str, display: str, right_pipeline: str,
                cards: list[tuple[int, int, str, str]], active_card: int) -> str:
    """Unified 1200x400 Signal Forge v2 canvas with dynamic active-stage card illumination."""
    uid = f"bn-{slug}"
    body = (
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{HEIGHT - 2}" rx="22" '
        f'fill="{INK}" stroke="#263B4F" stroke-width="2"/>'
    )
    # Subtle silicon alignment grid
    body += '<g opacity=".14">'
    for x in range(28, WIDTH - 16, 28):
        for y in range(104, 332, 28):
            body += f'<circle cx="{x}" cy="{y}" r=".85" fill="{MUTED}"/>'
    body += '</g>'
    # Header row
    body += f'<g transform="translate(28 17) scale(.203125)">{mark(slug, uid=uid + "-m")}</g>'
    body += brand_title(92, 54, display, 30, weight=700, letter_spacing="-0.6")
    body += text(1170, 50, right_pipeline, 14, "#B4C7D6", weight=700, mono=True,
                 text_anchor="end", letter_spacing="0.8")
    # Crisp segmented hardware divider (no GIF gradient banding)
    body += (f'<rect x="28" y="82" width="360" height="2" rx="1" fill="{COPPER}" opacity="0.8"/>'
             f'<rect x="394" y="82" width="412" height="2" rx="1" fill="{BORDER}"/>'
             f'<rect x="812" y="82" width="360" height="2" rx="1" fill="{TEAL}" opacity="0.8"/>')

    # 3 Stage cards with active-card illumination
    for idx, (x, w, number, label) in enumerate(cards):
        is_active = idx == active_card
        accent = COPPER if idx == 0 else TEAL
        stroke = accent if is_active else BORDER
        sw = "2.2" if is_active else "1.4"
        pill_bg = (COPPER_SOFT if idx == 0 else TEAL_SOFT) if is_active else "#162434"
        pill_fg = accent if is_active else MUTED
        title_fg = PAPER if is_active else "#C2D1DD"
        body += f'<rect x="{x}" y="98" width="{w}" height="226" rx="14" fill="{PANEL}" stroke="{stroke}" stroke-width="{sw}"/>'
        if is_active:
            body += f'<rect x="{x + 14}" y="98" width="{w - 28}" height="3" rx="1.5" fill="{accent}"/>'
        body += f'<rect x="{x + 16}" y="112" width="32" height="22" rx="6" fill="{pill_bg}" stroke="{stroke}" stroke-width="1"/>'
        body += text(x + 32, 128, number, 12, pill_fg, weight=700, mono=True, text_anchor="middle")
        body += text(x + 58, 128, label, 13.5, title_fg, weight=700, letter_spacing="0.6")
    return body


def cli_bar(cmd: str, tag: str) -> str:
    body = f'<rect x="28" y="340" width="1144" height="42" rx="10" fill="{PANEL_WELL}" stroke="{BORDER}" stroke-width="1.5"/>'
    body += text(46, 366, "$", 15, TEAL, weight=700, mono=True)
    body += text(68, 366, cmd, 13.5, PAPER, mono=True)
    tag_w = max(116, len(tag) * 8.5 + 24)
    tx = 1160 - tag_w
    body += f'<rect x="{tx}" y="348" width="{tag_w}" height="26" rx="6" fill="{COPPER_SOFT}" stroke="{COPPER}" stroke-width="1" opacity="0.9"/>'
    body += text(tx + tag_w / 2, 365.5, tag, 12, COPPER, weight=700, mono=True, text_anchor="middle")
    return body


def stage_arrows(t: float, pairs: list[tuple[int, int, str, float]]) -> str:
    body = ""
    for start, end, color, offset in pairs:
        body += wire(f"M{start} 211 H{end - 8}", color, 2.5)
        body += wire(f"M{end - 15} 204 L{end - 6} 211 L{end - 15} 218", color, 2.5)
        body += pulse((start, 211), (end - 10, 211), ((t - offset) * 3.5) % 1, color)
    return body


def status_pill(cx: float, y: float, label: str, color: str = TEAL) -> str:
    pw = max(180, len(label) * 8.4 + 28)
    bg = COPPER_SOFT if color == COPPER else TEAL_SOFT
    return (
        f'<rect x="{cx - pw / 2:.1f}" y="{y - 17}" width="{pw:.1f}" height="24" rx="6" '
        f'fill="{bg}" stroke="{color}" stroke-width="1.2"/>'
        + text(cx, y, label, 12, color, weight=700, mono=True, text_anchor="middle", letter_spacing="0.8")
    )


def banner_rusteda(frame: int) -> str:
    """RustEDA: RTL + disk workspace -> native RustSys/RustROAD/RustSTA loop -> verified receipt."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 304, "01", "RTL & WORKSPACE"),
        (398, 412, "02", "NATIVE ENGINE LOOP"),
        (876, 296, "03", "VERIFIED RECEIPT"),
    ]
    body = base_canvas("rusteda", "RustEDA", "RTL  →  SYNTH · P&R · STA  →  VERIFIED GDS", cards, active_card)

    code = [
        ("module ibex_core (", PAPER),
        ("  input  clk_i, rst_ni,", MUTED),
        ("  output instr_req_o", MUTED),
        (");", PAPER),
        ("  always_ff @(posedge clk_i)", COPPER),
        ("    if (!rst_ni) pc_q <= 0;", TEAL),
        ("endmodule", PAPER),
    ]
    revealed = min(len(code), 2 + int(t * 28))
    for i, (line, color) in enumerate(code):
        op = "1" if i < revealed else ".22"
        body += text(44, 162 + i * 20, line, 13, color, mono=True, opacity=op)
    body += text(44, 308, "workspace.lock · --base-rev rev0", 12, TEAL, weight=700, mono=True)
    body += stage_arrows(t, [(340, 390, COPPER, 0.05), (818, 868, TEAL, 0.30)])

    boxes = [
        (416, "RustSys", "RTL → cells", "ABC CEC", stage >= 0, stage == 0),
        (548, "RustROAD", "Place · CTS", "Route · GDS", stage >= 1, stage == 1),
        (680, "RustSTA", "RC Timing", "path1 · slack", stage >= 2, stage == 2),
    ]
    for bx, btitle, sub1, sub2, reached, current in boxes:
        stroke = COPPER if current and btitle == "RustSys" else (TEAL if reached else BORDER)
        sw = "2.4" if current else "1.8"
        body += f'<rect x="{bx}" y="152" width="112" height="78" rx="10" fill="{PANEL_WELL}" stroke="{stroke}" stroke-width="{sw}"/>'
        body += text(bx + 12, 177, btitle, 14.5, PAPER, weight=700)
        body += text(bx + 12, 198, sub1, 11.5, MUTED, mono=True)
        body += text(bx + 12, 217, sub2, 11.5, TEAL if reached else DIM, weight=700, mono=True)
    for sx, ex, col in [(528, 548, COPPER), (660, 680, TEAL)]:
        body += wire(f"M{sx} 191 H{ex}", col, 2.4)
        body += pulse((sx, 191), (ex, 191), (t * 4) % 1, col)

    # Closed-loop revision feedback trace
    fb_active = stage >= 2
    fb_color = COPPER if stage == 3 else (TEAL if fb_active else BORDER)
    body += wire("M736 230 V260 H472 V230", fb_color, 2.4)
    body += wire("M466 238 L472 230 L478 238", fb_color, 2.4)
    if fb_active:
        body += pulse((736, 260), (472, 260), ((t - 0.35) * 3) % 1, COPPER)
    for rx, rlabel in [(472, "rev1"), (604, "rev2"), (736, "rev3")]:
        body += f'<circle cx="{rx}" cy="260" r="5" fill="{INK}" stroke="{fb_color}" stroke-width="2.2"/>'
        body += text(rx, 279, rlabel, 11, COPPER if fb_active else MUTED, weight=700, mono=True, text_anchor="middle")

    body += status_pill(604, 309, [
        "SYNTHESIZE & PROVE CEC",
        "PLACE, CTS & ROUTE GDS",
        "EXTRACT RC & TIME PATHS",
        "COMMIT REVISION RECEIPT",
    ][stage], COPPER if stage == 0 else TEAL)

    body += text(894, 168, "rusteda.v1", 22, TEAL, weight=700, mono=True)
    body += text(1154, 167, "rev3", 14, COPPER, weight=700, mono=True, text_anchor="end")
    for i, (k, v, c) in enumerate([
        ("equivalence", "ABC CEC", TEAL),
        ("signoff", "DRC · LVS", TEAL),
        ("timing", "TT + SS RC", PAPER),
        ("outputs", "DEF · SPEF · GDS", COPPER),
    ]):
        y = 202 + i * 26
        body += text(894, y, k, 13, MUTED, mono=True)
        body += text(1154, y, v, 13.5, c, weight=700, mono=True, text_anchor="end")
    body += text(894, 308, "3 PDKs · 0 daemon · JSON", 12, TEAL, weight=700, mono=True)

    body += cli_bar("./rusteda flow exec --manifest compose/be/prefix-manifest.json --output run/out", "RTL → GDS")
    return svg(WIDTH, HEIGHT, body, "RustEDA: native RTL-to-GDS pipeline",
               "RTL flows through RustSys, RustROAD and RustSTA in a revisioned daemonless workspace to emit verified GDS and receipts.")


def banner_rustsys(frame: int) -> str:
    """RustSys: Verilog/SV -> AIG optimization & tech mapping -> mapped gates + ABC CEC."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 326, "01", "RTL / SYSTEMVERILOG"),
        (420, 294, "02", "AIG OPT & MAP"),
        (780, 392, "03", "MAPPED NETLIST + LEC"),
    ]
    body = base_canvas("rustsys", "RustSys", "VERILOG  →  AIG OPT & MAP  →  PROVEN GATES", cards, active_card)

    code = [
        ("module enabled_and (", PAPER),
        ("  input  clk, en,", MUTED),
        ("  input  [7:0] a, b,", MUTED),
        ("  output reg [7:0] q", MUTED),
        (");", PAPER),
        ("  always @(posedge clk)", COPPER),
        ("    if (en) q <= a & b;", TEAL),
        ("endmodule", PAPER),
    ]
    revealed = min(len(code), 1 + int(t * 32))
    for i, (line, color) in enumerate(code):
        op = "1" if i < revealed else ".20"
        body += text(44, 160 + i * 20, line, 13.5, color, mono=True, opacity=op)
    if t < 0.24:
        row = max(0, revealed - 1)
        body += f'<rect x="{46 + min(26, len(code[row][0])) * 8.1}" y="{148 + row * 20}" width="7" height="15" fill="{TEAL}"/>'
    body += stage_arrows(t, [(362, 412, COPPER, 0.05), (722, 772, TEAL, 0.33)])

    nodes = [
        (456, 178), (456, 240), (502, 152), (502, 209),
        (502, 268), (558, 178), (558, 240), (616, 209), (674, 209),
    ]
    edges = [(0, 3), (1, 3), (2, 5), (3, 5), (3, 6), (4, 6), (5, 7), (6, 7), (7, 8)]
    energized = t >= 0.22
    for i, (a, b) in enumerate(edges):
        xa, ya = nodes[a]
        xb, yb = nodes[b]
        pruned = (a in (2, 4)) and t >= 0.40
        c = BORDER_SOFT if pruned else (TEAL if (energized and (i + frame // 4) % 3 != 0) else BORDER)
        body += wire(f"M{xa} {ya} L{xb} {yb}", c, 2.2)
    red_op = "0.18" if t >= 0.40 else "0.85"
    for i, (x, y) in enumerate(nodes):
        fade = red_op if i in (2, 4) else "1"
        fill = TEAL if energized and (i + frame // 3) % 4 == 0 else PANEL_WELL
        stroke = TEAL if energized and i not in (2, 4) else BORDER
        body += f'<circle cx="{x}" cy="{y}" r="8" fill="{fill}" stroke="{stroke}" stroke-width="2.2" opacity="{fade}"/>'
    body += status_pill(567, 308, ["ELABORATE RTL", "REWRITE AIG CUTS", "MAP STD CELLS", "PROVE EQUIVALENCE"][stage],
                        COPPER if stage == 0 else TEAL)

    active = t >= 0.50
    body += text(800, 176, "a", 13, MUTED, mono=True)
    body += text(800, 218, "b", 13, MUTED, mono=True)
    body += text(796, 148, "en", 12, MUTED, mono=True)
    body += text(794, 268, "clk", 12, MUTED, mono=True)
    body += wire("M826 172 H858 M826 214 H858", COPPER if active else BORDER, 2.4)
    body += (f'<path d="M858 158 H880 C912 158 912 228 880 228 H858 Z" '
             f'fill="{PANEL_WELL}" stroke="{COPPER if active else MUTED}" stroke-width="2.2"/>')
    body += text(873, 199, "&", 19, PAPER, weight=700, mono=True)
    body += text(880, 248, "AND×8", 11.5, MUTED, mono=True, text_anchor="middle")
    body += wire("M904 193 H924", TEAL if active else BORDER, 2.4)
    body += f'<path d="M924 156 L952 169 V226 L924 239 Z" fill="{PANEL_WELL}" stroke="{TEAL if active else MUTED}" stroke-width="2.2"/>'
    body += text(933, 203, "M", 12, PAPER, weight=700, mono=True)
    body += wire("M826 144 H938 V162", TEAL if active else BORDER, 2.2)
    body += wire("M952 197 H998 M826 263 H982 V236 H998", TEAL if active else BORDER, 2.2)
    body += f'<rect x="998" y="170" width="76" height="82" rx="7" fill="{PANEL_WELL}" stroke="{TEAL if active else MUTED}" stroke-width="2.2"/>'
    body += text(1010, 202, "D", 14, PAPER, weight=700, mono=True)
    body += text(1052, 202, "Q", 14, PAPER, weight=700, mono=True)
    body += f'<path d="M998 228 L1010 236 L998 244" fill="none" stroke="{MUTED}" stroke-width="1.8"/>'
    body += text(1036, 158, "DFF×8", 11.5, MUTED, mono=True, text_anchor="middle")
    body += wire("M1074 197 H1142 M1118 197 V282 H914 V218 H924", TEAL if active else BORDER, 2.4)
    body += f'<circle cx="1118" cy="197" r="3.5" fill="{TEAL if active else BORDER}"/>'
    body += text(1148, 202, "q", 14, TEAL, weight=700, mono=True)
    if active:
        body += pulse((1076, 197), (1140, 197), ((t - 0.50) * 6) % 1, TEAL)
        body += pulse((828, 263), (980, 263), ((t - 0.50) * 6) % 1, COPPER)
    body += status_pill(976, 308, "ABC CEC · EQUIVALENT" if stage == 3 else "SKY130 · GF180 · IHP",
                        TEAL if stage == 3 else COPPER)

    body += cli_bar("rustsys synth --top core --lib sky130_hd.lib --out mapped.v rtl.v", "RTL → gates")
    return svg(WIDTH, HEIGHT, body, "RustSys: bounded synthesis and equivalence",
               "Verilog RTL is elaborated, optimized on an AIG, technology-mapped to standard cells and formally proved equivalent.")


def banner_ruststa(frame: int) -> str:
    """RustSTA: Design inputs -> timing DAG propagation -> path1 report."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 294, "01", "DESIGN INPUTS"),
        (388, 424, "02", "TIMING GRAPH & RC"),
        (878, 294, "03", "CRITICAL PATH"),
    ]
    body = base_canvas("ruststa", "RustSTA", "NETLIST + SPEF  →  PROPAGATE  →  SLACK", cards, active_card)

    for i, (name, label) in enumerate([
        ("mapped.v", "gate-level netlist"),
        ("cells.lib", "NLDM 1D–3D tables"),
        ("design.sdc", "clocks & constraints"),
        ("routed.spef", "extracted RC trees"),
    ]):
        y = 160 + 38 * i
        color = COPPER if (i == stage or stage >= 1) else MUTED
        body += wire(f"M46 {y - 10} H60 L67 {y - 3} V{y + 12} H46 Z", color, 2.0)
        body += text(82, y, name, 14.5, PAPER, weight=700, mono=True)
        body += text(82, y + 16, label, 11.5, MUTED)
    body += stage_arrows(t, [(330, 380, COPPER, 0.0), (820, 870, TEAL, 0.25)])

    critical = [
        ((464, 206), (524, 166)), ((524, 166), (602, 206)),
        ((602, 206), (682, 166)), ((682, 166), (736, 206)),
    ]
    other = [
        ((464, 206), (524, 250)), ((524, 250), (602, 206)),
        ((602, 206), (682, 250)), ((682, 250), (736, 206)),
    ]
    active = t >= 0.25
    for a, b in other:
        body += wire(f"M{a[0]} {a[1]} L{b[0]} {b[1]}", BORDER, 2.0)
    for i, (a, b) in enumerate(critical):
        color = TEAL if active else BORDER
        body += wire(f"M{a[0]} {a[1]} L{b[0]} {b[1]}", color, 3.2)
        if active:
            body += pulse(a, b, ((t - 0.25) * 4 - i * 0.23) % 1)
    for x, y in [(524, 166), (524, 250), (602, 206), (682, 166), (682, 250)]:
        color = TEAL if (active and y != 250) else BORDER
        body += f'<circle cx="{x}" cy="{y}" r="8.5" fill="{PANEL_WELL}" stroke="{color}" stroke-width="2.4"/>'
    for x, name, tag, col in [(416, "r0/Q", "launch", COPPER), (736, "r1/D", "capture", TEAL)]:
        body += f'<rect x="{x}" y="186" width="48" height="40" rx="6" fill="{PANEL_WELL}" stroke="{PAPER}" stroke-width="1.8"/>'
        body += wire(f"M{x} 214 L{x + 8} 219 L{x} 224", MUTED, 1.6)
        body += text(x + 24, 244, name, 12, PAPER, mono=True, text_anchor="middle")
        body += text(x + 24, 174, tag, 11.5, col, weight=700, text_anchor="middle")
    body += wire("M418 278 H456 V266 H496 V278 H536 V266 H576 V278 H616 V266 H656 V278 H696 V266 H782", COPPER, 2.0)
    body += status_pill(600, 309, [
        "BIND SPEF & LIBERTY",
        "PROPAGATE ARRIVALS",
        "RANK BOUNDED PATHS",
        "REPORT SLACK & DRV",
    ][stage], COPPER if stage == 0 else TEAL)

    body += text(896, 168, "path1", 24, TEAL, weight=700, mono=True)
    body += text(1154, 167, "setup", 14, COPPER, weight=700, mono=True, text_anchor="end")
    for i, (key, value, col) in enumerate([
        ("corners", "TT + SS", PAPER),
        ("wire model", "SPEF RC", PAPER),
        ("from", "r0/Q", COPPER),
        ("to", "r1/D", TEAL),
    ]):
        y = 202 + i * 26
        body += text(896, y, key, 13, MUTED, mono=True)
        body += text(1154, y, value, 13.5, col, weight=700, mono=True, text_anchor="end")
    body += text(896, 308, "Deterministic · 1/2/4 threads", 12, TEAL, weight=700, mono=True)

    body += cli_bar("ruststa run --workspace ./ws --base-rev rev1 --max-paths 5 --format json", "netlist → slack")
    return svg(WIDTH, HEIGHT, body, "RustSTA: static timing analysis",
               "Mapped netlist, Liberty, SDC and SPEF inputs feed a timing graph to propagate arrivals and report bounded paths.")


def banner_rustroad(frame: int) -> str:
    """RustROAD: Physical inputs -> Place/CTS/Route die grid -> DEF/SPEF/GDS."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 294, "01", "PHYSICAL INPUTS"),
        (388, 424, "02", "PLACE · CTS · ROUTE"),
        (878, 294, "03", "SIGNOFF OUTPUTS"),
    ]
    body = base_canvas("rustroad", "RustROAD", "NETLIST + LEF  →  PLACE · CTS · ROUTE  →  GDSII", cards, active_card)

    for i, (name, label) in enumerate([
        ("mapped.v", "synthesized stdcells"),
        ("tech + cells.lef", "layers, vias & abstracts"),
        ("floorplan.json", "die area, rows & macros"),
    ]):
        y = 166 + 46 * i
        color = COPPER if (i == stage or stage >= 1) else MUTED
        body += wire(f"M46 {y - 10} H60 L67 {y - 3} V{y + 12} H46 Z", color, 2.0)
        body += text(82, y, name, 14.5, PAPER, weight=700, mono=True)
        body += text(82, y + 18, label, 12, MUTED)
    body += text(46, 308, "Exact i64 DBU · 0 float drift", 12, TEAL, weight=700, mono=True)
    body += stage_arrows(t, [(330, 380, COPPER, 0.05), (820, 870, TEAL, 0.30)])

    # Die viewport
    body += f'<rect x="410" y="148" width="380" height="134" rx="8" fill="{PANEL_WELL}" stroke="{BORDER}" stroke-width="1.5"/>'
    for ry in (156, 200, 244):
        body += wire(f"M418 {ry} H782", "#1D3040", 1.2)
        body += wire(f"M418 {ry + 26} H782", "#1D3040", 1.2)
        body += f'<rect x="422" y="{ry + 2}" width="32" height="22" rx="4" fill="{INK}" stroke="{BORDER}"/>'
        body += text(438, ry + 17, "TAP", 10, MUTED, mono=True, text_anchor="middle")
        body += f'<rect x="746" y="{ry + 2}" width="32" height="22" rx="4" fill="{INK}" stroke="{BORDER}"/>'
        body += text(762, ry + 17, "TAP", 10, MUTED, mono=True, text_anchor="middle")

    cts_active = t >= 0.22
    cts_col = COPPER if cts_active else BORDER
    body += wire("M410 190 H576 V202", cts_col, 2.4)
    body += wire("M576 224 V236 H490 V180", cts_col, 2.4)
    body += wire("M576 236 H694 V246", cts_col, 2.4)
    if cts_active:
        body += pulse((410, 190), (576, 190), ((t - 0.22) * 4) % 1, COPPER)
        body += pulse((576, 236), (694, 236), ((t - 0.22) * 4 - 0.3) % 1, COPPER)

    route_active = t >= 0.45
    r_col = TEAL if route_active else BORDER
    body += wire("M516 169 H540 M586 169 H604 M650 169 H662 V257 H672 M720 257 H738 V190 H790", r_col, 2.8)

    cells = [
        (466, 158, 50, "ff0", PAPER),
        (540, 158, 46, "u1", PAPER),
        (604, 158, 46, "u2", PAPER),
        (548, 202, 58, "clkbuf", COPPER if stage >= 1 else MUTED),
        (618, 202, 42, "u3", PAPER),
        (502, 246, 48, "u4", PAPER),
        (672, 246, 48, "ff1", PAPER),
    ]
    for cx, cy, cw, cname, ccol in cells:
        body += f'<rect x="{cx}" y="{cy}" width="{cw}" height="22" rx="4" fill="{INK}" stroke="{ccol}" stroke-width="1.8"/>'
        body += text(cx + cw / 2, cy + 15.5, cname, 11, PAPER, weight=700, mono=True, text_anchor="middle")

    for vx, vy in [(662, 169), (662, 257), (738, 257), (738, 190)]:
        body += f'<rect x="{vx - 3.5}" y="{vy - 3.5}" width="7" height="7" rx="1.5" fill="{PAPER if route_active else BORDER}"/>'
    if route_active:
        body += pulse((662, 169), (662, 257), ((t - 0.45) * 4) % 1, TEAL)
        body += pulse((738, 257), (738, 190), ((t - 0.45) * 4 - 0.4) % 1, TEAL)

    body += status_pill(600, 309, [
        "PLACE ROWS & WELL TAPS",
        "SYNTHESIZE CLOCK TREE",
        "ROUTE METAL & VIAS",
        "EXTRACT RC & STREAM GDS",
    ][stage], COPPER if stage == 0 else TEAL)

    body += text(896, 168, "chip.gds", 23, TEAL, weight=700, mono=True)
    body += text(1154, 167, "rev5", 14, COPPER, weight=700, mono=True, text_anchor="end")
    for i, (k, v, col) in enumerate([
        ("layout", "routed.def", PAPER),
        ("parasitics", "routed.spef", PAPER),
        ("checks", "DRC · tap · ant", TEAL),
        ("streamout", "DBU-exact GDS", COPPER),
    ]):
        y = 202 + i * 26
        body += text(896, y, k, 13, MUTED, mono=True)
        body += text(1154, y, v, 13, col, weight=700, mono=True, text_anchor="end")
    body += text(896, 308, "Single binary · no PGO", 12, TEAL, weight=700, mono=True)

    body += cli_bar("rustroad route --workspace ./ws --base-rev rev3 --config sky130-routing.json", "netlist → GDS")
    return svg(WIDTH, HEIGHT, body, "RustROAD: physical implementation to GDSII",
               "Standard cells are placed into rows with well taps, clock-tree buffered, routed on metal tracks, extracted and streamed to GDSII.")


def banner_rustspice(frame: int) -> str:
    """RustSpice: SPICE deck -> SoA/JIT LU + oscilloscope waveform -> waveforms & adjoint gradients."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 294, "01", "SPICE NETLIST"),
        (388, 424, "02", "SOA · JIT LU · TRANSIENT"),
        (878, 294, "03", "WAVEFORMS & ∂V/∂θ"),
    ]
    body = base_canvas("rustspice", "RustSpice", "SPICE DECK  →  SOA & JIT LU  →  V(t) & ∂V/∂θ", cards, active_card)

    deck = [
        ("VDD vdd 0 DC 3.3", PAPER),
        ("VIN in1 0 PULSE(0 3.3)", COPPER),
        ("MP1 out in1 vdd PMOD", MUTED),
        ("MN1 out in1 0   NMOD", MUTED),
        ("CL1 out 0 20f", MUTED),
        (".tran 5p 2n", TEAL),
        (".adjoint V(out) d(W,R)", COPPER),
    ]
    revealed = min(len(deck), 2 + int(t * 28))
    for i, (line, color) in enumerate(deck):
        op = "1" if i < revealed else ".22"
        body += text(44, 162 + i * 20, line, 12.8, color, mono=True, opacity=op)
    body += text(44, 308, "Zero-copy lexer · f64 residual", 12, TEAL, weight=700, mono=True)
    body += stage_arrows(t, [(330, 380, COPPER, 0.05), (820, 870, TEAL, 0.30)])

    # Compute strip
    c1_stroke = COPPER if stage == 1 else (TEAL if stage >= 2 else BORDER)
    body += f'<rect x="410" y="148" width="380" height="44" rx="8" fill="{PANEL_WELL}" stroke="{c1_stroke}" stroke-width="1.8"/>'
    for px, pw, pill in [(420, 84, "SoA SIMD"), (512, 114, "Cranelift JIT"), (634, 68, "Metal"), (710, 70, "CUDA")]:
        body += f'<rect x="{px}" y="158" width="{pw}" height="24" rx="5" fill="{INK_HI}" stroke="{BORDER}"/>'
        body += text(px + pw / 2, 174.5, pill, 11.5, TEAL if stage >= 1 else MUTED, weight=700, mono=True, text_anchor="middle")

    # Oscilloscope viewport
    body += f'<rect x="410" y="202" width="380" height="82" rx="8" fill="{PANEL_WELL}" stroke="{TEAL if stage >= 2 else BORDER}" stroke-width="1.8"/>'
    for gy in (224, 243, 262):
        body += wire(f"M420 {gy} H780", "#1B2E3E", 1.1)
    body += wire("M426 264 H486 L498 220 H626 L638 264 H774", COPPER, 2.3)
    wave_active = t >= 0.22
    w_col = TEAL if wave_active else BORDER
    body += wire("M426 220 H490 C508 220 518 262 550 262 H630 C648 262 658 220 692 220 H774", w_col, 2.8)
    if wave_active:
        prog = ((t - 0.22) * 2.2) % 1
        wx = 426 + 348 * prog
        # Smooth analytical approximation of the inverter waveform for smooth probe tracking
        if prog < 0.19:
            wy = 220.0
        elif prog < 0.34:
            u = (prog - 0.19) / 0.15
            wy = 220.0 + 42.0 * (0.5 - 0.5 * math.cos(math.pi * u))
        elif prog < 0.60:
            wy = 262.0
        elif prog < 0.76:
            u = (prog - 0.60) / 0.16
            wy = 262.0 - 42.0 * (0.5 - 0.5 * math.cos(math.pi * u))
        else:
            wy = 220.0
        body += f'<circle cx="{wx:.1f}" cy="{wy:.1f}" r="9" fill="{TEAL}" opacity=".20"/>'
        body += f'<circle cx="{wx:.1f}" cy="{wy:.1f}" r="4.2" fill="{TEAL}"/>'
    body += text(428, 215, "V(in1)", 11, COPPER, weight=700, mono=True)
    body += text(772, 215, "V(out)", 11, TEAL, weight=700, mono=True, text_anchor="end")

    body += status_pill(600, 309, [
        "LOWER DEVICE AD IR",
        "ASSEMBLE SOA STAMPS",
        "FACTOR JIT SPARSE LU",
        "SOLVE V(t) & ADJOINT",
    ][stage], COPPER if stage == 0 else TEAL)

    body += text(896, 168, "V(t), ∂V/∂θ", 21, TEAL, weight=700, mono=True)
    body += text(1154, 167, "f64", 14, COPPER, weight=700, mono=True, text_anchor="end")
    for i, (k, v, col) in enumerate([
        ("analyses", ".op · .dc · .tran", PAPER),
        ("solver", "JIT Sparse LU", TEAL),
        ("adjoint", "1x L^T U^T", COPPER),
        ("bridge", "DLPack tensor", PAPER),
    ]):
        y = 202 + i * 26
        body += text(896, y, k, 13, MUTED, mono=True)
        body += text(1154, y, v, 13, col, weight=700, mono=True, text_anchor="end")
    body += text(896, 308, "CPU · CUDA · Metal", 12, TEAL, weight=700, mono=True)

    body += cli_bar("rustspice run ring_osc.sp --backend cpu && rustspice adjoint net.sp --target out", "netlist → ∂V/∂θ")
    return svg(WIDTH, HEIGHT, body, "RustSpice: differentiable SPICE simulation",
               "SPICE decks compile into SoA SIMD/GPU device stamps and sparse LU solvers to emit transient waveforms and adjoint gradients.")


def banner_common(frame: int) -> str:
    """RustEDA Common: Standard formats -> Arena IR & Mmap store -> Compute & CLI contract."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 294, "01", "STANDARD FORMATS"),
        (388, 424, "02", "ARENA IR & MMAP STORE"),
        (878, 294, "03", "COMPUTE & CLI"),
    ]
    body = base_canvas("rusteda-common", "RustEDA Common", "FORMATS  →  ARENAS & MMAP  →  SOA COMPUTE", cards, active_card)

    for i, (name, label) in enumerate([
        (".v / .sv / .aig", "RTL, netlists & logic"),
        (".lib (NLDM 1–3D)", "SKY130 · GF180 · IHP"),
        (".sdc + .spef.gz", "clocks & RC parasitics"),
    ]):
        y = 166 + 46 * i
        color = COPPER if (i == stage or stage >= 1) else MUTED
        body += wire(f"M46 {y - 10} H60 L67 {y - 3} V{y + 12} H46 Z", color, 2.0)
        body += text(82, y, name, 14.5, PAPER, weight=700, mono=True)
        body += text(82, y + 18, label, 12, MUTED)
    body += text(46, 308, "Explicit diagnostics · 0 silent skips", 11.5, TEAL, weight=700, mono=True)
    body += stage_arrows(t, [(330, 380, COPPER, 0.05), (820, 870, TEAL, 0.30)])

    c1_stroke = COPPER if stage == 1 else (TEAL if stage >= 2 else BORDER)
    body += f'<rect x="410" y="148" width="380" height="58" rx="9" fill="{PANEL_WELL}" stroke="{c1_stroke}" stroke-width="2"/>'
    body += text(424, 170, "rusteda-core · Generational Arenas", 13, PAPER, weight=700, mono=True)
    for px, pw, pill in [(424, 62, "InstId"), (492, 56, "NetId"), (554, 56, "PinId"), (616, 62, "CellId"), (684, 94, "Interner")]:
        body += f'<rect x="{px}" y="179" width="{pw}" height="21" rx="5" fill="{INK_HI}" stroke="{BORDER}"/>'
        body += text(px + pw / 2, 194, pill, 10.5, TEAL if stage >= 1 else MUTED, weight=700, mono=True, text_anchor="middle")

    for ax in (520, 680):
        body += wire(f"M{ax} 206 V228", TEAL if stage >= 2 else BORDER, 2.2)
        if stage >= 1:
            body += pulse((ax, 206), (ax, 228), (t * 4) % 1, COPPER if stage == 1 else TEAL)

    c2_stroke = TEAL if stage >= 2 else BORDER
    body += f'<rect x="410" y="228" width="380" height="56" rx="9" fill="{PANEL_WELL}" stroke="{c2_stroke}" stroke-width="2"/>'
    body += text(424, 250, "rusteda-workspace · Mmap Revisions", 13, PAPER, weight=700, mono=True)
    body += text(424, 272, "workspace.lock · --base-rev · *.rbin", 12, TEAL if stage >= 2 else MUTED, mono=True)

    body += status_pill(600, 309, [
        "PARSE EDA FORMATS",
        "INTERN TYPED ARENAS",
        "MMAP SIDE-TABLES",
        "LOWER SOA / CSR VIEWS",
    ][stage], COPPER if stage == 0 else TEAL)

    body += text(896, 168, "SoA / CSR", 22, TEAL, weight=700, mono=True)
    body += text(1154, 167, "rusteda.v1", 13, COPPER, weight=700, mono=True, text_anchor="end")
    for i, (k, v, col) in enumerate([
        ("cpu_ref", "Rayon det. f64", PAPER),
        ("gpu", "CUDA / Metal", TEAL),
        ("envelope", "JSON + codes", PAPER),
        ("handles", "rev1 · path1", COPPER),
    ]):
        y = 202 + i * 26
        body += text(896, y, k, 13, MUTED, mono=True)
        body += text(1154, y, v, 13, col, weight=700, mono=True, text_anchor="end")
    body += text(896, 308, "Pinned foundation · 5 crates", 12, TEAL, weight=700, mono=True)

    body += cli_bar("rusteda -w ./ws --format json import --base-rev rev0 --lib sky130.lib --rtl top.v", "core · mmap")
    return svg(WIDTH, HEIGHT, body, "RustEDA Common: shared EDA foundation",
               "Standard EDA formats parse into generational arena IRs, memory-mapped workspace revisions and contiguous SoA/CSR compute views.")


def banner_studio(frame: int) -> str:
    """RustEDA Studio: Revisioned workspace -> interactive cross-probe -> shared CLI/GUI selection."""
    t = frame / FRAMES
    stage = min(3, int(t * 4))
    active_card = 0 if stage == 0 else (1 if stage in (1, 2) else 2)
    cards = [
        (28, 294, "01", "WORKSPACE HIERARCHY"),
        (388, 424, "02", "PATH & CONE CROSS-PROBE"),
        (878, 294, "03", "SHARED SELECTION"),
    ]
    body = base_canvas("rusteda-studio", "RustEDA Studio", "WORKSPACE  →  CROSS-PROBE  →  SHARED SELECTION", cards, active_card)

    tree = [
        ("ibex_core", PAPER, 0),
        ("├─ u_if_stage", MUTED, 1),
        ("├─ u_id_stage", COPPER if stage >= 1 else MUTED, 1),
        ("│  └─ u_alu", TEAL if stage >= 1 else MUTED, 2),
        ("└─ u_load_store", MUTED, 1),
    ]
    for i, (label, col, _indent) in enumerate(tree):
        body += text(44, 166 + i * 24, label, 13.5, col, weight=700 if col in (PAPER, TEAL, COPPER) else 400, mono=True)
    body += text(44, 308, "mmap read-only · rev3 live", 12, TEAL, weight=700, mono=True)
    body += stage_arrows(t, [(330, 380, COPPER, 0.05), (820, 870, TEAL, 0.30)])

    # Split workbench pane
    body += f'<rect x="410" y="148" width="380" height="136" rx="9" fill="{PANEL_WELL}" stroke="{TEAL if stage >= 1 else BORDER}" stroke-width="1.8"/>'
    body += wire("M588 148 V284", BORDER, 1.5)
    # Left pane: highlighted critical path cone
    body += text(424, 168, "CONE · path1", 11, MUTED, weight=700, mono=True)
    body += wire("M436 244 L478 202 L524 224 L564 190", TEAL if stage >= 1 else BORDER, 3.0)
    for nx, ny, col in [(436, 244, COPPER), (478, 202, PAPER), (524, 224, PAPER), (564, 190, TEAL)]:
        body += f'<circle cx="{nx}" cy="{ny}" r="7" fill="{INK}" stroke="{col}" stroke-width="2.4"/>'
    if stage >= 1:
        segs = [((436, 244), (478, 202)), ((478, 202), (524, 224)), ((524, 224), (564, 190))]
        p3 = (((t - 0.25) * 3.0) % 1.0) * 3.0
        si = min(2, int(p3))
        body += pulse(segs[si][0], segs[si][1], p3 - si, TEAL)
    # Right pane: cross-probed RTL source line
    body += text(602, 168, "ibex_alu.sv:142", 11, COPPER, weight=700, mono=True)
    if stage >= 2:
        body += f'<rect x="598" y="198" width="182" height="22" rx="4" fill="{TEAL_SOFT}" stroke="{TEAL}" stroke-width="1"/>'
    for i, (ln, col) in enumerate([
        ("141  adder_a = op_a;", MUTED),
        ("142  res_o = a + b;", TEAL if stage >= 2 else PAPER),
        ("143  cmp_o = res[31];", MUTED),
    ]):
        body += text(604, 190 + i * 24, ln, 12, col, weight=700 if i == 1 and stage >= 2 else 400, mono=True)

    body += status_pill(600, 309, [
        "LOAD MMAP REVISION",
        "TRACE CRITICAL CONE",
        "CROSS-PROBE RTL LINE",
        "SYNC SELECTION HANDLE",
    ][stage], COPPER if stage == 0 else TEAL)

    body += text(896, 168, "sel1", 24, TEAL, weight=700, mono=True)
    body += text(1154, 167, "@ rev3", 14, COPPER, weight=700, mono=True, text_anchor="end")
    for i, (k, v, col) in enumerate([
        ("focus", "path1 (u_alu)", PAPER),
        ("rtl", "ibex_alu:142", TEAL),
        ("mode", "GUI + --print", PAPER),
        ("contract", "CLI ↔ Studio", COPPER),
    ]):
        y = 202 + i * 26
        body += text(896, y, k, 13, MUTED, mono=True)
        body += text(1154, y, v, 13, col, weight=700, mono=True, text_anchor="end")
    body += text(896, 308, "Zero-daemon · shared rev", 12, TEAL, weight=700, mono=True)

    body += cli_bar("./rusteda studio --workspace ./ws --select path1", "inspect → act")
    return svg(WIDTH, HEIGHT, body, "RustEDA Studio: interactive layout and timing workbench",
               "Engineers and agents cross-probe hierarchy, timing cones and RTL through shared revision-bound selections.")


BANNERS = {
    "rusteda": banner_rusteda,
    "rustsys": banner_rustsys,
    "ruststa": banner_ruststa,
    "rustroad": banner_rustroad,
    "rustspice": banner_rustspice,
    "rusteda-common": banner_common,
    "rusteda-studio": banner_studio,
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


def build_composite_palette(banner_fn) -> Image.Image:
    """Build a crisp 128-color palette spanning all active-card illumination phases."""
    samples = [raster(banner_fn(f)) for f in (8, 36, 72)]
    atlas = Image.new("RGB", (WIDTH, HEIGHT * len(samples)), INK)
    for i, sample in enumerate(samples):
        atlas.paste(sample, (0, i * HEIGHT))
    return atlas.quantize(colors=128, method=Image.Quantize.MEDIANCUT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true", help="Write SVG marks, wordmarks and static banners only")
    parser.add_argument("--product", action="append", help="Render only selected product names or slugs")
    parser.add_argument("--dest", type=Path, help="Override output assets directory when rendering a single product")
    args = parser.parse_args()
    if WIDTH * HEIGHT > MAX_PIXELS or not 1 <= FRAMES <= MAX_FRAMES:
        raise SystemExit("ASSET_BUDGET_EXCEEDED: configured dimensions or frames exceed hard caps")

    qa = ROOT / ".work" / "brand"
    qa.mkdir(parents=True, exist_ok=True)
    suite_logo_svg = svg(256, 256, mark("rusteda", uid="suite"), "RustEDA Signal Forge mark",
                         "A chamfered IC die frame surrounding a closed-loop copper and mint silicon forge core.")
    suite_wordmark_svg = wordmark("rusteda", "RustEDA", "AI-native RTL-to-GDS in Rust", "RTL → GDS")

    selected = [
        p for p in PRODUCTS
        if not args.product or p[0] in args.product or p[1] in args.product
    ]
    manifest = []
    for name, slug, display, subtitle, badge, default_dest in selected:
        dest = args.dest if (args.dest and len(selected) == 1) else default_dest
        dest.mkdir(parents=True, exist_ok=True)
        logo_svg = svg(256, 256, mark(slug, uid=f"logo-{slug}"), f"{display} mark", subtitle)
        wmark_svg = wordmark(slug, display, subtitle, badge)
        banner_fn = BANNERS[slug]
        poster_svg = banner_fn(68)

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

        palette = build_composite_palette(banner_fn)
        frames = []
        samples = []
        for frame in range(FRAMES):
            img = raster(banner_fn(frame))
            frames.append(img.quantize(palette=palette, dither=Image.Dither.NONE))
            if frame in (8, 28, 50, 72):
                samples.append(img.resize((600, 200), Image.Resampling.LANCZOS))

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

# One RustEDA family

The chip frame belongs to the suite. Each module has a different symbol inside it,
and all five marks use the same geometry, palette and spacing.

| Token | Value | Use |
| --- | --- | --- |
| Ink | `#101923` | Background |
| Paper | `#F4F1E9` | Primary text and chip symbols |
| Copper | `#EF8B52` | Inputs, actions and emphasis |
| Mint | `#57D6BE` | Outputs and the feedback loop |
| Slate | `#A4B6BF` | Secondary text |

Banners share a 1200 × 400 Signal Forge canvas and an 8.4-second (84-frame, 10 fps)
three-card pipeline animation that illustrates inputs, core transformation, and
resulting engineering artifacts alongside a runnable CLI command.
Keep the SVG fallback alongside the GIF, respect reduced-motion preferences,
omit fixed HTML `height` attributes on responsive README `<picture>` images so
GitHub preserves the 3:1 aspect ratio, and provide descriptive alternative text.
Animation is illustrative, never a way to communicate a benchmark result.

The canonical source is [`scripts/generate_brand.py`](../scripts/generate_brand.py).
Run it from a recursive checkout with Python and Pillow. It renders products
serially and keeps fewer than 100 MiB of image buffers in memory. Generation is
offline; the documentation site uses locally served assets and system fonts.

The marks and banners are original vector artwork distributed under Apache-2.0,
with the rest of this repository. Product names and logos do not imply an
affiliation with any reference tool, foundry or benchmark design.

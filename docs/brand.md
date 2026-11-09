# One RustEDA family

The chip frame belongs to the suite. Each module has a different symbol inside it,
and all six marks use the same geometry, palette and spacing.

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
Use `width="1200"` for a banner and `width="360"` for the 760 × 176 wordmark
card, with no HTML height on either. At an 830-pixel README content width the
banner should render at about 830 × 277 pixels; at 360 pixels wide it should be
360 × 120 pixels. The mark itself remains square (256 × 256).
The publication check verifies GIF/poster dimensions, responsive README
attributes and local links. Validate the 84 frames and 100 ms frame duration
when regenerating the animations.
Animation is illustrative, never a way to communicate a benchmark result.

The canonical source is [`scripts/generate_brand.py`](../scripts/generate_brand.py).
Run it from a recursive checkout with Python and Pillow. It renders products
serially and keeps fewer than 100 MiB of image buffers in memory. Generation is
offline; the documentation site uses locally served assets and system fonts.

The marks and banners are original vector artwork distributed under Apache-2.0,
with the rest of this repository. Product names and logos do not imply an
affiliation with any reference tool, foundry or benchmark design.

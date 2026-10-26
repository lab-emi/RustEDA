// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The colors and sizes of the workbench (a dark theme).

use gpui::{Hsla, Rgba, rgb, rgba};

/// Window background.
pub fn bg() -> Rgba {
    rgb(0x15171c)
}
/// Panel background.
pub fn panel() -> Rgba {
    rgb(0x1c1f26)
}
/// Header background of a panel.
pub fn header() -> Rgba {
    rgb(0x23272f)
}
/// Panel borders.
pub fn border() -> Rgba {
    rgb(0x30353f)
}
/// Main text.
pub fn text() -> Rgba {
    rgb(0xd8dee9)
}
/// Secondary text.
pub fn muted() -> Rgba {
    rgb(0x8a93a3)
}
/// Accent (focus, links).
pub fn accent() -> Rgba {
    rgb(0x5aa9ff)
}
/// The selected path in the schematic.
pub fn path() -> Rgba {
    rgb(0xffa94d)
}
/// Negative slack, errors.
pub fn bad() -> Rgba {
    rgb(0xff6b6b)
}
/// Positive slack.
pub fn good() -> Rgba {
    rgb(0x69db7c)
}
/// Warnings.
pub fn warn() -> Rgba {
    rgb(0xffd43b)
}
/// Background of selected rows.
pub fn selected_bg() -> Rgba {
    rgba(0x5aa9ff33)
}
/// Background of the focused row.
pub fn focus_bg() -> Rgba {
    rgba(0x5aa9ff1a)
}
/// Background of highlighted source lines.
pub fn hit_bg() -> Rgba {
    rgba(0xffa94d2e)
}
/// Node boxes of the schematic.
pub fn node() -> Rgba {
    rgb(0x262b35)
}
/// Port boxes of the schematic.
pub fn port() -> Rgba {
    rgb(0x1f3326)
}
/// Edges of the schematic.
pub fn edge() -> Rgba {
    rgb(0x56606f)
}

/// An `Rgba` as `Hsla`.
pub fn hsla(c: Rgba) -> Hsla {
    c.into()
}

/// Height of a list row in pixels.
pub const ROW_H: f32 = 22.0;

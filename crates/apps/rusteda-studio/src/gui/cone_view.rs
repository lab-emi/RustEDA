// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The schematic of the logic cone on a GPUI canvas (STUDIO-REQ-0095, STUDIO-REQ-0164).
//!
//! The scene is in integer layout units ([`crate::model::scene`]); the canvas fits it to the
//! panel, then applies the user's zoom (mouse wheel, around the cursor) and pan (drag). A click
//! on a node cross-probes it, a double click makes it the root of the cone, and a click with
//! control or shift adds it to the shared selection.

use std::cell::Cell;
use std::rc::Rc;

use gpui::{
    AnyElement, App, BorderStyle, Bounds, ClickEvent, ContentMask, Context, Hsla,
    InteractiveElement, IntoElement, MouseButton, MouseDownEvent, MouseMoveEvent, MouseUpEvent,
    ParentElement, PathBuilder, Pixels, Point, ScrollWheelEvent, SharedString,
    StatefulInteractiveElement, Styled, TextRun, Window, canvas, div, fill, point, px, quad, size,
};

use super::panels::panel;
use super::theme;
use super::workbench::Workbench;
use crate::model::cone::NodeKey;
use crate::model::scene::ConeScene;

/// Zoom and pan of the canvas, and the panel bounds of the last paint.
#[derive(Debug, Clone)]
pub struct ConeViewState {
    zoom: f32,
    pan: Point<Pixels>,
    drag: Option<(Point<Pixels>, Point<Pixels>, bool)>,
    bounds: Rc<Cell<Option<Bounds<Pixels>>>>,
}

impl Default for ConeViewState {
    fn default() -> ConeViewState {
        ConeViewState {
            zoom: 1.0,
            pan: point(px(0.0), px(0.0)),
            drag: None,
            bounds: Rc::new(Cell::new(None)),
        }
    }
}

impl ConeViewState {
    /// Fits the next drawing to the panel again.
    pub fn fit(&mut self) {
        self.zoom = 1.0;
        self.pan = point(px(0.0), px(0.0));
        self.drag = None;
    }
}

/// The scale and the screen position of layout point (0, 0).
fn transform(
    b: Bounds<Pixels>,
    scene: &ConeScene,
    zoom: f32,
    pan: Point<Pixels>,
) -> (f32, Point<Pixels>) {
    let bw = f32::from(b.size.width).max(1.0);
    let bh = f32::from(b.size.height).max(1.0);
    let w = scene.width.max(1) as f32;
    let h = scene.height.max(1) as f32;
    let fit = ((bw - 32.0) / w).min((bh - 32.0) / h).clamp(0.05, 1.25);
    let s = fit * zoom;
    let ox = f32::from(b.origin.x) + (bw - w * s) / 2.0 + f32::from(pan.x);
    let oy = f32::from(b.origin.y) + (bh - h * s) / 2.0 + f32::from(pan.y);
    (s, point(px(ox), px(oy)))
}

/// The node under window position `p`.
fn hit(
    scene: &ConeScene,
    b: Bounds<Pixels>,
    zoom: f32,
    pan: Point<Pixels>,
    p: Point<Pixels>,
) -> Option<NodeKey> {
    let (s, o) = transform(b, scene, zoom, pan);
    let x = (f32::from(p.x) - f32::from(o.x)) / s;
    let y = (f32::from(p.y) - f32::from(o.y)) / s;
    scene
        .nodes
        .iter()
        .find(|n| {
            x >= n.x as f32 && x < (n.x + n.w) as f32 && y >= n.y as f32 && y < (n.y + n.h) as f32
        })
        .map(|n| n.key)
}

/// Shapes and paints one line of text, clipped to `max_w` by dropping characters.
fn text(
    window: &mut Window,
    cx: &mut App,
    s: &str,
    at: Point<Pixels>,
    size: f32,
    color: Hsla,
    max_w: f32,
) {
    let font = window.text_style().font();
    let mut t: String = s.to_string();
    // A cheap clip: about 0.6 em per character.
    let max_chars = ((max_w / (size * 0.6)).floor() as usize).max(1);
    if t.chars().count() > max_chars {
        t = t
            .chars()
            .take(max_chars.saturating_sub(1))
            .collect::<String>()
            + "~";
    }
    let run = TextRun {
        len: t.len(),
        font,
        color,
        background_color: None,
        underline: None,
        strikethrough: None,
    };
    let shaped = window
        .text_system()
        .shape_line(SharedString::from(t), px(size), &[run], None);
    let _ = shaped.paint(at, px(size * 1.25), window, cx);
}

/// Paints the scene in `b`.
fn paint(
    scene: &ConeScene,
    b: Bounds<Pixels>,
    zoom: f32,
    pan: Point<Pixels>,
    window: &mut Window,
    cx: &mut App,
) {
    window.paint_quad(fill(b, theme::bg()));
    let (s, o) = transform(b, scene, zoom, pan);
    let at = |x: i64, y: i64| point(o.x + px(x as f32 * s), o.y + px(y as f32 * s));
    window.with_content_mask(Some(ContentMask { bounds: b }), |window| {
        // Edges below the boxes, the path's on top of the others.
        let mut order: Vec<usize> = (0..scene.edges.len()).collect();
        order.sort_by_key(|&k| scene.edges[k].on_path);
        for k in order {
            let e = &scene.edges[k];
            let (width, color) = if e.on_path {
                (2.6, theme::path())
            } else if e.feedback {
                (1.0, theme::muted())
            } else {
                (1.2, theme::edge())
            };
            let mut pb = PathBuilder::stroke(px((width * s.sqrt()).max(0.8)));
            if e.feedback {
                pb = pb.dash_array(&[px(4.0), px(3.0)]);
            }
            let mut first = true;
            for &(x, y) in &e.points {
                if first {
                    pb.move_to(at(x, y));
                    first = false;
                } else {
                    pb.line_to(at(x, y));
                }
            }
            if let Ok(path) = pb.build() {
                window.paint_path(path, color);
            }
            // An arrow head at the load.
            if let Some(&(x, y)) = e.points.last() {
                let tip = at(x, y);
                let a = px(6.0 * s.sqrt());
                let mut head = PathBuilder::fill();
                head.move_to(tip);
                head.line_to(point(tip.x - a, tip.y - a * 0.6));
                head.line_to(point(tip.x - a, tip.y + a * 0.6));
                head.close();
                if let Ok(path) = head.build() {
                    window.paint_path(path, color);
                }
            }
        }
        let font = (12.0 * s).clamp(10.0, 14.0);
        for n in &scene.nodes {
            let origin = at(n.x, n.y);
            let rect = Bounds::new(origin, size(px(n.w as f32 * s), px(n.h as f32 * s)));
            let bg = if n.port { theme::port() } else { theme::node() };
            let (border, width) = if n.root {
                (theme::accent(), 2.5)
            } else if n.on_path {
                (theme::path(), 2.0)
            } else if n.selected {
                (theme::accent(), 1.5)
            } else {
                (theme::border(), 1.0)
            };
            window.paint_quad(quad(
                rect,
                px(4.0 * s),
                bg,
                px(width),
                border,
                BorderStyle::Solid,
            ));
            if n.selected {
                window.paint_quad(fill(rect, theme::selected_bg()));
            }
            let box_h = n.h as f32 * s;
            if box_h >= font * 1.1 {
                let pad = (6.0 * s).max(2.0);
                let w = n.w as f32 * s - 2.0 * pad;
                let two_lines = box_h >= font * 2.5;
                let top = if two_lines {
                    (box_h - font * 2.35) / 2.0
                } else {
                    (box_h - font * 1.25) / 2.0
                };
                text(
                    window,
                    cx,
                    &n.title,
                    point(origin.x + px(pad), origin.y + px(top)),
                    font,
                    theme::hsla(theme::text()),
                    w,
                );
                if !two_lines {
                    continue;
                }
                let sub = if n.hidden_loads > 0 {
                    format!("{}  +{} loads", n.subtitle, n.hidden_loads)
                } else {
                    n.subtitle.clone()
                };
                let sub = sub
                    .rsplit_once("__")
                    .map_or(sub.as_str(), |(_, b)| b)
                    .to_string();
                text(
                    window,
                    cx,
                    &sub,
                    point(origin.x + px(pad), origin.y + px(top + font * 1.25)),
                    font * 0.85,
                    theme::hsla(theme::muted()),
                    w,
                );
            }
        }
    });
}

/// The cone panel: header with the depth controls, and the canvas (STUDIO-REQ-0164).
pub(super) fn cone_panel(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let Some(scene) = wb.studio.scene().cloned() else {
        return panel(
            "Logic cone",
            div()
                .p_2()
                .text_color(theme::muted())
                .child("select a path or an instance to draw its cone")
                .into_any_element(),
        );
    };
    let (fanin, fanout) = wb.studio.cone_depths();
    let title = format!(
        "Logic cone of {}  {} nodes  {} edges  {} layers  {} crossings{}",
        scene.root_label,
        scene.nodes.len(),
        scene.edges.len(),
        scene.layers,
        scene.crossings,
        if scene.truncation.any() {
            format!("  truncated: {}", scene.truncation.reasons().join(", "))
        } else {
            String::new()
        }
    );
    let step = |id: &'static str, label: &'static str, dfi: i32, dfo: i32| {
        div()
            .id(id)
            .debug_selector(move || format!("cone-{id}"))
            .px_2()
            .rounded_sm()
            .border_1()
            .border_color(theme::border())
            .cursor_pointer()
            .child(label)
            .on_click(
                cx.listener(move |this: &mut Workbench, _: &ClickEvent, _, cx| {
                    let (i, o) = this.studio.cone_depths();
                    let i = (i as i32 + dfi).max(0) as u32;
                    let o = (o as i32 + dfo).max(0) as u32;
                    this.studio.set_cone_depths(i, o);
                    this.cone.fit();
                    cx.notify();
                }),
            )
    };
    let controls = div()
        .flex()
        .flex_row()
        .items_center()
        .gap_2()
        .px_2()
        .h(px(26.0))
        .bg(theme::header())
        .border_b_1()
        .border_color(theme::border())
        .child(div().text_color(theme::muted()).child(format!("fan-in {fanin}")))
        .child(step("fi-", "-", -1, 0))
        .child(step("fi+", "+", 1, 0))
        .child(div().text_color(theme::muted()).child(format!("fan-out {fanout}")))
        .child(step("fo-", "-", 0, -1))
        .child(step("fo+", "+", 0, 1))
        .child(
            div()
                .id("fit")
                .px_2()
                .rounded_sm()
                .border_1()
                .border_color(theme::border())
                .cursor_pointer()
                .child("fit")
                .on_click(cx.listener(|this, _: &ClickEvent, _, cx| {
                    this.cone.fit();
                    cx.notify();
                })),
        )
        .child(
            div()
                .flex_1()
                .truncate()
                .text_color(theme::muted())
                .child("wheel zooms, drag pans; click probes RTL, double click re-roots, ctrl adds to the selection"),
        );
    let (zoom, pan) = (wb.cone.zoom, wb.cone.pan);
    let cell = wb.cone.bounds.clone();
    let paint_scene = scene.clone();
    let drawing = canvas(
        move |b, _, _| {
            cell.set(Some(b));
        },
        move |b, (), window, cx| paint(&paint_scene, b, zoom, pan, window, cx),
    )
    .size_full();
    let s_down = scene.clone();
    let s_up = scene.clone();
    let s_wheel = scene;
    let body = div()
        .id("cone-canvas")
        .debug_selector(|| "cone-canvas".into())
        .size_full()
        .on_mouse_down(
            MouseButton::Left,
            cx.listener(move |this, ev: &MouseDownEvent, _, _| {
                let _ = &s_down;
                this.cone.drag = Some((ev.position, this.cone.pan, false));
            }),
        )
        .on_mouse_move(cx.listener(|this, ev: &MouseMoveEvent, _, cx| {
            if let (Some((start, pan0, _)), Some(MouseButton::Left)) =
                (this.cone.drag, ev.pressed_button)
            {
                let d = ev.position - start;
                if f32::from(d.x).abs() + f32::from(d.y).abs() > 3.0 {
                    this.cone.pan = pan0 + d;
                    this.cone.drag = Some((start, pan0, true));
                    cx.notify();
                }
            }
        }))
        .on_mouse_up(
            MouseButton::Left,
            cx.listener(move |this, ev: &MouseUpEvent, _, cx| {
                let moved = this.cone.drag.is_some_and(|d| d.2);
                this.cone.drag = None;
                if moved {
                    return;
                }
                let Some(b) = this.cone.bounds.get() else {
                    return;
                };
                let Some(NodeKey::Inst(r)) =
                    hit(&s_up, b, this.cone.zoom, this.cone.pan, ev.position)
                else {
                    return;
                };
                let m = ev.modifiers;
                if m.control || m.shift || m.platform {
                    this.studio.select_instance(r, true);
                    this.after_select(cx);
                } else if ev.click_count >= 2 {
                    this.studio.focus_instance(r);
                    this.cone.fit();
                } else {
                    this.studio.cross_probe_instance(r);
                }
                cx.notify();
            }),
        )
        .on_scroll_wheel(cx.listener(move |this, ev: &ScrollWheelEvent, _, cx| {
            let Some(b) = this.cone.bounds.get() else {
                return;
            };
            let dy = f32::from(ev.delta.pixel_delta(px(20.0)).y);
            if dy == 0.0 {
                return;
            }
            let factor = (1.0 + dy.abs() / 200.0).min(1.5);
            let z_new = if dy > 0.0 {
                this.cone.zoom * factor
            } else {
                this.cone.zoom / factor
            }
            .clamp(0.2, 8.0);
            // Keep the layout point under the cursor in place.
            let (s_old, o_old) = transform(b, &s_wheel, this.cone.zoom, this.cone.pan);
            let lx = (f32::from(ev.position.x) - f32::from(o_old.x)) / s_old;
            let ly = (f32::from(ev.position.y) - f32::from(o_old.y)) / s_old;
            let (s_new, o_zero) = transform(b, &s_wheel, z_new, point(px(0.0), px(0.0)));
            this.cone.zoom = z_new;
            this.cone.pan = point(
                px(f32::from(ev.position.x) - f32::from(o_zero.x) - lx * s_new),
                px(f32::from(ev.position.y) - f32::from(o_zero.y) - ly * s_new),
            );
            cx.notify();
        }))
        .child(drawing);
    panel(
        title,
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(controls)
            .child(div().flex_1().min_h_0().child(body))
            .into_any_element(),
    )
    .into_any_element()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::cone::Truncation;
    use crate::model::scene::SceneNode;

    fn scene() -> ConeScene {
        let node = |key, x| SceneNode {
            key,
            title: String::new(),
            subtitle: String::new(),
            x,
            y: 0,
            w: 100,
            h: 40,
            root: false,
            on_path: false,
            selected: false,
            port: false,
            distance: Some(0),
            hidden_loads: 0,
        };
        ConeScene {
            root_label: String::new(),
            nodes: vec![node(NodeKey::Inst(0), 0), node(NodeKey::Inst(1), 200)],
            edges: Vec::new(),
            width: 300,
            height: 40,
            layers: 2,
            crossings: 0,
            truncation: Truncation::default(),
            layout_truncated: false,
            diagnostics: Vec::new(),
        }
    }

    // STUDIO-REQ-0164: a click hits the node drawn under it, at any zoom and pan.
    #[test]
    fn hit_inverts_the_drawing_transform() {
        let sc = scene();
        let b = Bounds::new(point(px(10.0), px(20.0)), size(px(400.0), px(300.0)));
        for (zoom, pan) in [
            (1.0, point(px(0.0), px(0.0))),
            (2.5, point(px(-40.0), px(15.0))),
        ] {
            let (s, o) = transform(b, &sc, zoom, pan);
            for n in &sc.nodes {
                let centre = point(
                    o.x + px((n.x + n.w / 2) as f32 * s),
                    o.y + px((n.y + n.h / 2) as f32 * s),
                );
                assert_eq!(hit(&sc, b, zoom, pan, centre), Some(n.key));
            }
            let gap = point(o.x + px(150.0 * s), o.y + px(20.0 * s));
            assert_eq!(hit(&sc, b, zoom, pan, gap), None);
        }
    }
}

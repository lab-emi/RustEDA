// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The list panels of the workbench: hierarchy, revisions, corner summary, path table, path
//! detail and RTL source (STUDIO-REQ-0163 to STUDIO-REQ-0165). Long lists are virtualized:
//! only their visible rows are built (STUDIO-REQ-0165).

use gpui::{
    AnyElement, ClickEvent, Context, FontWeight, InteractiveElement, IntoElement, ParentElement,
    Rgba, SharedString, StatefulInteractiveElement, Styled, div, prelude::FluentBuilder, px,
    uniform_list,
};

use super::theme::{self, ROW_H};
use super::workbench::Workbench;
use crate::model::hierarchy::TreeItem;
use crate::model::studio::Focus;
use crate::model::timing::{Family, PathFilter, format_ns};

/// The monospace font of code and numbers.
#[cfg(target_os = "macos")]
pub(super) const MONO: &str = "Menlo";
/// The monospace font of code and numbers.
#[cfg(not(target_os = "macos"))]
pub(super) const MONO: &str = "DejaVu Sans Mono";

/// Whether a click asks to add to the selection (control or the platform key).
fn additive(ev: &ClickEvent) -> bool {
    let m = ev.modifiers();
    m.control || m.platform || m.shift
}

/// A panel with a header line.
pub(super) fn panel(title: impl Into<SharedString>, body: AnyElement) -> AnyElement {
    div()
        .size_full()
        .flex()
        .flex_col()
        .bg(theme::panel())
        .child(
            div()
                .h(px(24.0))
                .px_2()
                .flex()
                .items_center()
                .bg(theme::header())
                .border_b_1()
                .border_color(theme::border())
                .text_color(theme::muted())
                .truncate()
                .child(title.into()),
        )
        .child(div().flex_1().min_h_0().child(body))
        .into_any_element()
}

/// A text cell of fixed width.
fn cell(width: f32, text: impl Into<SharedString>, color: Rgba) -> AnyElement {
    div()
        .w(px(width))
        .flex_none()
        .truncate()
        .text_color(color)
        .child(text.into())
        .into_any_element()
}

/// A text cell that takes the remaining width.
fn grow(text: impl Into<SharedString>, color: Rgba) -> AnyElement {
    div()
        .flex_1()
        .min_w_0()
        .truncate()
        .text_color(color)
        .child(text.into())
        .into_any_element()
}

/// A cell name without a library prefix ending in `__` (`lib__nand2_1` shows as `nand2_1`).
fn short_cell(cell: Option<&str>) -> String {
    let c = cell.unwrap_or_default();
    c.rsplit_once("__").map_or(c, |(_, base)| base).to_string()
}

fn slack_color(slack: f64) -> Rgba {
    if slack < 0.0 {
        theme::bad()
    } else {
        theme::good()
    }
}

/// The hierarchy explorer with its search field (STUDIO-REQ-0033, STUDIO-REQ-0034).
pub(super) fn hierarchy(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let snap = wb.studio.snapshot();
    let title = match (&snap.netlist, &snap.hierarchy) {
        (Some(nl), Some(h)) => format!(
            "Hierarchy  {}  {} inst  {} scopes",
            nl.module_name(),
            nl.instance_count(),
            h.scopes().len() - 1
        ),
        _ => format!("Hierarchy  ({} has no netlist)", snap.rev),
    };
    let query = wb.search_text.clone();
    let field = div()
        .id("search")
        .mx_2()
        .my_1()
        .px_2()
        .h(px(24.0))
        .flex()
        .items_center()
        .rounded_sm()
        .border_1()
        .border_color(theme::border())
        .bg(theme::bg())
        .cursor_text()
        .when(query.is_empty(), |d| {
            d.text_color(theme::muted())
                .child("type to search instances and scopes")
        })
        .when(!query.is_empty(), |d| d.child(format!("{query}|")))
        .on_click(cx.listener(|this, _: &ClickEvent, window, cx| {
            this.search_focus.focus(window);
            cx.notify();
        }));
    let list = match wb.studio.search_result().cloned() {
        Some(found) => {
            let note = format!(
                "{} matches{}",
                found.total,
                if found.truncated {
                    format!(", first {} listed", found.instances.len())
                } else {
                    String::new()
                }
            );
            let items: Vec<TreeItem> = found
                .scopes
                .iter()
                .map(|&s| TreeItem::Scope(s))
                .chain(found.instances.iter().map(|&r| TreeItem::Instance(r)))
                .collect();
            let count = items.len();
            div()
                .size_full()
                .flex()
                .flex_col()
                .child(div().px_2().text_color(theme::muted()).child(note))
                .child(
                    uniform_list(
                        "search-results",
                        count,
                        cx.processor(
                            move |this: &mut Workbench, range: std::ops::Range<usize>, _, cx| {
                                range
                                    .map(|ix| tree_row(this, items[ix], 0, ix, true, cx))
                                    .collect::<Vec<_>>()
                            },
                        ),
                    )
                    .flex_1(),
                )
                .into_any_element()
        }
        None => {
            let rows = wb.studio.tree_rows_shared();
            let count = rows.len();
            uniform_list(
                "tree",
                count,
                cx.processor(
                    move |this: &mut Workbench, range: std::ops::Range<usize>, _, cx| {
                        range
                            .map(|ix| tree_row(this, rows[ix].item, rows[ix].depth, ix, false, cx))
                            .collect::<Vec<_>>()
                    },
                ),
            )
            .track_scroll(wb.tree_scroll.clone())
            .size_full()
            .into_any_element()
        }
    };
    panel(
        title,
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(field)
            .child(div().flex_1().min_h_0().child(list))
            .into_any_element(),
    )
}

/// One row of the tree or of the search results.
fn tree_row(
    wb: &mut Workbench,
    item: TreeItem,
    depth: u32,
    ix: usize,
    search: bool,
    cx: &mut Context<Workbench>,
) -> AnyElement {
    let (label, detail) = wb.studio.tree_text(item);
    let (marker, selected, focused) = match item {
        TreeItem::Scope(s) => (
            if wb.studio.is_expanded(s) { "v " } else { "> " },
            false,
            false,
        ),
        TreeItem::Instance(r) => (
            "  ",
            wb.studio.selection().instances.contains(&r),
            wb.studio.focus() == Some(Focus::Instance(r)),
        ),
    };
    let label = if search {
        // Search results show the full name, which says where the match is.
        match item {
            TreeItem::Instance(r) => wb
                .studio
                .snapshot()
                .netlist
                .as_ref()
                .map_or(label, |n| n.instance_name(r).to_string()),
            TreeItem::Scope(s) => wb
                .studio
                .snapshot()
                .hierarchy
                .as_ref()
                .map_or(label, |h| h.scope_path(s)),
        }
    } else {
        label
    };
    let id = if search { ("found", ix) } else { ("node", ix) };
    let selector = if search {
        format!("found-row-{ix}")
    } else {
        format!("tree-row-{ix}")
    };
    div()
        .id(id)
        .debug_selector(move || selector)
        .w_full()
        .h(px(ROW_H))
        .flex()
        .flex_row()
        .items_center()
        .pl(px(6.0 + 14.0 * depth as f32))
        .pr_2()
        .gap_1()
        .cursor_pointer()
        .when(selected, |d| d.bg(theme::selected_bg()))
        .when(focused && !selected, |d| d.bg(theme::focus_bg()))
        .hover(|s| s.bg(theme::focus_bg()))
        .child(
            div()
                .text_color(theme::muted())
                .font_family(MONO)
                .child(marker),
        )
        .child(grow(label, theme::text()))
        .child(div().flex_none().text_color(theme::muted()).child(detail))
        .on_click(cx.listener(move |this, ev: &ClickEvent, _, cx| {
            match item {
                TreeItem::Scope(s) => {
                    if search {
                        // Show the scope in the tree: open it and its parents.
                        if let Some(h) = this.studio.snapshot().hierarchy.as_ref() {
                            let mut chain = Vec::new();
                            let mut at = Some(s);
                            while let Some(x) = at {
                                chain.push(x);
                                at = h.scope(x).and_then(|y| y.parent);
                            }
                            for x in chain {
                                if !this.studio.is_expanded(x) {
                                    this.studio.toggle_scope(x);
                                }
                            }
                        }
                        this.search_text.clear();
                        this.studio.search("");
                    } else {
                        this.studio.toggle_scope(s);
                    }
                }
                TreeItem::Instance(r) => {
                    this.studio.select_instance(r, additive(ev));
                    this.after_select(cx);
                }
            }
            cx.notify();
        }))
        .into_any_element()
}

/// The revisions, newest first; a click pins one (STUDIO-REQ-0162).
pub(super) fn revisions(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let shown = wb.studio.snapshot().rev;
    let head = wb.studio.snapshot().head;
    let rows: Vec<AnyElement> = wb
        .studio
        .revisions()
        .iter()
        .map(|r| {
            let rev = r.rev;
            let color = match r.timing.as_str() {
                "met" => theme::good(),
                "violated" => theme::bad(),
                _ => theme::muted(),
            };
            div()
                .id(("rev", rev.0 as usize))
                .debug_selector(move || format!("rev-row-{}", rev.0))
                .h(px(ROW_H))
                .px_2()
                .flex()
                .flex_row()
                .items_center()
                .gap_2()
                .cursor_pointer()
                .when(rev == shown, |d| d.bg(theme::selected_bg()))
                .hover(|s| s.bg(theme::focus_bg()))
                .child(cell(
                    52.0,
                    format!("{}{}", rev, if rev == head { "*" } else { "" }),
                    theme::text(),
                ))
                .child(grow(r.command.clone(), theme::muted()))
                .child(cell(84.0, r.timing.clone(), color))
                .on_click(cx.listener(move |this, _: &ClickEvent, _, cx| {
                    this.show_revision(Some(rev), cx);
                }))
                .into_any_element()
        })
        .collect();
    panel(
        "Revisions  (* HEAD; click to pin, follow HEAD at the top)",
        div()
            .id("revisions")
            .size_full()
            .overflow_y_scroll()
            .children(rows)
            .into_any_element(),
    )
}

/// The corner summary with the analysis scope and the filters (STUDIO-REQ-0051,
/// STUDIO-REQ-0052, STUDIO-REQ-0054).
pub(super) fn summary(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let snap = wb.studio.snapshot();
    let Some(t) = &snap.timing else {
        return div()
            .h(px(30.0))
            .px_3()
            .flex()
            .items_center()
            .bg(theme::header())
            .text_color(theme::muted())
            .child(format!("{}: timing not_analyzed", snap.rev))
            .into_any_element();
    };
    let chips: Vec<AnyElement> = t
        .corners()
        .iter()
        .map(|c| {
            div()
                .px_2()
                .py_1()
                .rounded_sm()
                .bg(theme::panel())
                .border_1()
                .border_color(theme::border())
                .flex()
                .flex_row()
                .gap_2()
                .child(div().text_color(theme::accent()).child(c.name.clone()))
                .child(
                    div()
                        .text_color(c.setup_wns_ns.map_or(theme::muted(), slack_color))
                        .child(format!(
                            "setup WNS {} TNS {} ({}/{})",
                            format_ns(c.setup_wns_ns, true),
                            format_ns(Some(c.setup_tns_ns), true),
                            c.setup_violating,
                            c.endpoints
                        )),
                )
                .child(
                    div()
                        .text_color(c.hold_wns_ns.map_or(theme::muted(), slack_color))
                        .child(format!(
                            "hold WNS {} TNS {}",
                            format_ns(c.hold_wns_ns, true),
                            format_ns(Some(c.hold_tns_ns), true)
                        )),
                )
                .child(
                    div()
                        .text_color(theme::muted())
                        .child(format!("{} unconstrained", c.unconstrained_endpoints)),
                )
                .into_any_element()
        })
        .collect();
    let f = wb.studio.filter().clone();
    let names: Vec<String> = t.corners().iter().map(|c| c.name.clone()).collect();
    let button = |id: &'static str, label: String, on: bool| {
        div()
            .id(id)
            .debug_selector(move || format!("filter-{id}"))
            .px_2()
            .rounded_sm()
            .cursor_pointer()
            .border_1()
            .border_color(theme::border())
            .when(on, |d| d.bg(theme::selected_bg()))
            .child(label)
    };
    let set = |g: fn(&mut PathFilter)| {
        cx.listener(move |this: &mut Workbench, _: &ClickEvent, _, cx| {
            let mut f = this.studio.filter().clone();
            g(&mut f);
            this.studio.set_filter(f);
            cx.notify();
        })
    };
    let corner_label = f
        .corner
        .and_then(|c| names.get(c).cloned())
        .unwrap_or_else(|| "all corners".into());
    let n_corners = names.len();
    div()
        .flex()
        .flex_col()
        .gap_1()
        .p_2()
        .bg(theme::header())
        .border_b_1()
        .border_color(theme::border())
        .child(div().flex().flex_row().flex_wrap().gap_2().children(chips))
        .child(
            div()
                .flex()
                .flex_row()
                .items_center()
                .gap_2()
                .child(
                    button("all", "all".into(), f.family == Family::All)
                        .on_click(set(|f| f.family = Family::All)),
                )
                .child(
                    button("setup", "setup".into(), f.family == Family::Setup)
                        .on_click(set(|f| f.family = Family::Setup)),
                )
                .child(
                    button("hold", "hold".into(), f.family == Family::Hold)
                        .on_click(set(|f| f.family = Family::Hold)),
                )
                .child(
                    button("violating", "violating only".into(), f.violating_only)
                        .on_click(set(|f| f.violating_only = !f.violating_only)),
                )
                .child(
                    button("corner", corner_label, f.corner.is_some()).on_click(cx.listener(
                        move |this, _: &ClickEvent, _, cx| {
                            let mut f = this.studio.filter().clone();
                            f.corner = match f.corner {
                                None if n_corners > 0 => Some(0),
                                Some(c) if c + 1 < n_corners => Some(c + 1),
                                _ => None,
                            };
                            this.studio.set_filter(f);
                            cx.notify();
                        },
                    )),
                )
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .truncate()
                        .text_color(theme::muted())
                        .child(format!("scope: {}", t.scope_text())),
                ),
        )
        .into_any_element()
}

/// The ranked path table (STUDIO-REQ-0050, STUDIO-REQ-0164).
pub(super) fn paths(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let snap = wb.studio.snapshot();
    let Some(t) = &snap.timing else {
        return panel(
            "Critical paths",
            div()
                .p_2()
                .text_color(theme::muted())
                .child("no timing table in this revision")
                .into_any_element(),
        );
    };
    let rows: Vec<usize> = wb.studio.visible_paths().iter().map(|r| r.row).collect();
    let title = format!(
        "Critical paths  {} shown of {} ranked  (click selects and shares; ctrl adds)",
        rows.len(),
        t.total_paths()
    );
    let header = div()
        .h(px(ROW_H))
        .px_2()
        .flex()
        .flex_row()
        .items_center()
        .gap_2()
        .text_color(theme::muted())
        .border_b_1()
        .border_color(theme::border())
        .child(cell(56.0, "handle", theme::muted()))
        .child(cell(58.0, "kind", theme::muted()))
        .child(cell(50.0, "corner", theme::muted()))
        .child(cell(74.0, "slack ns", theme::muted()))
        .child(cell(46.0, "levels", theme::muted()))
        .child(grow("startpoint", theme::muted()))
        .child(grow("endpoint", theme::muted()));
    let count = rows.len();
    let list = uniform_list(
        "paths",
        count,
        cx.processor(
            move |this: &mut Workbench, range: std::ops::Range<usize>, _, cx| {
                range
                    .filter_map(|ix| path_row(this, rows[ix], ix, cx))
                    .collect::<Vec<_>>()
            },
        ),
    )
    .track_scroll(wb.paths_scroll.clone())
    .flex_1();
    panel(
        title,
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(header)
            .child(list)
            .into_any_element(),
    )
}

fn path_row(
    wb: &mut Workbench,
    row: usize,
    ix: usize,
    cx: &mut Context<Workbench>,
) -> Option<AnyElement> {
    let snap = wb.studio.snapshot().clone();
    let r = snap.timing.as_ref()?.row(row)?;
    let selected = wb.studio.selection().paths.contains(&row);
    let focused = wb.studio.focus() == Some(Focus::Path(row));
    Some(
        div()
            .id(("path", ix))
            .debug_selector(move || format!("path-row-{ix}"))
            .w_full()
            .h(px(ROW_H))
            .px_2()
            .flex()
            .flex_row()
            .items_center()
            .gap_2()
            .cursor_pointer()
            .font_family(MONO)
            .when(selected, |d| d.bg(theme::selected_bg()))
            .when(focused && !selected, |d| d.bg(theme::focus_bg()))
            .hover(|s| s.bg(theme::focus_bg()))
            .child(cell(56.0, r.handle.clone(), theme::accent()))
            .child(cell(58.0, r.kind.as_str(), theme::text()))
            .child(cell(50.0, r.corner_name.clone(), theme::text()))
            .child(cell(
                74.0,
                format_ns(Some(r.slack_ns), r.slack_exact),
                slack_color(r.slack_ns),
            ))
            .child(cell(46.0, r.levels.to_string(), theme::text()))
            .child(grow(r.startpoint.clone(), theme::text()))
            .child(grow(r.endpoint.clone(), theme::text()))
            .on_click(cx.listener(move |this, ev: &ClickEvent, _, cx| {
                this.studio.select_path(row, additive(ev));
                this.after_select(cx);
            }))
            .into_any_element(),
    )
}

/// The focused path's points (STUDIO-REQ-0053); a click on a point cross-probes its instance.
pub(super) fn detail(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let Some(d) = wb.studio.detail().cloned() else {
        let text = match wb.studio.focus() {
            Some(Focus::Instance(r)) => format!(
                "instance {} selected; select a path for its points",
                wb.studio
                    .snapshot()
                    .netlist
                    .as_ref()
                    .map_or("", |n| n.instance_name(r))
            ),
            _ => "no path focused".to_string(),
        };
        return panel(
            "Path detail",
            div()
                .p_2()
                .text_color(theme::muted())
                .child(text)
                .into_any_element(),
        );
    };
    let title = format!(
        "{}  {} {}  slack {}  required {}  check {}{}",
        d.row.handle,
        d.row.kind.as_str(),
        d.row.corner_name,
        format_ns(Some(d.row.slack_ns), d.row.slack_exact),
        format_ns(d.required_ns, true),
        format_ns(d.check_ns, true),
        if d.materialized {
            ""
        } else {
            "  (no arrivals)"
        }
    );
    let points = d.points.clone();
    let count = points.len();
    let header = div()
        .h(px(ROW_H))
        .px_2()
        .flex()
        .flex_row()
        .items_center()
        .gap_2()
        .text_color(theme::muted())
        .border_b_1()
        .border_color(theme::border())
        .child(grow("point", theme::muted()))
        .child(cell(118.0, "cell", theme::muted()))
        .child(cell(38.0, "arc", theme::muted()))
        .child(cell(56.0, "incr", theme::muted()))
        .child(cell(56.0, "arrival", theme::muted()));
    let list = uniform_list(
        "points",
        count,
        cx.processor(
            move |_this: &mut Workbench, range: std::ops::Range<usize>, _, cx| {
                range
                    .map(|ix| {
                        let p = &points[ix];
                        let inst = p.instance_row;
                        let arc = match p.kind {
                            rusteda_workspace::sta::PointKind::Port => "port",
                            rusteda_workspace::sta::PointKind::Net => "net",
                            rusteda_workspace::sta::PointKind::Cell => "cell",
                            rusteda_workspace::sta::PointKind::ClockLaunch => "clock",
                        };
                        div()
                            .id(("point", ix))
                            .w_full()
                            .h(px(ROW_H))
                            .px_2()
                            .flex()
                            .flex_row()
                            .items_center()
                            .gap_2()
                            .font_family(MONO)
                            .when(inst.is_some(), |d| d.cursor_pointer())
                            .hover(|s| s.bg(theme::focus_bg()))
                            .child(grow(p.name.clone(), theme::text()))
                            .child(cell(118.0, short_cell(p.cell.as_deref()), theme::muted()))
                            .child(cell(38.0, arc, theme::muted()))
                            .child(cell(56.0, format_ns(p.increment_ns, true), theme::text()))
                            .child(cell(56.0, format_ns(p.arrival_ns, true), theme::text()))
                            .on_click(cx.listener(move |this, _: &ClickEvent, _, cx| {
                                if let Some(r) = inst {
                                    this.studio.cross_probe_instance(r);
                                    cx.notify();
                                }
                            }))
                            .into_any_element()
                    })
                    .collect::<Vec<_>>()
            },
        ),
    )
    .flex_1();
    panel(
        title,
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(header)
            .child(list)
            .into_any_element(),
    )
}

/// The RTL source with the cross-probe's lines highlighted (STUDIO-REQ-0126).
pub(super) fn source(wb: &mut Workbench, cx: &mut Context<Workbench>) -> AnyElement {
    let snap = wb.studio.snapshot().clone();
    let (Some(index), Some(view), Some(probe)) = (
        &snap.rtl,
        wb.studio.source().cloned(),
        wb.studio.probe().cloned(),
    ) else {
        let text = if snap.rtl.is_none() {
            format!("{} has no RTL snapshot", snap.rev)
        } else {
            match wb.studio.probe() {
                Some(p) => format!("no RTL line matches {}", p.subject),
                None => "select a path or an instance to cross-probe".into(),
            }
        };
        return panel(
            "RTL source",
            div()
                .p_2()
                .text_color(theme::muted())
                .child(text)
                .into_any_element(),
        );
    };
    let title = format!(
        "RTL  {}:{}  of {}  (approximate: matched by name)",
        view.path, view.focus_line, probe.subject
    );
    let hits: Vec<AnyElement> = probe
        .hits
        .iter()
        .take(6)
        .enumerate()
        .map(|(k, h)| {
            div()
                .id(("hit", k))
                .debug_selector(move || format!("hit-{k}"))
                .px_2()
                .h(px(ROW_H))
                .flex()
                .flex_row()
                .items_center()
                .gap_2()
                .cursor_pointer()
                .hover(|s| s.bg(theme::focus_bg()))
                .when(h.file == view.file && h.line == view.focus_line, |d| {
                    d.bg(theme::selected_bg())
                })
                .child(cell(
                    150.0,
                    format!("{}:{}", index.files[h.file].path, h.line),
                    theme::accent(),
                ))
                .child(cell(84.0, h.kind.as_str(), theme::path()))
                .child(grow(h.reason.clone(), theme::muted()))
                .on_click(cx.listener(move |this, _: &ClickEvent, _, cx| {
                    this.studio.show_hit(k);
                    cx.notify();
                }))
                .into_any_element()
        })
        .collect();
    let file = view.file;
    let n_lines = index.files[file].line_count();
    let highlighted = view.highlighted.clone();
    let focus = view.focus_line;
    let lines = uniform_list(
        "source",
        n_lines,
        cx.processor(
            move |this: &mut Workbench, range: std::ops::Range<usize>, _, _| {
                let snap = this.studio.snapshot().clone();
                let Some(index) = snap.rtl.as_ref() else {
                    return Vec::new();
                };
                range
                    .map(|ix| {
                        let line = ix as u32 + 1;
                        let hit = highlighted.contains(&line);
                        div()
                            .w_full()
                            .h(px(ROW_H))
                            .flex()
                            .flex_row()
                            .items_center()
                            .font_family(MONO)
                            .when(hit, |d| d.bg(theme::hit_bg()))
                            .child(
                                div()
                                    .w(px(44.0))
                                    .flex_none()
                                    .pr_2()
                                    .text_color(if line == focus {
                                        theme::path()
                                    } else {
                                        theme::muted()
                                    })
                                    .when(line == focus, |d| d.font_weight(FontWeight::BOLD))
                                    .child(format!("{line:>4}")),
                            )
                            .child(
                                div()
                                    .flex_1()
                                    .whitespace_nowrap()
                                    .child(index.files[file].line(line).replace('\t', "    ")),
                            )
                            .into_any_element()
                    })
                    .collect::<Vec<_>>()
            },
        ),
    )
    .track_scroll(wb.source_scroll.clone())
    .flex_1();
    panel(
        title,
        div()
            .size_full()
            .flex()
            .flex_col()
            .child(
                div()
                    .border_b_1()
                    .border_color(theme::border())
                    .children(hits),
            )
            .child(lines)
            .into_any_element(),
    )
}

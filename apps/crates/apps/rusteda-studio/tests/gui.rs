// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! View tests of the window on GPUI's headless test platform: the panels draw, clicks and keys
//! reach the model, and the window follows commits through its polling loop.

#![cfg(feature = "gui-tests")]

mod common;

use std::time::Duration;

use common::{Names, commit_timing, design_workspace, scratch};
use gpui::{Entity, Modifiers, TestAppContext, VisualTestContext};
use rusteda_studio::gui::Workbench;
use rusteda_studio::model::studio::Focus;
use rusteda_studio::{Studio, StudioOptions};
use rusteda_workspace::{Rev, Workspace};

/// A window on the design workspace at `root`, polling every 50 ms without debounce.
fn window(
    cx: &mut TestAppContext,
    root: &std::path::Path,
) -> (Entity<Workbench>, VisualTestContext) {
    let mut o = StudioOptions::new(root);
    o.debounce = Duration::ZERO;
    o.poll = Duration::from_millis(50);
    let studio = Studio::open(o).unwrap();
    let (view, vcx) = cx.add_window_view(|window, cx| Workbench::new(studio, window, cx));
    let vcx = vcx.clone();
    vcx.run_until_parked();
    (view, vcx)
}

fn click(cx: &mut VisualTestContext, selector: &'static str, modifiers: Modifiers) {
    let b = cx
        .debug_bounds(selector)
        .unwrap_or_else(|| panic!("{selector} is not drawn"));
    cx.simulate_click(b.center(), modifiers);
    cx.run_until_parked();
}

fn shared(root: &std::path::Path) -> rusteda_workspace::UiState {
    Workspace::open(root).unwrap().ui_state().unwrap().unwrap()
}

// STUDIO-REQ-0163, STUDIO-REQ-0165: the window draws the hierarchy, the revisions, the path
// table, the cone and its controls; the lists draw rows.
#[test]
fn draws_every_panel() {
    let root = scratch("gui-draw");
    design_workspace(&root, false);
    let mut cx = TestAppContext::single();
    let (view, mut vcx) = window(&mut cx, &root);
    for selector in [
        "tree-row-0",
        "rev-row-3",
        "rev-row-0",
        "path-row-0",
        "path-row-4",
        "cone-canvas",
        "cone-fi+",
        "filter-violating",
        "hit-0",
    ] {
        assert!(vcx.debug_bounds(selector).is_some(), "{selector} not drawn");
    }
    assert!(
        vcx.debug_bounds("follow-head").is_none(),
        "following HEAD already"
    );
    view.read_with(&vcx, |wb, _| {
        assert_eq!(wb.studio().snapshot().rev, Rev(3));
        assert_eq!(wb.studio().focus(), Some(Focus::Path(0)));
    });
}

// STUDIO-REQ-0164: a click on a path row selects and shares it; a click with control adds to
// the selection; a click on an instance row of the tree shares the instance.
#[test]
fn clicks_select_and_share() {
    let root = scratch("gui-click");
    design_workspace(&root, false);
    let mut cx = TestAppContext::single();
    let (view, mut vcx) = window(&mut cx, &root);
    click(&mut vcx, "path-row-1", Modifiers::none());
    let u = shared(&root);
    assert_eq!((u.selection_handle.as_str(), u.revision), ("sel1", Rev(3)));
    assert_eq!(u.selected_paths, ["path2"]);
    click(&mut vcx, "path-row-2", Modifiers::control());
    assert_eq!(shared(&root).selected_paths, ["path2", "path3"]);
    click(&mut vcx, "tree-row-0", Modifiers::none());
    let u = shared(&root);
    assert_eq!(u.selection_handle, "sel3");
    assert_eq!(u.selected_instances.len(), 1);
    assert!(u.selected_paths.is_empty());
    view.read_with(&vcx, |wb, _| {
        assert!(matches!(wb.studio().focus(), Some(Focus::Instance(_))));
        assert!(wb.studio().scene().is_some());
    });
    // A depth button redraws the cone with one more fan-in level.
    let before = view.read_with(&vcx, |wb, _| wb.studio().cone_depths());
    click(&mut vcx, "cone-fi+", Modifiers::none());
    let after = view.read_with(&vcx, |wb, _| wb.studio().cone_depths());
    assert_eq!(after, (before.0 + 1, before.1));
    // A filter button narrows the table.
    click(&mut vcx, "filter-hold", Modifiers::none());
    view.read_with(&vcx, |wb, _| {
        assert_eq!(wb.studio().visible_paths().len(), 1)
    });
}

// STUDIO-REQ-0164: typing searches the hierarchy; backspace and escape edit the query.
#[test]
fn typing_searches() {
    let root = scratch("gui-type");
    design_workspace(&root, false);
    let mut cx = TestAppContext::single();
    let (view, mut vcx) = window(&mut cx, &root);
    vcx.simulate_keystrokes("g 1");
    view.read_with(&vcx, |wb, _| {
        let r = wb.studio().search_result().unwrap();
        assert_eq!(r.query, "g1");
        // g1, g10, g11
        assert_eq!(r.total, 3);
    });
    assert!(vcx.debug_bounds("found-row-0").is_some());
    vcx.simulate_keystrokes("backspace backspace");
    view.read_with(&vcx, |wb, _| assert!(wb.studio().search_result().is_none()));
}

// STUDIO-REQ-0160, STUDIO-REQ-0166, STUDIO-REQ-0162: the polling loop sees a commit and loads it
// in the background; a click on a revision pins it, and "follow HEAD" follows again.
#[test]
fn follows_commits_and_pins_revisions() {
    let root = scratch("gui-live");
    let ws = design_workspace(&root, false);
    let mut cx = TestAppContext::single();
    let (view, mut vcx) = window(&mut cx, &root);
    let rev4 = commit_timing(&ws, Rev(3), &Names::new(false), 0.3);
    vcx.executor().advance_clock(Duration::from_millis(60));
    vcx.run_until_parked();
    vcx.executor().advance_clock(Duration::from_millis(60));
    vcx.run_until_parked();
    view.read_with(&vcx, |wb, _| {
        assert_eq!(wb.studio().snapshot().rev, rev4);
        assert!(!wb.is_loading());
        let d = wb.studio().detail().unwrap();
        assert!((d.row.slack_ns + 0.05).abs() < 1e-9, "{}", d.row.slack_ns);
    });
    click(&mut vcx, "rev-row-2", Modifiers::none());
    view.read_with(&vcx, |wb, _| {
        assert_eq!(wb.studio().snapshot().rev, Rev(2));
        assert!(!wb.studio().follows_head());
    });
    click(&mut vcx, "follow-head", Modifiers::none());
    view.read_with(&vcx, |wb, _| {
        assert_eq!(wb.studio().snapshot().rev, rev4);
        assert!(wb.studio().follows_head());
    });
}

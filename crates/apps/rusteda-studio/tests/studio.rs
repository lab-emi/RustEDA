// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The workbench model on workspaces built with the shared crates: loading, panels, selection
//! sharing through `ui_state.json`, and live updates while revisions are committed.

mod common;

use std::time::{Duration, Instant};

use common::{Names, commit_timing, design_workspace, scratch, tree};
use rusteda_studio::model::diag::Severity;
use rusteda_studio::model::hierarchy::TreeItem;
use rusteda_studio::model::print;
use rusteda_studio::model::rtl::HitKind;
use rusteda_studio::model::snapshot::Which;
use rusteda_studio::model::studio::Focus;
use rusteda_studio::model::watch::{WatchEvent, Watcher};
use rusteda_studio::{Code, Studio, StudioOptions};
use rusteda_workspace::{ErrorCode, Rev, UiState, Workspace};

fn open(root: &std::path::Path) -> Studio {
    Studio::open(StudioOptions::new(root)).unwrap()
}

fn has(studio: &Studio, code: Code) -> bool {
    studio.diagnostics().iter().any(|d| d.code == code)
}

// STUDIO-REQ-0005, STUDIO-REQ-0006, STUDIO-REQ-0145: HEAD's revision is shown with its
// netlist, hierarchy, timing and RTL; the revisions are listed newest first with their timing
// status; the worst path is focused and nothing is written.
#[test]
fn opens_head_with_every_panel() {
    let root = scratch("open");
    design_workspace(&root, false);
    let s = open(&root);
    let snap = s.snapshot();
    assert_eq!(snap.rev, Rev(3));
    assert_eq!(snap.head, Rev(3));
    assert!(snap.netlist.is_some() && snap.hierarchy.is_some());
    assert!(snap.timing.is_some() && snap.rtl.is_some());
    assert!(
        s.diagnostics()
            .iter()
            .all(|d| d.severity != Severity::Error),
        "{:?}",
        s.diagnostics()
    );
    let revs: Vec<(Rev, &str)> = s
        .revisions()
        .iter()
        .map(|r| (r.rev, r.timing.as_str()))
        .collect();
    assert_eq!(
        revs,
        [
            (Rev(3), "violated"),
            (Rev(2), "not_analyzed"),
            (Rev(1), "not_analyzed"),
            (Rev(0), "not_analyzed")
        ]
    );
    assert_eq!(s.focus(), Some(Focus::Path(0)));
    let d = s.detail().unwrap();
    assert_eq!(d.row.handle, "path1");
    assert_eq!(d.row.corner_name, "ss");
    assert_eq!(d.row.endpoint, "g3/D");
    assert_eq!(d.row.levels, 5);
    assert_eq!(d.points[1].cell.as_deref(), Some("dff"));
    assert!(s.selection().is_empty());
    assert!(s.shared().is_none());
    assert!(!root.join("ui_state.json").exists());
}

// STUDIO-REQ-0096, STUDIO-REQ-0122, STUDIO-REQ-0124, STUDIO-REQ-0126: the focused path's cone
// pins and highlights its instances; the endpoint's flop is cross-probed through its output net
// `u_cnt.cnt_q[3]` to the counter's declaration of `cnt_q`, which the source pane shows with the
// other lines of the probe highlighted; another hit moves the pane.
#[test]
fn path_cone_and_cross_probe() {
    let root = scratch("probe");
    design_workspace(&root, false);
    let s = open(&root);
    let scene = s.scene().unwrap();
    let on_path: Vec<&str> = scene
        .nodes
        .iter()
        .filter(|n| n.on_path)
        .map(|n| n.title.as_str())
        .collect();
    for want in ["g0", "g4", "g6", "g8", "g11", "g3"] {
        assert!(on_path.contains(&want), "{want} not on {on_path:?}");
    }
    assert!(scene.edges.iter().filter(|e| e.on_path).count() >= 5);
    let p = s.probe().unwrap();
    assert!(p.approximate);
    let top = &p.hits[0];
    let index = s.snapshot().rtl.as_ref().unwrap();
    assert_eq!(index.files[top.file].path, "src_1_counter.sv");
    assert_eq!(top.line, 6);
    assert_eq!(top.kind, HitKind::Declaration);
    let src = s.source().unwrap();
    assert_eq!(src.focus_line, 6);
    assert!(src.highlighted.contains(&7));
    assert!(has(&s, Code::CrossProbeApproximate));
    let top_file = top.file;
    let mut s = s;
    let k = s
        .probe()
        .unwrap()
        .hits
        .iter()
        .position(|h| h.file != top_file)
        .unwrap();
    s.show_hit(k);
    let src = s.source().unwrap();
    assert_eq!(src.path, "src_0_top.sv");
    // The best hit of the top file is the declaration of the clock input (an input net of the
    // flop); the instantiation of the counter is highlighted too.
    assert_eq!(src.focus_line, 2);
    assert_eq!(src.highlighted, [2, 6]);
}

// STUDIO-REQ-0031, STUDIO-REQ-0124: hierarchical instance names make scopes, and an
// instance's own RTL parts lead to the RTL (`cnt_q_reg` without its `_reg`).
#[test]
fn hierarchical_names() {
    let root = scratch("hier");
    design_workspace(&root, true);
    let mut s = open(&root);
    let h = s.snapshot().hierarchy.as_ref().unwrap();
    let names: Vec<&str> = h.scopes().iter().map(|x| x.name.as_str()).collect();
    assert_eq!(names, ["top_hier", "u_cnt", "add"]);
    assert_eq!(h.scope(1).unwrap().total_instances, 12);
    assert_eq!(h.scope(2).unwrap().total_instances, 8);
    let rows = s.tree_rows().to_vec();
    assert_eq!(rows.len(), 1, "only u_cnt at the top");
    s.toggle_scope(1);
    let (label, detail) = s.tree_text(s.tree_rows()[0].item);
    assert_eq!(
        (label.as_str(), detail.as_str()),
        ("u_cnt", "12 inst, 1 sub")
    );
    assert_eq!(s.tree_rows().len(), 6);
    let r2 = s
        .snapshot()
        .netlist
        .as_ref()
        .unwrap()
        .find_instance("u_cnt/cnt_q_reg[2]")
        .unwrap();
    s.focus_instance(r2);
    let p = s.probe().unwrap();
    let index = s.snapshot().rtl.as_ref().unwrap();
    let best = &p.hits[0];
    assert_eq!(
        (index.files[best.file].path.as_str(), best.line),
        ("src_1_counter.sv", 6)
    );
    assert!(p.hits.iter().any(|h| h.kind == HitKind::Instantiation));
    // The scope of the focused instance is opened in the tree.
    assert!(s.is_expanded(1));
    assert!(
        s.tree_rows()
            .iter()
            .any(|r| r.item == TreeItem::Instance(r2))
    );
    s.search("CNT_Q_REG");
    let found = s.search_result().unwrap();
    assert_eq!(found.instances.len(), 4);
}

// STUDIO-REQ-0005: a revision without a netlist or timing shows what it has, with notes.
#[test]
fn missing_tables_are_notes() {
    let root = scratch("missing");
    design_workspace(&root, false);
    let mut o = StudioOptions::new(&root);
    o.revision = Some(Rev(1));
    let s = Studio::open(o).unwrap();
    assert!(s.snapshot().netlist.is_none() && s.snapshot().timing.is_none());
    assert!(s.snapshot().rtl.is_some());
    assert!(has(&s, Code::NoNetlist) && has(&s, Code::NoTiming));
    assert!(s.diagnostics().iter().all(|d| d.severity == Severity::Note));
    assert_eq!(s.snapshot().info.timing, "not_analyzed");
    assert!(s.scene().is_none() && s.detail().is_none());
    let mut o = StudioOptions::new(&root);
    o.revision = Some(Rev(0));
    let s = Studio::open(o).unwrap();
    assert!(has(&s, Code::NoRtl));
    let mut o = StudioOptions::new(&root);
    o.revision = Some(Rev(9));
    assert_eq!(Studio::open(o).unwrap_err().code, Code::UnknownRevision);
    let e = Studio::open(StudioOptions::new(root.join("nope"))).unwrap_err();
    assert_eq!(e.code, Code::WorkspaceUnreadable);
}

// STUDIO-REQ-0141, STUDIO-REQ-0142, STUDIO-REQ-0143: a selection is written as `sel<N>` of the
// displayed revision with shell-safe handles, and the command line's resolver accepts it.
#[test]
fn selection_is_shared_through_ui_state() {
    let root = scratch("share");
    let ws = design_workspace(&root, false);
    let mut s = open(&root);
    s.select_path(0, false);
    let u = ws.ui_state().unwrap().unwrap();
    assert_eq!(u.selection_handle, "sel1");
    assert_eq!(u.revision, Rev(3));
    assert_eq!(u.selected_paths, ["path1"]);
    assert!(u.selected_instances.is_empty());
    let resolved = ws.resolve_selection("sel1", Rev(3)).unwrap();
    assert_eq!(resolved, u);
    let g5 = s
        .snapshot()
        .netlist
        .as_ref()
        .unwrap()
        .find_instance("g5")
        .unwrap();
    s.select_instance(g5, true);
    let u = ws.ui_state().unwrap().unwrap();
    assert_eq!(u.selection_handle, "sel2");
    assert_eq!(u.selected_paths, ["path1"]);
    assert_eq!(u.selected_instances, [format!("inst{}", g5 + 1)]);
    for h in u.selected_instances.iter().chain(&u.selected_paths) {
        assert!(rusteda_workspace::rev::is_handle(h), "{h}");
    }
    let stale = ws.resolve_selection("sel1", Rev(3)).unwrap_err();
    assert_eq!(stale.code(), ErrorCode::UnknownHandle);
    assert!(ws.resolve_selection("sel2", Rev(2)).is_err());
    assert_eq!(s.shared(), Some(&u));
}

// STUDIO-REQ-0001, STUDIO-REQ-0144, STUDIO-REQ-0146, STUDIO-REQ-0147: a selection written by
// another program is read and shown; one of another revision or with unknown handles is
// reported; a damaged file is reported and changes nothing.
#[test]
fn external_selection_is_applied() {
    let root = scratch("external");
    let ws = design_workspace(&root, false);
    let mut s = open(&root);
    let mut w = Watcher::new(&root, Duration::ZERO);
    let t = Instant::now();
    let other = UiState::new(Rev(3), "sel9", vec!["inst1".into()], vec!["path2".into()]);
    ws.write_ui_state(&other).unwrap();
    let events = w.poll(t);
    assert_eq!(events, [WatchEvent::UiState]);
    assert_eq!(s.handle_events(&events), None);
    assert_eq!(s.selection().paths, [1]);
    assert_eq!(s.selection().instances, [0]);
    assert_eq!(s.focus(), Some(Focus::Path(1)));
    // The next own selection numbers above the external one.
    s.select_path(0, false);
    assert_eq!(ws.ui_state().unwrap().unwrap().selection_handle, "sel10");
    w.mark_ui_state_seen();
    assert!(w.poll(t).is_empty());
    let older = UiState::new(Rev(2), "sel11", vec!["inst1".into()], vec![]);
    ws.write_ui_state(&older).unwrap();
    s.handle_events(&w.poll(t));
    assert!(has(&s, Code::SelectionOtherRevision));
    assert_eq!(s.selection().paths, [0], "kept");
    let partial = UiState::new(
        Rev(3),
        "sel12",
        vec!["inst999".into()],
        vec!["path1".into()],
    );
    ws.write_ui_state(&partial).unwrap();
    s.handle_events(&w.poll(t));
    assert!(has(&s, Code::SelectionHandleUnknown));
    assert_eq!(s.selection().paths, [0]);
    std::fs::write(root.join("ui_state.json"), "{ not json").unwrap();
    s.handle_events(&w.poll(t));
    assert!(has(&s, Code::UiStateUnreadable));
}

// STUDIO-REQ-0160, STUDIO-REQ-0161, STUDIO-REQ-0002, STUDIO-REQ-0007: a commit is seen through
// the watcher, the new HEAD is loaded and installed, the focus follows the same path by its
// endpoint, and the shared selection stays pinned to its revision.
#[test]
fn live_update_follows_head() {
    let root = scratch("live");
    let ws = design_workspace(&root, false);
    let mut s = open(&root);
    s.select_path(0, false);
    let shared = std::fs::read(root.join("ui_state.json")).unwrap();
    let mut w = Watcher::new(&root, Duration::from_millis(20));
    let t = Instant::now();
    let rev4 = commit_timing(&ws, Rev(3), &Names::new(false), 0.2);
    assert!(w.poll(t).is_empty(), "debounced");
    let events = w.poll(t + Duration::from_millis(25));
    assert_eq!(events, [WatchEvent::Manifest]);
    let which = s.handle_events(&events).unwrap();
    assert_eq!(which, Which::Rev(rev4));
    assert_eq!(s.revisions()[0].rev, rev4);
    s.reload(which);
    assert_eq!(s.snapshot().rev, rev4);
    let d = s.detail().unwrap();
    assert_eq!(
        (d.row.endpoint.as_str(), d.row.corner_name.as_str()),
        ("g3/D", "ss")
    );
    assert!((d.row.slack_ns + 0.15).abs() < 1e-9, "{}", d.row.slack_ns);
    assert_eq!(s.selection().paths, [d.row.row]);
    assert!(has(&s, Code::RevisionChanged));
    assert_eq!(std::fs::read(root.join("ui_state.json")).unwrap(), shared);
    // A revert moves HEAD back; the workbench follows it.
    ws.revert(
        ws.resolve_base(&rev4.to_string()).unwrap(),
        Rev(2),
        &common::inv("revert"),
    )
    .unwrap();
    let events = w.poll(t + Duration::from_millis(100));
    let events = if events.is_empty() {
        w.poll(t + Duration::from_millis(200))
    } else {
        events
    };
    let which = s.handle_events(&events).unwrap();
    s.reload(which);
    assert_eq!(s.snapshot().rev, Rev(2));
    assert!(s.snapshot().timing.is_none());
    assert!(s.detail().is_none());
}

// STUDIO-REQ-0162, STUDIO-REQ-0161: a pinned revision ignores commits until the workbench
// follows HEAD again; a failed reload keeps the displayed revision.
#[test]
fn pinned_revision_does_not_follow() {
    let root = scratch("pinned");
    let ws = design_workspace(&root, false);
    let mut o = StudioOptions::new(&root);
    o.revision = Some(Rev(3));
    let mut s = Studio::open(o).unwrap();
    assert!(!s.follows_head());
    commit_timing(&ws, Rev(3), &Names::new(false), 0.1);
    assert_eq!(s.handle_events(&[WatchEvent::Manifest]), None);
    assert_eq!(s.revisions()[0].rev, Rev(4));
    s.reload(Which::Rev(Rev(99)));
    assert!(has(&s, Code::ReloadFailed));
    assert_eq!(s.snapshot().rev, Rev(3));
    let which = s.set_revision(None);
    assert_eq!(which, Which::Head);
    s.reload(which);
    assert_eq!(s.snapshot().rev, Rev(4));
    assert!(s.follows_head());
    assert!(s.handle_events(&[WatchEvent::ManifestLost]).is_none());
    assert!(has(&s, Code::WatchManifestLost));
}

// STUDIO-REQ-0004, STUDIO-REQ-0147: opening, browsing, selecting and reloading write nothing
// but `ui_state.json`, and only on selection.
#[test]
fn workspace_is_never_written() {
    let root = scratch("readonly");
    design_workspace(&root, true);
    let before = tree(&root);
    let mut s = open(&root);
    s.toggle_scope(1);
    s.search("add");
    s.focus_instance(0);
    s.set_cone_depths(4, 4);
    s.focus_path(1);
    s.reload(Which::Head);
    assert_eq!(tree(&root), before, "browsing wrote nothing");
    s.select_path(0, false);
    let after = tree(&root);
    let added: Vec<_> = after
        .iter()
        .filter(|f| !before.contains(f))
        .map(|f| f.0.file_name().unwrap().to_string_lossy().into_owned())
        .collect();
    assert_eq!(added, ["ui_state.json"]);
    assert_eq!(after.len(), before.len() + 1);
}

// STUDIO-REQ-0160, STUDIO-REQ-0002: commits made by another thread while the workbench polls
// are all followed, ending at the last HEAD.
#[test]
fn concurrent_commits_while_watching() {
    let root = scratch("concurrent");
    let ws = design_workspace(&root, false);
    let mut s = open(&root);
    let mut w = Watcher::new(&root, Duration::from_millis(5));
    let writer = {
        let ws = ws.clone();
        std::thread::spawn(move || {
            let names = Names::new(false);
            let mut base = Rev(3);
            for k in 0..5 {
                base = commit_timing(&ws, base, &names, 0.01 * f64::from(k));
                std::thread::sleep(Duration::from_millis(15));
            }
            base
        })
    };
    let deadline = Instant::now() + Duration::from_secs(20);
    let last = loop {
        if let Some(which) = s.handle_events(&w.poll(Instant::now())) {
            s.reload(which);
        }
        if writer.is_finished() {
            break writer.join().unwrap();
        }
        assert!(Instant::now() < deadline, "the writer did not finish");
        std::thread::sleep(Duration::from_millis(3));
    };
    let deadline = Instant::now() + Duration::from_secs(5);
    while s.snapshot().rev != last {
        if let Some(which) = s.handle_events(&w.poll(Instant::now())) {
            s.reload(which);
        }
        assert!(Instant::now() < deadline, "did not reach {last}");
        std::thread::sleep(Duration::from_millis(3));
    }
    assert!(
        !s.diagnostics()
            .iter()
            .any(|d| d.severity == Severity::Error),
        "{:?}",
        s.diagnostics()
    );
    let _ = Workspace::open(&root).unwrap();
}

// STUDIO-REQ-0080, STUDIO-REQ-0081: an instance cone follows the depths, which are clamped.
#[test]
fn instance_cone_depths() {
    let root = scratch("depths");
    design_workspace(&root, false);
    let mut s = open(&root);
    let g6 = s
        .snapshot()
        .netlist
        .as_ref()
        .unwrap()
        .find_instance("g6")
        .unwrap();
    s.focus_instance(g6);
    s.set_cone_depths(0, 0);
    assert_eq!(s.scene().unwrap().nodes.len(), 1);
    s.set_cone_depths(1, 1);
    let titles: Vec<String> = s
        .scene()
        .unwrap()
        .nodes
        .iter()
        .map(|n| n.title.clone())
        .collect();
    assert!(titles.contains(&"g1".to_string()) && titles.contains(&"g4".to_string()));
    s.set_cone_depths(99, 99);
    assert_eq!(s.cone_depths(), (16, 16));
}

// STUDIO-REQ-0222: the text of the panels names the revision, the corners with their scope,
// the paths, the cone and the RTL lines, and is the same for the same workspace.
#[test]
fn print_summary() {
    let root = scratch("print");
    design_workspace(&root, false);
    let s = open(&root);
    let text = print::summary(&s, 10);
    for want in [
        "revision rev3 (HEAD rev3, following)",
        "timing: violated",
        "hierarchy top_hier: 12 instances",
        "timing scope:",
        "wire_model=none",
        "corner tt: setup WNS -0.120 TNS -0.120 (1 violating), hold WNS 0.050",
        "corner ss: setup WNS -0.350",
        "path1   setup    ss     slack    -0.350  levels   5  g0/CK -> g3/D",
        "cone of g3:",
        "rtl of g3:",
        "src_1_counter.sv:6 [declaration]",
        "shared selection: none",
    ] {
        assert!(text.contains(want), "missing {want:?} in\n{text}");
    }
    assert_eq!(text, print::summary(&open(&root), 10));
}

// STUDIO-REQ-0203: the session keeps its newest diagnostics, at most `MAX_NOTES`, and reports
// them with those of the revision, the cone and the cross-probe.
#[test]
fn session_diagnostics_are_bounded() {
    let root = scratch("notes");
    design_workspace(&root, false);
    let mut s = open(&root);
    for k in 0..(rusteda_studio::model::studio::MAX_NOTES + 5) {
        s.reload(Which::Rev(Rev(1000 + k as u32)));
    }
    assert_eq!(s.notes().len(), rusteda_studio::model::studio::MAX_NOTES);
    let last = s.notes().last().unwrap();
    assert_eq!(last.code, Code::ReloadFailed);
    assert!(last.message.contains("rev1204"), "{}", last.message);
    let all = s.diagnostics();
    assert!(all.len() > s.notes().len());
    assert!(all.iter().any(|d| d.code == Code::CrossProbeApproximate));
}

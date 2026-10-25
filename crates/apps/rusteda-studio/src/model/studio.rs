// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The workbench state and its commands, without any graphics (`docs/specs/studio/README.md`).
//!
//! [`Studio`] holds the displayed revision ([`Snapshot`]), what the user expanded, searched,
//! filtered and selected, and the views derived from the focus: the path detail, the cone
//! scene and the RTL cross-probe. The graphical front end renders these views and calls the
//! commands; tests and the `--print` mode call them directly. Loading a revision is a separate,
//! pure function ([`snapshot::load`]), so the front end can run it off the render thread and
//! [`Studio::install`] its result (STUDIO-REQ-0007).

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use rusteda_workspace::sta::CheckKind;
use rusteda_workspace::{Rev, UiState, Workspace};

use super::budget::Budgets;
use super::cone::{ConeRequest, ConeRoot};
use super::diag::{Code, StudioDiag};
use super::hierarchy::{ScopeId, SearchResult, SplitMode, TreeItem, TreeRow};
use super::layout::LayoutParams;
use super::netlist::NetRef;
use super::rtl::{self, Candidate, CrossProbe};
use super::scene::{self, ConeScene};
use super::selection::{self, Resolution, Selection};
use super::snapshot::{self, RevisionInfo, Snapshot, Which};
use super::timing::{PathDetail, PathFilter, PathRow};
use super::watch::WatchEvent;

/// How the workbench is started (STUDIO-REQ-0220).
#[derive(Debug, Clone)]
pub struct StudioOptions {
    /// The workspace directory.
    pub workspace: PathBuf,
    /// A revision to display; `None` follows HEAD.
    pub revision: Option<Rev>,
    /// The budgets.
    pub budgets: Budgets,
    /// How hierarchy names are split.
    pub split: SplitMode,
    /// The debounce interval of the watcher.
    pub debounce: Duration,
    /// The poll interval of the watcher.
    pub poll: Duration,
    /// The cone drawing sizes.
    pub layout: LayoutParams,
    /// Log watcher events, reloads and shared selections on standard error.
    pub verbose: bool,
}

impl StudioOptions {
    /// The options for `workspace` with the default budgets, following HEAD.
    pub fn new(workspace: impl Into<PathBuf>) -> StudioOptions {
        StudioOptions {
            workspace: workspace.into(),
            revision: None,
            budgets: Budgets::default(),
            split: SplitMode::default(),
            debounce: Duration::from_millis(150),
            poll: Duration::from_millis(250),
            layout: LayoutParams::default(),
            verbose: false,
        }
    }

    /// The revision to load: the pinned one or HEAD.
    pub fn which(&self) -> Which {
        self.revision.map_or(Which::Head, Which::Rev)
    }
}

/// The session diagnostics kept (STUDIO-REQ-0203).
pub const MAX_NOTES: usize = 200;

/// What the detail panels are about.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Focus {
    /// A ranked path, by row.
    Path(usize),
    /// An instance, by row.
    Instance(u32),
}

/// The source pane: one file of the RTL snapshot with highlighted lines (STUDIO-REQ-0126).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceView {
    /// The file index in the RTL index.
    pub file: usize,
    /// Its path in the snapshot.
    pub path: String,
    /// The line to scroll to.
    pub focus_line: u32,
    /// The lines with hits in this file, sorted.
    pub highlighted: Vec<u32>,
}

/// The workbench (STUDIO-REQ-0007).
#[derive(Debug)]
pub struct Studio {
    ws: Workspace,
    opts: StudioOptions,
    snapshot: Arc<Snapshot>,
    revisions: Vec<RevisionInfo>,
    expanded: BTreeSet<ScopeId>,
    rows: Arc<[TreeRow]>,
    search: Option<SearchResult>,
    filter: PathFilter,
    selection: Selection,
    focus: Option<Focus>,
    detail: Option<PathDetail>,
    scene: Option<ConeScene>,
    probe: Option<CrossProbe>,
    source: Option<SourceView>,
    shared: Option<UiState>,
    last_number: u32,
    notes: Vec<StudioDiag>,
    fanin: u32,
    fanout: u32,
}

impl Studio {
    /// Opens the workspace of `opts` and loads its revision (STUDIO-REQ-0005); a selection in
    /// `ui_state.json` of that revision is shown (STUDIO-REQ-0145), otherwise the worst path is
    /// focused without being shared.
    pub fn open(opts: StudioOptions) -> Result<Studio, StudioDiag> {
        let ws = Workspace::open(&opts.workspace).map_err(|e| {
            StudioDiag::new(
                Code::WorkspaceUnreadable,
                format!("{}: {e}", opts.workspace.display()),
            )
        })?;
        let snap = snapshot::load(&ws, opts.which(), &opts.budgets, opts.split)?;
        let revisions = snapshot::revisions(&ws, opts.budgets.max_revisions);
        let fanin = opts.budgets.cone_fanin_depth;
        let fanout = opts.budgets.cone_fanout_depth;
        let mut s = Studio {
            ws,
            opts,
            snapshot: Arc::new(snap),
            revisions,
            expanded: BTreeSet::new(),
            rows: Arc::from(Vec::new()),
            search: None,
            filter: PathFilter::default(),
            selection: Selection::default(),
            focus: None,
            detail: None,
            scene: None,
            probe: None,
            source: None,
            shared: None,
            last_number: 0,
            notes: Vec::new(),
            fanin,
            fanout,
        };
        s.expanded.insert(0);
        s.refresh_rows();
        s.read_shared();
        if s.focus.is_none() {
            if let Some(first) = s.snapshot.timing.as_ref().and_then(|t| t.rows().first()) {
                let row = first.row;
                s.focus_path(row);
            }
        }
        Ok(s)
    }

    /// The workspace.
    pub fn workspace(&self) -> &Workspace {
        &self.ws
    }

    /// The options.
    pub fn options(&self) -> &StudioOptions {
        &self.opts
    }

    /// The displayed revision.
    pub fn snapshot(&self) -> &Arc<Snapshot> {
        &self.snapshot
    }

    /// Whether the workbench follows HEAD.
    pub fn follows_head(&self) -> bool {
        self.opts.revision.is_none()
    }

    /// The newest revisions (STUDIO-REQ-0006).
    pub fn revisions(&self) -> &[RevisionInfo] {
        &self.revisions
    }

    // ----- hierarchy -----

    fn refresh_rows(&mut self) {
        self.rows = Arc::from(
            self.snapshot
                .hierarchy
                .as_ref()
                .map(|h| h.rows(&self.expanded))
                .unwrap_or_default(),
        );
    }

    /// The visible rows of the hierarchy tree (STUDIO-REQ-0033).
    pub fn tree_rows(&self) -> &[TreeRow] {
        &self.rows
    }

    /// The rows of the tree, shared without copying with a panel that draws some of them
    /// (STUDIO-REQ-0165).
    pub fn tree_rows_shared(&self) -> Arc<[TreeRow]> {
        Arc::clone(&self.rows)
    }

    /// Whether scope `id` is open.
    pub fn is_expanded(&self, id: ScopeId) -> bool {
        self.expanded.contains(&id)
    }

    /// Opens or closes scope `id` (STUDIO-REQ-0033).
    pub fn toggle_scope(&mut self, id: ScopeId) {
        if !self.expanded.remove(&id) {
            self.expanded.insert(id);
        }
        self.refresh_rows();
    }

    /// The label and detail text of a tree item: a scope with its instance count, an
    /// instance with its cell (STUDIO-REQ-0032).
    pub fn tree_text(&self, item: TreeItem) -> (String, String) {
        let (Some(h), Some(nl)) = (&self.snapshot.hierarchy, &self.snapshot.netlist) else {
            return (String::new(), String::new());
        };
        match item {
            TreeItem::Scope(s) => {
                let scope = h.scope(s);
                (
                    scope.map(|x| x.name.clone()).unwrap_or_default(),
                    scope.map_or(String::new(), |x| {
                        format!("{} inst, {} sub", x.total_instances, x.children.len())
                    }),
                )
            }
            TreeItem::Instance(r) => (h.leaf_text(nl, r), nl.instance_cell(r).to_string()),
        }
    }

    /// Opens the scopes that enclose instance `row`, so the tree shows it.
    pub fn reveal_instance(&mut self, row: u32) {
        if let Some(h) = &self.snapshot.hierarchy {
            for s in h.ancestors(row) {
                self.expanded.insert(s);
            }
        }
        self.refresh_rows();
    }

    /// Searches instance and scope names (STUDIO-REQ-0034).
    pub fn search(&mut self, query: &str) {
        self.search = match (&self.snapshot.hierarchy, &self.snapshot.netlist) {
            (Some(h), Some(nl)) if !query.trim().is_empty() => {
                Some(h.search(nl, query, self.opts.budgets.max_search_results))
            }
            _ => None,
        };
    }

    /// The last search.
    pub fn search_result(&self) -> Option<&SearchResult> {
        self.search.as_ref()
    }

    // ----- paths -----

    /// The path filter.
    pub fn filter(&self) -> &PathFilter {
        &self.filter
    }

    /// Sets the path filter (STUDIO-REQ-0054).
    pub fn set_filter(&mut self, f: PathFilter) {
        self.filter = f;
    }

    /// The rows of the path table that pass the filter.
    pub fn visible_paths(&self) -> Vec<&PathRow> {
        self.snapshot
            .timing
            .as_ref()
            .map(|t| t.filtered(&self.filter))
            .unwrap_or_default()
    }

    // ----- selection -----

    /// The selection.
    pub fn selection(&self) -> &Selection {
        &self.selection
    }

    /// The selection last written or read in `ui_state.json`.
    pub fn shared(&self) -> Option<&UiState> {
        self.shared.as_ref()
    }

    /// Selects ranked path `row` (alone or toggled) and shares the selection
    /// (STUDIO-REQ-0141, STUDIO-REQ-0142).
    pub fn select_path(&mut self, row: usize, additive: bool) {
        self.selection.pick_path(row, additive);
        self.focus_path(row);
        self.share();
    }

    /// Selects instance `row` (alone or toggled) and shares the selection.
    pub fn select_instance(&mut self, row: u32, additive: bool) {
        self.selection.pick_instance(row, additive);
        self.focus_instance(row);
        self.share();
    }

    /// Replaces the selection with `sel`, focuses its first path (or instance) and shares it
    /// once (STUDIO-REQ-0141, STUDIO-REQ-0223).
    pub fn select(&mut self, sel: Selection) {
        self.selection = sel;
        if let Some(&p) = self.selection.paths.first() {
            self.focus_path(p);
        } else if let Some(&i) = self.selection.instances.first() {
            self.focus_instance(i);
        }
        self.share();
    }

    /// Writes the selection as `sel<N>` of the displayed revision (STUDIO-REQ-0142,
    /// STUDIO-REQ-0143); a failure is a `U0042` diagnostic.
    fn share(&mut self) {
        let current = self.ws.ui_state().ok().flatten();
        let n = selection::next_number(current.as_ref(), self.last_number);
        let result = selection::to_ui_state(self.snapshot.rev, n, &self.selection)
            .map_err(|e| e.to_string())
            .and_then(|state| {
                self.ws
                    .write_ui_state(&state)
                    .map(|()| state)
                    .map_err(|e| e.to_string())
            });
        match result {
            Ok(state) => {
                self.last_number = n;
                self.shared = Some(state);
            }
            Err(e) => self.note(StudioDiag::new(
                Code::UiStateWriteFailed,
                format!("ui_state.json: {e}"),
            )),
        }
    }

    /// Reads `ui_state.json` and shows its selection when it belongs to the displayed
    /// revision (STUDIO-REQ-0144, STUDIO-REQ-0145); returns whether it changed.
    fn read_shared(&mut self) -> bool {
        let state = match self.ws.ui_state() {
            Ok(s) => s,
            Err(e) => {
                self.note(StudioDiag::new(
                    Code::UiStateUnreadable,
                    format!("ui_state.json: {e}"),
                ));
                return false;
            }
        };
        if state == self.shared {
            return false;
        }
        self.shared = state.clone();
        let Some(state) = state else {
            return true;
        };
        if let Ok(n) = rusteda_workspace::Handle::parse_as(&state.selection_handle, "sel") {
            self.last_number = self.last_number.max(n);
        }
        let instances = self
            .snapshot
            .netlist
            .as_ref()
            .map_or(0, |n| n.instance_count());
        let paths = self.snapshot.timing.as_ref().map_or(0, |t| t.total_paths());
        let sel = match selection::resolve(&state, self.snapshot.rev, instances, paths) {
            Resolution::Applied(sel) => sel,
            Resolution::Partial(sel, unknown) => {
                self.note(StudioDiag::new(
                    Code::SelectionHandleUnknown,
                    format!(
                        "{} of {} names nothing here: {}",
                        state.selection_handle,
                        state.revision,
                        unknown.join(", ")
                    ),
                ));
                sel
            }
            Resolution::OtherRevision(rev) => {
                self.note(StudioDiag::new(
                    Code::SelectionOtherRevision,
                    format!(
                        "{} belongs to {rev}; the workbench shows {}",
                        state.selection_handle, self.snapshot.rev
                    ),
                ));
                return true;
            }
        };
        self.selection = sel;
        if let Some(&p) = self.selection.paths.first() {
            self.focus_path(p);
        } else if let Some(&i) = self.selection.instances.first() {
            self.focus_instance(i);
        }
        true
    }

    // ----- focus and derived views -----

    /// What the detail panels are about.
    pub fn focus(&self) -> Option<Focus> {
        self.focus
    }

    /// The detail of the focused path.
    pub fn detail(&self) -> Option<&PathDetail> {
        self.detail.as_ref()
    }

    /// The cone of the focus.
    pub fn scene(&self) -> Option<&ConeScene> {
        self.scene.as_ref()
    }

    /// The cross-probe of the focus.
    pub fn probe(&self) -> Option<&CrossProbe> {
        self.probe.as_ref()
    }

    /// The source pane.
    pub fn source(&self) -> Option<&SourceView> {
        self.source.as_ref()
    }

    /// The cone depths (fan-in, fan-out).
    pub fn cone_depths(&self) -> (u32, u32) {
        (self.fanin, self.fanout)
    }

    /// Changes the cone depths, within 0 to 16 levels, and redraws the cone (STUDIO-REQ-0081).
    pub fn set_cone_depths(&mut self, fanin: u32, fanout: u32) {
        self.fanin = fanin.min(16);
        self.fanout = fanout.min(16);
        self.rebuild_scene();
    }

    /// Focuses ranked path `row`: its detail, the fan-in cone of its endpoint with the path's
    /// instances pinned and highlighted, and the RTL of its endpoint (STUDIO-REQ-0053,
    /// STUDIO-REQ-0096). The selection is not changed.
    pub fn focus_path(&mut self, row: usize) {
        self.focus = Some(Focus::Path(row));
        self.detail = self
            .snapshot
            .timing
            .as_ref()
            .and_then(|t| t.detail(row, self.snapshot.netlist.as_ref()).ok());
        self.rebuild_scene();
        let subject = self.detail.as_ref().and_then(|d| {
            d.points
                .iter()
                .rev()
                .find_map(|p| p.instance_row)
                .map(|r| (r, d.row.endpoint.clone()))
        });
        match subject {
            Some((r, _)) => self.probe_instance(r),
            None => {
                let end = self.detail.as_ref().map(|d| d.row.endpoint.clone());
                self.probe_names(end.as_deref().unwrap_or(""), &[]);
            }
        }
    }

    /// Focuses instance `row`: its cone and its RTL, and reveals it in the tree
    /// (STUDIO-REQ-0080, STUDIO-REQ-0122).
    pub fn focus_instance(&mut self, row: u32) {
        self.focus = Some(Focus::Instance(row));
        self.detail = None;
        self.reveal_instance(row);
        self.rebuild_scene();
        self.probe_instance(row);
    }

    fn rebuild_scene(&mut self) {
        let Some(nl) = self.snapshot.netlist.as_ref() else {
            self.scene = None;
            return;
        };
        let b = &self.opts.budgets;
        let (root, pinned, fanin) = match self.focus {
            None => {
                self.scene = None;
                return;
            }
            Some(Focus::Instance(r)) => (ConeRoot::Instance(r), Vec::new(), self.fanin),
            Some(Focus::Path(_)) => {
                let Some(d) = &self.detail else {
                    self.scene = None;
                    return;
                };
                let pinned: Vec<u32> = d.points.iter().filter_map(|p| p.instance_row).collect();
                let mut pinned_unique = Vec::new();
                for r in pinned {
                    if !pinned_unique.contains(&r) {
                        pinned_unique.push(r);
                    }
                }
                // The endpoint's pin: the last point on an instance.
                let root = d
                    .points
                    .iter()
                    .rev()
                    .find_map(|p| {
                        let r = p.instance_row?;
                        let pin = p.name.rsplit('/').next()?;
                        nl.find_pin(r, pin).map(ConeRoot::Pin)
                    })
                    .or_else(|| {
                        let end = &d.row.endpoint;
                        scene::find_port_bit(nl, end)
                            .map(|(port, bit)| ConeRoot::Port { port, bit })
                    });
                let Some(root) = root else {
                    self.scene = None;
                    return;
                };
                (root, pinned_unique, self.fanin.max(1))
            }
        };
        let req = ConeRequest {
            root,
            fanin_depth: fanin,
            fanout_depth: self.fanout,
            max_nodes: b.cone_max_nodes,
            max_edges: b.cone_max_edges,
            max_net_fanout: b.cone_max_net_fanout,
            max_visits: b.cone_max_visits,
            pinned,
        };
        let params = LayoutParams {
            sweeps: b.layout_sweeps,
            max_virtual: b.layout_max_virtual,
            ..self.opts.layout
        };
        match scene::build(
            nl,
            &req,
            self.detail.as_ref(),
            &self.selection.instances,
            params,
        ) {
            Ok(s) => self.scene = Some(s),
            Err(d) => {
                self.scene = None;
                self.note(d);
            }
        }
    }

    /// The cross-probe candidates of an instance: its name, then the names of the nets on its
    /// output pins, then those on its input pins (STUDIO-REQ-0124).
    fn probe_instance(&mut self, row: u32) {
        let Some(nl) = self.snapshot.netlist.as_ref() else {
            return;
        };
        let name = nl.instance_name(row).to_string();
        let mut cands = vec![Candidate {
            name: name.clone(),
            role: "instance",
            weight: 100,
        }];
        let pins = nl.instance_pins(row);
        for out in [true, false] {
            for p in pins.iter().filter(|p| p.dir.drives() == out) {
                if let NetRef::Net(n) = p.net {
                    if nl.net_name(n).is_some() && cands.len() < 16 {
                        cands.push(Candidate {
                            name: nl.net_name(n).map(|x| x.0.to_string()).unwrap_or_default(),
                            role: if out { "output net" } else { "input net" },
                            weight: if out { 80 } else { 40 },
                        });
                    }
                }
            }
        }
        let cands: Vec<Candidate> = cands;
        self.probe_names(&name, &cands);
    }

    /// Probes the RTL for `candidates` (or for `subject` itself without candidates).
    fn probe_names(&mut self, subject: &str, candidates: &[Candidate]) {
        let Some(index) = self.snapshot.rtl.as_ref() else {
            self.probe = None;
            self.source = None;
            return;
        };
        let own = [Candidate {
            name: subject.to_string(),
            role: "port",
            weight: 100,
        }];
        let cands = if candidates.is_empty() {
            &own[..]
        } else {
            candidates
        };
        let top = self
            .snapshot
            .netlist
            .as_ref()
            .map_or("", |n| n.module_name())
            .to_string();
        let p = rtl::probe(index, subject, cands, &top, self.opts.budgets.rtl_max_hits);
        self.source = p.hits.first().map(|h| SourceView {
            file: h.file,
            path: index.files[h.file].path.clone(),
            focus_line: h.line,
            highlighted: {
                let mut v: Vec<u32> = p
                    .hits
                    .iter()
                    .filter(|x| x.file == h.file)
                    .map(|x| x.line)
                    .collect();
                v.sort_unstable();
                v.dedup();
                v
            },
        });
        self.probe = Some(p);
    }

    /// Cross-probes instance `row` without changing the focus, as a click on a point of the
    /// path detail does (STUDIO-REQ-0122).
    pub fn cross_probe_instance(&mut self, row: u32) {
        if self
            .snapshot
            .netlist
            .as_ref()
            .is_some_and(|n| (row as usize) < n.instance_count())
        {
            self.probe_instance(row);
        }
    }

    /// Shows hit `k` of the cross-probe in the source pane (STUDIO-REQ-0126).
    pub fn show_hit(&mut self, k: usize) {
        let (Some(p), Some(index)) = (&self.probe, &self.snapshot.rtl) else {
            return;
        };
        let Some(h) = p.hits.get(k) else {
            return;
        };
        let mut lines: Vec<u32> = p
            .hits
            .iter()
            .filter(|x| x.file == h.file)
            .map(|x| x.line)
            .collect();
        lines.sort_unstable();
        lines.dedup();
        self.source = Some(SourceView {
            file: h.file,
            path: index.files[h.file].path.clone(),
            focus_line: h.line,
            highlighted: lines,
        });
    }

    // ----- live update -----

    /// Reacts to watcher events (STUDIO-REQ-0160, STUDIO-REQ-0146): a changed manifest makes
    /// HEAD's revision the one to load when following HEAD; a changed `ui_state.json` is read
    /// and shown. Returns the revision to load, if any.
    pub fn handle_events(&mut self, events: &[WatchEvent]) -> Option<Which> {
        let mut reload = None;
        for e in events {
            match e {
                WatchEvent::Manifest => match self.ws.manifest() {
                    Ok(m) => {
                        self.revisions =
                            snapshot::revisions(&self.ws, self.opts.budgets.max_revisions);
                        if self.follows_head() && m.head != self.snapshot.rev {
                            reload = Some(Which::Rev(m.head));
                        }
                    }
                    Err(e) => self.note(StudioDiag::new(
                        Code::WatchManifestLost,
                        format!("manifest.json: {e}"),
                    )),
                },
                WatchEvent::ManifestLost => self.note(StudioDiag::new(
                    Code::WatchManifestLost,
                    "manifest.json disappeared; the displayed revision stays",
                )),
                WatchEvent::UiState => {
                    self.read_shared();
                }
            }
        }
        reload
    }

    /// Shows `next` instead of the displayed revision (STUDIO-REQ-0161): the expanded scopes,
    /// the filter and the depths stay; the selection and the focus move to the objects of the
    /// same names (instances by name, paths by endpoint, corner and check); `ui_state.json` is
    /// not rewritten.
    pub fn install(&mut self, next: Snapshot) {
        let old = Arc::clone(&self.snapshot);
        let inst_name = |r: u32| old.netlist.as_ref().map(|n| n.instance_name(r).to_string());
        let path_key = |r: usize| {
            old.timing
                .as_ref()
                .and_then(|t| t.row(r))
                .map(|p| (p.endpoint.clone(), p.corner_name.clone(), p.kind))
        };
        let map_inst = |r: u32, snap: &Snapshot| {
            inst_name(r).and_then(|n| snap.netlist.as_ref().and_then(|nl| nl.find_instance(&n)))
        };
        let map_path = |r: usize, snap: &Snapshot| -> Option<usize> {
            let (end, corner, kind): (String, String, CheckKind) = path_key(r)?;
            snap.timing.as_ref().and_then(|t| {
                t.rows()
                    .iter()
                    .find(|p| p.endpoint == end && p.corner_name == corner && p.kind == kind)
                    .map(|p| p.row)
            })
        };
        let sel = Selection {
            paths: self
                .selection
                .paths
                .iter()
                .filter_map(|&r| map_path(r, &next))
                .collect(),
            instances: self
                .selection
                .instances
                .iter()
                .filter_map(|&r| map_inst(r, &next))
                .collect(),
        };
        let focus = match self.focus {
            Some(Focus::Path(r)) => map_path(r, &next).map(Focus::Path),
            Some(Focus::Instance(r)) => map_inst(r, &next).map(Focus::Instance),
            None => None,
        };
        // Scopes are kept by path, since scope ids are per hierarchy.
        let open: Vec<String> = match &old.hierarchy {
            Some(h) => self.expanded.iter().map(|&s| h.scope_path(s)).collect(),
            None => Vec::new(),
        };
        self.note(StudioDiag::new(
            Code::RevisionChanged,
            format!("now showing {} (was {})", next.rev, old.rev),
        ));
        self.snapshot = Arc::new(next);
        self.expanded = BTreeSet::from([0]);
        if let Some(h) = &self.snapshot.hierarchy {
            for (k, _) in h.scopes().iter().enumerate() {
                if open.contains(&h.scope_path(k as ScopeId)) {
                    self.expanded.insert(k as ScopeId);
                }
            }
        }
        self.selection = sel;
        self.refresh_rows();
        let query = self.search.take().map(|r| r.query);
        if let Some(q) = query {
            self.search(&q);
        }
        match focus.or_else(|| {
            self.snapshot
                .timing
                .as_ref()
                .and_then(|t| t.rows().first())
                .map(|p| Focus::Path(p.row))
        }) {
            Some(Focus::Path(r)) => self.focus_path(r),
            Some(Focus::Instance(r)) => self.focus_instance(r),
            None => {
                self.focus = None;
                self.detail = None;
                self.scene = None;
                self.probe = None;
                self.source = None;
            }
        }
    }

    /// Pins revision `rev`, or follows HEAD with `None`; returns the revision to load
    /// (STUDIO-REQ-0162).
    pub fn set_revision(&mut self, rev: Option<Rev>) -> Which {
        self.opts.revision = rev;
        self.opts.which()
    }

    /// Loads `which` and installs it (the synchronous form of a reload, for tests and the
    /// `--print` mode); a failure is a `U0081` diagnostic and the displayed revision stays.
    pub fn reload(&mut self, which: Which) {
        match snapshot::load(&self.ws, which, &self.opts.budgets, self.opts.split) {
            Ok(s) => self.install(s),
            Err(d) => self.note(StudioDiag::new(
                Code::ReloadFailed,
                format!("{} ({})", d.message, d.code),
            )),
        }
    }

    /// A reload failure reported by the front end (STUDIO-REQ-0161).
    pub fn reload_failed(&mut self, d: StudioDiag) {
        self.note(StudioDiag::new(
            Code::ReloadFailed,
            format!("{} ({})", d.message, d.code),
        ));
    }

    // ----- diagnostics -----

    /// Adds a session diagnostic, keeping the newest [`MAX_NOTES`].
    fn note(&mut self, d: StudioDiag) {
        if self.notes.len() >= MAX_NOTES {
            self.notes.remove(0);
        }
        self.notes.push(d);
    }

    /// Every diagnostic of the session, the newest last: the displayed revision's, the
    /// cone's, the cross-probe's, the search's and the session's (STUDIO-REQ-0203).
    pub fn diagnostics(&self) -> Vec<StudioDiag> {
        let mut out = self.snapshot.diagnostics.clone();
        if let Some(s) = &self.scene {
            out.extend(s.diagnostics.iter().cloned());
        }
        if let Some(p) = &self.probe {
            out.extend(p.diagnostics());
        }
        if let Some(r) = &self.search {
            out.extend(r.diagnostics());
        }
        out.extend(self.notes.iter().cloned());
        out
    }

    /// The session diagnostics only (watcher, selection, reloads).
    pub fn notes(&self) -> &[StudioDiag] {
        &self.notes
    }
}

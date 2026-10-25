// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The netlist hierarchy explorer (`docs/specs/studio/hierarchy.md`).
//!
//! The netlist of a revision is flat: hierarchy survives only in the names of its instances. The
//! explorer splits every instance name into levels ([`split`]) and builds a tree of scopes with
//! their instances and counts ([`Hierarchy`]). The core naming rules separate the segments of a
//! hierarchical name by unescaped `/` (CORE-REQ-0111, CORE-REQ-0112); flattening flows also leave
//! `.` (the RTL hierarchy joined by the synthesis tool of the suite) and `/` inside escaped
//! identifiers. [`SplitMode::Flattened`], the default, splits at all three outside brackets, so
//! that `u_core.gen_lane[2].u_add/g7` gives `u_core`, `gen_lane[2]`, `u_add`, `g7`.
//!
//! The tree is built in time linear in the total length of the names; its depth is bounded
//! (`max_hierarchy_depth`, deeper parts are folded into the leaf), searches stop after
//! `max_search_results` matches, and the rows a panel shows are computed for the scopes the
//! user expanded only (STUDIO-REQ-0030 to STUDIO-REQ-0035).

use std::cmp::Ordering;
use std::collections::{BTreeSet, HashMap};

use rusteda_core::job::{Cancelled, Ctx};
use rusteda_core::names;

use super::diag::{Code, StudioDiag};
use super::netlist::NetlistView;

/// How instance names are split into levels (STUDIO-REQ-0030).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SplitMode {
    /// Only at unescaped `/`, the separator of the core naming rules (CORE-REQ-0111).
    Core,
    /// Also at `.` and at escaped `\/` outside brackets: the separators that flattening leaves.
    #[default]
    Flattened,
}

/// The levels of a hierarchical name: byte ranges of `name` (escaped text), outermost first;
/// the last one is the leaf (STUDIO-REQ-0030). A name without separators is one level, and no
/// level is empty: a separator at the start, at the end or next to another one is kept as text.
pub fn split(name: &str, mode: SplitMode) -> Vec<(usize, usize)> {
    let b = name.as_bytes();
    let mut cuts: Vec<(usize, usize)> = Vec::new(); // (separator start, separator end)
    let mut depth = 0usize; // bracket nesting
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'\\' => {
                // An escape: `\/` or `\\` (CORE-REQ-0112); the escaped slash is a raw `/`.
                let next = b.get(i + 1).copied();
                if mode == SplitMode::Flattened && depth == 0 && next == Some(b'/') {
                    cuts.push((i, i + 2));
                }
                i += if next.is_some() { 2 } else { 1 };
                continue;
            }
            b'[' => depth += 1,
            b']' => depth = depth.saturating_sub(1),
            b'/' => cuts.push((i, i + 1)),
            b'.' if mode == SplitMode::Flattened && depth == 0 => cuts.push((i, i + 1)),
            _ => {}
        }
        i += 1;
    }
    let mut out = Vec::with_capacity(cuts.len() + 1);
    let mut start = 0;
    for (s, e) in cuts {
        if s > start && e < b.len() {
            out.push((start, s));
            start = e;
        }
    }
    out.push((start, b.len()));
    out
}

/// The display text of a level: its escapes undone (CORE-REQ-0112).
pub fn level_text(name: &str, range: (usize, usize)) -> String {
    names::unescape(name.get(range.0..range.1).unwrap_or(""))
}

/// Orders texts with their digit runs compared as numbers (`u2` before `u10`), then bytewise.
pub fn natural_cmp(a: &str, b: &str) -> Ordering {
    let (x, y) = (a.as_bytes(), b.as_bytes());
    let (mut i, mut j) = (0, 0);
    while i < x.len() && j < y.len() {
        if x[i].is_ascii_digit() && y[j].is_ascii_digit() {
            let si = i;
            while i < x.len() && x[i].is_ascii_digit() {
                i += 1;
            }
            let sj = j;
            while j < y.len() && y[j].is_ascii_digit() {
                j += 1;
            }
            let (dx, dy) = (
                a[si..i].trim_start_matches('0'),
                b[sj..j].trim_start_matches('0'),
            );
            let o = dx.len().cmp(&dy.len()).then_with(|| dx.cmp(dy));
            if o != Ordering::Equal {
                return o;
            }
        } else {
            if x[i] != y[j] {
                return x[i].cmp(&y[j]);
            }
            i += 1;
            j += 1;
        }
    }
    (x.len() - i).cmp(&(y.len() - j)).then_with(|| a.cmp(b))
}

/// A scope of the hierarchy, by index; scope 0 is the module (STUDIO-REQ-0031).
pub type ScopeId = u32;

/// A scope: a level that instances share (STUDIO-REQ-0031).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scope {
    /// Its level text; the module name for scope 0.
    pub name: String,
    /// The enclosing scope; `None` for scope 0.
    pub parent: Option<ScopeId>,
    /// Levels below the module (0 for the module).
    pub depth: u32,
    /// Child scopes in natural order of their names.
    pub children: Vec<ScopeId>,
    /// Instances directly in the scope (rows), in natural order of their leaf text.
    pub instances: Vec<u32>,
    /// Instances in the scope and below it.
    pub total_instances: u64,
}

/// One row of the tree panel (STUDIO-REQ-0033).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TreeItem {
    /// A scope.
    Scope(ScopeId),
    /// An instance, by row.
    Instance(u32),
}

/// A visible row: the item and its indentation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TreeRow {
    /// What the row shows.
    pub item: TreeItem,
    /// Its indentation level.
    pub depth: u32,
}

/// Counts of one hierarchy level (STUDIO-REQ-0032).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct LevelCount {
    /// Scopes at this level.
    pub scopes: u64,
    /// Instances whose leaf is at this level.
    pub instances: u64,
}

/// The matches of a search (STUDIO-REQ-0034).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct SearchResult {
    /// The query.
    pub query: String,
    /// Matching instance rows, in row order, at most the budget.
    pub instances: Vec<u32>,
    /// Matching scopes, in index order, at most the budget.
    pub scopes: Vec<ScopeId>,
    /// All matches, listed or not.
    pub total: u64,
    /// Whether matches were left out.
    pub truncated: bool,
}

impl SearchResult {
    /// `U0061` when the budget left matches out (STUDIO-REQ-0034).
    pub fn diagnostics(&self) -> Vec<StudioDiag> {
        if !self.truncated {
            return Vec::new();
        }
        vec![StudioDiag::new(
            Code::SearchTruncated,
            format!(
                "the search for '{}' lists {} instances and {} scopes of {} matches",
                self.query,
                self.instances.len(),
                self.scopes.len(),
                self.total
            ),
        )]
    }
}

/// The hierarchy of a netlist (STUDIO-REQ-0031).
#[derive(Debug, Clone)]
pub struct Hierarchy {
    mode: SplitMode,
    scopes: Vec<Scope>,
    /// Per instance: its scope.
    inst_scope: Vec<ScopeId>,
    /// Per instance: the byte offset of its leaf in its (escaped) name.
    leaf_start: Vec<u32>,
    /// Instances whose names were deeper than the budget.
    folded: u64,
}

impl Hierarchy {
    /// Builds the hierarchy of `netlist`, folding names deeper than `max_depth` levels into
    /// their leaf (STUDIO-REQ-0031, STUDIO-REQ-0035).
    pub fn build(netlist: &NetlistView, mode: SplitMode, max_depth: usize) -> Hierarchy {
        // Without a cancel token there is no checkpoint that can fail.
        Hierarchy::build_with(netlist, mode, max_depth, &Ctx::NONE).unwrap_or_else(|Cancelled| {
            Hierarchy {
                mode,
                scopes: Vec::new(),
                inst_scope: Vec::new(),
                leaf_start: Vec::new(),
                folded: 0,
            }
        })
    }

    /// [`Hierarchy::build`] as a phase of a run: progress per instance and a checkpoint every
    /// 4096 instances, on the calling thread (JOB-REQ-0004, JOB-REQ-0005; STUDIO-REQ-0008).
    pub fn build_with(
        netlist: &NetlistView,
        mode: SplitMode,
        max_depth: usize,
        ctx: &Ctx<'_>,
    ) -> Result<Hierarchy, Cancelled> {
        let max_depth = max_depth.max(1);
        let n = netlist.instance_count();
        let mut scopes = vec![Scope {
            name: netlist.module_name().to_string(),
            parent: None,
            depth: 0,
            children: Vec::new(),
            instances: Vec::new(),
            total_instances: 0,
        }];
        let mut child_of: HashMap<(ScopeId, String), ScopeId> = HashMap::new();
        let mut inst_scope = Vec::with_capacity(n);
        let mut leaf_start = Vec::with_capacity(n);
        let mut folded = 0u64;
        for row in 0..n as u32 {
            if row % 4096 == 0 {
                ctx.checkpoint()?;
            }
            ctx.progress("hierarchy", u64::from(row) + 1, n as u64);
            let name = netlist.instance_name(row);
            let levels = split(name, mode);
            // The scopes are the levels before the leaf, at most `max_depth - 1` of them; the
            // leaf starts after the last scope used.
            let scope_levels = (levels.len() - 1).min(max_depth - 1);
            if levels.len() > max_depth {
                folded += 1;
            }
            let mut at: ScopeId = 0;
            for &range in &levels[..scope_levels] {
                let text = level_text(name, range);
                at = match child_of.get(&(at, text.clone())) {
                    Some(&s) => s,
                    None => {
                        let id = scopes.len() as ScopeId;
                        let depth = scopes[at as usize].depth + 1;
                        scopes.push(Scope {
                            name: text.clone(),
                            parent: Some(at),
                            depth,
                            children: Vec::new(),
                            instances: Vec::new(),
                            total_instances: 0,
                        });
                        scopes[at as usize].children.push(id);
                        child_of.insert((at, text), id);
                        id
                    }
                };
            }
            let leaf = levels[scope_levels].0;
            scopes[at as usize].instances.push(row);
            inst_scope.push(at);
            leaf_start.push(leaf as u32);
        }
        let mut h = Hierarchy {
            mode,
            scopes,
            inst_scope,
            leaf_start,
            folded,
        };
        ctx.checkpoint()?;
        h.finish(netlist);
        Ok(h)
    }

    /// Sorts children and instances in natural order and sums the subtree counts.
    fn finish(&mut self, netlist: &NetlistView) {
        let names: Vec<String> = self.scopes.iter().map(|s| s.name.clone()).collect();
        for k in 0..self.scopes.len() {
            let mut children = std::mem::take(&mut self.scopes[k].children);
            children.sort_by(|&a, &b| {
                natural_cmp(&names[a as usize], &names[b as usize]).then(a.cmp(&b))
            });
            self.scopes[k].children = children;
            let mut insts = std::mem::take(&mut self.scopes[k].instances);
            let leaf = |r: u32| {
                let name = netlist.instance_name(r);
                name.get(self.leaf_start[r as usize] as usize..)
                    .unwrap_or("")
            };
            insts.sort_by(|&a, &b| natural_cmp(leaf(a), leaf(b)).then(a.cmp(&b)));
            self.scopes[k].instances = insts;
        }
        // Children have larger indices than their parents: sum from the back.
        for k in (0..self.scopes.len()).rev() {
            let total = self.scopes[k].total_instances + self.scopes[k].instances.len() as u64;
            self.scopes[k].total_instances = total;
            if let Some(p) = self.scopes[k].parent {
                self.scopes[p as usize].total_instances += total;
            }
        }
    }

    /// The split mode.
    pub fn mode(&self) -> SplitMode {
        self.mode
    }

    /// The scopes; scope 0 is the module.
    pub fn scopes(&self) -> &[Scope] {
        &self.scopes
    }

    /// Scope `id`.
    pub fn scope(&self, id: ScopeId) -> Option<&Scope> {
        self.scopes.get(id as usize)
    }

    /// Instances whose names had more levels than the depth budget.
    pub fn folded(&self) -> u64 {
        self.folded
    }

    /// The diagnostic of folded names, if any (STUDIO-REQ-0035).
    pub fn diagnostics(&self, max_depth: usize) -> Vec<StudioDiag> {
        if self.folded == 0 {
            return Vec::new();
        }
        vec![StudioDiag::new(
            Code::HierarchyDepthFolded,
            format!(
                "{} instance names have more than {max_depth} hierarchy levels; their deeper \
                 levels are shown in the leaf",
                self.folded
            ),
        )]
    }

    /// The scope of instance `row`.
    pub fn instance_scope(&self, row: u32) -> Option<ScopeId> {
        self.inst_scope.get(row as usize).copied()
    }

    /// The leaf text of instance `row`: its name after its scopes, escapes undone.
    pub fn leaf_text(&self, netlist: &NetlistView, row: u32) -> String {
        let name = netlist.instance_name(row);
        let start = self.leaf_start.get(row as usize).copied().unwrap_or(0) as usize;
        names::unescape(name.get(start..).unwrap_or(name))
    }

    /// The path of scope `id` from the module, as level texts joined by `/` (the module is
    /// left out); empty for the module.
    pub fn scope_path(&self, id: ScopeId) -> String {
        let mut parts = Vec::new();
        let mut at = Some(id);
        while let Some(s) = at.and_then(|s| self.scopes.get(s as usize)) {
            if s.parent.is_some() {
                parts.push(s.name.as_str());
            }
            at = s.parent;
        }
        parts.reverse();
        parts.join("/")
    }

    /// The scopes enclosing instance `row`, from the module down to its own scope.
    pub fn ancestors(&self, row: u32) -> Vec<ScopeId> {
        let mut out = Vec::new();
        let mut at = self.instance_scope(row);
        while let Some(s) = at {
            out.push(s);
            at = self.scopes.get(s as usize).and_then(|x| x.parent);
        }
        out.reverse();
        out
    }

    /// Scopes and instances per level below the module (STUDIO-REQ-0032): entry `d` counts the
    /// scopes of depth `d + 1` and the instances directly in scopes of depth `d`.
    pub fn level_counts(&self) -> Vec<LevelCount> {
        let mut out: Vec<LevelCount> = Vec::new();
        for s in &self.scopes {
            let d = s.depth as usize;
            if out.len() <= d {
                out.resize(d + 1, LevelCount::default());
            }
            out[d].instances += s.instances.len() as u64;
            if d > 0 {
                out[d - 1].scopes += 1;
            }
        }
        out
    }

    /// The rows of the tree with the scopes of `expanded` open (STUDIO-REQ-0033): the module's
    /// children at depth 0; an open scope lists its child scopes, then its instances.
    pub fn rows(&self, expanded: &BTreeSet<ScopeId>) -> Vec<TreeRow> {
        let mut out = Vec::new();
        let mut stack: Vec<(TreeItem, u32)> = Vec::new();
        self.push_children(0, 0, &mut stack);
        while let Some((item, depth)) = stack.pop() {
            out.push(TreeRow { item, depth });
            if let TreeItem::Scope(s) = item {
                if expanded.contains(&s) {
                    self.push_children(s, depth + 1, &mut stack);
                }
            }
        }
        out
    }

    /// Pushes the children of `scope` in reverse, so that they pop in order.
    fn push_children(&self, scope: ScopeId, depth: u32, stack: &mut Vec<(TreeItem, u32)>) {
        let Some(s) = self.scopes.get(scope as usize) else {
            return;
        };
        for &i in s.instances.iter().rev() {
            stack.push((TreeItem::Instance(i), depth));
        }
        for &c in s.children.iter().rev() {
            stack.push((TreeItem::Scope(c), depth));
        }
    }

    /// Instances and scopes whose name contains `query`, ignoring ASCII case, at most `max` of
    /// each (STUDIO-REQ-0034); an empty query matches nothing.
    pub fn search(&self, netlist: &NetlistView, query: &str, max: usize) -> SearchResult {
        let mut r = SearchResult {
            query: query.to_string(),
            ..SearchResult::default()
        };
        let q = query.trim();
        if q.is_empty() {
            return r;
        }
        for row in 0..netlist.instance_count() as u32 {
            if contains_ignore_case(netlist.instance_name(row), q) {
                r.total += 1;
                if r.instances.len() < max {
                    r.instances.push(row);
                } else {
                    r.truncated = true;
                }
            }
        }
        for (k, s) in self.scopes.iter().enumerate().skip(1) {
            if contains_ignore_case(&s.name, q) {
                r.total += 1;
                if r.scopes.len() < max {
                    r.scopes.push(k as ScopeId);
                } else {
                    r.truncated = true;
                }
            }
        }
        r
    }
}

/// Whether `hay` contains `needle`, comparing ASCII letters without case.
pub fn contains_ignore_case(hay: &str, needle: &str) -> bool {
    let (h, n) = (hay.as_bytes(), needle.as_bytes());
    if n.is_empty() {
        return true;
    }
    if n.len() > h.len() {
        return false;
    }
    h.windows(n.len()).any(|w| w.eq_ignore_ascii_case(n))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusteda_core::netlist::{GateNetlist, PinDir};

    fn texts(name: &str, mode: SplitMode) -> Vec<String> {
        split(name, mode)
            .into_iter()
            .map(|r| level_text(name, r))
            .collect()
    }

    // STUDIO-REQ-0030: the core rule splits at unescaped `/` only; the flattened mode also at
    // `.` and escaped `\/` outside brackets; no level is empty.
    #[test]
    fn split_modes() {
        assert_eq!(texts("a/b/c", SplitMode::Core), ["a", "b", "c"]);
        assert_eq!(texts("a\\/b/c", SplitMode::Core), ["a/b", "c"]);
        assert_eq!(texts("u.x/g1", SplitMode::Core), ["u.x", "g1"]);
        assert_eq!(
            texts("u_core.gen_lane[2].u_add/g7", SplitMode::Flattened),
            ["u_core", "gen_lane[2]", "u_add", "g7"]
        );
        assert_eq!(
            texts("u_alu\\/reg_a_q_reg[31]", SplitMode::Flattened),
            ["u_alu", "reg_a_q_reg[31]"]
        );
        assert_eq!(texts("mem[1.5]", SplitMode::Flattened), ["mem[1.5]"]);
        assert_eq!(texts(".x", SplitMode::Flattened), [".x"]);
        assert_eq!(texts("x.", SplitMode::Flattened), ["x."]);
        assert_eq!(texts("a..b", SplitMode::Flattened), ["a", ".b"]);
        assert_eq!(texts("g42", SplitMode::Flattened), ["g42"]);
        assert_eq!(texts("a\\\\b", SplitMode::Flattened), ["a\\b"]);
    }

    #[test]
    fn natural_order() {
        let mut v = vec!["u10", "u2", "u1", "a", "u02", "u2b"];
        v.sort_by(|a, b| natural_cmp(a, b));
        assert_eq!(v, ["a", "u1", "u02", "u2", "u2b", "u10"]);
    }

    fn netlist(names: &[&str]) -> NetlistView {
        let mut g = GateNetlist::new("top").unwrap();
        let buf = g
            .add_cell_type("buf", &[("A", PinDir::Input), ("Y", PinDir::Output)])
            .unwrap();
        for n in names {
            g.add_instance(n, buf).unwrap();
        }
        NetlistView::new(g.freeze())
    }

    // STUDIO-REQ-0031, STUDIO-REQ-0032: scopes with their children, instances and subtree
    // counts, and the counts per level.
    #[test]
    fn tree_and_counts() {
        let v = netlist(&[
            "g10",
            "g9",
            "u_cnt.r0",
            "u_cnt.r1",
            "u_crc/u_x/g1",
            "u_crc/g2",
        ]);
        let h = Hierarchy::build(&v, SplitMode::Flattened, 64);
        let root = h.scope(0).unwrap();
        assert_eq!(root.name, "top");
        assert_eq!(root.total_instances, 6);
        let names: Vec<&str> = root
            .children
            .iter()
            .map(|&c| h.scope(c).unwrap().name.as_str())
            .collect();
        assert_eq!(names, ["u_cnt", "u_crc"]);
        let leaf: Vec<String> = root.instances.iter().map(|&r| h.leaf_text(&v, r)).collect();
        assert_eq!(leaf, ["g9", "g10"]);
        let crc = h.scope(root.children[1]).unwrap();
        assert_eq!(crc.total_instances, 2);
        assert_eq!(crc.instances.len(), 1);
        assert_eq!(h.scope_path(crc.children[0]), "u_crc/u_x");
        let counts = h.level_counts();
        assert_eq!(
            counts[0],
            LevelCount {
                scopes: 2,
                instances: 2
            }
        );
        assert_eq!(
            counts[1],
            LevelCount {
                scopes: 1,
                instances: 3
            }
        );
        assert_eq!(
            counts[2],
            LevelCount {
                scopes: 0,
                instances: 1
            }
        );
        let g1 = v.find_instance("u_crc/u_x/g1").unwrap();
        let anc = h.ancestors(g1);
        assert_eq!(anc.len(), 3);
        assert_eq!(h.scope_path(*anc.last().unwrap()), "u_crc/u_x");
    }

    // STUDIO-REQ-0033: rows list child scopes then instances, and only open scopes show their
    // content.
    #[test]
    fn rows_follow_expansion() {
        let v = netlist(&["u_a.x", "u_a.y", "z"]);
        let h = Hierarchy::build(&v, SplitMode::Flattened, 64);
        let closed = h.rows(&BTreeSet::new());
        assert_eq!(closed.len(), 2);
        assert_eq!(closed[0].item, TreeItem::Scope(1));
        let open = h.rows(&BTreeSet::from([1]));
        assert_eq!(open.len(), 4);
        assert_eq!(open[1].depth, 1);
        assert!(matches!(open[1].item, TreeItem::Instance(_)));
        assert!(matches!(open[3].item, TreeItem::Instance(_)));
    }

    // STUDIO-REQ-0034: searches ignore ASCII case, list at most the budget and count the rest.
    #[test]
    fn search_budget() {
        let names: Vec<String> = (0..10).map(|k| format!("u_core.Reg{k}")).collect();
        let refs: Vec<&str> = names.iter().map(String::as_str).collect();
        let v = netlist(&refs);
        let h = Hierarchy::build(&v, SplitMode::Flattened, 64);
        let r = h.search(&v, "reg", 3);
        assert_eq!(r.instances.len(), 3);
        assert_eq!(r.total, 10);
        assert!(r.truncated);
        assert_eq!(r.diagnostics()[0].code, Code::SearchTruncated);
        let r = h.search(&v, "U_CORE", 3);
        assert_eq!(r.scopes, [1]);
        assert_eq!(r.instances.len(), 3);
        let r = h.search(&v, "Reg9", 3);
        assert!(!r.truncated && r.diagnostics().is_empty());
        assert!(h.search(&v, "  ", 3).instances.is_empty());
        assert!(contains_ignore_case("abc", ""));
        assert!(!contains_ignore_case("ab", "abc"));
    }

    // STUDIO-REQ-0035: names deeper than the budget fold into their leaf and are reported.
    #[test]
    fn depth_budget_folds() {
        let v = netlist(&["a.b.c.d.e", "a.x"]);
        let h = Hierarchy::build(&v, SplitMode::Flattened, 3);
        let deep = v.find_instance("a.b.c.d.e").unwrap();
        assert_eq!(h.leaf_text(&v, deep), "c.d.e");
        assert_eq!(h.scope_path(h.instance_scope(deep).unwrap()), "a/b");
        assert_eq!(h.folded(), 1);
        let d = h.diagnostics(3);
        assert_eq!(d.len(), 1);
        assert_eq!(d[0].code, Code::HierarchyDepthFolded);
        assert!(
            Hierarchy::build(&v, SplitMode::Flattened, 64)
                .diagnostics(64)
                .is_empty()
        );
    }
}

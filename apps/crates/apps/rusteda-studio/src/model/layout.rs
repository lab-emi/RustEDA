// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Deterministic layered layout of a logic cone (`docs/specs/studio/cone.md`).
//!
//! The layout follows the layered method of [sugiyama1981methods], in four steps:
//!
//! 1. **Cycle breaking.** Nodes are grouped by their signed distance from the cone's root
//!    (fan-in before the root, fan-out after it); an edge back to an earlier group, which only
//!    sequential loops make, is a feedback edge, and inside a group a depth-first search in the
//!    order of the stable node ids marks the edges that close a cycle. The other edges form a
//!    DAG (STUDIO-REQ-0090).
//! 2. **Layering.** Each node's layer is its topological depth: the longest DAG path from a
//!    source, computed in O(V + E) with Kahn's algorithm (STUDIO-REQ-0091). Signals flow from
//!    left to right.
//! 3. **Ordering.** Edges that span several layers get virtual nodes, at most
//!    `max_virtual` of them; then alternating downward and upward sweeps order every layer by
//!    the barycenter of its neighbors in the previous layer ([sugiyama1981methods]; the median
//!    variant of [gansner1993technique] behaves alike), keeping the order with the fewest
//!    crossings, which are counted exactly per layer pair (STUDIO-REQ-0092). Minimizing
//!    crossings is NP-hard even for two layers [eades1994edge]; the sweeps are a heuristic.
//! 4. **Coordinates.** Integer coordinates: a column per layer and a row per position, each
//!    layer centered vertically (STUDIO-REQ-0093).
//!
//! Every comparison is exact (barycenters are compared as fractions in integers) and ties are
//! broken by the stable node ids, so the same cone always gives the same drawing
//! (STUDIO-REQ-0094).

use std::cmp::Ordering;
use std::collections::BinaryHeap;

use super::cone::{Cone, NodeKey};

/// Sizes of the drawing in layout units (one unit is one pixel at zoom 1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LayoutParams {
    /// Width of a node box.
    pub node_w: i64,
    /// Height of a node box.
    pub node_h: i64,
    /// Horizontal gap between layers.
    pub layer_gap: i64,
    /// Vertical gap between rows.
    pub row_gap: i64,
    /// Ordering sweeps (one downward and one upward pass each).
    pub sweeps: usize,
    /// Virtual nodes on long edges.
    pub max_virtual: usize,
}

impl Default for LayoutParams {
    fn default() -> LayoutParams {
        LayoutParams {
            node_w: 132,
            node_h: 40,
            layer_gap: 52,
            row_gap: 14,
            sweeps: 12,
            max_virtual: 4000,
        }
    }
}

/// A placed node (STUDIO-REQ-0093).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlacedNode {
    /// Its layer (column), from 0 at the left.
    pub layer: u32,
    /// Its position in the layer, from 0 at the top.
    pub order: u32,
    /// Left edge.
    pub x: i64,
    /// Top edge.
    pub y: i64,
}

/// A routed edge: a polyline from the driver's right side to the load's left side through its
/// virtual nodes (STUDIO-REQ-0093).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlacedEdge {
    /// The edge index in the cone.
    pub edge: usize,
    /// The points of the polyline.
    pub points: Vec<(i64, i64)>,
    /// Whether the edge closes a cycle and is drawn against the flow.
    pub feedback: bool,
}

/// The layout of a cone (STUDIO-REQ-0090 to STUDIO-REQ-0094).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConeLayout {
    /// One per cone node, in cone order.
    pub nodes: Vec<PlacedNode>,
    /// One per cone edge, in cone order.
    pub edges: Vec<PlacedEdge>,
    /// Layers.
    pub layers: u32,
    /// Crossings between adjacent layers of the chosen order.
    pub crossings: u64,
    /// Crossings of the initial order, before the sweeps.
    pub initial_crossings: u64,
    /// Width of the drawing.
    pub width: i64,
    /// Height of the drawing.
    pub height: i64,
    /// Whether the virtual-node budget left long edges without virtual nodes.
    pub truncated: bool,
    /// The parameters used.
    pub params: LayoutParams,
}

/// A barycenter as an exact fraction; `None` for a node without neighbors on that side.
#[derive(Debug, Clone, Copy)]
struct Bary {
    sum: i64,
    count: i64,
}

impl Bary {
    fn cmp(self, other: Bary) -> Ordering {
        // sum / count compared exactly: count > 0 on both sides.
        (i128::from(self.sum) * i128::from(other.count))
            .cmp(&(i128::from(other.sum) * i128::from(self.count)))
    }
}

/// Counts the inversions of `seq` (a Fenwick tree over its values, which are below `max`).
fn inversions(seq: &[usize], max: usize) -> u64 {
    let mut tree = vec![0u64; max + 1];
    let mut total = 0u64;
    for (seen, &v) in seq.iter().enumerate() {
        // Elements before v that are greater than v.
        let mut i = v + 1;
        let mut le = 0u64;
        while i > 0 {
            le += tree[i];
            i -= i & i.wrapping_neg();
        }
        total += seen as u64 - le;
        let mut i = v + 1;
        while i <= max {
            tree[i] += 1;
            i += i & i.wrapping_neg();
        }
    }
    total
}

/// The proper layered graph: real and virtual nodes, layers, and segment edges.
struct Layered {
    /// Layer of every node (real nodes first, then virtual ones).
    layer: Vec<u32>,
    /// Stable sort key of every node: real nodes by their key's rank, virtual nodes after.
    key: Vec<u64>,
    /// Segments between adjacent layers: (upper node, lower node).
    segs: Vec<(usize, usize)>,
    /// Per cone edge: the chain of nodes from driver to load, or empty for a feedback edge.
    chains: Vec<Vec<usize>>,
    truncated: bool,
}

/// Lays out `cone` (STUDIO-REQ-0090 to STUDIO-REQ-0094).
pub fn layout(cone: &Cone, params: LayoutParams) -> ConeLayout {
    let n = cone.nodes.len();
    // Stable ranks: nodes sorted by key.
    let mut by_key: Vec<usize> = (0..n).collect();
    by_key.sort_by_key(|&i| cone.nodes[i].key);
    let mut rank = vec![0usize; n];
    for (r, &i) in by_key.iter().enumerate() {
        rank[i] = r;
    }
    let feedback = feedback_edges(cone, &rank);
    let layer = longest_path_layers(cone, &rank, &feedback);
    let g = proper_graph(cone, &rank, &feedback, &layer, params.max_virtual);
    let layers = g.layer.iter().copied().max().map_or(0, |m| m + 1);
    let mut rows: Vec<Vec<usize>> = vec![Vec::new(); layers as usize];
    let mut order_of: Vec<usize> = (0..g.layer.len()).collect();
    order_of.sort_by_key(|&v| (g.layer[v], g.key[v], v));
    for v in order_of {
        rows[g.layer[v] as usize].push(v);
    }
    let mut up: Vec<Vec<usize>> = vec![Vec::new(); g.layer.len()];
    let mut down: Vec<Vec<usize>> = vec![Vec::new(); g.layer.len()];
    for &(a, b) in &g.segs {
        down[a].push(b);
        up[b].push(a);
    }
    let initial_crossings = crossings(&rows, &g.segs, g.layer.len());
    let mut best = rows.clone();
    let mut best_x = initial_crossings;
    for _ in 0..params.sweeps {
        if best_x == 0 {
            break;
        }
        for l in 1..rows.len() {
            reorder(&mut rows, l, l - 1, &up, &g.key);
        }
        for l in (0..rows.len().saturating_sub(1)).rev() {
            reorder(&mut rows, l, l + 1, &down, &g.key);
        }
        let x = crossings(&rows, &g.segs, g.layer.len());
        if x < best_x {
            best_x = x;
            best = rows.clone();
        }
    }
    place(cone, &g, &best, layers, best_x, initial_crossings, params)
}

/// The group of a node for cycle breaking: its signed distance from the root, so that fan-in
/// draws left of the root and fan-out right of it; pinned nodes that the searches did not reach
/// come first.
fn group(cone: &Cone, v: usize) -> i64 {
    cone.nodes[v]
        .distance
        .map_or(i64::from(i32::MIN), i64::from)
}

/// Marks the feedback edges (STUDIO-REQ-0090): an edge from a node of a later group to one of
/// an earlier group (fan-out back into fan-in, through sequential loops), and, inside a group,
/// the edges that close a cycle in a depth-first search in rank order.
fn feedback_edges(cone: &Cone, rank: &[usize]) -> Vec<bool> {
    let n = cone.nodes.len();
    let mut feedback = vec![false; cone.edges.len()];
    let mut out: Vec<Vec<(usize, usize)>> = vec![Vec::new(); n];
    for (k, e) in cone.edges.iter().enumerate() {
        let (a, b) = (group(cone, e.from), group(cone, e.to));
        if a > b {
            feedback[k] = true;
        } else if a == b {
            out[e.from].push((e.to, k));
        }
    }
    for list in &mut out {
        list.sort_by_key(|&(to, k)| (rank[to], k));
    }
    let mut state = vec![0u8; n]; // 0 new, 1 on the stack, 2 done
    let mut roots: Vec<usize> = (0..n).collect();
    roots.sort_by_key(|&i| rank[i]);
    for s in roots {
        if state[s] != 0 {
            continue;
        }
        let mut stack: Vec<(usize, usize)> = vec![(s, 0)];
        state[s] = 1;
        while let Some(&mut (v, ref mut next)) = stack.last_mut() {
            if let Some(&(w, k)) = out[v].get(*next) {
                *next += 1;
                match state[w] {
                    0 => {
                        state[w] = 1;
                        stack.push((w, 0));
                    }
                    1 => feedback[k] = true,
                    _ => {}
                }
            } else {
                state[v] = 2;
                stack.pop();
            }
        }
    }
    feedback
}

/// Topological depth of every node over the non-feedback edges (STUDIO-REQ-0091).
fn longest_path_layers(cone: &Cone, rank: &[usize], feedback: &[bool]) -> Vec<u32> {
    let n = cone.nodes.len();
    let mut indeg = vec![0usize; n];
    let mut out: Vec<Vec<usize>> = vec![Vec::new(); n];
    for (k, e) in cone.edges.iter().enumerate() {
        if !feedback[k] {
            out[e.from].push(e.to);
            indeg[e.to] += 1;
        }
    }
    let mut layer = vec![0u32; n];
    // A min-heap on rank keeps the visiting order deterministic.
    let mut heap: BinaryHeap<std::cmp::Reverse<(usize, usize)>> = (0..n)
        .filter(|&v| indeg[v] == 0)
        .map(|v| std::cmp::Reverse((rank[v], v)))
        .collect();
    while let Some(std::cmp::Reverse((_, v))) = heap.pop() {
        for &w in &out[v] {
            layer[w] = layer[w].max(layer[v] + 1);
            indeg[w] -= 1;
            if indeg[w] == 0 {
                heap.push(std::cmp::Reverse((rank[w], w)));
            }
        }
    }
    layer
}

/// Splits long edges with virtual nodes, at most `max_virtual` (STUDIO-REQ-0092).
fn proper_graph(
    cone: &Cone,
    rank: &[usize],
    feedback: &[bool],
    layer: &[u32],
    max_virtual: usize,
) -> Layered {
    let n = cone.nodes.len();
    let mut g = Layered {
        layer: layer.to_vec(),
        key: rank.iter().map(|&r| r as u64).collect(),
        segs: Vec::new(),
        chains: vec![Vec::new(); cone.edges.len()],
        truncated: false,
    };
    let mut virtual_count = 0usize;
    for (k, e) in cone.edges.iter().enumerate() {
        if feedback[k] {
            continue;
        }
        let (a, b) = (e.from, e.to);
        let span = layer[b].saturating_sub(layer[a]) as usize;
        let mut chain = vec![a];
        if span > 1 && virtual_count + span - 1 <= max_virtual {
            for step in 1..span {
                let v = g.layer.len();
                g.layer.push(layer[a] + step as u32);
                // Virtual nodes order after real ones, by their edge and step.
                g.key.push(((n + k) as u64) << 16 | step as u64);
                chain.push(v);
                virtual_count += 1;
            }
        } else if span > 1 {
            g.truncated = true;
        }
        chain.push(b);
        for w in chain.windows(2) {
            if g.layer[w[1]] == g.layer[w[0]] + 1 {
                g.segs.push((w[0], w[1]));
            }
        }
        g.chains[k] = chain;
    }
    g
}

/// Reorders layer `l` by the barycenters of the neighbors in layer `fixed`.
fn reorder(rows: &mut [Vec<usize>], l: usize, fixed: usize, nbr: &[Vec<usize>], key: &[u64]) {
    let mut pos = std::collections::HashMap::new();
    for (i, &v) in rows[fixed].iter().enumerate() {
        pos.insert(v, i as i64);
    }
    let current: Vec<(usize, i64)> = rows[l]
        .iter()
        .enumerate()
        .map(|(i, &v)| (v, i as i64))
        .collect();
    let bary = |v: usize, at: i64| -> Bary {
        let ps: Vec<i64> = nbr[v].iter().filter_map(|w| pos.get(w).copied()).collect();
        if ps.is_empty() {
            // Without neighbors on that side a node keeps its place.
            Bary { sum: at, count: 1 }
        } else {
            Bary {
                sum: ps.iter().sum(),
                count: ps.len() as i64,
            }
        }
    };
    let mut keyed: Vec<(Bary, i64, u64, usize)> = current
        .iter()
        .map(|&(v, at)| (bary(v, at), at, key[v], v))
        .collect();
    keyed.sort_by(|a, b| a.0.cmp(b.0).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
    rows[l] = keyed.into_iter().map(|t| t.3).collect();
}

/// Crossings between all adjacent layers (STUDIO-REQ-0092).
fn crossings(rows: &[Vec<usize>], segs: &[(usize, usize)], nodes: usize) -> u64 {
    let mut pos = vec![0usize; nodes];
    let mut layer_of = vec![0usize; nodes];
    let mut width = 0;
    for (l, row) in rows.iter().enumerate() {
        width = width.max(row.len());
        for (i, &v) in row.iter().enumerate() {
            pos[v] = i;
            layer_of[v] = l;
        }
    }
    let mut by_layer: Vec<Vec<(usize, usize)>> = vec![Vec::new(); rows.len()];
    for &(a, b) in segs {
        by_layer[layer_of[a]].push((pos[a], pos[b]));
    }
    by_layer
        .iter_mut()
        .map(|s| {
            s.sort_unstable();
            let seq: Vec<usize> = s.iter().map(|&(_, b)| b).collect();
            inversions(&seq, width)
        })
        .sum()
}

/// Integer coordinates and polylines (STUDIO-REQ-0093).
fn place(
    cone: &Cone,
    g: &Layered,
    rows: &[Vec<usize>],
    layers: u32,
    crossings: u64,
    initial_crossings: u64,
    p: LayoutParams,
) -> ConeLayout {
    let tallest = rows.iter().map(Vec::len).max().unwrap_or(0) as i64;
    let row_h = p.node_h + p.row_gap;
    let height = (tallest * row_h - p.row_gap).max(p.node_h);
    let mut at = vec![(0i64, 0i64, 0u32, 0u32); g.layer.len()];
    for (l, row) in rows.iter().enumerate() {
        let offset = (height - (row.len() as i64 * row_h - p.row_gap)) / 2;
        for (i, &v) in row.iter().enumerate() {
            let x = l as i64 * (p.node_w + p.layer_gap);
            let y = offset + i as i64 * row_h;
            at[v] = (x, y, l as u32, i as u32);
        }
    }
    let nodes = (0..cone.nodes.len())
        .map(|v| PlacedNode {
            layer: at[v].2,
            order: at[v].3,
            x: at[v].0,
            y: at[v].1,
        })
        .collect();
    let mid = p.node_h / 2;
    let edges = cone
        .edges
        .iter()
        .enumerate()
        .map(|(k, e)| {
            let chain = &g.chains[k];
            let feedback = chain.is_empty();
            let mut points = vec![(at[e.from].0 + p.node_w, at[e.from].1 + mid)];
            if !feedback {
                for &v in &chain[1..chain.len() - 1] {
                    points.push((at[v].0 + p.node_w / 2, at[v].1 + mid));
                }
            }
            points.push((at[e.to].0, at[e.to].1 + mid));
            PlacedEdge {
                edge: k,
                points,
                feedback,
            }
        })
        .collect();
    let width = (i64::from(layers) * (p.node_w + p.layer_gap) - p.layer_gap).max(p.node_w);
    ConeLayout {
        nodes,
        edges,
        layers,
        crossings,
        initial_crossings,
        width,
        height,
        truncated: g.truncated,
        params: p,
    }
}

/// The node whose box contains point `(x, y)` (layout units), for hit testing.
pub fn hit(layout: &ConeLayout, x: i64, y: i64) -> Option<usize> {
    let p = layout.params;
    layout
        .nodes
        .iter()
        .position(|n| x >= n.x && x < n.x + p.node_w && y >= n.y && y < n.y + p.node_h)
}

/// The key order used for stable ids, exposed for tests and the panels.
pub fn stable_order(cone: &Cone) -> Vec<NodeKey> {
    let mut keys: Vec<NodeKey> = cone.nodes.iter().map(|n| n.key).collect();
    keys.sort();
    keys
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::cone::{ConeEdge, ConeNode, Truncation};

    fn cone(n: usize, edges: &[(usize, usize)]) -> Cone {
        Cone {
            root: NodeKey::Inst(0),
            nodes: (0..n)
                .map(|i| ConeNode {
                    key: NodeKey::Inst(i as u32),
                    distance: Some(0),
                    pinned: false,
                    hidden_loads: 0,
                    self_loop: false,
                })
                .collect(),
            edges: edges
                .iter()
                .map(|&(from, to)| ConeEdge { from, to, net: 0 })
                .collect(),
            truncated: Truncation::default(),
            visits: 0,
        }
    }

    // STUDIO-REQ-0090, STUDIO-REQ-0091: feedback edges are marked, and layers are the longest
    // path depth over the rest.
    #[test]
    fn layers_and_feedback() {
        // 0 -> 1 -> 2 -> 3, 0 -> 3, and 3 -> 1 closing a cycle.
        let c = cone(4, &[(0, 1), (1, 2), (2, 3), (0, 3), (3, 1)]);
        let l = layout(&c, LayoutParams::default());
        let layer: Vec<u32> = l.nodes.iter().map(|n| n.layer).collect();
        assert_eq!(layer, [0, 1, 2, 3]);
        assert_eq!(l.layers, 4);
        assert!(l.edges[4].feedback);
        assert!(!l.edges[3].feedback);
        // The long edge 0 -> 3 passes through two virtual nodes.
        assert_eq!(l.edges[3].points.len(), 4);
        assert_eq!(l.edges[0].points.len(), 2);
    }

    // STUDIO-REQ-0090: fan-in draws left of the root and fan-out right of it; the loop back
    // from fan-out into fan-in is the feedback edge, whatever the node ids.
    #[test]
    fn distance_groups_break_sequential_loops() {
        // 0 = root, 1 = fan-in (distance -1), 2 = fan-out (distance 1), loop 2 -> 1.
        let mut c = cone(3, &[(1, 0), (0, 2), (2, 1)]);
        c.nodes[1].distance = Some(-1);
        c.nodes[2].distance = Some(1);
        c.nodes[0].key = NodeKey::Inst(9);
        let l = layout(&c, LayoutParams::default());
        let layer: Vec<u32> = l.nodes.iter().map(|n| n.layer).collect();
        assert_eq!(layer, [1, 0, 2]);
        assert!(l.edges[2].feedback && !l.edges[0].feedback && !l.edges[1].feedback);
        // A pinned node the searches did not reach comes first.
        let mut c = cone(3, &[(2, 1), (1, 0)]);
        c.nodes[1].distance = Some(-1);
        c.nodes[2].distance = None;
        let l = layout(&c, LayoutParams::default());
        assert_eq!(l.nodes[2].layer, 0);
        assert!(l.edges.iter().all(|e| !e.feedback));
    }

    // STUDIO-REQ-0092: sweeps remove the crossings of a twisted bipartite graph and never end
    // worse than the initial order.
    #[test]
    fn sweeps_reduce_crossings() {
        // Layer 0: 0, 1, 2; layer 1: 3, 4, 5 with edges 0-5, 1-4, 2-3 (three crossings).
        let c = cone(6, &[(0, 5), (1, 4), (2, 3)]);
        let l = layout(&c, LayoutParams::default());
        assert_eq!(l.initial_crossings, 3);
        assert_eq!(l.crossings, 0);
        for e in &l.edges {
            assert_eq!(e.points[0].1, e.points[1].1, "straight edges");
        }
        let c = cone(6, &[(0, 3), (0, 4), (1, 3), (2, 5), (1, 5), (2, 4)]);
        let l = layout(&c, LayoutParams::default());
        assert!(l.crossings <= l.initial_crossings);
        assert_eq!(inversions(&[2, 1, 0], 3), 3);
        assert_eq!(inversions(&[0, 1, 2], 3), 0);
    }

    // STUDIO-REQ-0093: integer boxes in columns per layer, centered rows, polylines between
    // box sides, and hit testing.
    #[test]
    fn coordinates() {
        let c = cone(3, &[(0, 1), (0, 2)]);
        let p = LayoutParams::default();
        let l = layout(&c, p);
        assert_eq!(l.nodes[0].x, 0);
        assert_eq!(l.nodes[1].x, p.node_w + p.layer_gap);
        assert_eq!(l.height, 2 * p.node_h + p.row_gap);
        assert_eq!(l.nodes[0].y, (l.height - p.node_h) / 2);
        assert_eq!(
            l.edges[0].points[0],
            (p.node_w, l.nodes[0].y + p.node_h / 2)
        );
        assert_eq!(hit(&l, 1, l.nodes[0].y + 1), Some(0));
        assert_eq!(hit(&l, p.node_w + 1, 0), None);
    }

    // STUDIO-REQ-0092: the virtual-node budget leaves long edges straight and says so.
    #[test]
    fn virtual_budget() {
        let c = cone(4, &[(0, 1), (1, 2), (2, 3), (0, 3)]);
        let l = layout(
            &c,
            LayoutParams {
                max_virtual: 1,
                ..LayoutParams::default()
            },
        );
        assert!(l.truncated);
        assert_eq!(l.edges[3].points.len(), 2);
    }

    // STUDIO-REQ-0094: the same cone gives the same layout, whatever the order of its nodes.
    #[test]
    fn deterministic() {
        let c = cone(
            8,
            &[
                (0, 4),
                (1, 4),
                (2, 5),
                (3, 6),
                (4, 7),
                (5, 7),
                (6, 7),
                (0, 7),
            ],
        );
        assert_eq!(
            layout(&c, LayoutParams::default()),
            layout(&c, LayoutParams::default())
        );
        // Reversing the node list (keys travel with the nodes) gives the same boxes per key.
        let mut r = c.clone();
        r.nodes.reverse();
        let n = r.nodes.len();
        for e in &mut r.edges {
            e.from = n - 1 - e.from;
            e.to = n - 1 - e.to;
        }
        let a = layout(&c, LayoutParams::default());
        let b = layout(&r, LayoutParams::default());
        for i in 0..n {
            assert_eq!(a.nodes[i], b.nodes[n - 1 - i]);
        }
        assert_eq!(stable_order(&c), stable_order(&r));
    }
}

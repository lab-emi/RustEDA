// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The schematic view of a logic cone: the cone, its layout and what to highlight
//! (`docs/specs/studio/cone.md`, STUDIO-REQ-0095 to STUDIO-REQ-0097).
//!
//! [`ConeScene`] is what the canvas draws, in layout units: node boxes with their labels, edge
//! polylines, and flags for the root, the selected instances, and the instances and connections
//! of the selected path. Computing it needs no graphics, so it is tested headless.

use std::collections::HashSet;

use super::cone::{self, ConeRequest, NodeKey, Truncation};
use super::diag::{Code, StudioDiag};
use super::layout::{self, LayoutParams};
use super::netlist::{NetlistView, PortDirection};
use super::timing::PathDetail;

/// A node box (STUDIO-REQ-0095).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SceneNode {
    /// Its stable id.
    pub key: NodeKey,
    /// The instance name or port bit.
    pub title: String,
    /// The cell, or `input port` / `output port` / `inout port`.
    pub subtitle: String,
    /// Left edge, top edge, width and height in layout units.
    pub x: i64,
    /// Top edge.
    pub y: i64,
    /// Width.
    pub w: i64,
    /// Height.
    pub h: i64,
    /// The root of the cone.
    pub root: bool,
    /// On the selected path.
    pub on_path: bool,
    /// In the selection.
    pub selected: bool,
    /// A port bit.
    pub port: bool,
    /// Levels from the root, as in the cone.
    pub distance: Option<i32>,
    /// Loads not followed (`max_net_fanout`).
    pub hidden_loads: u32,
}

/// An edge polyline (STUDIO-REQ-0095).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SceneEdge {
    /// The points in layout units.
    pub points: Vec<(i64, i64)>,
    /// A connection of the selected path.
    pub on_path: bool,
    /// Closes a cycle (drawn against the flow).
    pub feedback: bool,
    /// The net.
    pub net: String,
}

/// A cone ready to draw (STUDIO-REQ-0095).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConeScene {
    /// What the cone is of.
    pub root_label: String,
    /// The boxes.
    pub nodes: Vec<SceneNode>,
    /// The polylines.
    pub edges: Vec<SceneEdge>,
    /// Width of the drawing.
    pub width: i64,
    /// Height of the drawing.
    pub height: i64,
    /// Layers.
    pub layers: u32,
    /// Crossings of the drawing.
    pub crossings: u64,
    /// The budgets the extraction reached.
    pub truncation: Truncation,
    /// Whether the layout left long edges without virtual nodes.
    pub layout_truncated: bool,
    /// Notes on the cone (truncation).
    pub diagnostics: Vec<StudioDiag>,
}

/// The node of a path point: its instance, or the port bit of its name.
fn point_key(nl: &NetlistView, instance_row: Option<u32>, name: &str) -> Option<NodeKey> {
    if let Some(r) = instance_row {
        return Some(NodeKey::Inst(r));
    }
    find_port_bit(nl, name).map(|(port, bit)| NodeKey::Port { port, bit })
}

/// The port bit named `label` (`name` or `name[index]`).
pub fn find_port_bit(nl: &NetlistView, label: &str) -> Option<(u32, u32)> {
    for port in 0..nl.port_count() as u32 {
        let bits = nl.frozen().port_bits(port).len() as u32;
        for bit in 0..bits {
            if nl.port_bit_label(port, bit) == label {
                return Some((port, bit));
            }
        }
    }
    None
}

/// The instances and the connections of a path as node keys (STUDIO-REQ-0096): a connection
/// joins two consecutive points on different nodes.
pub fn path_highlight(
    nl: &NetlistView,
    path: &PathDetail,
) -> (HashSet<NodeKey>, HashSet<(NodeKey, NodeKey)>) {
    let keys: Vec<Option<NodeKey>> = path
        .points
        .iter()
        .map(|p| point_key(nl, p.instance_row, &p.name))
        .collect();
    let nodes: HashSet<NodeKey> = keys.iter().flatten().copied().collect();
    let mut edges = HashSet::new();
    for w in keys.windows(2) {
        if let (Some(a), Some(b)) = (w[0], w[1]) {
            if a != b {
                edges.insert((a, b));
            }
        }
    }
    (nodes, edges)
}

/// Extracts, lays out and decorates the cone of `req` (STUDIO-REQ-0095 to STUDIO-REQ-0097).
/// The pinned instances of `req` should hold the path's instances, so that the path stays in
/// the cone within the node budget.
pub fn build(
    nl: &NetlistView,
    req: &ConeRequest,
    path: Option<&PathDetail>,
    selected: &[u32],
    params: LayoutParams,
) -> Result<ConeScene, StudioDiag> {
    let Some(c) = cone::extract(nl, req) else {
        return Err(StudioDiag::new(
            Code::ConeRootUnknown,
            format!("the cone root {:?} is not in the netlist", req.root),
        ));
    };
    let lay = layout::layout(&c, params);
    let (path_nodes, path_edges) = path.map(|p| path_highlight(nl, p)).unwrap_or_default();
    let root_label = cone::node_label(nl, c.root);
    let nodes = c
        .nodes
        .iter()
        .zip(&lay.nodes)
        .map(|(n, p)| {
            let (title, subtitle, port) = match n.key {
                NodeKey::Inst(r) => (
                    nl.instance_name(r).to_string(),
                    nl.instance_cell(r).to_string(),
                    false,
                ),
                NodeKey::Port { port, bit } => (
                    nl.port_bit_label(port, bit),
                    match nl.port_direction(port) {
                        PortDirection::Input => "input port",
                        PortDirection::Output => "output port",
                        PortDirection::Inout => "inout port",
                    }
                    .to_string(),
                    true,
                ),
            };
            SceneNode {
                key: n.key,
                title,
                subtitle,
                x: p.x,
                y: p.y,
                w: params.node_w,
                h: params.node_h,
                root: n.key == c.root,
                on_path: path_nodes.contains(&n.key),
                selected: matches!(n.key, NodeKey::Inst(r) if selected.contains(&r)),
                port,
                distance: n.distance,
                hidden_loads: n.hidden_loads,
            }
        })
        .collect();
    let edges = c
        .edges
        .iter()
        .zip(&lay.edges)
        .map(|(e, p)| {
            let pair = (c.nodes[e.from].key, c.nodes[e.to].key);
            SceneEdge {
                points: p.points.clone(),
                on_path: path_edges.contains(&pair),
                feedback: p.feedback,
                net: nl.net_label(e.net),
            }
        })
        .collect();
    let mut diagnostics = c.diagnostics(&root_label);
    if lay.truncated {
        diagnostics.push(StudioDiag::new(
            Code::LayoutTruncated,
            format!(
                "the layout of the cone of {root_label} draws some long edges straight (virtual \
                 node budget {})",
                params.max_virtual
            ),
        ));
    }
    Ok(ConeScene {
        root_label,
        nodes,
        edges,
        width: lay.width,
        height: lay.height,
        layers: lay.layers,
        crossings: lay.crossings,
        truncation: c.truncated,
        layout_truncated: lay.truncated,
        diagnostics,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::cone::ConeRoot;
    use crate::model::timing::{DetailPoint, PathRow};
    use rusteda_core::netlist::{Conn, GateNetlist, PinDir, PortDir};
    use rusteda_workspace::sta::{CheckKind, PointKind, Transition};

    fn netlist() -> NetlistView {
        let mut g = GateNetlist::new("top").unwrap();
        let inv = g
            .add_cell_type("inv", &[("A", PinDir::Input), ("Y", PinDir::Output)])
            .unwrap();
        g.add_port("a", PortDir::Input, None).unwrap();
        g.add_port("y", PortDir::Output, Some((1, 0))).unwrap();
        let a = g.find_net("a", None).unwrap();
        let y0 = g.find_net("y", Some(0)).unwrap();
        let w = g.add_net(Some("w"), None).unwrap();
        let u1 = g.add_instance("u1", inv).unwrap();
        let u2 = g.add_instance("u2", inv).unwrap();
        g.connect(u1, "A", Conn::Net(a)).unwrap();
        g.connect(u1, "Y", Conn::Net(w)).unwrap();
        g.connect(u2, "A", Conn::Net(w)).unwrap();
        g.connect(u2, "Y", Conn::Net(y0)).unwrap();
        NetlistView::new(g.freeze())
    }

    fn point(nl: &NetlistView, name: &str, inst: Option<&str>) -> DetailPoint {
        DetailPoint {
            name: name.into(),
            instance: inst.map(str::to_string),
            instance_row: inst.and_then(|i| nl.find_instance(i)),
            cell: None,
            kind: PointKind::Net,
            transition: Transition::Rise,
            arrival_ns: None,
            increment_ns: None,
        }
    }

    // STUDIO-REQ-0095, STUDIO-REQ-0096: boxes with titles and cells, the root, the path's
    // instances and connections highlighted, ports named by their bits.
    #[test]
    fn scene_highlights_the_path() {
        let nl = netlist();
        let u2 = nl.find_instance("u2").unwrap();
        let path = PathDetail {
            row: PathRow {
                row: 0,
                handle: "path1".into(),
                kind: CheckKind::Setup,
                corner: 0,
                corner_name: "tt".into(),
                slack_ns: -0.1,
                slack_exact: true,
                startpoint: "a".into(),
                endpoint: "y[0]".into(),
                levels: 2,
            },
            required_ns: None,
            check_ns: None,
            materialized: false,
            points: vec![
                point(&nl, "a", None),
                point(&nl, "u1/A", Some("u1")),
                point(&nl, "u1/Y", Some("u1")),
                point(&nl, "u2/A", Some("u2")),
                point(&nl, "y[0]", None),
            ],
            instances: vec!["u1".into(), "u2".into()],
        };
        let req = ConeRequest {
            root: ConeRoot::Instance(u2),
            fanin_depth: 3,
            fanout_depth: 1,
            max_nodes: 50,
            max_edges: 50,
            max_net_fanout: 8,
            max_visits: 10_000,
            pinned: vec![],
        };
        let s = build(&nl, &req, Some(&path), &[u2], LayoutParams::default()).unwrap();
        assert_eq!(s.root_label, "u2");
        let node = |t: &str| s.nodes.iter().find(|n| n.title == t).unwrap();
        assert!(node("u2").root && node("u2").selected && node("u2").on_path);
        assert_eq!(node("u1").subtitle, "inv");
        assert!(node("a").port && node("a").on_path);
        assert_eq!(node("y[0]").subtitle, "output port");
        assert!(s.edges.iter().all(|e| e.on_path), "{:?}", s.edges);
        assert_eq!(s.edges.len(), 3);
        assert_eq!(s.layers, 4);
        assert_eq!(s.crossings, 0);
        assert!(node("a").x < node("u1").x && node("u1").x < node("u2").x);
        assert_eq!(find_port_bit(&nl, "y[1]"), Some((1, 0)));
        assert_eq!(find_port_bit(&nl, "nope"), None);
    }

    // STUDIO-REQ-0097: an unknown root is a diagnostic, not a panic.
    #[test]
    fn unknown_root() {
        let nl = netlist();
        let req = ConeRequest {
            root: ConeRoot::Instance(42),
            fanin_depth: 1,
            fanout_depth: 1,
            max_nodes: 5,
            max_edges: 5,
            max_net_fanout: 5,
            max_visits: 100,
            pinned: vec![],
        };
        let e = build(&nl, &req, None, &[], LayoutParams::default()).unwrap_err();
        assert_eq!(e.code, Code::ConeRootUnknown);
    }
}

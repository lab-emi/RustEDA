// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Bounded fan-in and fan-out cones of the netlist (`docs/specs/studio/cone.md`).
//!
//! A cone is the set of instances and port bits within a number of levels before (fan-in) and
//! after (fan-out) a root pin, instance or port bit, and the connections among them. Two
//! breadth-first searches over the netlist decide the nodes; the edges are the connections
//! among the chosen nodes (driver pin to load pin through a net, also across continuous
//! assignments). Every search is bounded (Rule 3.4, STUDIO-REQ-0081): by depth on each side, by
//! nodes, by edges, by the loads followed per net, and by a work budget of pin visits; a visited
//! set guards against the cycles of sequential feedback. A budget that is reached leaves a
//! structured [`Truncation`] and a `U0050` diagnostic, never an unbounded list.
//!
//! The order of everything is deterministic: searches visit pins in row order, nodes keep their
//! order of discovery, and [`NodeKey`] is the stable id of a node across extractions
//! (STUDIO-REQ-0083).

use std::collections::{HashMap, HashSet, VecDeque};

use super::diag::{Code, StudioDiag};
use super::netlist::{NetRef, NetlistView, PortDirection};

/// The stable id of a cone node (STUDIO-REQ-0083): port bits order before instances.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum NodeKey {
    /// A bit of a module port: the port row and the bit offset from its msb.
    Port {
        /// The port row.
        port: u32,
        /// The bit offset from the msb.
        bit: u32,
    },
    /// An instance, by row.
    Inst(u32),
}

/// Where a cone starts (STUDIO-REQ-0080).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ConeRoot {
    /// An instance: fan-in through its inputs, fan-out through its outputs.
    Instance(u32),
    /// A pin (row of `pin_net`): an input pin's fan-in through its net and its instance's
    /// fan-out; an output pin's fan-out through its net and its instance's fan-in.
    Pin(u32),
    /// A port bit.
    Port {
        /// The port row.
        port: u32,
        /// The bit offset from the msb.
        bit: u32,
    },
}

/// The budgets of one extraction (STUDIO-REQ-0081).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConeRequest {
    /// The root.
    pub root: ConeRoot,
    /// Fan-in levels.
    pub fanin_depth: u32,
    /// Fan-out levels.
    pub fanout_depth: u32,
    /// Nodes, the root and the pinned instances included.
    pub max_nodes: usize,
    /// Edges.
    pub max_edges: usize,
    /// Loads of one net that a fan-out step follows.
    pub max_net_fanout: usize,
    /// Pin, port-bit and net visits of the whole extraction.
    pub max_visits: usize,
    /// Instances that are always nodes (the instances of a selected path), as far as the node
    /// budget allows (STUDIO-REQ-0082).
    pub pinned: Vec<u32>,
}

/// A node of a cone.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConeNode {
    /// Its stable id.
    pub key: NodeKey,
    /// Levels from the root: negative on the fan-in side, positive on the fan-out side, 0 for
    /// the root; `None` for a pinned instance that the searches did not reach.
    pub distance: Option<i32>,
    /// Whether the node is pinned (an instance of the selected path).
    pub pinned: bool,
    /// Loads of its output nets that the fan-out did not follow (`max_net_fanout`).
    pub hidden_loads: u32,
    /// Whether one of its outputs feeds one of its own inputs.
    pub self_loop: bool,
}

/// A connection between two nodes through a net (STUDIO-REQ-0084).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConeEdge {
    /// The driving node (index into [`Cone::nodes`]).
    pub from: usize,
    /// The loaded node.
    pub to: usize,
    /// The net at the driver.
    pub net: u32,
}

/// Which budgets ended the extraction early (STUDIO-REQ-0081).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Truncation {
    /// Nodes at the last level have further fan-in or fan-out.
    pub depth: bool,
    /// The node budget was reached.
    pub nodes: bool,
    /// The edge budget was reached.
    pub edges: bool,
    /// A net had more loads than the fan-out budget.
    pub fanout: bool,
    /// The work budget was reached.
    pub visits: bool,
}

impl Truncation {
    /// Whether any budget was reached.
    pub fn any(&self) -> bool {
        self.depth || self.nodes || self.edges || self.fanout || self.visits
    }

    /// The reached budgets by name (`depth`, `nodes`, `edges`, `fanout`, `visits`).
    pub fn reasons(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        for (on, name) in [
            (self.depth, "depth"),
            (self.nodes, "nodes"),
            (self.edges, "edges"),
            (self.fanout, "fanout"),
            (self.visits, "visits"),
        ] {
            if on {
                out.push(name);
            }
        }
        out
    }
}

/// A bounded cone (STUDIO-REQ-0080).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cone {
    /// The root node's key.
    pub root: NodeKey,
    /// The nodes in order of discovery; the root first.
    pub nodes: Vec<ConeNode>,
    /// The edges, without self loops, each `(from, to)` pair once.
    pub edges: Vec<ConeEdge>,
    /// The budgets that were reached.
    pub truncated: Truncation,
    /// Pin, port-bit and net visits made.
    pub visits: usize,
}

impl Cone {
    /// The index of the node with key `key`.
    pub fn index_of(&self, key: NodeKey) -> Option<usize> {
        self.nodes.iter().position(|n| n.key == key)
    }

    /// The diagnostic of a truncated cone (STUDIO-REQ-0081).
    pub fn diagnostics(&self, root_label: &str) -> Vec<StudioDiag> {
        if !self.truncated.any() {
            return Vec::new();
        }
        vec![StudioDiag::new(
            Code::ConeTruncated,
            format!(
                "the cone of {root_label} is cut short by its budgets ({}): {} nodes, {} edges",
                self.truncated.reasons().join(", "),
                self.nodes.len(),
                self.edges.len()
            ),
        )]
    }
}

/// The work of one extraction: the node table and the budgets.
struct Walk<'a> {
    nl: &'a NetlistView,
    req: &'a ConeRequest,
    nodes: Vec<ConeNode>,
    index: HashMap<NodeKey, usize>,
    truncated: Truncation,
    visits: usize,
}

impl Walk<'_> {
    /// Counts one visit; false once the work budget is spent.
    fn visit(&mut self) -> bool {
        if self.visits >= self.req.max_visits {
            self.truncated.visits = true;
            return false;
        }
        self.visits += 1;
        true
    }

    /// The node of `key`, added when the budget allows.
    fn node(&mut self, key: NodeKey, distance: Option<i32>, pinned: bool) -> Option<usize> {
        if let Some(&i) = self.index.get(&key) {
            let n = &mut self.nodes[i];
            if n.distance.is_none() {
                n.distance = distance;
            }
            n.pinned |= pinned;
            return Some(i);
        }
        if self.nodes.len() >= self.req.max_nodes {
            self.truncated.nodes = true;
            return None;
        }
        self.nodes.push(ConeNode {
            key,
            distance,
            pinned,
            hidden_loads: 0,
            self_loop: false,
        });
        self.index.insert(key, self.nodes.len() - 1);
        Some(self.nodes.len() - 1)
    }

    /// The nets that `net` stands for: itself and the nets an assignment connects to it in
    /// the given direction, transitively (`backward`: the right-hand sides that drive it).
    fn net_closure(&mut self, net: u32, backward: bool) -> Vec<u32> {
        let mut out = vec![net];
        let mut seen = HashSet::from([net]);
        let mut k = 0;
        while k < out.len() {
            let n = out[k];
            k += 1;
            let next: Vec<u32> = if backward {
                self.nl.assign_sources(n).to_vec()
            } else {
                self.nl.assign_sinks(n).to_vec()
            };
            for m in next {
                if !self.visit() {
                    return out;
                }
                if seen.insert(m) {
                    out.push(m);
                }
            }
        }
        out
    }

    /// The nodes that drive `net`: instances with a driving pin on it and input port bits.
    fn drivers(&mut self, net: u32) -> Vec<NodeKey> {
        let mut out = Vec::new();
        for n in self.net_closure(net, true) {
            for &p in self.nl.net_pins(n) {
                if !self.visit() {
                    return out;
                }
                if self.nl.pin_dir(p).drives() {
                    out.push(NodeKey::Inst(self.nl.pin_instance(p)));
                }
            }
            for (port, bit) in self.nl.net_port_bits(n) {
                if self.nl.port_direction(port) != PortDirection::Output {
                    out.push(NodeKey::Port { port, bit });
                }
            }
        }
        out
    }

    /// The nodes that `net` loads: instances with an input pin on it and output port bits.
    fn loads(&mut self, net: u32) -> Vec<NodeKey> {
        let mut out = Vec::new();
        for n in self.net_closure(net, false) {
            for &p in self.nl.net_pins(n) {
                if !self.visit() {
                    return out;
                }
                let d = self.nl.pin_dir(p);
                if matches!(
                    d,
                    rusteda_core::netlist::PinDir::Input | rusteda_core::netlist::PinDir::Inout
                ) {
                    out.push(NodeKey::Inst(self.nl.pin_instance(p)));
                }
            }
            for (port, bit) in self.nl.net_port_bits(n) {
                if self.nl.port_direction(port) != PortDirection::Input {
                    out.push(NodeKey::Port { port, bit });
                }
            }
        }
        out
    }

    /// The input nets (fan-in) or output nets (fan-out) of a node.
    fn nets(&self, key: NodeKey, fanin: bool) -> Vec<u32> {
        match key {
            NodeKey::Inst(row) => self
                .nl
                .instance_pins(row)
                .into_iter()
                .filter(|p| {
                    if fanin {
                        !matches!(
                            p.dir,
                            rusteda_core::netlist::PinDir::Output
                                | rusteda_core::netlist::PinDir::Other
                        )
                    } else {
                        p.dir.drives()
                    }
                })
                .filter_map(|p| match p.net {
                    NetRef::Net(n) => Some(n),
                    NetRef::Const(_) => None,
                })
                .collect(),
            NodeKey::Port { port, bit } => {
                let dir = self.nl.port_direction(port);
                let wanted = if fanin {
                    dir != PortDirection::Input
                } else {
                    dir != PortDirection::Output
                };
                if !wanted {
                    return Vec::new();
                }
                let bits = self.nl.frozen().port_bits(port);
                bits.get(bit as usize).copied().into_iter().collect()
            }
        }
    }

    /// One breadth-first search from the root's nets, fan-in (`fanin`) or fan-out.
    fn search(&mut self, start: Vec<u32>, fanin: bool) {
        let limit = if fanin {
            self.req.fanin_depth
        } else {
            self.req.fanout_depth
        };
        let sign = if fanin { -1 } else { 1 };
        let mut queue: VecDeque<(Vec<u32>, u32, Option<usize>)> = VecDeque::new();
        // The root is node 0 and is expanded here only.
        queue.push_back((start, 0, Some(0)));
        let mut expanded: HashSet<usize> = HashSet::from([0]);
        while let Some((nets, depth, from)) = queue.pop_front() {
            if nets.is_empty() {
                continue;
            }
            if depth >= limit {
                // The cone is cut by depth only where a node at the last level has a neighbor
                // outside the cone.
                if !self.truncated.depth {
                    for net in nets {
                        let next = if fanin {
                            self.drivers(net)
                        } else {
                            self.loads(net)
                        };
                        if next.iter().any(|k| !self.index.contains_key(k)) {
                            self.truncated.depth = true;
                            break;
                        }
                    }
                }
                continue;
            }
            for net in nets {
                let mut next = if fanin {
                    self.drivers(net)
                } else {
                    self.loads(net)
                };
                next.sort();
                next.dedup();
                if !fanin && next.len() > self.req.max_net_fanout {
                    let hidden = next.len() - self.req.max_net_fanout;
                    next.truncate(self.req.max_net_fanout);
                    self.truncated.fanout = true;
                    if let Some(f) = from {
                        self.nodes[f].hidden_loads += hidden as u32;
                    }
                }
                for key in next {
                    let distance = sign * (depth as i32 + 1);
                    let Some(i) = self.node(key, Some(distance), false) else {
                        continue;
                    };
                    if Some(i) == from || !expanded.insert(i) {
                        continue;
                    }
                    let more = self.nets(key, fanin);
                    queue.push_back((more, depth + 1, Some(i)));
                }
                if self.truncated.visits {
                    return;
                }
            }
        }
    }
}

/// Extracts the bounded cone of `req.root` (STUDIO-REQ-0080 to STUDIO-REQ-0084); `None` when
/// the root names nothing in the netlist.
pub fn extract(nl: &NetlistView, req: &ConeRequest) -> Option<Cone> {
    let (root, fanin_start, fanout_start) = match req.root {
        ConeRoot::Instance(row) => {
            if row as usize >= nl.instance_count() {
                return None;
            }
            (NodeKey::Inst(row), None, None)
        }
        ConeRoot::Pin(pin) => {
            if pin as usize >= nl.frozen().pin_net.len() {
                return None;
            }
            let row = nl.pin_instance(pin);
            let net = match nl.pin_net(pin) {
                NetRef::Net(n) => vec![n],
                NetRef::Const(_) => Vec::new(),
            };
            if nl.pin_dir(pin).drives() {
                (NodeKey::Inst(row), None, Some(net))
            } else {
                (NodeKey::Inst(row), Some(net), None)
            }
        }
        ConeRoot::Port { port, bit } => {
            if port as usize >= nl.port_count() || bit as usize >= nl.frozen().port_bits(port).len()
            {
                return None;
            }
            (NodeKey::Port { port, bit }, None, None)
        }
    };
    let mut w = Walk {
        nl,
        req,
        nodes: Vec::new(),
        index: HashMap::new(),
        truncated: Truncation::default(),
        visits: 0,
    };
    w.node(root, Some(0), false)?;
    for &row in &req.pinned {
        if (row as usize) < nl.instance_count() {
            w.node(NodeKey::Inst(row), None, true);
        }
    }
    let fanin = fanin_start.unwrap_or_else(|| w.nets(root, true));
    w.search(fanin, true);
    let fanout = fanout_start.unwrap_or_else(|| w.nets(root, false));
    w.search(fanout, false);
    let edges = induced_edges(&mut w);
    Some(Cone {
        root,
        nodes: w.nodes,
        edges,
        truncated: w.truncated,
        visits: w.visits,
    })
}

/// The connections among the chosen nodes, from each node's output nets to the loads that are
/// nodes too, in node and pin order (STUDIO-REQ-0084).
fn induced_edges(w: &mut Walk<'_>) -> Vec<ConeEdge> {
    let mut edges = Vec::new();
    let mut seen: HashSet<(usize, usize)> = HashSet::new();
    for from in 0..w.nodes.len() {
        let key = w.nodes[from].key;
        for net in w.nets(key, false) {
            for load in w.loads(net) {
                let Some(&to) = w.index.get(&load) else {
                    continue;
                };
                if to == from {
                    w.nodes[from].self_loop = true;
                    continue;
                }
                if !seen.insert((from, to)) {
                    continue;
                }
                if edges.len() >= w.req.max_edges {
                    w.truncated.edges = true;
                    return edges;
                }
                edges.push(ConeEdge { from, to, net });
            }
            if w.truncated.visits {
                return edges;
            }
        }
    }
    edges
}

/// A short label of a node: the instance name or the port bit.
pub fn node_label(nl: &NetlistView, key: NodeKey) -> String {
    match key {
        NodeKey::Inst(row) => nl.instance_name(row).to_string(),
        NodeKey::Port { port, bit } => nl.port_bit_label(port, bit),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusteda_core::netlist::{Conn, GateNetlist, PinDir, PortDir};

    /// A chain `a -> u0 -> u1 -> ... -> u{n-1} -> y`, a flop `r` fed by the chain's end and
    /// feeding `u0` back, and a fanout net from `u0` to `m` buffers.
    fn chain(n: usize, m: usize) -> NetlistView {
        let mut g = GateNetlist::new("top").unwrap();
        let and2 = g
            .add_cell_type(
                "and2",
                &[
                    ("A", PinDir::Input),
                    ("B", PinDir::Input),
                    ("Y", PinDir::Output),
                ],
            )
            .unwrap();
        let buf = g
            .add_cell_type("buf", &[("A", PinDir::Input), ("Y", PinDir::Output)])
            .unwrap();
        let dff = g
            .add_cell_type(
                "dff",
                &[
                    ("D", PinDir::Input),
                    ("CK", PinDir::Input),
                    ("Q", PinDir::Output),
                ],
            )
            .unwrap();
        g.add_port("a", PortDir::Input, None).unwrap();
        g.add_port("clk", PortDir::Input, None).unwrap();
        g.add_port("y", PortDir::Output, None).unwrap();
        let a = g.find_net("a", None).unwrap();
        let clk = g.find_net("clk", None).unwrap();
        let y = g.find_net("y", None).unwrap();
        let q = g.add_net(Some("q"), None).unwrap();
        let mut prev = a;
        for k in 0..n {
            let u = g.add_instance(&format!("u{k}"), and2).unwrap();
            g.connect(u, "A", Conn::Net(prev)).unwrap();
            g.connect(u, "B", Conn::Net(if k == 0 { q } else { a }))
                .unwrap();
            let out = if k + 1 == n {
                y
            } else {
                g.add_net(Some(&format!("n{k}")), None).unwrap()
            };
            g.connect(u, "Y", Conn::Net(out)).unwrap();
            prev = out;
        }
        let r = g.add_instance("r", dff).unwrap();
        g.connect(r, "D", Conn::Net(y)).unwrap();
        g.connect(r, "CK", Conn::Net(clk)).unwrap();
        g.connect(r, "Q", Conn::Net(q)).unwrap();
        let n0 = g.find_net("n0", None).unwrap();
        for k in 0..m {
            let b = g.add_instance(&format!("b{k}"), buf).unwrap();
            g.connect(b, "A", Conn::Net(n0)).unwrap();
            g.connect(b, "Y", Conn::Const(false)).ok();
        }
        NetlistView::new(g.freeze())
    }

    fn req(root: ConeRoot) -> ConeRequest {
        ConeRequest {
            root,
            fanin_depth: 2,
            fanout_depth: 2,
            max_nodes: 100,
            max_edges: 1000,
            max_net_fanout: 100,
            max_visits: 100_000,
            pinned: Vec::new(),
        }
    }

    fn labels(nl: &NetlistView, cone: &Cone) -> Vec<String> {
        cone.nodes.iter().map(|n| node_label(nl, n.key)).collect()
    }

    // STUDIO-REQ-0080, STUDIO-REQ-0084: fan-in and fan-out levels around an instance, with
    // ports, distances and the edges among the nodes.
    #[test]
    fn fanin_and_fanout_levels() {
        let nl = chain(5, 0);
        let u2 = nl.find_instance("u2").unwrap();
        let c = extract(&nl, &req(ConeRoot::Instance(u2))).unwrap();
        assert_eq!(c.root, NodeKey::Inst(u2));
        let l = labels(&nl, &c);
        assert_eq!(l[0], "u2");
        for want in ["u1", "u0", "a", "u3", "u4"] {
            assert!(l.iter().any(|x| x == want), "{want} missing from {l:?}");
        }
        let d = |name: &str| {
            let i = l.iter().position(|x| x == name).unwrap();
            c.nodes[i].distance
        };
        assert_eq!(d("u1"), Some(-1));
        assert_eq!(d("u0"), Some(-2));
        assert_eq!(d("u4"), Some(2));
        assert!(c.truncated.depth);
        let has = |a: &str, b: &str| {
            c.edges.iter().any(|e| {
                node_label(&nl, c.nodes[e.from].key) == a && node_label(&nl, c.nodes[e.to].key) == b
            })
        };
        assert!(has("u1", "u2") && has("u2", "u3") && has("a", "u2"));
        assert!(!has("u3", "u2"));
    }

    // STUDIO-REQ-0081: the node, fan-out, edge and work budgets cut the cone short and say so.
    #[test]
    fn budgets() {
        let nl = chain(4, 50);
        let u0 = nl.find_instance("u0").unwrap();
        let mut r = req(ConeRoot::Instance(u0));
        r.max_net_fanout = 8;
        let c = extract(&nl, &r).unwrap();
        assert!(c.truncated.fanout);
        assert_eq!(c.nodes[0].hidden_loads, 50 + 1 - 8);
        r.max_nodes = 5;
        let c = extract(&nl, &r).unwrap();
        assert_eq!(c.nodes.len(), 5);
        assert!(c.truncated.nodes);
        let d = c.diagnostics("u0");
        assert_eq!(d[0].code, Code::ConeTruncated);
        assert!(d[0].message.contains("nodes"));
        r.max_nodes = 100;
        r.max_edges = 3;
        let c = extract(&nl, &r).unwrap();
        assert_eq!(c.edges.len(), 3);
        assert!(c.truncated.edges);
        r.max_edges = 1000;
        r.max_visits = 10;
        let c = extract(&nl, &r).unwrap();
        assert!(c.truncated.visits);
        assert!(c.visits <= 10);
    }

    // STUDIO-REQ-0080: a pin root follows its own net on its side; a port root works; unknown
    // roots give no cone.
    #[test]
    fn pin_and_port_roots() {
        let nl = chain(3, 0);
        let u1 = nl.find_instance("u1").unwrap();
        let a_pin = nl.find_pin(u1, "A").unwrap();
        let c = extract(&nl, &req(ConeRoot::Pin(a_pin))).unwrap();
        let l = labels(&nl, &c);
        let at = |name: &str| {
            l.iter()
                .position(|x| x == name)
                .map(|i| c.nodes[i].distance)
        };
        assert_eq!(at("u0"), Some(Some(-1)));
        // Pin B of u1 is on port `a`; a root on pin A does not follow B's net, so `a` is reached
        // through u0 only, two levels back.
        assert_eq!(at("a"), Some(Some(-2)));
        assert_eq!(at("u2"), Some(Some(1)));
        let c = extract(&nl, &req(ConeRoot::Port { port: 0, bit: 0 })).unwrap();
        assert_eq!(node_label(&nl, c.root), "a");
        assert!(c.nodes.iter().all(|n| n.distance.unwrap_or(0) >= 0));
        assert!(extract(&nl, &req(ConeRoot::Instance(99))).is_none());
        assert!(extract(&nl, &req(ConeRoot::Port { port: 0, bit: 3 })).is_none());
        assert!(extract(&nl, &req(ConeRoot::Pin(9999))).is_none());
    }

    // STUDIO-REQ-0082: pinned instances are nodes even outside the levels, and feedback through
    // the flop is visited once.
    #[test]
    fn pinned_and_cycles() {
        let nl = chain(6, 0);
        let u0 = nl.find_instance("u0").unwrap();
        let u5 = nl.find_instance("u5").unwrap();
        let mut r = req(ConeRoot::Instance(u0));
        r.fanin_depth = 1;
        r.fanout_depth = 1;
        r.pinned = vec![u5];
        let c = extract(&nl, &r).unwrap();
        let i = c.index_of(NodeKey::Inst(u5)).unwrap();
        assert!(c.nodes[i].pinned);
        assert_eq!(c.nodes[i].distance, None);
        // A long fan-out through the loop r -> u0 terminates.
        let mut r = req(ConeRoot::Instance(u0));
        r.fanout_depth = 50;
        r.fanin_depth = 50;
        let c = extract(&nl, &r).unwrap();
        let keys: HashSet<NodeKey> = c.nodes.iter().map(|n| n.key).collect();
        assert_eq!(keys.len(), c.nodes.len());
    }

    // STUDIO-REQ-0083: the same request gives the same cone.
    #[test]
    fn deterministic() {
        let nl = chain(8, 20);
        let u3 = nl.find_instance("u3").unwrap();
        let a = extract(&nl, &req(ConeRoot::Instance(u3))).unwrap();
        let b = extract(&nl, &req(ConeRoot::Instance(u3))).unwrap();
        assert_eq!(a, b);
    }
}

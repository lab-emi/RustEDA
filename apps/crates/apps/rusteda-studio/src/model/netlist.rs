// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The netlist of a revision as the workbench reads it (STUDIO-REQ-0010).
//!
//! [`NetlistView`] holds the validated tables of the revision's `netlist` table
//! (`FrozenNetlist`, WS-REQ-0122) in memory, with the few indices the panels need: instances by
//! name, the port bits on each net, and the continuous assignments between nets in both
//! directions. Rows are those of the table, so `inst<N>` is instance row `N - 1` (WS-REQ-0022).
//! The view never writes anything.

use rusteda_core::netlist::PinDir;
use rusteda_workspace::netlist::{ANONYMOUS, CONST0, CONST1, FrozenNetlist, SCALAR};

/// What a pin or an assignment is connected to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum NetRef {
    /// A net, by row.
    Net(u32),
    /// The constant 0 or 1.
    Const(bool),
}

impl NetRef {
    fn from_raw(raw: u32) -> NetRef {
        match raw {
            CONST0 => NetRef::Const(false),
            CONST1 => NetRef::Const(true),
            n => NetRef::Net(n),
        }
    }
}

/// The direction of a module port.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PortDirection {
    /// An input: it drives its bit nets.
    Input,
    /// An output: its bit nets drive it.
    Output,
    /// Both.
    Inout,
}

/// A connected pin of an instance.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PinInfo {
    /// The pin row (of `pin_net`).
    pub pin: u32,
    /// The direction of the cell pin.
    pub dir: PinDir,
    /// What it is connected to.
    pub net: NetRef,
}

/// Compressed rows of `u32` items per net.
#[derive(Debug, Clone, Default)]
struct Csr {
    start: Vec<u32>,
    items: Vec<u32>,
}

impl Csr {
    /// Rows of `n` keys from `(key, item)` pairs, items kept in pair order within a key.
    fn build(n: usize, pairs: &[(u32, u32)]) -> Csr {
        let mut count = vec![0u32; n + 1];
        for &(k, _) in pairs {
            count[k as usize + 1] += 1;
        }
        for i in 0..n {
            count[i + 1] += count[i];
        }
        let mut fill = count.clone();
        let mut items = vec![0u32; pairs.len()];
        for &(k, v) in pairs {
            let at = &mut fill[k as usize];
            items[*at as usize] = v;
            *at += 1;
        }
        Csr {
            start: count,
            items,
        }
    }

    fn row(&self, k: u32) -> &[u32] {
        let k = k as usize;
        match (self.start.get(k), self.start.get(k + 1)) {
            (Some(&a), Some(&b)) => &self.items[a as usize..b as usize],
            _ => &[],
        }
    }
}

/// The netlist of a revision, validated and held in memory (STUDIO-REQ-0010).
#[derive(Debug, Clone)]
pub struct NetlistView {
    frozen: FrozenNetlist<'static>,
    /// Instance rows sorted by name, for lookups by name.
    by_name: Vec<u32>,
    /// Per net: port bits on it, encoded `port << 32 | bit` split over two arrays.
    net_port: Csr,
    net_port_bit: Csr,
    /// Per net: the right-hand nets of assignments whose left-hand side it is.
    assign_src: Csr,
    /// Per net: the left-hand nets of assignments whose right-hand side it is.
    assign_dst: Csr,
}

impl NetlistView {
    /// The view of validated tables (taken by value: the workbench keeps its own copy).
    pub fn new(frozen: FrozenNetlist<'static>) -> NetlistView {
        let mut by_name: Vec<u32> = (0..frozen.instances.len() as u32).collect();
        by_name.sort_by(|&a, &b| {
            let na = frozen
                .string(frozen.instances[a as usize].name)
                .unwrap_or("");
            let nb = frozen
                .string(frozen.instances[b as usize].name)
                .unwrap_or("");
            na.cmp(nb).then(a.cmp(&b))
        });
        let n_nets = frozen.nets.len();
        let mut ports = Vec::new();
        let mut bits = Vec::new();
        for p in 0..frozen.ports.len() as u32 {
            for (k, &net) in frozen.port_bits(p).iter().enumerate() {
                if (net as usize) < n_nets {
                    ports.push((net, p));
                    bits.push((net, k as u32));
                }
            }
        }
        let mut src = Vec::new();
        let mut dst = Vec::new();
        for a in frozen.assigns.iter() {
            if let NetRef::Net(rhs) = NetRef::from_raw(a.rhs) {
                if (a.lhs as usize) < n_nets && (rhs as usize) < n_nets {
                    src.push((a.lhs, rhs));
                    dst.push((rhs, a.lhs));
                }
            }
        }
        NetlistView {
            net_port: Csr::build(n_nets, &ports),
            net_port_bit: Csr::build(n_nets, &bits),
            assign_src: Csr::build(n_nets, &src),
            assign_dst: Csr::build(n_nets, &dst),
            frozen,
            by_name,
        }
    }

    /// The validated tables.
    pub fn frozen(&self) -> &FrozenNetlist<'static> {
        &self.frozen
    }

    fn string(&self, id: u32) -> &str {
        self.frozen.string(id).unwrap_or("")
    }

    /// The module name.
    pub fn module_name(&self) -> &str {
        self.string(self.frozen.module[0])
    }

    /// Instances.
    pub fn instance_count(&self) -> usize {
        self.frozen.instances.len()
    }

    /// Nets.
    pub fn net_count(&self) -> usize {
        self.frozen.nets.len()
    }

    /// Ports.
    pub fn port_count(&self) -> usize {
        self.frozen.ports.len()
    }

    /// The name of instance `row`.
    pub fn instance_name(&self, row: u32) -> &str {
        self.frozen
            .instances
            .get(row as usize)
            .map_or("", |i| self.string(i.name))
    }

    /// The cell of instance `row`.
    pub fn instance_cell(&self, row: u32) -> &str {
        self.frozen
            .instances
            .get(row as usize)
            .and_then(|i| self.frozen.cells.get(i.cell as usize))
            .map_or("", |&c| self.string(c))
    }

    /// The row of the instance named `name`.
    pub fn find_instance(&self, name: &str) -> Option<u32> {
        let at = self
            .by_name
            .partition_point(|&r| self.instance_name(r) < name);
        let &row = self.by_name.get(at)?;
        (self.instance_name(row) == name).then_some(row)
    }

    /// The connected pins of instance `row`, in connection order.
    pub fn instance_pins(&self, row: u32) -> Vec<PinInfo> {
        if row as usize >= self.instance_count() {
            return Vec::new();
        }
        self.frozen
            .instance_pins(row)
            .map(|p| {
                let pin = p as u32;
                PinInfo {
                    pin,
                    dir: self.frozen.pin_dir(pin),
                    net: NetRef::from_raw(self.frozen.pin_net[p]),
                }
            })
            .collect()
    }

    /// What pin `pin` is connected to.
    pub fn pin_net(&self, pin: u32) -> NetRef {
        self.frozen
            .pin_net
            .get(pin as usize)
            .map_or(NetRef::Const(false), |&raw| NetRef::from_raw(raw))
    }

    /// The instance of pin `pin`.
    pub fn pin_instance(&self, pin: u32) -> u32 {
        self.frozen.pin_instance(pin)
    }

    /// The direction of pin `pin`.
    pub fn pin_dir(&self, pin: u32) -> PinDir {
        self.frozen.pin_dir(pin)
    }

    /// The cell pin name of pin `pin`.
    pub fn pin_name(&self, pin: u32) -> &str {
        let inst = self.frozen.instances[self.pin_instance(pin) as usize];
        let rows = self.frozen.cell_pins(inst.cell);
        let k = self.frozen.pin_cellpin[pin as usize] as usize;
        self.frozen
            .cell_pin_name
            .get(rows.start + k)
            .map_or("", |&s| self.string(s))
    }

    /// The connected pin `pin_name` of instance `row`.
    pub fn find_pin(&self, row: u32, pin_name: &str) -> Option<u32> {
        self.instance_pins(row)
            .into_iter()
            .find(|p| self.pin_name(p.pin) == pin_name)
            .map(|p| p.pin)
    }

    /// The pins on net `net` (rows of `pin_net`), increasing.
    pub fn net_pins(&self, net: u32) -> &[u32] {
        if net as usize >= self.net_count() {
            return &[];
        }
        self.frozen.net_pins_of(net)
    }

    /// The name and bit index of net `net`, `None` for an anonymous net.
    pub fn net_name(&self, net: u32) -> Option<(&str, Option<i32>)> {
        let rec = self.frozen.nets.get(net as usize)?;
        if rec.name == ANONYMOUS {
            return None;
        }
        let index = (rec.index != SCALAR).then_some(rec.index);
        Some((self.string(rec.name), index))
    }

    /// `name`, `name[index]`, or `net#<row>` for an anonymous net.
    pub fn net_label(&self, net: u32) -> String {
        match self.net_name(net) {
            Some((name, None)) => name.to_string(),
            Some((name, Some(i))) => format!("{name}[{i}]"),
            None => format!("net#{net}"),
        }
    }

    /// The port bits on net `net`: `(port, bit offset from the msb)`.
    pub fn net_port_bits(&self, net: u32) -> Vec<(u32, u32)> {
        self.net_port
            .row(net)
            .iter()
            .copied()
            .zip(self.net_port_bit.row(net).iter().copied())
            .collect()
    }

    /// The direction of port `port`.
    pub fn port_direction(&self, port: u32) -> PortDirection {
        match self.frozen.ports.get(port as usize).map(|p| p.dir) {
            Some(0) => PortDirection::Input,
            Some(1) => PortDirection::Output,
            _ => PortDirection::Inout,
        }
    }

    /// The name of port `port`.
    pub fn port_name(&self, port: u32) -> &str {
        self.frozen
            .ports
            .get(port as usize)
            .map_or("", |p| self.string(p.name))
    }

    /// `name` of a scalar port bit or `name[index]` of a bus bit at offset `bit` from the msb.
    pub fn port_bit_label(&self, port: u32, bit: u32) -> String {
        let Some(p) = self.frozen.ports.get(port as usize) else {
            return String::new();
        };
        let name = self.string(p.name);
        if p.is_bus == 0 {
            return name.to_string();
        }
        let index = if p.msb >= p.lsb {
            i64::from(p.msb) - i64::from(bit)
        } else {
            i64::from(p.msb) + i64::from(bit)
        };
        format!("{name}[{index}]")
    }

    /// The nets that drive net `net` through assignments (`net = other`).
    pub fn assign_sources(&self, net: u32) -> &[u32] {
        self.assign_src.row(net)
    }

    /// The nets that net `net` drives through assignments (`other = net`).
    pub fn assign_sinks(&self, net: u32) -> &[u32] {
        self.assign_dst.row(net)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusteda_core::netlist::{Conn, GateNetlist, PortDir};

    fn sample() -> NetlistView {
        let mut g = GateNetlist::new("top").unwrap();
        let inv = g
            .add_cell_type("inv", &[("A", PinDir::Input), ("Y", PinDir::Output)])
            .unwrap();
        g.add_port("a", PortDir::Input, None).unwrap();
        g.add_port("y", PortDir::Output, Some((1, 0))).unwrap();
        let a = g.find_net("a", None).unwrap();
        let y0 = g.find_net("y", Some(0)).unwrap();
        let y1 = g.find_net("y", Some(1)).unwrap();
        let w = g.add_net(Some("u_core/w"), None).unwrap();
        let u2 = g.add_instance("u_core/u2", inv).unwrap();
        let u1 = g.add_instance("u1", inv).unwrap();
        g.connect(u1, "A", Conn::Net(a)).unwrap();
        g.connect(u1, "Y", Conn::Net(w)).unwrap();
        g.connect(u2, "A", Conn::Net(w)).unwrap();
        g.connect(u2, "Y", Conn::Net(y0)).unwrap();
        g.add_assign(y1, Conn::Net(y0)).unwrap();
        NetlistView::new(g.freeze())
    }

    // STUDIO-REQ-0010: names, cells, pins, nets, port bits and assignments of the netlist table
    // are read by row; instances are found by name.
    #[test]
    fn rows_names_and_connectivity() {
        let v = sample();
        assert_eq!(v.module_name(), "top");
        assert_eq!(v.instance_count(), 2);
        let u1 = v.find_instance("u1").unwrap();
        let u2 = v.find_instance("u_core/u2").unwrap();
        assert_eq!(v.instance_name(u1), "u1");
        assert_eq!(v.instance_cell(u2), "inv");
        assert_eq!(v.find_instance("u3"), None);
        let pins = v.instance_pins(u1);
        assert_eq!(pins.len(), 2);
        assert_eq!(v.pin_name(pins[0].pin), "A");
        assert_eq!(pins[1].dir, PinDir::Output);
        let NetRef::Net(w) = pins[1].net else {
            panic!("u1/Y is connected")
        };
        assert_eq!(v.net_label(w), "u_core/w");
        assert_eq!(v.net_pins(w).len(), 2);
        assert_eq!(v.find_pin(u2, "Y").map(|p| v.pin_instance(p)), Some(u2));
        // y[1] = y[0]: the assignment links the two bit nets both ways.
        let y0 = (0..v.net_count() as u32)
            .find(|&n| v.net_label(n) == "y[0]")
            .unwrap();
        let y1 = (0..v.net_count() as u32)
            .find(|&n| v.net_label(n) == "y[1]")
            .unwrap();
        assert_eq!(v.assign_sources(y1), &[y0]);
        assert_eq!(v.assign_sinks(y0), &[y1]);
        let bits = v.net_port_bits(y1);
        assert_eq!(bits.len(), 1);
        assert_eq!(v.port_bit_label(bits[0].0, bits[0].1), "y[1]");
        assert_eq!(v.port_direction(bits[0].0), PortDirection::Output);
        assert_eq!(v.port_name(0), "a");
        assert!(v.instance_pins(99).is_empty());
        assert!(v.net_pins(999).is_empty());
    }
}

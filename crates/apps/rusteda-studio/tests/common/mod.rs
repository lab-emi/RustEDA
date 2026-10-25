// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Workspaces for the tests, built with the shared crates only: the netlist codec of
//! `rusteda-workspace` over a `GateNetlist`, `StaTableBuilder` for the timing table and a
//! directory table for the RTL snapshot. No tool of the suite is run.
//!
//! The design is a 4-bit counter `top_hier` with its counter in instance `u_cnt`: flip-flops
//! `r<k>`, half-adder cells `a<k>` (carry) and `x<k>` (sum), state nets `u_cnt.cnt_q[<k>]` as the
//! synthesis tool of the suite names them, and the outputs assigned from the state nets. In the
//! flat variant the instances have generated names (`g0`, `g1`, ...); in the hierarchical
//! variant they are named `u_cnt/cnt_q_reg[<k>]`, `u_cnt/add/a<k>` and `u_cnt/add/x<k>`.

#![allow(dead_code)]

use std::path::{Path, PathBuf};

use rusteda_core::netlist::{Conn, GateNetlist, PinDir, PortDir};
use rusteda_workspace::sta::{
    CheckKind, EndpointKind, PathSpec, PointKind, StaTableBuilder, StoreMode, Transition,
};
use rusteda_workspace::{Invocation, JsonMap, Rev, Upstream, Workspace, netlist};

/// The RTL of the design.
pub const TOP_SV: &str = "module top_hier (
  input  logic       clk_i,
  input  logic       en_i,
  output logic [3:0] cnt_o
);
  counter u_cnt (.clk_i, .en_i, .cnt_o);
endmodule
";

/// The counter.
pub const COUNTER_SV: &str = "module counter (
  input  logic       clk_i,
  input  logic       en_i,
  output logic [3:0] cnt_o
);
  logic [3:0] cnt_q;
  always_ff @(posedge clk_i) if (en_i) cnt_q <= cnt_q + 4'd1;
  assign cnt_o = cnt_q;
endmodule
";

/// A fresh directory below the integration tests' scratch directory.
pub fn scratch(tag: &str) -> PathBuf {
    let base = PathBuf::from(env!("CARGO_TARGET_TMPDIR"));
    let d = base.join(format!("studio-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// An invocation of a test tool.
pub fn inv(command: &str) -> Invocation {
    Invocation::new("studio-test", "0.1.0", command, ["studio-test", command])
}

/// The instance names of the design.
#[derive(Debug, Clone)]
pub struct Names {
    /// Flip-flops by bit.
    pub r: Vec<String>,
    /// Carry cells by bit.
    pub a: Vec<String>,
    /// Sum cells by bit.
    pub x: Vec<String>,
}

impl Names {
    /// The names of the flat (`hier == false`) or hierarchical variant.
    pub fn new(hier: bool) -> Names {
        if hier {
            Names {
                r: (0..4).map(|k| format!("u_cnt/cnt_q_reg[{k}]")).collect(),
                a: (0..4).map(|k| format!("u_cnt/add/a{k}")).collect(),
                x: (0..4).map(|k| format!("u_cnt/add/x{k}")).collect(),
            }
        } else {
            Names {
                r: (0..4).map(|k| format!("g{k}")).collect(),
                a: (0..4).map(|k| format!("g{}", 4 + 2 * k)).collect(),
                x: (0..4).map(|k| format!("g{}", 5 + 2 * k)).collect(),
            }
        }
    }
}

/// The gate-level netlist of the design.
pub fn netlist(names: &Names) -> GateNetlist {
    let mut g = GateNetlist::new("top_hier").unwrap();
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
    let xor2 = g
        .add_cell_type(
            "xor2",
            &[
                ("A", PinDir::Input),
                ("B", PinDir::Input),
                ("Y", PinDir::Output),
            ],
        )
        .unwrap();
    g.add_port("clk_i", PortDir::Input, None).unwrap();
    g.add_port("en_i", PortDir::Input, None).unwrap();
    g.add_port("cnt_o", PortDir::Output, Some((3, 0))).unwrap();
    let clk = g.find_net("clk_i", None).unwrap();
    let en = g.find_net("en_i", None).unwrap();
    let q: Vec<_> = (0..4)
        .map(|k| g.add_net(Some(&format!("u_cnt.cnt_q[{k}]")), None).unwrap())
        .collect();
    let d: Vec<_> = (0..4)
        .map(|k| g.add_net(Some(&format!("n_d{k}")), None).unwrap())
        .collect();
    let mut carry = vec![en];
    for k in 0..4 {
        carry.push(g.add_net(Some(&format!("n_c{}", k + 1)), None).unwrap());
    }
    for k in 0..4 {
        let r = g.add_instance(&names.r[k], dff).unwrap();
        g.connect(r, "D", Conn::Net(d[k])).unwrap();
        g.connect(r, "CK", Conn::Net(clk)).unwrap();
        g.connect(r, "Q", Conn::Net(q[k])).unwrap();
    }
    for k in 0..4 {
        let a = g.add_instance(&names.a[k], and2).unwrap();
        g.connect(a, "A", Conn::Net(q[k])).unwrap();
        g.connect(a, "B", Conn::Net(carry[k])).unwrap();
        g.connect(a, "Y", Conn::Net(carry[k + 1])).unwrap();
        let x = g.add_instance(&names.x[k], xor2).unwrap();
        g.connect(x, "A", Conn::Net(q[k])).unwrap();
        g.connect(x, "B", Conn::Net(carry[k])).unwrap();
        g.connect(x, "Y", Conn::Net(d[k])).unwrap();
    }
    for k in 0..4 {
        let o = g.find_net("cnt_o", Some(k as i32)).unwrap();
        g.add_assign(o, Conn::Net(q[k])).unwrap();
    }
    g
}

/// The timing table of the design: setup paths to `r3/D` (worst) and `r1/D` in corners
/// `tt` and `ss`, and a hold path to `r2/D`. `delta` is added to every setup slack.
pub fn timing(names: &Names, delta: f64) -> StaTableBuilder {
    let mut b = StaTableBuilder::new(StoreMode::Full);
    b.top("top_hier");
    let tt = b.corner("tt");
    let ss = b.corner("ss");
    b.clock("clk", 1.0, 0.05);
    for k in 0..4 {
        b.instance(&names.r[k], "dff")
            .instance(&names.a[k], "and2")
            .instance(&names.x[k], "xor2");
    }
    let launch = b.pin(&names.r[0], "CK");
    let q0 = b.pin(&names.r[0], "Q");
    let mut pts = vec![
        (launch, Transition::Rise, PointKind::ClockLaunch, 0.0),
        (q0, Transition::Rise, PointKind::Cell, 0.3),
    ];
    let mut t = 0.3;
    // Through the carry cells a0, a1, a2 into the sum cell x3.
    for k in 0..3 {
        let a_in = b.pin(&names.a[k], if k == 0 { "A" } else { "B" });
        let a_out = b.pin(&names.a[k], "Y");
        t += 0.02;
        pts.push((a_in, Transition::Rise, PointKind::Net, t));
        t += 0.15;
        pts.push((a_out, Transition::Rise, PointKind::Cell, t));
    }
    let x3_b = b.pin(&names.x[3], "B");
    let x3_y = b.pin(&names.x[3], "Y");
    let r3_d = b.pin(&names.r[3], "D");
    t += 0.02;
    pts.push((x3_b, Transition::Rise, PointKind::Net, t));
    t += 0.2;
    pts.push((x3_y, Transition::Fall, PointKind::Cell, t));
    t += 0.02;
    pts.push((r3_d, Transition::Fall, PointKind::Net, t));
    let short = {
        let x1_b = b.pin(&names.x[1], "B");
        let x1_y = b.pin(&names.x[1], "Y");
        let a0_a = b.pin(&names.a[0], "A");
        let a0_y = b.pin(&names.a[0], "Y");
        let r1_d = b.pin(&names.r[1], "D");
        vec![
            (launch, Transition::Rise, PointKind::ClockLaunch, 0.0),
            (q0, Transition::Rise, PointKind::Cell, 0.3),
            (a0_a, Transition::Rise, PointKind::Net, 0.32),
            (a0_y, Transition::Rise, PointKind::Cell, 0.47),
            (x1_b, Transition::Rise, PointKind::Net, 0.49),
            (x1_y, Transition::Fall, PointKind::Cell, 0.69),
            (r1_d, Transition::Fall, PointKind::Net, 0.71),
        ]
    };
    let hold = {
        let ck = b.pin(&names.r[2], "CK");
        let q = b.pin(&names.r[2], "Q");
        let x_a = b.pin(&names.x[2], "A");
        let x_y = b.pin(&names.x[2], "Y");
        let r_d = b.pin(&names.r[2], "D");
        vec![
            (ck, Transition::Rise, PointKind::ClockLaunch, 0.0),
            (q, Transition::Rise, PointKind::Cell, 0.25),
            (x_a, Transition::Rise, PointKind::Net, 0.26),
            (x_y, Transition::Rise, PointKind::Cell, 0.36),
            (r_d, Transition::Rise, PointKind::Net, 0.37),
        ]
    };
    let r1_d = b.pin(&names.r[1], "D");
    let r2_d = b.pin(&names.r[2], "D");
    for (node, kind) in [
        (r3_d, EndpointKind::FlipFlop),
        (r1_d, EndpointKind::FlipFlop),
    ] {
        b.endpoint(node, kind);
    }
    b.endpoint(r2_d, EndpointKind::FlipFlop);
    b.endpoint_slack(r3_d, tt, Some(-0.12 + delta), None);
    b.endpoint_slack(r3_d, ss, Some(-0.35 + delta), None);
    b.endpoint_slack(r1_d, tt, Some(0.2 + delta), None);
    b.endpoint_slack(r1_d, ss, Some(0.05 + delta), None);
    b.endpoint_slack(r2_d, tt, None, Some(0.05));
    for (corner, slack, points) in [
        (tt, -0.12, pts.clone()),
        (ss, -0.35, pts),
        (tt, 0.2, short.clone()),
        (ss, 0.05, short),
    ] {
        b.path(PathSpec {
            corner,
            kind: CheckKind::Setup,
            slack_ns: slack + delta,
            required_ns: 0.9,
            points,
        });
    }
    b.path(PathSpec {
        corner: tt,
        kind: CheckKind::Hold,
        slack_ns: 0.05,
        required_ns: 0.32,
        points: hold,
    });
    b
}

/// A workspace at `root`: `rev1` with the RTL snapshot only, `rev2` with the netlist (and the
/// RTL carried), `rev3` with the timing table. Returns the workspace.
pub fn design_workspace(root: &Path, hier: bool) -> Workspace {
    let ws = Workspace::init(root, &inv("init")).unwrap().workspace;
    let names = Names::new(hier);
    // rev1: the RTL snapshot.
    let mut txn = ws
        .begin(ws.resolve_base("rev0").unwrap(), &inv("import"))
        .unwrap();
    let dir = txn.staging_path("rtl").unwrap();
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("src_0_top.sv"), TOP_SV).unwrap();
    std::fs::write(dir.join("src_1_counter.sv"), COUNTER_SV).unwrap();
    std::fs::write(
        dir.join("rtl.json"),
        "{\"top\": \"top_hier\", \"defines\": {}, \"params\": {}, \"files\": [], \
         \"predefined_macros\": true}\n",
    )
    .unwrap();
    txn.add_dir("rtl", "rtl", Upstream::new()).unwrap();
    txn.commit(JsonMap::new()).unwrap();
    // rev2: the netlist.
    let mut txn = ws
        .begin(ws.resolve_base("rev1").unwrap(), &inv("synth"))
        .unwrap();
    let up = txn.upstream(&["rtl"]).unwrap();
    netlist::add_to_txn(&mut txn, &netlist(&names).freeze(), up).unwrap();
    txn.commit(JsonMap::new()).unwrap();
    // rev3: the timing table.
    commit_timing(&ws, Rev(2), &names, 0.0);
    ws
}

/// Commits a timing table on `base` with setup slacks moved by `delta`; returns the new
/// revision.
pub fn commit_timing(ws: &Workspace, base: Rev, names: &Names, delta: f64) -> Rev {
    let mut txn = ws
        .begin(ws.resolve_base(&base.to_string()).unwrap(), &inv("timing"))
        .unwrap();
    let up = txn.upstream(&["netlist"]).unwrap();
    timing(names, delta).stage(&mut txn, up).unwrap();
    txn.commit(JsonMap::new()).unwrap().rev
}

/// Every file below `dir` with its bytes, sorted by path.
pub fn tree(dir: &Path) -> Vec<(PathBuf, Vec<u8>)> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).unwrap().flatten() {
            let p = e.path();
            if p.is_dir() {
                stack.push(p);
            } else {
                out.push((p.clone(), std::fs::read(&p).unwrap()));
            }
        }
    }
    out.sort();
    out
}

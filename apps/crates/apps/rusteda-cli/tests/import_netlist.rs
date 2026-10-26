// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Integration tests for `rusteda import --netlist` (RCLI-REQ-0011, RCLI-REQ-0019,
//! RCLI-REQ-0024).

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use rusteda_core::netlist::{Conn, GateNetlist, PinDir, PinRef, PortDir};
use rusteda_workspace::Workspace;
use serde_json::Value;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_rusteda")
}

fn temp_dir(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    let id = NEXT.fetch_add(1, Ordering::Relaxed);
    let mut dir = env::temp_dir();
    let sanitized: String = label
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    dir.push(format!(
        "rusteda-import-netlist-{}-{sanitized}-{id}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

fn run_cmd(args: &[&str]) -> Output {
    Command::new(bin())
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("spawn rusteda")
}

fn run_json(args: &[&str]) -> (i32, Value, String) {
    let mut full = vec!["--format", "json"];
    full.extend_from_slice(args);
    let out = run_cmd(&full);
    let code = out.status.code().unwrap_or(-1);
    let stdout = String::from_utf8(out.stdout).expect("stdout utf8");
    let stderr = String::from_utf8(out.stderr).expect("stderr utf8");
    let v: Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|e| {
        panic!("invalid JSON on stdout (exit {code}): {e}\nstdout: {stdout}\nstderr: {stderr}")
    });
    assert!(
        stderr.is_empty(),
        "JSON mode must not write to stderr: {stderr}"
    );
    (code, v, stdout)
}

const SYNTH_PG_LIB: &str = r#"
library (synth_pg) {
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  pulling_resistance_unit : "1kohm";
  capacitive_load_unit (1, pf);
  nom_process : 1.0;
  nom_temperature : 25.0;
  nom_voltage : 1.8;

  cell (INVX1) {
    pg_pin (VPWR) { pg_type : primary_power; voltage_name : "VPWR"; }
    pg_pin (VGND) { pg_type : primary_ground; voltage_name : "VGND"; }
    pin (A) { direction : input; capacitance : 0.01; }
    pin (Y) { direction : output; function : "(!A)"; }
  }

  cell (NAND2X1) {
    pg_pin (VPWR) { pg_type : primary_power; voltage_name : "VPWR"; }
    pg_pin (VGND) { pg_type : primary_ground; voltage_name : "VGND"; }
    pin (A) { direction : input; capacitance : 0.01; }
    pin (B) { direction : input; capacitance : 0.01; }
    pin (Y) { direction : output; function : "(!(A & B))"; }
  }

  cell (TIEHI) {
    pg_pin (VPWR) { pg_type : primary_power; voltage_name : "VPWR"; }
    pg_pin (VGND) { pg_type : primary_ground; voltage_name : "VGND"; }
    pin (HI) { direction : output; function : "1"; }
  }

  cell (TIELO) {
    pg_pin (VPWR) { pg_type : primary_power; voltage_name : "VPWR"; }
    pg_pin (VGND) { pg_type : primary_ground; voltage_name : "VGND"; }
    pin (LO) { direction : output; function : "0"; }
  }
}
"#;

/// RCLI-REQ-0011, RCLI-REQ-0019: Import synthetic netlists covering escaped names, buses in both
/// directions (`[1:0]` and `[0:1]`), `inout` ports, continuous `assign`s, constants (`1'b0`,
/// `1'b1`), tie cells, `supply0`/`supply1` nets, and `pg_pin` connections; verify exact
/// `FrozenNetlist` equality and ONE aggregated `V0310` warning when PG/supply connections are dropped.
#[test]
fn import_synthetic_netlist_escaped_buses_assigns_tie_and_pg_pins() {
    let root = temp_dir("synth-netlist");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();

    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let lib_file = root.join("synth_pg.lib");
    fs::write(&lib_file, SYNTH_PG_LIB).unwrap();

    let v_file = root.join("top.v");
    let verilog = r#"
module top (
  input wire [1:0] a,
  input wire [0:1] b,
  inout wire io_p,
  output wire [1:0] y,
  input supply1 VPWR,
  input supply0 VGND
);
  supply1 vdd_net;
  supply0 vss_net;
  wire \u1/n0 ;
  wire tie_hi_net;

  TIEHI \u_tie/0 (
    .HI(tie_hi_net),
    .VPWR(VPWR),
    .VGND(VGND)
  );

  INVX1 u_inv (
    .A(a[1]),
    .Y(\u1/n0 ),
    .VPWR(vdd_net),
    .VGND(vss_net)
  );

  NAND2X1 u_nand (
    .A(\u1/n0 ),
    .B(b[0]),
    .Y(y[1]),
    .VPWR(VPWR),
    .VGND(VGND)
  );

  assign y[0] = 1'b0;
  assign io_p = tie_hi_net;
endmodule
"#;
    fs::write(&v_file, verilog).unwrap();

    // Combined --lib + --netlist import in one transaction.
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_file.to_str().unwrap(),
        "--corner",
        "tt",
        "--netlist",
        v_file.to_str().unwrap(),
        "-m",
        "import lib and netlist",
    ]);
    assert_eq!(code, 0, "import failed: {env}");
    assert_eq!(env["new_rev"], "rev1");
    assert_eq!(env["metrics"]["top"], "top");
    assert_eq!(env["metrics"]["cells"], 3);
    assert_eq!(env["metrics"]["ports"], 4);
    assert_eq!(env["metrics"]["assigns"], 2);

    // Exactly ONE aggregated V0310 warning for dropped pg_pin / supply ports.
    let diags = env["diagnostics"].as_array().unwrap();
    let pg_warns: Vec<&Value> = diags
        .iter()
        .filter(|d| d["code"] == "V0310" && d["severity"] == "warning")
        .collect();
    assert_eq!(
        pg_warns.len(),
        1,
        "expected exactly one aggregated V0310 warning, got: {diags:?}"
    );
    assert!(
        pg_warns[0]["message"]
            .as_str()
            .unwrap()
            .contains("dropped 6 pg_pin connection(s) and 2 supply port(s)"),
        "unexpected warning message: {}",
        pg_warns[0]["message"]
    );

    // Build expected GateNetlist and compare FrozenNetlist equality.
    let mut g = GateNetlist::new("top").unwrap();
    let p_a = g.add_port("a", PortDir::Input, Some((1, 0))).unwrap();
    let a_bits = g.ports()[p_a].bits.clone();
    let p_b = g.add_port("b", PortDir::Input, Some((0, 1))).unwrap();
    let b_bits = g.ports()[p_b].bits.clone();
    let p_io = g.add_port("io_p", PortDir::Inout, None).unwrap();
    let io_bit = g.ports()[p_io].bits[0];
    let p_y = g.add_port("y", PortDir::Output, Some((1, 0))).unwrap();
    let y_bits = g.ports()[p_y].bits.clone();

    let n_u1_n0 = g.add_net(Some(r"u1\/n0"), None).unwrap();
    let n_tie_hi = g.add_net(Some("tie_hi_net"), None).unwrap();

    let c_tiehi = g
        .add_cell_type("TIEHI", &[("HI".to_string(), PinDir::Output)])
        .unwrap();
    let i_tie = g.add_instance(r"u_tie\/0", c_tiehi).unwrap();
    g.connect(i_tie, PinRef::Index(0), Conn::Net(n_tie_hi))
        .unwrap();

    let c_inv = g
        .add_cell_type(
            "INVX1",
            &[
                ("A".to_string(), PinDir::Input),
                ("Y".to_string(), PinDir::Output),
            ],
        )
        .unwrap();
    let i_inv = g.add_instance("u_inv", c_inv).unwrap();
    g.connect(i_inv, PinRef::Index(0), Conn::Net(a_bits[0]))
        .unwrap();
    g.connect(i_inv, PinRef::Index(1), Conn::Net(n_u1_n0))
        .unwrap();

    let c_nand = g
        .add_cell_type(
            "NAND2X1",
            &[
                ("A".to_string(), PinDir::Input),
                ("B".to_string(), PinDir::Input),
                ("Y".to_string(), PinDir::Output),
            ],
        )
        .unwrap();
    let i_nand = g.add_instance("u_nand", c_nand).unwrap();
    g.connect(i_nand, PinRef::Index(0), Conn::Net(n_u1_n0))
        .unwrap();
    g.connect(i_nand, PinRef::Index(1), Conn::Net(b_bits[0]))
        .unwrap();
    g.connect(i_nand, PinRef::Index(2), Conn::Net(y_bits[0]))
        .unwrap();

    g.add_assign(y_bits[1], Conn::Const(false)).unwrap();
    g.add_assign(io_bit, Conn::Net(n_tie_hi)).unwrap();

    let expected_frozen = g.freeze();

    let ws = Workspace::open(&ws_path).unwrap();
    let rev1 = ws.revision(ws.resolve_query("rev1").unwrap()).unwrap();
    let rbin = rusteda_workspace::netlist::open_rev(&rev1).unwrap();
    let loaded = rusteda_workspace::netlist::decode(&rbin).unwrap();
    assert_eq!(&loaded, &expected_frozen);

    // Re-importing the exact same netlist on rev1 -> rev2 produces byte-identical netlist.rbin.
    let (code2, env2, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--netlist",
        v_file.to_str().unwrap(),
    ]);
    assert_eq!(code2, 0);
    assert_eq!(env2["new_rev"], "rev2");
    assert_eq!(
        env["tables"]["netlist"]["hash"], env2["tables"]["netlist"]["hash"],
        "re-import must produce identical table hash"
    );
    let rev2 = ws.revision(ws.resolve_query("rev2").unwrap()).unwrap();
    let rbin2_map = rusteda_workspace::netlist::open_rev(&rev2).unwrap();
    let rbin1 = fs::read(rbin.path()).unwrap();
    let rbin2 = fs::read(rbin2_map.path()).unwrap();
    assert_eq!(
        rbin1, rbin2,
        "re-imported netlist.rbin must be byte-identical"
    );

    // Stale base revision (rev1 when current is rev2) -> exits 4 (REVISION_CONFLICT).
    let (code_stale, env_stale, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--netlist",
        v_file.to_str().unwrap(),
    ]);
    assert_eq!(code_stale, 4);
    assert_eq!(env_stale["error"]["code"], "REVISION_CONFLICT");

    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0019: a flat netlist with concatenations, part-selects in both directions, sized
/// constants and escaped buses in `assign` statements imports bit for bit as the reader specifies
/// (most significant bit first, the bits of a part-select from its left to its right index); a
/// width mismatch is still rejected.
#[test]
fn import_flat_concatenations_part_selects_and_sized_constants() {
    let root = temp_dir("concat-assigns");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();
    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);
    let lib_file = root.join("synth_pg.lib");
    fs::write(&lib_file, SYNTH_PG_LIB).unwrap();

    let v_file = root.join("top.v");
    let verilog = r#"
module top(a, b, y, z, q);
  input [3:0] a;
  input b;
  output [3:0] y;
  output [1:0] z;
  output [0:2] q;
  wire [3:0] \u.bus ;
  wire [1:0] w;
  INVX1 i0 (
    .A(a[0]),
    .Y(\u.bus [0])
  );
  INVX1 i1 (
    .A(a[1]),
    .Y(\u.bus [1])
  );
  NAND2X1 n0 (
    .A(a[2]),
    .B(b),
    .Y(w[1])
  );
  assign \u.bus [3:2] = 2'h1;
  assign w[0] = 1'b0;
  assign y = { \u.bus [1:0], w };
  assign z[1:0] = { a[3], 1'h1 };
  assign q = a[1:3];
endmodule
"#;
    fs::write(&v_file, verilog).unwrap();
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_file.to_str().unwrap(),
        "--netlist",
        v_file.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "import failed: {env}");
    assert_eq!(env["metrics"]["cells"], 3);
    assert_eq!(env["metrics"]["assigns"], 12);

    let ws = Workspace::open(&ws_path).unwrap();
    let rev1 = ws.revision(ws.resolve_query("rev1").unwrap()).unwrap();
    let rbin = rusteda_workspace::netlist::open_rev(&rev1).unwrap();
    let n = rusteda_workspace::netlist::decode(&rbin).unwrap();
    let name = |row: u32| -> String {
        match row {
            rusteda_core::netlist::CONST0 => "0".to_string(),
            rusteda_core::netlist::CONST1 => "1".to_string(),
            r => {
                let rec = &n.nets[r as usize];
                let base = n.string(rec.name).unwrap().to_string();
                if rec.index == rusteda_core::netlist::SCALAR {
                    base
                } else {
                    format!("{base}[{}]", rec.index)
                }
            }
        }
    };
    let got: Vec<(String, String)> = n
        .assigns
        .iter()
        .map(|a| (name(a.lhs), name(a.rhs)))
        .collect();
    let want: Vec<(String, String)> = [
        ("u.bus[3]", "0"),
        ("u.bus[2]", "1"),
        ("w[0]", "0"),
        ("y[3]", "u.bus[1]"),
        ("y[2]", "u.bus[0]"),
        ("y[1]", "w[1]"),
        ("y[0]", "w[0]"),
        ("z[1]", "a[3]"),
        ("z[0]", "1"),
        ("q[0]", "a[1]"),
        ("q[1]", "a[2]"),
        ("q[2]", "a[3]"),
    ]
    .iter()
    .map(|&(l, r)| (l.to_string(), r.to_string()))
    .collect();
    assert_eq!(got, want);

    // 3 bits to 4: rejected with a width diagnostic, as by the full reader.
    let bad = verilog.replace("assign q = a[1:3];", "assign y = a[2:0];");
    let v_bad = root.join("bad.v");
    fs::write(&v_bad, bad).unwrap();
    let ws2 = root.join("ws2");
    let ws2_s = ws2.to_str().unwrap();
    assert_eq!(run_json(&["init", "-w", ws2_s]).0, 0);
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws2_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_file.to_str().unwrap(),
        "--netlist",
        v_bad.to_str().unwrap(),
    ]);
    assert_ne!(code, 0, "a width mismatch must be rejected: {env}");
    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0024: a netlist that the streaming reader declines (here a part-select against the
/// declared direction) is read by the full reader only within `--max-netlist-ast-mib`; beyond it
/// the import is rejected with a `V0300` error that names the budget and the option, and HEAD
/// does not move. A netlist that the streaming reader takes ignores the budget.
#[test]
fn full_reader_budget_bounds_netlists_the_streaming_reader_declines() {
    let root = temp_dir("ast-budget");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();
    assert_eq!(run_json(&["init", "-w", ws_s]).0, 0);
    let lib_file = root.join("synth_pg.lib");
    fs::write(&lib_file, SYNTH_PG_LIB).unwrap();
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_file.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "library import failed: {env}");

    let netlist = |select: &str| {
        format!(
            "module top(a, y, q);\n  input [3:0] a;\n  output y;\n  output [2:0] q;\n  \
             INVX1 i0 (.A(a[0]), .Y(y));\n  assign q = {select};\nendmodule\n"
        )
    };
    let declined = root.join("declined.v");
    fs::write(&declined, netlist("a[1:3]")).unwrap();
    let streamed = root.join("streamed.v");
    fs::write(&streamed, netlist("a[3:1]")).unwrap();
    let import = |file: &Path, extra: &[&str]| {
        let mut args = vec![
            "import",
            "-w",
            ws_s,
            "--base-rev",
            "rev1",
            "--netlist",
            file.to_str().unwrap(),
        ];
        args.extend_from_slice(extra);
        run_json(&args)
    };

    let (code, env, _) = import(&declined, &["--max-netlist-ast-mib", "0"]);
    assert_eq!(code, 1, "over the budget: {env}");
    assert_eq!(env["error"]["code"], "INPUT_REJECTED");
    let d0 = &env["diagnostics"][0];
    assert_eq!(d0["code"], "V0300");
    let msg = d0["message"].as_str().unwrap();
    assert!(
        msg.contains("0 MiB") && msg.contains("--max-netlist-ast-mib"),
        "{msg}"
    );

    // The rejection left HEAD at rev1; the streaming reader ignores the budget.
    assert_eq!(run_json(&["show", "-w", ws_s]).1["rev"], "rev1");
    let (code, env, _) = import(&streamed, &["--max-netlist-ast-mib", "0"]);
    assert_eq!(code, 0, "streamed netlist: {env}");
    assert_eq!(env["metrics"]["assigns"], 3);
    assert_eq!(run_json(&["show", "-w", ws_s]).1["rev"], "rev2");

    // The default budget (64 MiB) reads the declined netlist with the full reader.
    let ws2 = root.join("ws2");
    let ws2_s = ws2.to_str().unwrap();
    assert_eq!(run_json(&["init", "-w", ws2_s]).0, 0);
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws2_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_file.to_str().unwrap(),
        "--netlist",
        declined.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "within the default budget: {env}");
    assert_eq!(env["metrics"]["assigns"], 3);

    // The budget only applies to a netlist import.
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws2_s,
        "--base-rev",
        "rev1",
        "--lib",
        lib_file.to_str().unwrap(),
        "--max-netlist-ast-mib",
        "1",
    ]);
    assert_eq!(code, 1, "a budget without --netlist: {env}");
    assert_eq!(env["error"]["code"], "USAGE");
    let _ = fs::remove_dir_all(&root);
}

/// RCLI-REQ-0019: Unresolved cell, unresolved pin, and multi-corner pin-direction conflict must
/// fail with exit 1 (`INPUT_REJECTED`), a `V0302` diagnostic, and `file:line:col` location.
#[test]
fn unresolved_cell_unresolved_pin_and_corner_direction_conflict() {
    let root = temp_dir("netlist-errors");
    let ws_path = root.join("ws");
    let ws_s = ws_path.to_str().unwrap();

    let (code, _, _) = run_json(&["init", "-w", ws_s]);
    assert_eq!(code, 0);

    let lib_a = root.join("corner_a.lib");
    let lib_b = root.join("corner_b.lib");
    fs::write(
        &lib_a,
        r#"
library (lib_a) {
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  pulling_resistance_unit : "1kohm";
  capacitive_load_unit (1, pf);
  nom_process : 1.0; nom_temperature : 25.0; nom_voltage : 1.8;
  cell (INVX1) {
    pin (A) { direction : input; capacitance : 0.01; }
    pin (Y) { direction : output; function : "(!A)"; }
  }
  cell (BADCELL) {
    pin (P) { direction : input; capacitance : 0.01; }
    pin (Q) { direction : output; }
  }
}
"#,
    )
    .unwrap();
    fs::write(
        &lib_b,
        r#"
library (lib_b) {
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  pulling_resistance_unit : "1kohm";
  capacitive_load_unit (1, pf);
  nom_process : 1.0; nom_temperature : 25.0; nom_voltage : 1.8;
  cell (INVX1) {
    pin (A) { direction : input; capacitance : 0.01; }
    pin (Y) { direction : output; function : "(!A)"; }
  }
  cell (BADCELL) {
    pin (P) { direction : output; }
    pin (Q) { direction : output; }
  }
}
"#,
    )
    .unwrap();

    let (code, _, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev0",
        "--lib",
        lib_a.to_str().unwrap(),
        "--corner",
        "fast",
        "--lib",
        lib_b.to_str().unwrap(),
        "--corner",
        "slow",
    ]);
    assert_eq!(code, 0);

    // 1. Unresolved cell -> exit 1 with V0302 and file:line:col.
    let v_missing_cell = root.join("missing_cell.v");
    fs::write(
        &v_missing_cell,
        "module top(input wire a, output wire y);\n  NO_SUCH_CELL u0 (.A(a), .Y(y));\nendmodule\n",
    )
    .unwrap();
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--netlist",
        v_missing_cell.to_str().unwrap(),
    ]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "INPUT_REJECTED");
    let d0 = &env["diagnostics"][0];
    assert_eq!(d0["code"], "V0302");
    assert_eq!(d0["location"]["line"], 2);
    assert!(d0["location"]["col"].as_u64().unwrap() >= 1);
    assert_eq!(d0["location"]["file"], v_missing_cell.to_str().unwrap());

    // 2. Unresolved pin -> exit 1 with V0302 and file:line:col.
    let v_missing_pin = root.join("missing_pin.v");
    fs::write(
        &v_missing_pin,
        "module top(input wire a, output wire y);\n  INVX1 u0 (.A(a), .NO_SUCH_PIN(y));\nendmodule\n",
    )
    .unwrap();
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--netlist",
        v_missing_pin.to_str().unwrap(),
    ]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "INPUT_REJECTED");
    let d0 = &env["diagnostics"][0];
    assert_eq!(d0["code"], "V0302");
    assert_eq!(d0["location"]["line"], 2);

    // 3. Cross-corner pin direction disagreement -> exit 1 with V0302 naming both corners.
    let v_conflict = root.join("conflict.v");
    fs::write(
        &v_conflict,
        "module top(input wire a, output wire y);\n  BADCELL u0 (.P(a), .Q(y));\nendmodule\n",
    )
    .unwrap();
    let (code, env, _) = run_json(&[
        "import",
        "-w",
        ws_s,
        "--base-rev",
        "rev1",
        "--netlist",
        v_conflict.to_str().unwrap(),
    ]);
    assert_eq!(code, 1);
    assert_eq!(env["error"]["code"], "INPUT_REJECTED");
    let d0 = &env["diagnostics"][0];
    assert_eq!(d0["code"], "V0302");
    assert_eq!(d0["location"]["line"], 2);
    let msg = d0["message"].as_str().unwrap();
    assert!(
        msg.contains("fast") && msg.contains("slow"),
        "expected both corner names in conflict diagnostic: {msg}"
    );

    let _ = fs::remove_dir_all(&root);
}

fn read_vm_hwm_kib() -> Option<u64> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("VmHWM:") {
            let num = rest.split_whitespace().next()?;
            return num.parse().ok();
        }
    }
    None
}

/// Local-only corpus and 500k-cell scale benchmark (`--ignored`).
#[test]
#[ignore = "requires local .work/assess/probe-* corpus and PDK files"]
fn corpus_and_500k_scale_benchmark() {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo_root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .unwrap();
    let Some(assess_dir) = repo_root.ancestors().find_map(|p| {
        let cand = p.join(".work/assess");
        cand.exists().then_some(cand)
    }) else {
        eprintln!("skipping: .work/assess not present");
        return;
    };

    let sky130_lib = assess_dir.join("probe-sky130/lib/sky130_fd_sc_hd__tt_025C_1v80.lib");
    let gf180_7t_lib = assess_dir.join("probe-gf180/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_3v30.lib");
    let gf180_9t_lib = assess_dir.join("probe-gf180/lib/gf180mcu_fd_sc_mcu9t5v0__tt_025C_3v30.lib");
    if !sky130_lib.is_file() || !gf180_7t_lib.is_file() || !gf180_9t_lib.is_file() {
        eprintln!("skipping: probe-* liberty files not present");
        return;
    }

    let root = temp_dir("corpus-bench");

    // 1. sky130 corpus: syn/* and hand/*
    let ws_sky = root.join("ws-sky130");
    let ws_sky_s = ws_sky.to_str().unwrap();
    assert_eq!(run_json(&["init", "-w", ws_sky_s]).0, 0);
    assert_eq!(
        run_json(&[
            "import",
            "-w",
            ws_sky_s,
            "--base-rev",
            "rev0",
            "--lib",
            sky130_lib.to_str().unwrap(),
            "--corner",
            "tt",
        ])
        .0,
        0
    );

    let mut rev = "rev1".to_string();
    let mut syn_counter8_frozen = None;
    let mut sky_count = 0usize;
    for group in ["syn", "hand"] {
        let gdir = assess_dir.join("probe-sky130").join(group);
        let mut entries: Vec<PathBuf> = fs::read_dir(&gdir)
            .unwrap()
            .filter_map(|e| e.ok().map(|x| x.path()))
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("v"))
            .collect();
        entries.sort();
        for vpath in entries {
            let fname = vpath.file_name().unwrap().to_str().unwrap();
            let (code, env, _) = run_json(&[
                "import",
                "-w",
                ws_sky_s,
                "--base-rev",
                &rev,
                "--netlist",
                vpath.to_str().unwrap(),
            ]);
            assert_eq!(code, 0, "sky130/{group}/{fname} failed: {env}");
            rev = env["new_rev"].as_str().unwrap().to_string();
            sky_count += 1;

            let ws = Workspace::open(&ws_sky).unwrap();
            let rev_obj = ws.revision(ws.resolve_query(&rev).unwrap()).unwrap();
            let rbin = rusteda_workspace::netlist::open_rev(&rev_obj).unwrap();
            let loaded = rusteda_workspace::netlist::decode(&rbin).unwrap();
            if group == "syn" && fname == "counter8.v" {
                let g = rusteda_workspace::netlist::thaw_rev(&rev_obj).unwrap();
                syn_counter8_frozen = Some(g.freeze());
            }
            if group == "hand" && fname.starts_with("counter8_pwr_") {
                let diags = env["diagnostics"].as_array().unwrap();
                let v0310_count = diags.iter().filter(|d| d["code"] == "V0310").count();
                assert_eq!(
                    v0310_count, 1,
                    "sky130/{group}/{fname} must emit 1 V0310 warning: {diags:?}"
                );
                assert_eq!(
                    Some(&loaded),
                    syn_counter8_frozen.as_ref(),
                    "sky130/{group}/{fname} FrozenNetlist must equal syn/counter8.v"
                );
            }
        }
    }
    eprintln!("CORPUS_SKY130: imported {sky_count} netlists (syn + hand)");

    // 2. gf180 corpus: syn/7t-* and syn/9t-*
    let ws_gf = root.join("ws-gf180");
    let ws_gf_s = ws_gf.to_str().unwrap();
    assert_eq!(run_json(&["init", "-w", ws_gf_s]).0, 0);
    assert_eq!(
        run_json(&[
            "import",
            "-w",
            ws_gf_s,
            "--base-rev",
            "rev0",
            "--lib",
            gf180_7t_lib.to_str().unwrap(),
            "--corner",
            "gf7t",
            "--lib",
            gf180_9t_lib.to_str().unwrap(),
            "--corner",
            "gf9t",
        ])
        .0,
        0
    );

    let mut rev_gf = "rev1".to_string();
    let mut gf_count = 0usize;
    let mut subdirs: Vec<PathBuf> = fs::read_dir(assess_dir.join("probe-gf180/syn"))
        .unwrap()
        .filter_map(|e| e.ok().map(|x| x.path()))
        .filter(|p| p.is_dir())
        .collect();
    subdirs.sort();
    for sdir in subdirs {
        let sname = sdir.file_name().unwrap().to_str().unwrap();
        let mut entries: Vec<PathBuf> = fs::read_dir(&sdir)
            .unwrap()
            .filter_map(|e| e.ok().map(|x| x.path()))
            .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("v"))
            .collect();
        entries.sort();
        for vpath in entries {
            let fname = vpath.file_name().unwrap().to_str().unwrap();
            let (code, env, _) = run_json(&[
                "import",
                "-w",
                ws_gf_s,
                "--base-rev",
                &rev_gf,
                "--netlist",
                vpath.to_str().unwrap(),
            ]);
            assert_eq!(code, 0, "gf180/syn/{sname}/{fname} failed: {env}");
            rev_gf = env["new_rev"].as_str().unwrap().to_string();
            gf_count += 1;
        }
    }
    eprintln!("CORPUS_GF180: imported {gf_count} netlists across 7t/9t flows");

    // 3. 500k scale benchmark
    let scale_v = assess_dir.join("probe-scale/designs/r500k.v");
    if scale_v.exists() {
        let ws_scale = root.join("ws-scale");
        let ws_s = ws_scale.to_str().unwrap();
        assert_eq!(run_json(&["init", "-w", ws_s]).0, 0);
        assert_eq!(
            run_json(&[
                "import",
                "-w",
                ws_s,
                "--base-rev",
                "rev0",
                "--lib",
                sky130_lib.to_str().unwrap(),
                "--corner",
                "tt",
            ])
            .0,
            0
        );

        let t0 = Instant::now();
        let (c, env, _) = run_json(&[
            "import",
            "-w",
            ws_s,
            "--base-rev",
            "rev1",
            "--netlist",
            scale_v.to_str().unwrap(),
        ]);
        let elapsed = t0.elapsed();
        assert_eq!(c, 0, "r500k import failed: {env}");
        let ws = Workspace::open(&ws_scale).unwrap();
        let rev2 = ws.revision(ws.resolve_query("rev2").unwrap()).unwrap();
        let rbin = rusteda_workspace::netlist::open_rev(&rev2).unwrap();
        let rbin_bytes = fs::metadata(rbin.path()).unwrap().len();
        eprintln!(
            "SCALE_500K: cells={} nets={} ports={} wall_ms={:.1} rbin_mib={:.2} parent_hwm_kib={:?}",
            env["metrics"]["cells"],
            env["metrics"]["nets"],
            env["metrics"]["ports"],
            elapsed.as_secs_f64() * 1000.0,
            (rbin_bytes as f64) / (1024.0 * 1024.0),
            read_vm_hwm_kib()
        );
    }

    let _ = fs::remove_dir_all(&root);
}

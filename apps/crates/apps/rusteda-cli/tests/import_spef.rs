// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Atomic import and dependency invalidation (RCLI-REQ-0070, RCLI-REQ-0075, RCLI-REQ-0076,
//! STA-REQ-0700).
use rusteda_workspace::{Invocation, RbinWriter, Upstream, Workspace};
use serde_json::Value;
use std::{
    fs,
    process::{Command, Stdio},
    sync::atomic::{AtomicU64, Ordering},
};
fn run(args: &[&str]) -> Value {
    let out = Command::new(env!("CARGO_BIN_EXE_rusteda"))
        .args(["--format", "json"])
        .args(args)
        .stdin(Stdio::null())
        .output()
        .unwrap();
    serde_json::from_slice(&out.stdout).unwrap_or_else(|_| panic!("{:?}", out))
}
#[test]
fn spef_requires_corner_limits_and_invalidates_old_timing() {
    static ID: AtomicU64 = AtomicU64::new(0);
    let dir = std::env::temp_dir().join(format!(
        "spef-import-{}-{}",
        std::process::id(),
        ID.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&dir).unwrap();
    let ws_path = dir.join("ws");
    let w = ws_path.to_str().unwrap();
    let lib = dir.join("cells.lib");
    let net = dir.join("top.v");
    let spef = dir.join("top.spef");
    fs::write(&lib, "library (l) { time_unit : \"1ns\"; capacitive_load_unit(1,pf); cell (BUF) { pin(A) {direction: input;} pin(Y) {direction: output; function: \"A\";} } }").unwrap();
    fs::write(
        &net,
        "module top(input a, output y); BUF u(.A(a),.Y(y)); endmodule\n",
    )
    .unwrap();
    fs::write(&spef,"*SPEF \"ieee 1481-1999\"\n*DESIGN top\n*DIVIDER /\n*DELIMITER :\n*BUS_DELIMITER []\n*T_UNIT 1 NS\n*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*D_NET a 0\n*CONN\n*P a I\n*I u:A I\n*RES\n1 a u:A 2\n*END\n").unwrap();
    assert_eq!(run(&["init", "-w", w])["new_rev"], "rev0");
    assert_eq!(
        run(&[
            "import",
            "-w",
            w,
            "--base-rev",
            "rev0",
            "--lib",
            lib.to_str().unwrap(),
            "--corner",
            "tt",
            "--netlist",
            net.to_str().unwrap()
        ])["new_rev"],
        "rev1"
    );
    // Deliberately stage an old timing table without a parasitic dependency.
    {
        let ws = Workspace::open(&ws_path).unwrap();
        let base = ws.resolve_base("rev1").unwrap();
        let inv = Invocation::new("test", "0.1.0", "seed", ["seed"]);
        let mut tx = ws.begin(base, &inv).unwrap();
        let mut writer = RbinWriter::new("STA", 1).unwrap();
        writer.section_bytes(1, 1, &b"old"[..]).unwrap();
        tx.add_rbin("sta", "sta.rbin", &writer, Upstream::new())
            .unwrap();
        tx.commit(Default::default()).unwrap();
    }
    let imported = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev2",
        "--spef",
        spef.to_str().unwrap(),
        "--spef-corner",
        "tt",
    ]);
    assert_eq!(imported["ok"], true, "{imported}");
    assert!(imported["tables"].get("sta").is_none());
    assert!(imported["tables"].get("parasitics").is_some());
    let conflict = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev2",
        "--spef",
        spef.to_str().unwrap(),
        "--spef-corner",
        "tt",
    ]);
    assert_eq!(conflict["error"]["code"], "REVISION_CONFLICT");
    fs::write(&spef, "*NOT_SUPPORTED\n").unwrap();
    let bad = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev3",
        "--spef",
        spef.to_str().unwrap(),
        "--spef-corner",
        "tt",
    ]);
    assert_eq!(bad["ok"], false);
    assert_eq!(run(&["show", "-w", w])["rev"], "rev3");
    fs::remove_dir_all(&dir).unwrap();
}

// RCLI-REQ-0073: gzip is a bounded transport; canonical table bytes do not depend on it.
#[test]
fn compressed_sources_are_bounded_and_fail_atomically() {
    use std::io::Write;
    let dir = std::env::temp_dir().join(format!("spef-compressed-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    let ws_path = dir.join("ws");
    let w = ws_path.to_str().unwrap();
    let lib = dir.join("cells.lib");
    let net = dir.join("top.v");
    let plain = dir.join("input.spef");
    let compressed = dir.join("input.spef.gz");
    fs::write(&lib,"library (l) { time_unit : \"1ns\"; capacitive_load_unit(1,pf); cell(BUF){pin(A){direction:input;}pin(Y){direction:output;function:\"A\";}}}").unwrap();
    fs::write(
        &net,
        "module top(input a,output y);BUF u(.A(a),.Y(y));endmodule\n",
    )
    .unwrap();
    let source = "*SPEF \"ieee 1481-1999\"\n*DESIGN top\n*DIVIDER /\n*DELIMITER :\n*BUS_DELIMITER []\n*T_UNIT 1 NS\n*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*D_NET a 0\n*CONN\n*P a I\n*I u:A I\n*RES\n1 a u:A 2\n*END\n";
    fs::write(&plain, source).unwrap();
    let gzip = |bytes: &[u8]| {
        let mut g = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        g.write_all(bytes).unwrap();
        g.finish().unwrap()
    };
    fs::write(&compressed, gzip(source.as_bytes())).unwrap();
    assert_eq!(run(&["init", "-w", w])["new_rev"], "rev0");
    assert_eq!(
        run(&[
            "import",
            "-w",
            w,
            "--base-rev",
            "rev0",
            "--lib",
            lib.to_str().unwrap(),
            "--corner",
            "tt",
            "--netlist",
            net.to_str().unwrap()
        ])["new_rev"],
        "rev1"
    );
    let a = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev1",
        "--spef",
        plain.to_str().unwrap(),
        "--spef-corner",
        "tt",
    ]);
    assert_eq!(a["ok"], true, "{a}");
    let b = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev2",
        "--spef",
        compressed.to_str().unwrap(),
        "--spef-corner",
        "tt",
    ]);
    assert_eq!(b["ok"], true, "{b}");
    assert_eq!(b["metrics"]["spef_compression"], "gzip");
    let ws = Workspace::open(&ws_path).unwrap();
    let a = ws.revision(ws.resolve_query("rev2").unwrap()).unwrap();
    let b = ws.revision(ws.resolve_query("rev3").unwrap()).unwrap();
    // PARASIT v2 (RCLI-REQ-0075): section 1 names the corners and their sources, section 2 holds
    // the decoded source of the only corner, the same for the plain and the gzip file.
    let x = a.open_rbin("parasitics", "PARASIT", 2..=2).unwrap();
    let y = b.open_rbin("parasitics", "PARASIT", 2..=2).unwrap();
    assert_eq!(x.section_bytes(2).unwrap(), y.section_bytes(2).unwrap());
    drop((x, y, a, b));
    let mut corrupt = gzip(source.as_bytes());
    let end = corrupt.len() - 8;
    corrupt[end] ^= 1;
    fs::write(&compressed, corrupt).unwrap();
    let bad = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev3",
        "--spef",
        compressed.to_str().unwrap(),
        "--spef-corner",
        "tt",
    ]);
    assert_eq!(bad["ok"], false);
    fs::write(&compressed, gzip(&vec![b'x'; 2 << 20])).unwrap();
    let bad = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev3",
        "--spef",
        compressed.to_str().unwrap(),
        "--spef-corner",
        "tt",
        "--max-spef-mib",
        "1",
    ]);
    assert_eq!(bad["ok"], false);
    assert!(bad["error"]["message"].as_str().unwrap().contains("T0072"));
    #[cfg(unix)]
    {
        let link = dir.join("input-link.spef");
        std::os::unix::fs::symlink(&plain, &link).unwrap();
        let bad = run(&[
            "import",
            "-w",
            w,
            "--base-rev",
            "rev3",
            "--spef",
            link.to_str().unwrap(),
            "--spef-corner",
            "tt",
        ]);
        assert_eq!(bad["ok"], false);
    }
    assert_eq!(run(&["show", "-w", w])["rev"], "rev3");
    fs::remove_dir_all(dir).unwrap();
}

// RCLI-REQ-0075: one import binds several SPEF files to named corners in a PARASIT v2 table; a
// later import replaces its corners and keeps the others; flags pair in order; a netlist change
// drops the table.
#[test]
fn several_corners_are_bound_kept_and_replaced() {
    use rusteda_formats::spef::CornerSetMetadata;
    let dir = std::env::temp_dir().join(format!("spef-corners-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let ws_path = dir.join("ws");
    let w = ws_path.to_str().unwrap();
    let lib = dir.join("cells.lib");
    let net = dir.join("top.v");
    fs::write(&lib, "library (l) { time_unit : \"1ns\"; capacitive_load_unit(1,pf); cell(BUF){pin(A){direction:input;}pin(Y){direction:output;function:\"A\";}}}").unwrap();
    fs::write(
        &net,
        "module top(input a,output y);BUF u(.A(a),.Y(y));endmodule\n",
    )
    .unwrap();
    let spef = |name: &str, res: u32| {
        let p = dir.join(format!("{name}.spef"));
        fs::write(&p, format!("*SPEF \"ieee 1481-1999\"\n*DESIGN top\n*DIVIDER /\n*DELIMITER :\n*BUS_DELIMITER []\n*T_UNIT 1 NS\n*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*D_NET a 0\n*CONN\n*P a I\n*I u:A I\n*RES\n1 a u:A {res}\n*END\n")).unwrap();
        p.to_str().unwrap().to_string()
    };
    let (fast, slow, fast2) = (spef("fast", 1), spef("slow", 3), spef("fast2", 2));
    assert_eq!(run(&["init", "-w", w])["new_rev"], "rev0");
    let l = lib.to_str().unwrap();
    let r = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev0",
        "--lib",
        l,
        "--corner",
        "fast",
        "--lib",
        l,
        "--corner",
        "slow",
        "--netlist",
        net.to_str().unwrap(),
    ]);
    assert_eq!(r["new_rev"], "rev1", "{r}");
    let corners = |rev: &str| -> (Vec<String>, Vec<Vec<u8>>) {
        let ws = Workspace::open(&ws_path).unwrap();
        let r = ws.revision(ws.resolve_query(rev).unwrap()).unwrap();
        let t = r.open_rbin("parasitics", "PARASIT", 2..=2).unwrap();
        let set: CornerSetMetadata = serde_json::from_slice(t.section_bytes(1).unwrap()).unwrap();
        let names = set.corners.iter().map(|c| c.corner.clone()).collect();
        let texts = (0..set.corners.len())
            .map(|i| t.section_bytes(i as u32 + 2).unwrap().to_vec())
            .collect();
        (names, texts)
    };
    let both = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev1",
        "--spef",
        &slow,
        "--spef-corner",
        "slow",
        "--spef",
        &fast,
        "--spef-corner",
        "fast",
    ]);
    assert_eq!(both["ok"], true, "{both}");
    assert_eq!(
        both["metrics"]["spef_corners"],
        serde_json::json!(["fast", "slow"])
    );
    let (names, texts) = corners("rev2");
    assert_eq!(names, ["fast", "slow"]);
    assert!(String::from_utf8_lossy(&texts[0]).contains("u:A 1"));
    assert!(String::from_utf8_lossy(&texts[1]).contains("u:A 3"));
    // The netlist and one source per corner.
    assert_eq!(both["sources"].as_array().unwrap().len(), 3, "{both}");

    let again = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev2",
        "--spef",
        &fast2,
        "--spef-corner",
        "fast",
    ]);
    assert_eq!(again["ok"], true, "{again}");
    assert_eq!(
        again["metrics"]["spef_kept_corners"],
        serde_json::json!(["slow"])
    );
    // A table that is replaced, not dropped, gives no T0140 (RCLI-REQ-0076).
    assert!(
        !again["diagnostics"]
            .as_array()
            .unwrap()
            .iter()
            .any(|d| d["code"] == "T0140"),
        "{again}"
    );
    let (names3, texts3) = corners("rev3");
    assert_eq!(names3, ["fast", "slow"]);
    assert!(String::from_utf8_lossy(&texts3[0]).contains("u:A 2"));
    assert_eq!(texts3[1], texts[1], "the kept corner keeps its source");

    for bad in [
        vec![
            "--spef",
            fast.as_str(),
            "--spef",
            slow.as_str(),
            "--spef-corner",
            "fast",
        ],
        vec![
            "--spef",
            fast.as_str(),
            "--spef-corner",
            "fast",
            "--spef",
            slow.as_str(),
            "--spef-corner",
            "fast",
        ],
        vec!["--spef", fast.as_str(), "--spef-corner", "typ"],
    ] {
        let mut args = vec!["import", "-w", w, "--base-rev", "rev3"];
        args.extend(bad.iter().copied());
        let r = run(&args);
        assert_eq!(r["ok"], false, "{args:?}: {r}");
    }
    assert_eq!(run(&["show", "-w", w])["rev"], "rev3");

    // A changed netlist drops the parasitics of every corner.
    fs::write(
        &net,
        "module top(input a,output y);wire n;BUF u(.A(a),.Y(n));BUF v(.A(n),.Y(y));endmodule\n",
    )
    .unwrap();
    let eco = run(&[
        "import",
        "-w",
        w,
        "--base-rev",
        "rev3",
        "--netlist",
        net.to_str().unwrap(),
    ]);
    assert_eq!(eco["ok"], true, "{eco}");
    assert!(eco["tables"].get("parasitics").is_none(), "{eco}");
    // RCLI-REQ-0076: the drop is never silent: one warning T0140 names the dropped corners.
    let warned: Vec<&Value> = eco["diagnostics"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|d| d["code"] == "T0140")
        .collect();
    assert_eq!(warned.len(), 1, "{eco}");
    assert_eq!(warned[0]["severity"], "warning", "{eco}");
    let msg = warned[0]["message"].as_str().unwrap();
    assert!(msg.contains("fast, slow") && msg.contains("rev4"), "{msg}");
    fs::remove_dir_all(&dir).unwrap();
}

// RCLI-REQ-0075: the decoded sources that one import holds at once total at most eight per-file
// byte budgets: eight corners of almost one MiB each fit a 1 MiB budget, a ninth is rejected with
// T0072 and nothing is committed.
#[test]
fn the_sources_of_a_table_are_bounded_together() {
    let dir = std::env::temp_dir().join(format!("spef-budget-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let ws_path = dir.join("ws");
    let w = ws_path.to_str().unwrap();
    let lib = dir.join("cells.lib");
    let net = dir.join("top.v");
    fs::write(&lib, "library (l) { time_unit : \"1ns\"; capacitive_load_unit(1,pf); cell(BUF){pin(A){direction:input;}pin(Y){direction:output;function:\"A\";}}}").unwrap();
    fs::write(
        &net,
        "module top(input a,output y);BUF u(.A(a),.Y(y));endmodule\n",
    )
    .unwrap();
    // A valid SPEF padded with comment lines to just under 1 MiB.
    let mut text = String::from(
        "*SPEF \"ieee 1481-1999\"\n*DESIGN top\n*DIVIDER /\n*DELIMITER :\n*BUS_DELIMITER []\n*T_UNIT 1 NS\n*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*D_NET a 0\n*CONN\n*P a I\n*I u:A I\n*RES\n1 a u:A 2\n*END\n",
    );
    let pad = format!("// {}\n", "x".repeat(1020));
    while text.len() + pad.len() < (1 << 20) - 4096 {
        text.push_str(&pad);
    }
    let spef = dir.join("padded.spef");
    fs::write(&spef, &text).unwrap();
    let s = spef.to_str().unwrap();
    let names: Vec<String> = (0..9).map(|i| format!("c{i}")).collect();
    let l = lib.to_str().unwrap();
    let mut args = vec!["import", "-w", w, "--base-rev", "rev0"];
    for n in &names {
        args.extend(["--lib", l, "--corner", n.as_str()]);
    }
    args.extend(["--netlist", net.to_str().unwrap()]);
    assert_eq!(run(&["init", "-w", w])["new_rev"], "rev0");
    assert_eq!(run(&args)["new_rev"], "rev1");
    let import = |count: usize| {
        let mut args = vec![
            "import",
            "-w",
            w,
            "--base-rev",
            "rev1",
            "--max-spef-mib",
            "1",
        ];
        for n in &names[..count] {
            args.extend(["--spef", s, "--spef-corner", n.as_str()]);
        }
        run(&args)
    };
    let nine = import(9);
    assert_eq!(nine["ok"], false, "{nine}");
    assert!(
        nine["error"]["message"]
            .as_str()
            .unwrap_or_default()
            .contains("T0072"),
        "{nine}"
    );
    assert_eq!(run(&["show", "-w", w])["rev"], "rev1", "nothing committed");
    let eight = import(8);
    assert_eq!(eight["ok"], true, "{eight}");
    assert_eq!(
        eight["metrics"]["spef_corners"].as_array().unwrap().len(),
        8
    );
    fs::remove_dir_all(&dir).unwrap();
}

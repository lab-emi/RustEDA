// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! PDK identity and profile-bound artifact controls for the step-graph executor.

use super::{Fixture, id, sh};
use rusteda_flow::exec::{self, manifest};
use serde_json::{Value, json};
use std::fs;

/// A profile-bound manifest of another PDK: the fixture's manifest with the
/// `rusteda.native-flow.v1` schema, the given identity and a pinned profile document.
fn native(f: &mut Fixture, pdk: &str, library: &str, corners: &[&str], profile: &Value) {
    fs::write(f.root.join("profile.json"), profile.to_string()).unwrap();
    f.data["schema"] = json!("rusteda.native-flow.v1");
    f.data["pdk"] = json!(pdk);
    f.data["library"] = json!(library);
    f.data["corners"] = json!(corners);
    f.data["files"]["profile"] = id(&f.root.join("profile.json"));
}

fn sky130_profile() -> Value {
    json!({"schema": "rusteda.native-pdk-profile.v1", "name": "sky130hd", "pdk": "sky130A",
        "library": "sky130_fd_sc_hd", "corners": [{"name": "tt"}, {"name": "ss"}]})
}

/// FLOWEXEC-REQ-0033: a `rusteda.native-flow.v1` manifest runs when its identity is that of
/// its pinned PDK profile; its summary names the PDK; `--pdk` must name the manifest's PDK.
#[test]
fn a_profile_bound_manifest_of_another_pdk_runs() {
    let Some(mut f) = Fixture::new(1) else { return };
    native(
        &mut f,
        "sky130A",
        "sky130_fd_sc_hd",
        &["tt", "ss"],
        &sky130_profile(),
    );
    f.data["steps"] = json!([sh("one", "touch one"), sh("two", "touch two")]);
    f.data["gates"] = json!({});
    f.data["artifacts"] = json!({"made": "one"});
    f.write();
    let plan = exec::plan(&f.options()).unwrap();
    assert_eq!(plan["pdk"], "sky130A");
    assert_eq!(plan["library"], "sky130_fd_sc_hd");
    assert_eq!(plan["corners"], json!(["tt", "ss"]));
    // `--pdk`: the family or the exact name is accepted, another PDK is refused.
    for (pdk, accepted) in [
        ("sky130", true),
        ("sky130A", true),
        ("ihp", false),
        ("gf180", false),
        ("sky130B", false),
    ] {
        let mut options = f.options();
        options.pdk = Some(pdk.into());
        match exec::plan(&options) {
            Ok(_) => assert!(accepted, "--pdk {pdk} accepted"),
            Err(error) => {
                assert!(!accepted, "--pdk {pdk}: {}", error.message);
                assert_eq!(error.code, "PDK_MISMATCH");
            }
        }
    }
    let mut refused = f.options();
    refused.pdk = Some("ihp".into());
    assert_eq!(f.run_with(&refused).unwrap_err().code, "PDK_MISMATCH");
    assert!(!f.root.join("run/ownership").exists());
    let mut options = f.options();
    options.pdk = Some("sky130".into());
    options.stop_after = Some("two".into());
    let outcome = f.run_with(&options).unwrap();
    assert_eq!(outcome.exit_code, 0, "{}", outcome.value);
    let summary = f.read("summary.json");
    assert_eq!(summary["schema"], "rusteda.native-frontend.v1");
    assert_eq!(summary["status"], "frontend_completed");
    assert_eq!(summary["pdk"], "sky130A");
    assert_eq!(summary["library"], "sky130_fd_sc_hd");
    assert_eq!(summary["corners"], json!(["tt", "ss"]));
    assert_eq!(summary["artifacts"]["made"], id(&f.output().join("one")));
}

/// FLOWEXEC-REQ-0033: the IHP manifest keeps its fixed identity and accepts `--pdk ihp` only.
#[test]
fn the_ihp_manifest_keeps_its_fixed_identity() {
    let Some(mut f) = Fixture::new(1) else { return };
    f.data["steps"] = json!([sh("one", "true")]);
    f.write();
    let parsed = manifest::parse(f.data.to_string().as_bytes()).unwrap();
    assert_eq!(parsed.identity, manifest::FlowIdentity::ihp());
    let mut options = f.options();
    options.pdk = Some("ihp".into());
    assert_eq!(exec::plan(&options).unwrap()["pdk"], "ihp-sg13g2");
    options.pdk = Some("sky130".into());
    assert_eq!(exec::plan(&options).unwrap_err().code, "PDK_MISMATCH");
    // The IHP schema with another identity, and another PDK without a profile, stay refused.
    type Change = fn(&mut Value);
    let changes: [Change; 5] = [
        |m| m["pdk"] = json!("sky130A"),
        |m| m["library"] = json!("sky130_fd_sc_hd"),
        |m| m["corners"] = json!(["tt"]),
        |m| m["corners"] = json!(["ss", "tt"]),
        |m| m["schema"] = json!("rusteda.native-flow.v1"),
    ];
    for (index, change) in changes.into_iter().enumerate() {
        let mut data = f.data.clone();
        change(&mut data);
        let error = manifest::parse(data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, "MANIFEST_IDENTITY", "case {index}");
    }
}

/// FLOWEXEC-REQ-0033: a profile-bound manifest whose identity is malformed, or differs from
/// the pinned profile, is refused before anything is created.
#[test]
fn a_manifest_that_differs_from_its_profile_is_refused() {
    let Some(mut f) = Fixture::new(1) else { return };
    native(
        &mut f,
        "sky130A",
        "sky130_fd_sc_hd",
        &["tt", "ss"],
        &sky130_profile(),
    );
    f.data["steps"] = json!([sh("one", "touch one")]);
    f.data["gates"] = json!({});
    assert!(manifest::parse(f.data.to_string().as_bytes()).is_ok());
    type Change = fn(&mut Value);
    let malformed: [Change; 6] = [
        |m| m["pdk"] = json!("sky 130"),
        |m| m["library"] = json!(""),
        |m| m["corners"] = json!([]),
        |m| m["corners"] = json!(["tt", "tt"]),
        |m| m["corners"] = json!(["TT", "ss"]),
        |m| {
            m["files"].as_object_mut().unwrap().remove("profile");
        },
    ];
    for (index, change) in malformed.into_iter().enumerate() {
        let mut data = f.data.clone();
        change(&mut data);
        let error = manifest::parse(data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, "MANIFEST_IDENTITY", "case {index}");
    }
    // Well-formed, but not the pinned profile's identity.
    let differing: [Change; 4] = [
        |m| m["pdk"] = json!("gf180mcuD"),
        |m| m["library"] = json!("sky130_fd_sc_hs"),
        |m| m["corners"] = json!(["tt"]),
        |m| m["corners"] = json!(["ss", "tt"]),
    ];
    let good = f.data.clone();
    for (index, change) in differing.into_iter().enumerate() {
        f.data = good.clone();
        change(&mut f.data);
        f.write();
        for error in [
            exec::plan(&f.options()).unwrap_err(),
            f.run_with(&f.options()).unwrap_err(),
        ] {
            assert_eq!(error.code, "MANIFEST_IDENTITY", "case {index}");
            assert!(
                error.message.contains("pinned PDK profile"),
                "case {index}: {}",
                error.message
            );
        }
        assert!(!f.root.join("run/ownership").exists());
        assert!(!f.output().join("one").exists());
    }
    // A pinned file that is not a profile document.
    f.data = good.clone();
    fs::write(f.root.join("profile.json"), "{\"schema\": \"other\"}").unwrap();
    f.data["files"]["profile"] = id(&f.root.join("profile.json"));
    f.write();
    assert_eq!(
        exec::plan(&f.options()).unwrap_err().code,
        "MANIFEST_IDENTITY"
    );
}

/// FLOWEXEC-REQ-0023, 0033: the pin-capacitance and corner-report exports of a profile-bound
/// manifest are recognised for its own corners and Liberty groups.
#[test]
fn stdout_artifacts_follow_the_manifest_corners() {
    let Some(mut f) = Fixture::new(1) else { return };
    let profile = json!({"schema": "rusteda.native-pdk-profile.v1", "pdk": "gf180mcuD",
        "library": "gf180mcu_fd_sc_mcu7t5v0", "corners": [{"name": "typ"}, {"name": "slow"}]});
    native(
        &mut f,
        "gf180mcuD",
        "gf180mcu_fd_sc_mcu7t5v0",
        &["typ", "slow"],
        &profile,
    );
    f.data["files"]["mem_slow"] = id(&f.root.join("input.txt"));
    f.data["gates"] = json!({});
    f.data["scope"]["max_stdout_artifact_bytes"] = json!(1024);
    let caps = |file: &str| {
        json!([{"id": "caps", "kind": "command", "tool": "pin_caps",
            "arguments": [format!("{{files:{file}}}")], "timeout_s": 10, "stdout_artifact": "caps"}])
    };
    f.data["stdout_artifacts"] =
        json!({"caps": {"path": "logs/caps.stdout", "format": "pin_capacitance", "max_bytes": 64}});
    f.data["steps"] = caps("mem_slow");
    assert!(manifest::parse(f.data.to_string().as_bytes()).is_ok());
    // A file of no corner of this manifest is not a Liberty export.
    for file in ["std_tt", "source", "mem_fast", "MEM_slow"] {
        f.data["steps"] = caps(file);
        let error = manifest::parse(f.data.to_string().as_bytes()).unwrap_err();
        assert_eq!(error.code, "STDOUT_ARTIFACT_POLICY", "{file}");
    }
    // The IHP recipe keeps its four Liberty keys.
    let Some(mut ihp) = Fixture::new(1) else {
        return;
    };
    ihp.data["scope"]["max_stdout_artifact_bytes"] = json!(1024);
    ihp.data["stdout_artifacts"] = f.data["stdout_artifacts"].clone();
    ihp.data["files"]["mem_tt"] = id(&ihp.root.join("input.txt"));
    ihp.data["steps"] = caps("std_tt");
    assert!(manifest::parse(ihp.data.to_string().as_bytes()).is_ok());
    ihp.data["steps"] = caps("mem_tt");
    assert_eq!(
        manifest::parse(ihp.data.to_string().as_bytes())
            .unwrap_err()
            .code,
        "STDOUT_ARTIFACT_POLICY"
    );
}

// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! IHP/EZ130's native worker protocol. The legacy three-corner producer contract
//! remains separate. File validation only: <=256 inputs, <=32 tools, <=128
//! artifacts, <=16 MiB per report; sequential parsing, <128 MiB expected RAM.

use crate::{
    config::{self, Config},
    engine,
    error::{Error, Result},
    integration,
    workspace::{self, FileIdentity, StageResult, State},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    path::PathBuf,
};

pub const GATES: &[&str] = &[
    "inputs_frozen",
    "macros",
    "synthesis_equivalence",
    "final_equivalence",
    "simulation",
    "placement",
    "cts",
    "power_connectivity",
    "routing",
    "extraction",
    "drc",
    "antenna",
    "lvs",
    "timing",
    "timing_accuracy",
    "controls",
];
const REPORT_BYTES: u64 = 16 * 1024 * 1024;

fn bad(message: impl Into<String>) -> Error {
    Error::new("IHP_FLOW_PROTOCOL", message)
}

pub fn validate_config(config: &Config) -> Result<()> {
    let l = &config.limits;
    if config.road.is_none()
        || !config.verilog_files.is_empty()
        || !config.libraries.is_empty()
        || !config.sdc_file.as_os_str().is_empty()
        || !config.physical_steps.is_empty()
        || !config.collateral.is_empty()
        || !config.defines.is_empty()
        || !config.parameters.is_empty()
    {
        return Err(bad(
            "native IHP flow requires one authoritative producer manifest",
        ));
    }
    // An empty driver path is the native executor's; a driver script must be named.
    if config.native_executor()
        != config
            .road
            .as_ref()
            .is_some_and(|r| r.driver.as_os_str().is_empty())
    {
        return Err(bad(
            "road.driver names the driver script; with road.executor native it is omitted",
        ));
    }
    if !(1..=86400).contains(&l.timeout_secs)
        || !(8192..=16384).contains(&l.memory_mib)
        || !(1..=16).contains(&l.threads)
        || !(1024..=64 * 1024 * 1024).contains(&l.max_log_bytes)
        || !(1..=4 * 1024 * 1024 * 1024).contains(&l.max_file_bytes)
        || l.max_input_bytes < l.max_file_bytes
        || l.max_input_bytes > 16 * 1024 * 1024 * 1024
    {
        return Err(bad("native IHP worker resource bounds are invalid"));
    }
    Ok(())
}

fn manifest(config: &Config) -> Result<Value> {
    let path = &config
        .road
        .as_ref()
        .ok_or_else(|| bad("missing producer"))?
        .manifest;
    let value = integration::read_json(path, 2 * 1024 * 1024)?;
    if value["schema"] != "rusteda.ihp-flow.v1"
        || value["name"] != config.design_name
        || value["pdk"] != "ihp-sg13g2"
        || value["library"] != "ez130_8t"
        || value["corners"] != json!(["tt", "ss"])
        || value["scope"]["threads"] != config.limits.threads
        || value["scope"]["memory_mib"] != config.limits.memory_mib
        || value["scope"]["max_log_bytes"] != config.limits.max_log_bytes
    {
        return Err(bad("IHP design/library/corner identity differs"));
    }
    affinity(&value["scope"]["cpu_affinity"], config.limits.threads)?;
    Ok(value)
}

fn affinity(value: &Value, threads: u32) -> Result<Vec<u32>> {
    let values = value
        .as_array()
        .ok_or_else(|| bad("missing explicit IHP CPU allocation"))?;
    if values.len() != threads as usize || !(1..=16).contains(&threads) {
        return Err(bad("IHP CPU allocation differs from its worker count"));
    }
    let cpus = values
        .iter()
        .map(|value| {
            value
                .as_u64()
                .filter(|&cpu| cpu < 4096)
                .map(|cpu| cpu as u32)
                .ok_or_else(|| bad("IHP CPU index is outside its bounded range"))
        })
        .collect::<Result<Vec<_>>>()?;
    if cpus.iter().copied().collect::<BTreeSet<_>>().len() != cpus.len() {
        return Err(bad("IHP CPU allocation contains duplicate indices"));
    }
    Ok(cpus)
}

pub(crate) fn worker_cpu_affinity(config: &Config) -> Result<Vec<u32>> {
    affinity(
        &manifest(config)?["scope"]["cpu_affinity"],
        config.limits.threads,
    )
}

fn descriptor(value: &Value, base: &Path) -> Result<FileIdentity> {
    let mut spec: FileIdentity = serde_json::from_value(value.clone())?;
    if spec.sha256.len() != 64 || !spec.sha256.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(bad("invalid SHA-256 descriptor"));
    }
    if !spec.path.is_absolute() {
        if !config::relative_output(&spec.path) {
            return Err(bad("descriptor has an unsafe relative path"));
        }
        spec.path = base.join(&spec.path);
    }
    spec.path = fs::canonicalize(&spec.path)?;
    Ok(spec)
}

pub fn input_paths(config: &Config) -> Result<Vec<PathBuf>> {
    let producer = config
        .road
        .as_ref()
        .ok_or_else(|| bad("missing producer"))?;
    let value = manifest(config)?;
    let native = config.native_executor();
    let mut files = BTreeSet::from([producer.manifest.clone()]);
    if !native {
        files.insert(producer.driver.clone());
    }
    for (key, max) in [("files", 256), ("tools", 32)] {
        let records = value[key]
            .as_object()
            .ok_or_else(|| bad(format!("missing {key}")))?;
        if records.is_empty() || records.len() > max {
            return Err(bad("input descriptor count exceeds budget"));
        }
        for (name, record) in records {
            if !config::handle(name) {
                return Err(bad("input labels must be safe handles"));
            }
            let spec = descriptor(record, &producer.project)?;
            workspace::verify(&spec, config.limits.max_file_bytes)?;
            files.insert(spec.path);
        }
    }
    // The driver script pins itself as `producer`; the native executor has no script.
    for name in ["contract", "rtl", "sdc", "sram_qualification", "producer"]
        .iter()
        .take(if native { 4 } else { 5 })
    {
        if value["files"].get(name).is_none() {
            return Err(bad(format!("missing required input {name}")));
        }
    }
    for tool in [
        &config.tools.python,
        &config.tools.rustroad,
        &config.tools.gnu_time,
        &config.tools.podman,
    ]
    .into_iter()
    .skip(usize::from(native))
    {
        files.insert(engine::resolve_tool(tool)?);
    }
    if files.len() > 512 {
        return Err(bad("aggregate dependency count exceeds budget"));
    }
    Ok(files.into_iter().collect())
}

fn capture(
    state: &State,
    output: &Path,
    name: &str,
    value: &Value,
    result: &mut StageResult,
) -> Result<FileIdentity> {
    let spec = descriptor(value, output)?;
    if !spec.path.starts_with(fs::canonicalize(output)?) {
        return Err(bad("result artifact escapes its output directory"));
    }
    integration::capture_bound(state, output, name, spec.clone(), result)?;
    Ok(spec)
}

fn zero(value: &Value, key: &str) -> bool {
    value[key].as_u64() == Some(0)
}

fn empty(value: &Value) -> bool {
    value.as_array().is_some_and(Vec::is_empty)
}

fn finite_nonnegative(value: &Value) -> bool {
    value.as_f64().is_some_and(|v| v.is_finite() && v >= 0.0)
}

fn timing_gate(value: &Value) -> Result<()> {
    if value["scope"]["wire_model"] != "spef_rc"
        || value["scope"]["rc_corner"] != "nom"
        || value["scope"]["clock_model"] != "propagated"
        || value["scope"]["cppr"] != "off"
        || value["scope"]["precision"] != "f64"
        || value["scope"]["backend"] != "cpu"
    {
        return Err(bad(
            "timing scope is not propagated CPU/f64/nominal-SPEF/CPPR-off",
        ));
    }
    for source in ["native", "openrcx"] {
        let rows = value["sources"][source]
            .as_object()
            .ok_or_else(|| bad("timing source missing"))?;
        if rows.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != ["tt", "ss"].into_iter().collect()
        {
            return Err(bad("timing corner coverage differs"));
        }
        for row in rows.values() {
            if !finite_nonnegative(&row["setup_ws_ns"])
                || !finite_nonnegative(&row["hold_ws_ns"])
                || [
                    "setup_violations",
                    "hold_violations",
                    "slew_violations",
                    "cap_violations",
                    "fanout_violations",
                    "active_unconstrained_endpoints",
                    "unconstrained_inputs",
                    "unmodeled_arcs",
                ]
                .iter()
                .any(|key| !zero(row, key))
                || row["checks_truncated"] != false
            {
                return Err(bad("extracted setup/hold/DRV/completeness did not pass"));
            }
            let raw = row["unconstrained_endpoints"]
                .as_u64()
                .ok_or_else(|| bad("missing raw unconstrained count"))?;
            let witnesses = row["constant_disabled_endpoints"]
                .as_array()
                .ok_or_else(|| bad("missing disabled endpoint witnesses"))?;
            let names: BTreeSet<_> = witnesses
                .iter()
                .filter_map(|w| w["endpoint"].as_str())
                .collect();
            if raw > 100000
                || witnesses.len() as u64 != raw
                || names.len() != witnesses.len()
                || names
                    .iter()
                    .any(|name| name.is_empty() || name.len() > 4096)
                || witnesses
                    .iter()
                    .any(|w| w["reason"] != "constant_disabled" || w["verified"] != true)
            {
                return Err(bad(
                    "unconstrained endpoints lack explicit constant-disabled evidence",
                ));
            }
        }
    }
    Ok(())
}

fn artifact_bindings(
    name: &str,
    report: &Value,
    hashes: &BTreeMap<String, String>,
    rtl: &Value,
) -> Result<()> {
    let artifact = |key: &str| {
        hashes
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| bad("required artifact hash missing"))
    };
    let matches = |value: &Value, key: &str| -> Result<()> {
        if value.as_str() != Some(artifact(key)?) {
            return Err(bad(format!(
                "{name} evidence belongs to a different {key} artifact"
            )));
        }
        Ok(())
    };
    match name {
        "drc" | "antenna" | "lvs" => {
            let gds = report
                .get("gds_sha256")
                .or_else(|| report.pointer("/gds/sha256"))
                .unwrap_or(&Value::Null);
            matches(gds, "gds")?;
            if name == "lvs" {
                matches(&report["powered_netlist_sha256"], "netlist_powered_final")?;
            }
        }
        "synthesis_equivalence" | "final_equivalence" => {
            if &report["rtl_sha256"] != rtl || !rtl.is_string() {
                return Err(bad("equivalence evidence does not bind the original RTL"));
            }
            matches(
                &report["netlist_sha256"],
                if name == "synthesis_equivalence" {
                    "netlist_synth"
                } else {
                    "netlist_final"
                },
            )?;
        }
        "simulation" => matches(&report["netlist_sha256"], "netlist_final")?,
        "timing" => {
            matches(&report["scope"]["netlist_sha256"], "netlist_final")?;
            for (source, key) in [
                ("native", "spef_native_nom"),
                ("openrcx", "spef_openrcx_nom"),
            ] {
                let corners = report["sources"][source]
                    .as_object()
                    .ok_or_else(|| bad("timing source missing"))?;
                for row in corners.values() {
                    matches(&row["spef_sha256"], key)?;
                }
            }
        }
        "extraction" | "timing_accuracy" => {
            matches(&report["netlist_sha256"], "netlist_final")?;
            for (source, key) in [
                ("native", "spef_native_nom"),
                ("openrcx", "spef_openrcx_nom"),
            ] {
                matches(&report["spef_sha256"][source], key)?;
            }
        }
        _ => (),
    }
    Ok(())
}

pub fn ingest(state: &State, output: &Path, result: &mut StageResult) -> Result<Value> {
    let design = manifest(&state.config)?;
    let summary_path = output.join("summary.json");
    let summary = integration::read_json(&summary_path, REPORT_BYTES)?;
    let identity = workspace::identify(
        &state.config.road.as_ref().unwrap().manifest,
        2 * 1024 * 1024,
    )?;
    if summary["schema"] != "rusteda.ihp-rtl2gds.v1"
        || summary["status"] != "completed"
        || summary["implementation"] != "rusteda"
        || summary["manifest_sha256"] != identity.sha256
        || summary["scope"] != design["scope"]
        || !empty(&summary["failed_items"])
        || !empty(&summary["unverified_items"])
    {
        return Err(bad(
            "native IHP producer did not establish the complete gate",
        ));
    }
    let gates = summary["gates"]
        .as_object()
        .ok_or_else(|| bad("gate files missing"))?;
    if gates.keys().map(String::as_str).collect::<BTreeSet<_>>() != GATES.iter().copied().collect()
    {
        return Err(bad("IHP gate set differs"));
    }
    let artifacts = summary["artifacts"]
        .as_object()
        .ok_or_else(|| bad("artifacts missing"))?;
    if artifacts.len() > 128
        || [
            "gds",
            "def",
            "netlist_synth",
            "netlist_final",
            "netlist_powered_final",
            "spef_native_nom",
            "spef_openrcx_nom",
        ]
        .iter()
        .any(|key| !artifacts.contains_key(*key))
    {
        return Err(bad(
            "required full-flow artifacts are missing or exceed budget",
        ));
    }
    let mut hashes = BTreeMap::new();
    for (name, value) in artifacts {
        let id = capture(state, output, name, value, result)?;
        hashes.insert(name.clone(), id.sha256);
    }
    let mut statuses = serde_json::Map::new();
    let mut timing = Value::Null;
    for (name, record) in gates {
        let id = capture(state, output, name, record, result)?;
        let report = integration::read_json(&id.path, REPORT_BYTES)?;
        let proven = matches!(name.as_str(), "synthesis_equivalence" | "final_equivalence")
            && report["status"] == "proven";
        if report["status"] != "passed" && !proven {
            return Err(bad(format!("gate {name} did not pass")));
        }
        artifact_bindings(name, &report, &hashes, &design["files"]["rtl"]["sha256"])?;
        match name.as_str() {
            "drc" | "antenna" => {
                let deck = if name == "drc" {
                    "drc_deck"
                } else {
                    "antenna_deck"
                };
                if !zero(&report, "violations")
                    || report["complete"] != true
                    || report["deck_sha256"] != design["files"][deck]["sha256"]
                    || (name == "antenna" && !empty(&report["disabled_rules"]))
                    || (name == "drc" && report["disabled_rules"] != json!(["recommended"]))
                {
                    return Err(bad(
                        "foundry DRC/antenna scope is incomplete, changed or has violations",
                    ));
                }
            }
            "routing" => {
                if report["complete"] != true
                    || report["scope"] != "all"
                    || !zero(&report, "unrouted_signal_nets")
                    || !zero(&report, "unconnected_signal_pins")
                {
                    return Err(bad("partial routing cannot qualify a complete flow"));
                }
            }
            "extraction" => {
                if report["complete"] != true
                    || report["rc_corner"] != "nom"
                    || report["spef_sources"] != json!(["native", "openrcx"])
                    || report["resistance_model"] != "openrcx-pdk"
                {
                    return Err(bad(
                        "extraction does not use the common complete nominal RC model",
                    ));
                }
            }
            "lvs" => {
                if report["top"] != state.config.design_name
                    || report["top_port_identity"] != true
                    || report["macro_instances"] != 2
                    || report["reference_macro_instances"] != 2
                    || report["macro_devices_verified"] != true
                    || !empty(&report["extraction_errors"])
                    || report["macro_qualification_sha256"]
                        != design["files"]["sram_qualification"]["sha256"]
                {
                    return Err(bad(
                        "device-level LVS or real SRAM qualification is incomplete",
                    ));
                }
            }
            "timing" => {
                timing_gate(&report)?;
                if report["scope"]["sdc_sha256"] != design["files"]["sdc"]["sha256"] {
                    return Err(bad("extracted timing did not use the original constraints"));
                }
                timing = report;
            }
            "timing_accuracy"
                if report["comparison"] != "ruststa_vs_opensta_on_identical_spef"
                    || report["cppr"] != "off"
                    || report["corners"] != json!(["tt", "ss"])
                    || report["spef_sources"] != json!(["native", "openrcx"])
                    || !report["compared_values"]
                        .as_u64()
                        .is_some_and(|n| (8..=1_000_000).contains(&n))
                    || !report["max_abs_difference_ns"]
                        .as_f64()
                        .is_some_and(|d| d.is_finite() && (0.0..=0.001).contains(&d)) =>
            {
                return Err(bad(
                    "independent timing parity did not meet its declared 1 ps scope",
                ));
            }
            _ => (),
        }
        statuses.insert(name.clone(), json!({"status":"passed", "report":id}));
    }
    let id = workspace::identify(&summary_path, REPORT_BYTES)?;
    integration::capture_bound(state, output, "road_summary", id, result)?;
    Ok(
        json!({"qualification_status":"passed", "scope":summary["scope"], "pdk":"ihp",
        "statuses":statuses, "drc_status":"passed", "lvs_status":"passed",
        "resources":summary["resources"], "declared_deviations":[],
        "timing_sources":{"native":{"scope":timing["scope"], "metrics":timing["sources"]["native"]}},
        "source_manifest_sha256":identity.sha256}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    // Protocol tests only. Synthetic rows cannot establish EDA correctness.
    // Four rows, no graph/PDK parsing; <1 MiB state, two test workers.
    fn timing() -> Value {
        let row = json!({"setup_ws_ns":1.0,"hold_ws_ns":0.01,"setup_violations":0,
            "hold_violations":0,"slew_violations":0,"cap_violations":0,"fanout_violations":0,
            "active_unconstrained_endpoints":0,"unconstrained_inputs":0,"unmodeled_arcs":0,
            "checks_truncated":false,"unconstrained_endpoints":1,
            "constant_disabled_endpoints":[{"endpoint":"memory/A_BIST_CLK","reason":"constant_disabled","verified":true}]});
        json!({"scope":{"wire_model":"spef_rc","rc_corner":"nom","clock_model":"propagated",
            "cppr":"off","precision":"f64","backend":"cpu"},
            "sources":{"native":{"tt":row,"ss":row},"openrcx":{"tt":row,"ss":row}}})
    }

    #[test]
    fn worker_cpu_allocation_is_explicit_unique_and_bounded() {
        assert_eq!(affinity(&json!([0, 2]), 2).unwrap(), vec![0, 2]);
        for invalid in [
            json!(null),
            json!([0]),
            json!([0, 0]),
            json!([0, -1]),
            json!([0, true]),
            json!([0, 4096]),
        ] {
            assert!(affinity(&invalid, 2).is_err());
        }
    }

    #[test]
    fn ihp_accepts_explicit_worker_budget_without_changing_legacy_rules() {
        let mut config: Config = serde_json::from_value(json!({
            "schema_version":1,"design_name":"ibex_sram_bench","pdk":"ihp_sg13g2_ez130",
            "flow":"rusteda","road":{"project":".","manifest":"manifest.json","driver":"producer.py"},
            "limits":{"memory_mib":8192,"threads":16,"max_file_bytes":2147483648u64,
                "max_input_bytes":8589934592u64}})).unwrap();
        assert!(config.validate().is_ok());
        for threads in [0, 17] {
            config.limits.threads = threads;
            assert!(config.validate().is_err());
        }
    }

    #[test]
    fn extracted_timing_requires_both_sources_and_corners() {
        let positive = timing();
        assert!(timing_gate(&positive).is_ok());
        for path in ["/sources/native/ss", "/sources/openrcx/tt"] {
            let mut value = positive.clone();
            *value.pointer_mut(path).unwrap() = Value::Null;
            assert!(timing_gate(&value).is_err());
        }
        for (path, invalid) in [
            ("/sources/native/tt/hold_ws_ns", json!(-0.01)),
            (
                "/sources/native/tt/active_unconstrained_endpoints",
                json!(1),
            ),
            ("/sources/native/tt/checks_truncated", json!(true)),
            ("/scope/cppr", json!("on")),
            ("/scope/wire_model", json!("ideal")),
        ] {
            let mut value = positive.clone();
            *value.pointer_mut(path).unwrap() = invalid;
            assert!(timing_gate(&value).is_err());
        }
    }

    #[test]
    fn unverified_or_duplicate_disabled_endpoints_are_rejected() {
        let mut value = timing();
        value["sources"]["native"]["tt"]["constant_disabled_endpoints"][0]["verified"] =
            json!(false);
        assert!(timing_gate(&value).is_err());
        let mut value = timing();
        let row = &mut value["sources"]["native"]["tt"];
        row["unconstrained_endpoints"] = json!(2);
        let first = row["constant_disabled_endpoints"][0].clone();
        row["constant_disabled_endpoints"]
            .as_array_mut()
            .unwrap()
            .push(first);
        assert!(timing_gate(&value).is_err());
    }

    #[test]
    fn a_clean_report_cannot_qualify_a_different_gds() {
        let mut hashes = BTreeMap::from([("gds".to_string(), "a".repeat(64))]);
        let report = json!({"gds_sha256":"a".repeat(64)});
        assert!(artifact_bindings("drc", &report, &hashes, &Value::Null).is_ok());
        hashes.insert("gds".into(), "b".repeat(64));
        assert!(artifact_bindings("drc", &report, &hashes, &Value::Null).is_err());
    }

    #[test]
    fn timing_parity_must_bind_the_actual_netlist_and_both_spefs() {
        let hashes = BTreeMap::from([
            ("netlist_final".into(), "n".repeat(64)),
            ("spef_native_nom".into(), "a".repeat(64)),
            ("spef_openrcx_nom".into(), "b".repeat(64)),
        ]);
        let mut report = json!({"netlist_sha256":"n".repeat(64),
            "spef_sha256":{"native":"a".repeat(64),"openrcx":"b".repeat(64)}});
        assert!(artifact_bindings("timing_accuracy", &report, &hashes, &Value::Null).is_ok());
        report["spef_sha256"]["openrcx"] = json!("old".repeat(64));
        assert!(artifact_bindings("timing_accuracy", &report, &hashes, &Value::Null).is_err());
    }
}

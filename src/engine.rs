use crate::{
    config::{Config, PhysicalStep},
    error::{Error, Result},
    process::{self, ProcessLimits},
    workspace::{self, Artifact, StageResult, State},
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub fn resolve_tool(program: &Path) -> Result<PathBuf> {
    let candidate = if program.components().count() > 1 || program.is_absolute() {
        program.to_path_buf()
    } else {
        std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
            .map(|p| p.join(program))
            .find(|p| executable(p))
            .ok_or_else(|| {
                Error::new(
                    "TOOL_UNAVAILABLE",
                    format!("{} not on PATH", program.display()),
                )
            })?
    };
    if !executable(&candidate) {
        return Err(Error::new(
            "TOOL_UNAVAILABLE",
            format!("not an executable regular file: {}", candidate.display()),
        ));
    }
    Ok(fs::canonicalize(candidate)?)
}

fn executable(path: &Path) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn path(p: &Path) -> Result<String> {
    p.to_str()
        .map(str::to_owned)
        .ok_or_else(|| Error::new("PATH_ENCODING", "CLI paths must be valid UTF-8"))
}

struct Execution<'a> {
    config: &'a Config,
    directory: &'a Path,
    result: &'a mut StageResult,
}

impl Execution<'_> {
    fn command(&mut self, tool: &Path, args: Vec<String>, tag: &str) -> Result<PathBuf> {
        let program = resolve_tool(tool)?;
        let identity = workspace::identify(&program, self.config.limits.max_file_bytes)?;
        if !self.result.tools.iter().any(|t| t.path == identity.path) {
            self.result.tools.push(identity);
        }
        let limits = ProcessLimits {
            timeout_secs: self.config.limits.timeout_secs,
            memory_mib: self.config.limits.memory_mib,
            max_log_bytes: self.config.limits.max_log_bytes,
        };
        let receipt = process::run(
            &program,
            &args,
            self.directory,
            &self.directory.join(tag),
            &limits,
        )?;
        let success = receipt.success();
        let stdout = receipt.stdout.clone();
        let stderr = receipt.stderr.clone();
        let explanation = format!(
            "{} exited {:?}; timeout={}, log_limit={}; stderr={}",
            program.display(),
            receipt.exit_code,
            receipt.timed_out,
            receipt.log_limit_exceeded,
            receipt.stderr.display()
        );
        // The supervisor always returns bounded log files, including failed executions.
        self.result.processes.push(receipt);
        for (kind, p) in [("stdout", &stdout), ("stderr", &stderr)] {
            self.result.artifacts.push(Artifact {
                kind: format!("{tag}_{kind}"),
                file: workspace::identify(p, self.config.limits.max_log_bytes)?,
            });
        }
        if !success {
            return Err(Error::new("TOOL_EXECUTION_FAILED", explanation));
        }
        Ok(stdout)
    }

    fn json_command(
        &mut self,
        tool: &Path,
        args: Vec<String>,
        tag: &str,
        envelope: bool,
    ) -> Result<Value> {
        let stdout = match self.command(tool, args, tag) {
            Ok(stdout) => stdout,
            Err(error) => {
                // A nonzero exit can still carry the most useful structured
                // diagnostic. Preserve it inline as well as in hashed logs.
                if let Some(receipt) = self.result.processes.last().filter(|r| {
                    r.stdout
                        .parent()
                        .and_then(Path::file_name)
                        .is_some_and(|n| n == tag)
                }) && let Ok(response) =
                    read_json(&receipt.stdout, self.config.limits.max_log_bytes)
                {
                    self.result.evidence = json!({"failed_engine_response": selected(&response,
                        &["schema", "ok", "error", "diagnostics", "format", "verdict", "errors", "undecided", "mismatches", "scope", "completeness"])});
                    let upstream = response
                        .pointer("/error/code")
                        .and_then(Value::as_str)
                        .unwrap_or("engine diagnostic");
                    let message: String = response
                        .pointer("/error/message")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .chars()
                        .take(512)
                        .collect();
                    return Err(Error::new(
                        error.code,
                        format!("{}; upstream {upstream}: {message}", error.message),
                    ));
                }
                return Err(error);
            }
        };
        let value = read_json(&stdout, self.config.limits.max_log_bytes)?;
        if envelope
            && (value.get("schema").and_then(Value::as_str) != Some("rusteda.v1")
                || value.get("ok").and_then(Value::as_bool) != Some(true)
                || value.get("error").is_some_and(|e| !e.is_null()))
        {
            self.result.evidence = json!({"failed_engine_response": selected(&value,
                &["schema", "ok", "error", "diagnostics", "scope", "completeness"])});
            return Err(Error::new(
                "ENGINE_DIAGNOSTIC",
                format!("{} reported failure; see {}", tag, stdout.display()),
            ));
        }
        Ok(value)
    }

    fn artifact(&mut self, kind: &str, p: &Path) -> Result<()> {
        let file = workspace::identify(p, self.config.limits.max_file_bytes)?;
        if file.bytes == 0 {
            return Err(Error::new(
                "EMPTY_ARTIFACT",
                format!("{} is empty", p.display()),
            ));
        }
        self.result.artifacts.push(Artifact {
            kind: kind.into(),
            file,
        });
        Ok(())
    }
}

pub fn execute(
    operation: &str,
    state: &State,
    directory: &Path,
    result: &mut StageResult,
) -> Result<()> {
    let config = &state.config;
    let netlist = latest_artifact(state, "netlist");
    let mut ex = Execution {
        config,
        directory,
        result,
    };
    match operation {
        "RustSys.Synthesis" => {
            let netlist = directory.join("mapped.v");
            let report = directory.join("synthesis.json");
            let mut args = vec![
                "--threads".into(),
                config.limits.threads.to_string(),
                "synth".into(),
                "--top".into(),
                config.design_name.clone(),
            ];
            synthesis_inputs(config, &mut args, false)?;
            args.extend([
                "--out".into(),
                path(&netlist)?,
                "--report".into(),
                path(&report)?,
                "--print".into(),
                "json".into(),
            ]);
            args.extend(
                config
                    .verilog_files
                    .iter()
                    .map(|p| path(p))
                    .collect::<Result<Vec<_>>>()?,
            );
            let output = ex.json_command(&config.tools.rustsys, args, "synthesis", false)?;
            if output.get("format").and_then(Value::as_str) != Some("rustsys-synth-report") {
                return Err(Error::new(
                    "ENGINE_PROTOCOL",
                    "unexpected RustSys report format",
                ));
            }
            ex.artifact("netlist", &netlist)?;
            ex.artifact("synthesis_report", &report)?;
            ex.result.evidence = selected(
                &output,
                &[
                    "format",
                    "format_version",
                    "area",
                    "sequential_cells",
                    "combinational_cells",
                    "estimated_delay",
                    "time_unit_ns",
                    "warnings",
                    "runtime_s",
                ],
            );
        }
        "RustSys.Equivalence" | "RustSys.EquivalencePostPhysical" => {
            let netlist =
                netlist.ok_or_else(|| Error::new("MISSING_INPUT", "no mapped netlist"))?;
            let report = directory.join("equivalence.json");
            let mut args = vec![
                "--threads".into(),
                config.limits.threads.to_string(),
                "check".into(),
                "synth".into(),
                "--top".into(),
                config.design_name.clone(),
            ];
            synthesis_inputs(config, &mut args, true)?;
            args.extend([
                "--netlist".into(),
                path(&netlist)?,
                "--report".into(),
                path(&report)?,
                "--budget".into(),
                config.limits.max_conflicts.to_string(),
                "--model-budget".into(),
                config.limits.max_conflicts.to_string(),
                "--sweep-max-candidates".into(),
                "256".into(),
                "--print".into(),
                "json".into(),
            ]);
            args.extend(
                config
                    .verilog_files
                    .iter()
                    .map(|p| path(p))
                    .collect::<Result<Vec<_>>>()?,
            );
            let output = ex.json_command(&config.tools.rustsys, args, "equivalence", false)?;
            ex.artifact("equivalence_report", &report)?;
            ex.result.evidence = output.clone();
            if output.get("format").and_then(Value::as_str) != Some("rustsys-check-synth-report")
                || output.get("format_version").and_then(Value::as_u64) != Some(2)
                || output.get("verdict").and_then(Value::as_str) != Some("equivalent")
                || output.get("internal_error").and_then(Value::as_bool) != Some(false)
                || ["errors", "mismatches", "undecided"].iter().any(|key| {
                    !output
                        .get(*key)
                        .and_then(Value::as_array)
                        .is_some_and(|a| a.is_empty())
                })
            {
                return Err(Error::new(
                    "EQUIVALENCE_UNVERIFIED",
                    "CEC did not establish equivalence; retain report and inspect verdict",
                ));
            }
        }
        "RustSTA.PrePnR" => {
            let netlist =
                netlist.ok_or_else(|| Error::new("MISSING_INPUT", "no mapped netlist"))?;
            let child_ws = directory.join("timing-workspace");
            let common = vec![
                "--workspace".to_owned(),
                path(&child_ws)?,
                "--format".into(),
                "json".into(),
                "--threads".into(),
                config.limits.threads.to_string(),
            ];
            let mut args = vec!["init".into()];
            args.extend(common.clone());
            let init = ex.json_command(&config.tools.rusteda, args, "timing_init", true)?;
            let rev = next_revision(&init)?;
            let mut args = vec!["import".into(), "--base-rev".into(), rev];
            args.extend(common.clone());
            for library in &config.libraries {
                args.extend([
                    "--lib".into(),
                    path(&library.path)?,
                    "--corner".into(),
                    library.corner.clone(),
                ]);
            }
            args.extend([
                "--netlist".into(),
                path(&netlist)?,
                "--sdc".into(),
                path(&config.sdc_file)?,
            ]);
            let imported = ex.json_command(&config.tools.rusteda, args, "timing_import", true)?;
            let mut args = vec![
                "run".into(),
                "--base-rev".into(),
                next_revision(&imported)?,
                "--backend".into(),
                "cpu".into(),
                "--precision".into(),
                "f64".into(),
                "--max-paths".into(),
                config.limits.max_paths.to_string(),
                "--max-state-gib".into(),
                config.limits.max_state_gib.to_string(),
            ];
            args.extend(common.clone());
            let timed = ex.json_command(&config.tools.ruststa, args, "timing_run", true)?;
            let mut args = vec![
                "check".into(),
                "--base-rev".into(),
                next_revision(&timed)?,
                "--method".into(),
                "full".into(),
            ];
            args.extend(common);
            let checked = ex.json_command(&config.tools.ruststa, args, "timing_check", true)?;
            ex.result.evidence = selected(
                &timed,
                &[
                    "metrics",
                    "scope",
                    "completeness",
                    "diagnostics",
                    "top_paths",
                    "truncated_paths",
                ],
            );
            ex.result.evidence["verification"] = selected(
                &checked,
                &[
                    "verdict",
                    "method",
                    "methods",
                    "max_abs_difference_ns",
                    "slacks_compared",
                    "tolerance_ns",
                    "mismatches",
                ],
            );
            ex.result.evidence["child_workspace"] = json!(child_ws);
            ex.result.evidence["child_revision"] = json!(next_revision(&checked)?);
            if checked.get("verdict").and_then(Value::as_str) != Some("agree")
                || !checked
                    .get("mismatches")
                    .and_then(Value::as_array)
                    .is_some_and(|a| a.is_empty())
            {
                return Err(Error::new(
                    "TIMING_UNVERIFIED",
                    "full-rerun consistency check did not agree",
                ));
            }
            let corner = timed.pointer("/scope/corner");
            let corner_valid = corner.is_some_and(|v| {
                v.as_str().is_some_and(|s| !s.is_empty())
                    || v.as_array().is_some_and(|a| {
                        !a.is_empty() && a.iter().all(|x| x.as_str().is_some_and(|s| !s.is_empty()))
                    })
            });
            if !corner_valid
                || !timed
                    .pointer("/scope/wire_model")
                    .and_then(Value::as_str)
                    .is_some_and(|s| !s.is_empty())
                || timed.pointer("/scope/precision").and_then(Value::as_str) != Some("f64")
                || timed
                    .pointer("/completeness/unconstrained_endpoints")
                    .and_then(Value::as_u64)
                    .is_none()
            {
                return Err(Error::new(
                    "ENGINE_PROTOCOL",
                    "RustSTA omitted mandatory analysis scope or unconstrained endpoint count",
                ));
            }
        }
        "RustSTA.PostPnR" => {
            return Err(Error::new(
                "CAPABILITY_UNAVAILABLE",
                "generic Classic recipes lack a qualified post-route contract; use flow rusteda",
            ));
        }
        other => {
            let step = config
                .physical_steps
                .iter()
                .find(|s| format!("{}:{}", s.tool.name(), s.id) == other)
                .ok_or_else(|| Error::new("UNKNOWN_OPERATION", other))?;
            execute_physical(&mut ex, state, step)?;
        }
    }
    Ok(())
}

fn synthesis_inputs(config: &Config, args: &mut Vec<String>, _checking: bool) -> Result<()> {
    for library in config
        .libraries
        .iter()
        .filter(|l| l.corner == config.synthesis_corner)
    {
        args.extend(["--lib".into(), path(&library.path)?]);
    }
    for (key, value) in &config.defines {
        args.extend(["-D".into(), format!("{key}={value}")]);
    }
    for (key, value) in &config.parameters {
        args.extend(["-P".into(), format!("{key}={value}")]);
    }
    Ok(())
}

fn next_revision(value: &Value) -> Result<String> {
    let rev = value
        .get("new_rev")
        .and_then(Value::as_str)
        .ok_or_else(|| Error::new("ENGINE_PROTOCOL", "successful mutation omitted new_rev"))?;
    workspace::parse_revision(rev)?;
    Ok(rev.to_owned())
}

pub fn latest_artifact(state: &State, kind: &str) -> Option<PathBuf> {
    state
        .stages
        .iter()
        .rev()
        .filter(|s| s.status == workspace::StageStatus::Passed)
        .filter_map(|s| s.result.as_ref())
        .flat_map(|r| r.artifacts.iter().rev())
        .find(|a| a.kind == kind)
        .map(|a| a.file.path.clone())
}

fn read_json(path: &Path, cap: u64) -> Result<Value> {
    let mut file = fs::File::open(path)?;
    if file.metadata()?.len() > cap {
        return Err(Error::new("REPORT_BUDGET", "report exceeds byte limit"));
    }
    let mut bytes = Vec::new();
    (&mut file).take(cap + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > cap {
        return Err(Error::new("REPORT_BUDGET", "report grew beyond byte limit"));
    }
    serde_json::from_slice(&bytes)
        .map_err(|e| Error::new("ENGINE_PROTOCOL", format!("{}: {e}", path.display())))
}

fn selected(value: &Value, keys: &[&str]) -> Value {
    let mut map = serde_json::Map::new();
    for key in keys {
        if let Some(v) = value.get(*key) {
            map.insert((*key).into(), v.clone());
        }
    }
    Value::Object(map)
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new().create_new(true).write(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

/// UTF-8 hex decoding gives Tcl exactly one literal string even for braces,
/// brackets, dollar signs, newlines and backslashes in caller-selected paths.
pub fn tcl_literal(value: &str) -> String {
    let hex: String = value
        .as_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    format!("[encoding convertfrom utf-8 [binary decode hex {{{hex}}}]]")
}

fn execute_physical(ex: &mut Execution<'_>, state: &State, step: &PhysicalStep) -> Result<()> {
    let netlist = latest_artifact(state, "netlist")
        .ok_or_else(|| Error::new("MISSING_INPUT", "no mapped netlist"))?;
    let output_dir = ex.directory.join("outputs");
    fs::create_dir(&output_dir)?;
    let artifacts: BTreeMap<String, PathBuf> = ["netlist", "spef", "def", "odb", "gds"]
        .into_iter()
        .filter_map(|k| latest_artifact(state, k).map(|p| (k.into(), p)))
        .collect();
    let context = json!({"top": ex.config.design_name, "output_dir": output_dir, "netlist": netlist,
        "sdc": ex.config.sdc_file, "spef": artifacts.get("spef"), "libraries": ex.config.libraries,
        "collateral": ex.config.collateral, "artifacts": artifacts});
    let context_path = ex.directory.join("context.json");
    write_new(&context_path, &serde_json::to_vec_pretty(&context)?)?;
    let mut driver = String::new();
    for (key, value) in [
        ("top", ex.config.design_name.clone()),
        ("netlist", path(&netlist)?),
        ("sdc", path(&ex.config.sdc_file)?),
        (
            "spef",
            artifacts
                .get("spef")
                .map(|p| path(p))
                .transpose()?
                .unwrap_or_default(),
        ),
        ("output_dir", path(&output_dir)?),
    ] {
        driver.push_str(&format!("set rustlane_{key} {}\n", tcl_literal(&value)));
    }
    let libs = ex
        .config
        .libraries
        .iter()
        .filter(|l| l.corner == ex.config.synthesis_corner)
        .map(|l| path(&l.path).map(|p| tcl_literal(&p)))
        .collect::<Result<Vec<_>>>()?
        .join(" ");
    driver.push_str(&format!("set rustlane_libraries [list {libs}]\n"));
    for (name, values) in [
        ("collateral", &ex.config.collateral),
        ("artifacts", &artifacts),
    ] {
        driver.push_str(&format!("set rustlane_{name} [dict create"));
        for (key, p) in values {
            driver.push_str(&format!(" {} {}", tcl_literal(key), tcl_literal(&path(p)?)));
        }
        driver.push_str("]\n");
    }
    driver.push_str(&format!("source {}\n", tcl_literal(&path(&step.script)?)));
    let driver_path = ex.directory.join("driver.tcl");
    write_new(&driver_path, driver.as_bytes())?;
    let mut replacements = BTreeMap::from([
        ("script".to_owned(), path(&step.script)?),
        ("driver".into(), path(&driver_path)?),
        ("context".into(), path(&context_path)?),
        ("output_dir".into(), path(&output_dir)?),
        ("top".into(), ex.config.design_name.clone()),
        ("netlist".into(), path(&netlist)?),
        ("sdc".into(), path(&ex.config.sdc_file)?),
    ]);
    for (key, p) in &artifacts {
        replacements.insert(key.clone(), path(p)?);
    }
    for (key, p) in &ex.config.collateral {
        replacements.insert(format!("collateral_{key}"), path(p)?);
    }
    let mut args = Vec::new();
    for arg in &step.arguments {
        args.push(expand(arg, &replacements)?);
    }
    let tool = match step.tool {
        crate::config::PhysicalTool::Openroad => &ex.config.tools.openroad,
        crate::config::PhysicalTool::Klayout => &ex.config.tools.klayout,
        crate::config::PhysicalTool::Magic => &ex.config.tools.magic,
        crate::config::PhysicalTool::Netgen => &ex.config.tools.netgen,
    };
    ex.command(tool, args, "physical")?;
    let root = fs::canonicalize(&output_dir)?;
    for output in &step.outputs {
        let p = output_dir.join(&output.path);
        if !fs::canonicalize(&p)?.starts_with(&root) {
            return Err(Error::new(
                "OUTPUT_ESCAPE",
                "declared output resolves outside stage output directory",
            ));
        }
        if fs::symlink_metadata(&p)?.file_type().is_symlink() {
            return Err(Error::new("OUTPUT_ESCAPE", "symlink output refused"));
        }
        let kind = serde_json::to_value(output.kind)?
            .as_str()
            .unwrap()
            .to_owned();
        ex.artifact(&kind, &p)?;
    }
    ex.artifact("context", &context_path)?;
    ex.artifact("driver", &driver_path)?;
    ex.result.evidence = json!({"execution": "completed", "tool": step.tool.name(),
        "drc_status": "unchecked", "lvs_status": "unchecked", "physical_result": "unverified",
        "limitations": ["caller-supplied recipes are trusted executable inputs; they are not a sandbox", "declared output identity and process exit do not establish physical correctness", "recipe-internal timing is not verified as RustSTA-owned"]});
    Ok(())
}

fn expand(arg: &str, values: &BTreeMap<String, String>) -> Result<String> {
    let mut output = String::new();
    let mut rest = arg;
    while let Some(start) = rest.find('{') {
        output.push_str(&rest[..start]);
        let end = rest[start + 1..]
            .find('}')
            .ok_or_else(|| Error::new("ARGUMENT_TEMPLATE", "unclosed placeholder"))?
            + start
            + 1;
        let key = &rest[start + 1..end];
        output.push_str(values.get(key).ok_or_else(|| {
            Error::new(
                "ARGUMENT_TEMPLATE",
                format!("unavailable placeholder {{{key}}}"),
            )
        })?);
        if output.len() > 65536 {
            return Err(Error::new(
                "ARGUMENT_BUDGET",
                "expanded argument exceeds 64 KiB",
            ));
        }
        rest = &rest[end + 1..];
    }
    output.push_str(rest);
    if output.len() > 65536 {
        return Err(Error::new(
            "ARGUMENT_BUDGET",
            "expanded argument exceeds 64 KiB",
        ));
    }
    Ok(output)
}

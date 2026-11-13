// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! The `rusteda.ihp-flow.v1` step-graph manifest: model and validation before launch.
//!
//! Budgets are those of the reference producer: at most 129 steps, 256 files, 32 tools,
//! 128 artifacts, 8 parallel groups of at most 128 steps, a 2 MiB document. Validation
//! is O(steps + group edges); nothing is launched and no path is enumerated here.

use super::pyjson::Json;
use crate::error::{Error, Result};
use std::collections::{BTreeMap, BTreeSet};

pub const SCHEMA: &str = "rusteda.ihp-flow.v1";
pub const MAX_MANIFEST_BYTES: u64 = 2 * 1024 * 1024;
pub const MAX_STEPS: usize = 129;
/// Upper bound of the declared sum of stdout-artifact sizes (1536 MiB).
pub const MAX_STDOUT_ARTIFACT_BYTES: u64 = 1536 << 20;
const REQUIRED: [&str; 11] = [
    "schema",
    "name",
    "pdk",
    "library",
    "corners",
    "files",
    "tools",
    "scope",
    "steps",
    "gates",
    "artifacts",
];
const OPTIONAL: [&str; 2] = ["stdout_artifacts", "parallel_groups"];

fn bad(code: &'static str, message: impl Into<String>) -> Error {
    Error::new(code, message)
}

/// `[a-zA-Z0-9_-]{1,128}`
pub fn handle(text: &str) -> bool {
    (1..=128).contains(&text.len())
        && text
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// Per-file byte cap of a stdout-artifact format.
pub fn artifact_cap(format: &str) -> Option<u64> {
    Some(match format {
        "verilog" | "pin_capacitance" | "timing_drv" | "timing_endpoints" => 16 << 20,
        "timing_hold_fix" | "placement_seed" => 64 << 20,
        "def" => 128 << 20,
        _ => return None,
    })
}

#[derive(Debug, Clone)]
pub struct Command {
    pub tool: String,
    pub arguments: Vec<String>,
    pub timeout_s: u64,
    pub stdout_artifact: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Kind {
    Render { destination: String, content: Json },
    Command(Command),
}

#[derive(Debug, Clone)]
pub struct Step {
    pub id: String,
    pub kind: Kind,
}

impl Step {
    pub fn kind_name(&self) -> &'static str {
        match self.kind {
            Kind::Render { .. } => "render",
            Kind::Command(_) => "command",
        }
    }
}

/// One step's admission request inside a parallel group.
#[derive(Debug, Clone, Default)]
pub struct Node {
    pub depends_on: Vec<String>,
    /// Indices into `scope.cpu_affinity`; empty for a render step.
    pub cpu_slots: Vec<usize>,
    pub memory_mib: u64,
}

#[derive(Debug, Clone)]
pub struct Group {
    pub max_parallel: usize,
    pub max_memory_mib: u64,
    pub nodes: BTreeMap<String, Node>,
    /// Indices into `Manifest::steps`, ascending and contiguous.
    pub steps: Vec<usize>,
}

#[derive(Debug, Clone)]
pub struct ArtifactSpec {
    /// The registry entry as written (member order is kept in receipts).
    pub raw: Json,
    pub format: String,
    pub max_bytes: u64,
}

#[derive(Debug, Clone)]
pub struct CapturePolicy {
    pub mode: &'static str,
    pub declared_bytes: u64,
    pub aggregate_limit_bytes: u64,
    pub files: usize,
}

#[derive(Debug, Clone)]
pub struct Scope {
    pub raw: Json,
    pub threads: usize,
    pub cpu_affinity: Vec<usize>,
    pub max_log_bytes: u64,
}

/// One pinned input as the manifest states it (not yet verified).
#[derive(Debug, Clone)]
pub struct Pin {
    /// `files:<name>` or `tools:<name>`.
    pub key: String,
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone)]
pub struct Manifest {
    pub raw: Json,
    pub name: Option<String>,
    pub pins: Vec<Pin>,
    pub scope: Scope,
    pub steps: Vec<Step>,
    /// Keyed by the index of the group's first step.
    pub groups: BTreeMap<usize, Group>,
    pub stdout_artifacts: Option<BTreeMap<String, ArtifactSpec>>,
    pub capture: CapturePolicy,
    /// Gate name to output-relative path, in manifest order.
    pub gates: Vec<(String, String)>,
    pub artifacts: Vec<(String, String)>,
}

impl Manifest {
    pub fn step_index(&self, id: &str) -> Option<usize> {
        self.steps.iter().position(|step| step.id == id)
    }

    /// The group that contains the step, if any.
    pub fn group_of(&self, index: usize) -> Option<&Group> {
        self.groups
            .values()
            .find(|group| group.steps.contains(&index))
    }

    pub fn has_tool(&self, name: &str) -> bool {
        let key = format!("tools:{name}");
        self.pins.iter().any(|pin| pin.key == key)
    }
}

fn strings(value: Option<&Json>) -> Option<Vec<String>> {
    value?
        .items()?
        .iter()
        .map(|item| item.as_str().map(str::to_owned))
        .collect()
}

fn path_map(value: &Json, what: &str) -> Result<Vec<(String, String)>> {
    let members = value
        .members()
        .ok_or_else(|| bad("MANIFEST_FIELDS", format!("{what} must be an object")))?;
    members
        .iter()
        .map(|(name, path)| {
            path.as_str()
                .map(|path| (name.clone(), path.to_owned()))
                .ok_or_else(|| bad("MANIFEST_FIELDS", format!("{what} paths must be strings")))
        })
        .collect()
}

/// The stdout stream a pinned native export command produces, if it is one of the data
/// exports the recipe consumes. The match is over the unexpanded arguments: naming a tool or
/// an export-looking suffix does not exempt a command (inspect, route, run and script
/// commands are diagnostics).
pub fn stdout_artifact_format(command: &Command) -> Option<&'static str> {
    fn fresh_workspace(value: &str) -> bool {
        value.strip_prefix("{output}/").is_some_and(|suffix| {
            !suffix.is_empty()
                && suffix.split('/').all(|part| {
                    !part.is_empty()
                        && part
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                })
        })
    }
    let args: Vec<&str> = command.arguments.iter().map(String::as_str).collect();
    match command.tool.as_str() {
        "pin_caps"
            if args.len() == 1
                && [
                    "{files:std_tt}",
                    "{files:std_ss}",
                    "{files:ram_tt}",
                    "{files:ram_ss}",
                ]
                .contains(&args[0]) =>
        {
            Some("pin_capacitance")
        }
        "rustroad" if args.len() >= 3 && args[0] == "--workspace" && fresh_workspace(args[1]) => {
            match &args[2..] {
                ["export-verilog"] | ["export-verilog", "--include-power"] => Some("verilog"),
                ["export-def"] => Some("def"),
                ["export-placement-seed"] => Some("placement_seed"),
                _ => None,
            }
        }
        "ruststa"
            if args.len() >= 3 && args[..2] == ["report", "-w"] && fresh_workspace(args[2]) =>
        {
            let tail = &args[3..];
            for corner in ["tt", "ss"] {
                if let ["--corner", c, "--threads", "2", "--section", rest @ ..] = tail {
                    if *c != corner {
                        continue;
                    }
                    match rest {
                        [
                            "drv",
                            "--filter",
                            "violating" | "slack<0.2",
                            "--limit",
                            "100000",
                            "--format",
                            "json",
                        ] => {
                            return Some("timing_drv");
                        }
                        ["endpoints", "--limit", "0", "--format", "json"] => {
                            return Some("timing_endpoints");
                        }
                        _ => (),
                    }
                }
            }
            if tail
                == [
                    "--threads",
                    "2",
                    "--section",
                    "hold_fix",
                    "--target-margin",
                    "0.5",
                    "--limit",
                    "0",
                    "--format",
                    "json",
                ]
            {
                return Some("timing_hold_fix");
            }
            None
        }
        _ => None,
    }
}

fn step(value: &Json, seen: &mut BTreeSet<String>) -> Result<Step> {
    let id = value
        .get("id")
        .and_then(Json::as_str)
        .filter(|id| handle(id))
        .ok_or_else(|| bad("STEP_INVALID", "invalid or duplicate step handle"))?;
    if !seen.insert(id.to_owned()) {
        return Err(bad(
            "STEP_INVALID",
            format!("invalid or duplicate step handle: {id}"),
        ));
    }
    let kind = match value.get("kind").and_then(Json::as_str) {
        Some("render") => {
            if !value.has_exactly(&["id", "kind", "destination", "content"]) {
                return Err(bad(
                    "STEP_INVALID",
                    format!("unknown render step field: {id}"),
                ));
            }
            let destination = value
                .get("destination")
                .and_then(Json::as_str)
                .ok_or_else(|| {
                    bad(
                        "STEP_INVALID",
                        format!("render destination must be a string: {id}"),
                    )
                })?;
            Kind::Render {
                destination: destination.to_owned(),
                content: value.get("content").cloned().unwrap_or(Json::Null),
            }
        }
        Some("command") => {
            let plain = ["id", "kind", "tool", "arguments", "timeout_s"];
            let declared = [
                "id",
                "kind",
                "tool",
                "arguments",
                "timeout_s",
                "stdout_artifact",
            ];
            if !value.has_exactly(&plain) && !value.has_exactly(&declared) {
                return Err(bad(
                    "STEP_INVALID",
                    format!("unknown command step field: {id}"),
                ));
            }
            let tool = value
                .get("tool")
                .and_then(Json::as_str)
                .ok_or_else(|| bad("STEP_INVALID", format!("command tool must be a name: {id}")))?;
            let arguments = strings(value.get("arguments"))
                .filter(|arguments| (1..=256).contains(&arguments.len()))
                .ok_or_else(|| {
                    bad(
                        "STEP_INVALID",
                        format!("command argument count budget exceeded: {id}"),
                    )
                })?;
            let timeout_s = value
                .get("timeout_s")
                .and_then(Json::as_u64)
                .filter(|seconds| (1..=86400).contains(seconds))
                .ok_or_else(|| bad("STEP_INVALID", format!("invalid command deadline: {id}")))?;
            let stdout_artifact = match value.get("stdout_artifact") {
                None => None,
                Some(handle) => Some(
                    handle
                        .as_str()
                        .ok_or_else(|| {
                            bad(
                                "STDOUT_ARTIFACT_POLICY",
                                "invalid or multiply referenced stdout artifact handle",
                            )
                        })?
                        .to_owned(),
                ),
            };
            Kind::Command(Command {
                tool: tool.to_owned(),
                arguments,
                timeout_s,
                stdout_artifact,
            })
        }
        _ => {
            return Err(bad("STEP_INVALID", format!("unsupported step kind: {id}")));
        }
    };
    Ok(Step {
        id: id.to_owned(),
        kind,
    })
}

fn stdout_artifacts(
    steps: &[Step],
    registry: Option<&Json>,
    scope: &Json,
) -> Result<(Option<BTreeMap<String, ArtifactSpec>>, CapturePolicy)> {
    let policy = |message: &str| bad("STDOUT_ARTIFACT_POLICY", message);
    let declared_handle = |step: &Step| match &step.kind {
        Kind::Command(command) => command.stdout_artifact.clone(),
        Kind::Render { .. } => None,
    };
    let Some(registry) = registry.filter(|value| **value != Json::Null) else {
        if steps.iter().any(|step| declared_handle(step).is_some()) {
            return Err(policy("stdout artifact is not registered"));
        }
        if scope.get("max_stdout_artifact_bytes").is_some() {
            return Err(policy(
                "stdout artifact budget requires an explicit registry",
            ));
        }
        return Ok((
            None,
            CapturePolicy {
                mode: "legacy_all_streams_are_logs",
                declared_bytes: 0,
                aggregate_limit_bytes: 0,
                files: 0,
            },
        ));
    };
    let entries = registry
        .members()
        .filter(|entries| (1..=128).contains(&entries.len()))
        .ok_or_else(|| policy("stdout artifact registry count budget exceeded"))?;
    let cap = scope
        .get("max_stdout_artifact_bytes")
        .and_then(Json::as_u64)
        .filter(|cap| (1..=MAX_STDOUT_ARTIFACT_BYTES).contains(cap))
        .ok_or_else(|| policy("invalid aggregate stdout artifact byte budget"))?;
    let mut specs = BTreeMap::new();
    let mut declared = 0_u64;
    for step in steps {
        let Some(name) = declared_handle(step) else {
            continue;
        };
        if !handle(&name) || specs.contains_key(&name) {
            return Err(policy(
                "invalid or multiply referenced stdout artifact handle",
            ));
        }
        let Kind::Command(command) = &step.kind else {
            continue;
        };
        let format = stdout_artifact_format(command)
            .ok_or_else(|| policy("non-export command cannot declare a stdout artifact"))?;
        let spec = registry.get(&name);
        let exact = spec.is_some_and(|spec| {
            spec.has_exactly(&["path", "format", "max_bytes"])
                && name == step.id
                && spec.get("path").and_then(Json::as_str)
                    == Some(format!("logs/{}.stdout", step.id).as_str())
                && spec.get("format").and_then(Json::as_str) == Some(format)
        });
        let Some(spec) = spec.filter(|_| exact) else {
            return Err(policy(
                "stdout artifact must name this exact supported command output",
            ));
        };
        let limit = artifact_cap(format).unwrap_or(0);
        let size = spec
            .get("max_bytes")
            .and_then(Json::as_u64)
            .filter(|size| (1..=limit).contains(size))
            .ok_or_else(|| policy("invalid per-file stdout artifact byte budget"))?;
        declared += size;
        specs.insert(
            name,
            ArtifactSpec {
                raw: spec.clone(),
                format: format.to_owned(),
                max_bytes: size,
            },
        );
    }
    if specs.len() != entries.len() || entries.iter().any(|(name, _)| !specs.contains_key(name)) {
        return Err(policy("unreferenced or undeclared stdout artifact"));
    }
    if declared > MAX_STDOUT_ARTIFACT_BYTES {
        return Err(policy("declared stdout artifact byte sum exceeds 1536 MiB"));
    }
    let files = specs.len();
    Ok((
        Some(specs),
        CapturePolicy {
            mode: "explicit_stdout_artifacts",
            declared_bytes: declared,
            aggregate_limit_bytes: cap,
            files,
        },
    ))
}

/// Bounded, contiguous DAG regions. Dependencies must precede their dependants in manifest
/// order, which both excludes cycles and keeps a stop target a true serial prefix. CPU
/// slots index the worker's CPU allocation; memory reservations are admission control
/// under the caller's hard limit, not a per-process claim.
fn parallel_groups(
    steps: &[Step],
    raw_steps: &[Json],
    groups: Option<&Json>,
    scope: &Json,
) -> Result<BTreeMap<usize, Group>> {
    let invalid = |message: &str| bad("PARALLEL_GROUP_INVALID", message);
    let Some(groups) = groups.filter(|value| **value != Json::Null) else {
        return Ok(BTreeMap::new());
    };
    let groups = groups
        .items()
        .filter(|groups| (1..=8).contains(&groups.len()))
        .ok_or_else(|| invalid("parallel group count budget exceeded"))?;
    let order: BTreeMap<&str, usize> = steps
        .iter()
        .enumerate()
        .map(|(index, step)| (step.id.as_str(), index))
        .collect();
    let (Some(memory), Some(threads)) = (
        scope.get("memory_mib").and_then(Json::as_i64),
        scope.get("threads").and_then(Json::as_i64),
    ) else {
        return Err(invalid(
            "parallel execution requires explicit memory/CPU allocation",
        ));
    };
    let mut result = BTreeMap::new();
    let mut assigned = BTreeSet::new();
    for group in groups {
        if !group.has_exactly(&["max_parallel", "max_memory_mib", "steps"]) {
            return Err(invalid("unknown parallel group field"));
        }
        let width = group.get("max_parallel").and_then(Json::as_i64);
        let cap = group.get("max_memory_mib").and_then(Json::as_i64);
        let (Some(width), Some(cap)) = (
            width.filter(|width| (1..=16).contains(width)),
            cap.filter(|cap| (1..=memory - 128).contains(cap)),
        ) else {
            return Err(invalid(
                "parallel CPU/memory scheduling cap exceeds allocation",
            ));
        };
        let nodes = group
            .get("steps")
            .and_then(Json::members)
            .filter(|nodes| {
                (1..=128).contains(&nodes.len())
                    && nodes
                        .iter()
                        .all(|(tag, _)| order.contains_key(tag.as_str()))
            })
            .ok_or_else(|| invalid("unknown or excessive parallel step"))?;
        let mut indices: Vec<usize> = nodes.iter().map(|(tag, _)| order[tag.as_str()]).collect();
        indices.sort_unstable();
        let contiguous = indices
            .iter()
            .enumerate()
            .all(|(offset, index)| *index == indices[0] + offset);
        if !contiguous || indices.iter().any(|index| assigned.contains(index)) {
            return Err(invalid(
                "parallel groups must be disjoint contiguous manifest regions",
            ));
        }
        assigned.extend(indices.iter().copied());
        let mut parsed = BTreeMap::new();
        for (tag, request) in nodes {
            let index = order[tag.as_str()];
            if request.members().is_none() {
                return Err(invalid("invalid parallel resource request"));
            }
            let depends_on = strings(request.get("depends_on"))
                .filter(|deps| {
                    deps.len() <= 128
                        && deps.iter().collect::<BTreeSet<_>>().len() == deps.len()
                        && deps.iter().all(|dep| {
                            nodes.iter().any(|(name, _)| name == dep) && order[dep.as_str()] < index
                        })
                })
                .ok_or_else(|| {
                    invalid("parallel dependencies must be unique earlier group steps (no cycles)")
                })?;
            let raw = &raw_steps[index];
            match &steps[index].kind {
                Kind::Render { .. } => {
                    if !request.has_exactly(&["depends_on"])
                        || !raw.has_exactly(&["id", "kind", "destination", "content"])
                    {
                        return Err(invalid("invalid parallel render fields"));
                    }
                    parsed.insert(
                        tag.clone(),
                        Node {
                            depends_on,
                            ..Node::default()
                        },
                    );
                }
                Kind::Command(_) => {
                    if !request.has_exactly(&["depends_on", "cpu_slots", "memory_mib"]) {
                        return Err(invalid("parallel commands must be bounded commands"));
                    }
                    let slots = request
                        .get("cpu_slots")
                        .and_then(Json::items)
                        .map(|slots| slots.iter().map(Json::as_i64).collect::<Vec<_>>())
                        .filter(|slots| {
                            !slots.is_empty()
                                && slots.len() as i64 <= threads
                                && slots.iter().all(|slot| {
                                    slot.is_some_and(|slot| (0..threads).contains(&slot))
                                })
                                && slots.iter().collect::<BTreeSet<_>>().len() == slots.len()
                        });
                    let reservation = request
                        .get("memory_mib")
                        .and_then(Json::as_i64)
                        .filter(|reservation| (1..=cap).contains(reservation));
                    let (Some(slots), Some(reservation)) = (slots, reservation) else {
                        return Err(invalid("parallel CPU/memory request exceeds allocation"));
                    };
                    parsed.insert(
                        tag.clone(),
                        Node {
                            depends_on,
                            cpu_slots: slots
                                .into_iter()
                                .flatten()
                                .map(|slot| slot as usize)
                                .collect(),
                            memory_mib: reservation as u64,
                        },
                    );
                }
            }
        }
        result.insert(
            indices[0],
            Group {
                max_parallel: width as usize,
                max_memory_mib: cap as u64,
                nodes: parsed,
                steps: indices,
            },
        );
    }
    Ok(result)
}

/// Parse and validate a manifest document. No file other than the document is read.
pub fn parse(bytes: &[u8]) -> Result<Manifest> {
    if bytes.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(bad("MANIFEST_BUDGET", "JSON byte budget exceeded"));
    }
    let raw = Json::parse(bytes)?;
    let text = |key: &str| raw.get(key).and_then(Json::as_str);
    let corners = strings(raw.get("corners"));
    if text("schema") != Some(SCHEMA)
        || text("pdk") != Some("ihp-sg13g2")
        || text("library") != Some("ez130_8t")
        || corners.as_deref() != Some(&["tt".to_owned(), "ss".to_owned()])
    {
        return Err(bad("MANIFEST_IDENTITY", "IHP producer identity mismatch"));
    }
    let keys = raw.keys();
    if REQUIRED.iter().any(|name| !keys.contains(name))
        || keys
            .iter()
            .any(|name| !REQUIRED.contains(name) && !OPTIONAL.contains(name))
    {
        return Err(bad(
            "MANIFEST_FIELDS",
            "unknown or missing producer manifest field",
        ));
    }
    let raw_steps = raw
        .get("steps")
        .and_then(Json::items)
        .ok_or_else(|| bad("MANIFEST_FIELDS", "steps must be an array"))?;
    let artifacts = path_map(raw.get("artifacts").unwrap_or(&Json::Null), "artifacts")?;
    if !(1..=MAX_STEPS).contains(&raw_steps.len()) || artifacts.len() > 128 {
        return Err(bad(
            "MANIFEST_BUDGET",
            "step/artifact count budget exceeded",
        ));
    }
    let mut pins = Vec::new();
    for (group, limit) in [("files", 256), ("tools", 32)] {
        let entries = raw
            .get(group)
            .and_then(Json::members)
            .filter(|entries| (1..=limit).contains(&entries.len()))
            .ok_or_else(|| bad("MANIFEST_BUDGET", "input collection budget exceeded"))?;
        for (name, spec) in entries {
            if !handle(name) {
                return Err(bad("MANIFEST_FIELDS", "unsafe input handle"));
            }
            let field = |key: &str| spec.get(key).and_then(Json::as_str);
            let (Some(path), Some(sha256), Some(bytes)) = (
                field("path"),
                field("sha256"),
                spec.get("bytes").and_then(Json::as_u64),
            ) else {
                return Err(bad(
                    "MANIFEST_FIELDS",
                    format!("input {group}:{name} needs path, sha256 and bytes"),
                ));
            };
            pins.push(Pin {
                key: format!("{group}:{name}"),
                path: path.to_owned(),
                sha256: sha256.to_owned(),
                bytes,
            });
        }
    }
    let scope_raw = raw.get("scope").cloned().unwrap_or(Json::Null);
    let max_log_bytes = scope_raw
        .get("max_log_bytes")
        .and_then(Json::as_u64)
        .filter(|budget| (1024..=64 * 1024 * 1024).contains(budget))
        .ok_or_else(|| bad("SCOPE_INVALID", "invalid aggregate log byte budget"))?;
    let mut seen = BTreeSet::new();
    let steps = raw_steps
        .iter()
        .map(|value| step(value, &mut seen))
        .collect::<Result<Vec<_>>>()?;
    let (stdout_artifacts, capture) =
        stdout_artifacts(&steps, raw.get("stdout_artifacts"), &scope_raw)?;
    let groups = parallel_groups(&steps, raw_steps, raw.get("parallel_groups"), &scope_raw)?;
    let threads = scope_raw
        .get("threads")
        .and_then(Json::as_u64)
        .filter(|threads| (1..=16).contains(threads));
    let cpus = scope_raw
        .get("cpu_affinity")
        .and_then(Json::items)
        .map(|cpus| {
            cpus.iter()
                .filter_map(Json::as_u64)
                .map(|cpu| cpu as usize)
                .collect::<Vec<_>>()
        });
    let (Some(threads), Some(cpus)) = (threads, cpus) else {
        return Err(bad(
            "CPU_ALLOCATION_INVALID",
            "invalid physical-core CPU allocation",
        ));
    };
    let declared = scope_raw
        .get("cpu_affinity")
        .and_then(Json::items)
        .map_or(0, <[Json]>::len);
    if cpus.len() != threads as usize
        || declared != cpus.len()
        || cpus.iter().collect::<BTreeSet<_>>().len() != cpus.len()
    {
        return Err(bad(
            "CPU_ALLOCATION_INVALID",
            "invalid physical-core CPU allocation",
        ));
    }
    let manifest = Manifest {
        name: text("name").map(str::to_owned),
        pins,
        scope: Scope {
            raw: scope_raw,
            threads: threads as usize,
            cpu_affinity: cpus,
            max_log_bytes,
        },
        steps,
        groups,
        stdout_artifacts,
        capture,
        gates: path_map(raw.get("gates").unwrap_or(&Json::Null), "gates")?,
        artifacts,
        raw,
    };
    for step in &manifest.steps {
        if let Kind::Command(command) = &step.kind
            && !manifest.has_tool(&command.tool)
        {
            return Err(bad(
                "STEP_INVALID",
                format!("step {} names an unpinned tool: {}", step.id, command.tool),
            ));
        }
    }
    for tool in ["rustroad", "gnu_time"] {
        if !manifest.has_tool(tool) {
            return Err(bad(
                "MANIFEST_FIELDS",
                format!("the manifest must pin the {tool} tool"),
            ));
        }
    }
    Ok(manifest)
}

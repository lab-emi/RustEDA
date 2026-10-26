// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
};

pub const MAX_CONFIG_BYTES: u64 = 2 * 1024 * 1024;
pub const MAX_STEPS: usize = 128;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum Pdk {
    #[serde(rename = "sky130_fd_sc_hd", alias = "sky130A")]
    Sky130,
    #[serde(rename = "gf180mcu_fd_sc_mcu7t5v0", alias = "gf180mcuD")]
    Gf180Seven,
    #[serde(rename = "gf180mcu_fd_sc_mcu9t5v0")]
    Gf180Nine,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
pub enum FlowKind {
    #[default]
    #[serde(rename = "synthesis_sta")]
    SynthesisSta,
    #[serde(rename = "classic", alias = "Classic")]
    Classic,
    #[serde(rename = "rusteda")]
    Rusteda,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RoadFlow {
    pub project: PathBuf,
    pub manifest: PathBuf,
    pub driver: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Library {
    pub path: PathBuf,
    pub corner: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Tools {
    pub rustsys: PathBuf,
    pub rusteda: PathBuf,
    pub ruststa: PathBuf,
    pub openroad: PathBuf,
    pub klayout: PathBuf,
    pub magic: PathBuf,
    pub netgen: PathBuf,
    pub python: PathBuf,
    pub rustroad: PathBuf,
    pub gnu_time: PathBuf,
    pub podman: PathBuf,
}

impl Default for Tools {
    fn default() -> Self {
        Self {
            rustsys: "rustsys".into(),
            rusteda: "rusteda".into(),
            ruststa: "ruststa".into(),
            openroad: "openroad".into(),
            klayout: "klayout".into(),
            magic: "magic".into(),
            netgen: "netgen".into(),
            python: "python3".into(),
            rustroad: "rustroad".into(),
            gnu_time: "time".into(),
            podman: "podman".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Limits {
    pub timeout_secs: u64,
    pub memory_mib: u64,
    pub max_log_bytes: u64,
    pub max_file_bytes: u64,
    pub max_input_bytes: u64,
    pub max_paths: u32,
    pub max_state_gib: u32,
    pub threads: u32,
    pub max_conflicts: u64,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            timeout_secs: 180,
            memory_mib: 4096,
            max_log_bytes: 8 * 1024 * 1024,
            max_file_bytes: 1024 * 1024 * 1024,
            max_input_bytes: 4 * 1024 * 1024 * 1024,
            max_paths: 8,
            max_state_gib: 1,
            threads: 2,
            max_conflicts: 10_000,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PhysicalTool {
    Openroad,
    Klayout,
    Magic,
    Netgen,
}

impl PhysicalTool {
    pub fn name(self) -> &'static str {
        match self {
            Self::Openroad => "openroad",
            Self::Klayout => "klayout",
            Self::Magic => "magic",
            Self::Netgen => "netgen",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactKind {
    Netlist,
    Spef,
    Def,
    Odb,
    Gds,
    DrcReport,
    LvsReport,
    Other,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhysicalOutput {
    pub kind: ArtifactKind,
    pub path: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhysicalStep {
    pub id: String,
    pub tool: PhysicalTool,
    pub script: PathBuf,
    /// Explicit argv, with whole-string placeholders expanded without a shell.
    pub arguments: Vec<String>,
    pub outputs: Vec<PhysicalOutput>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    #[serde(default = "one")]
    pub schema_version: u32,
    #[serde(alias = "DESIGN_NAME")]
    pub design_name: String,
    #[serde(alias = "PDK")]
    pub pdk: Pdk,
    #[serde(alias = "VERILOG_FILES")]
    #[serde(default)]
    pub verilog_files: Vec<PathBuf>,
    #[serde(default)]
    pub libraries: Vec<Library>,
    #[serde(alias = "SDC_FILE")]
    #[serde(default)]
    pub sdc_file: PathBuf,
    #[serde(default = "default_corner")]
    pub synthesis_corner: String,
    #[serde(default)]
    pub flow: FlowKind,
    #[serde(default)]
    pub tools: Tools,
    #[serde(default)]
    pub limits: Limits,
    #[serde(default)]
    pub defines: BTreeMap<String, String>,
    #[serde(default)]
    pub parameters: BTreeMap<String, String>,
    #[serde(default)]
    pub physical_steps: Vec<PhysicalStep>,
    /// Explicit physical collateral (LEF, rule decks, layer maps, etc.).
    #[serde(default)]
    pub collateral: BTreeMap<String, PathBuf>,
    #[serde(default)]
    pub road: Option<RoadFlow>,
}

fn one() -> u32 {
    1
}
fn default_corner() -> String {
    "tt".into()
}

pub fn handle(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}

impl Config {
    pub fn load(path: &Path) -> Result<Self> {
        let mut file = fs::File::open(path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() || metadata.len() > MAX_CONFIG_BYTES {
            return Err(Error::new(
                "CONFIG_BUDGET",
                "configuration must be a regular file <= 2 MiB",
            ));
        }
        let mut content = Vec::new();
        (&mut file)
            .take(MAX_CONFIG_BYTES + 1)
            .read_to_end(&mut content)?;
        if content.len() as u64 > MAX_CONFIG_BYTES {
            return Err(Error::new(
                "CONFIG_BUDGET",
                "configuration grew beyond its bound",
            ));
        }
        let mut config: Self = serde_json::from_slice(&content)
            .map_err(|e| Error::new("CONFIG_ERROR", e.to_string()))?;
        config.validate()?;
        let parent = fs::canonicalize(path)?.parent().unwrap().to_path_buf();
        config.resolve(&parent)?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<()> {
        let bad = |s: &str| Error::new("CONFIG_ERROR", s);
        if self.schema_version != 1 {
            return Err(bad("unsupported schema_version"));
        }
        if !handle(&self.design_name) || !handle(&self.synthesis_corner) {
            return Err(bad(
                "design_name and synthesis_corner must be shell-safe handles",
            ));
        }
        if self.flow == FlowKind::Rusteda {
            return crate::integration::validate_config(self);
        }
        if self.road.is_some() {
            return Err(bad("road is only valid for flow rusteda"));
        }
        if self.verilog_files.is_empty() || self.verilog_files.len() > 256 {
            return Err(bad("1..256 verilog_files required"));
        }
        if self.libraries.is_empty() || self.libraries.len() > 256 {
            return Err(bad("1..256 libraries required"));
        }
        let corners: BTreeSet<_> = self.libraries.iter().map(|l| &l.corner).collect();
        if corners.len() > 32
            || corners.iter().any(|s| !handle(s))
            || !corners.contains(&self.synthesis_corner)
        {
            return Err(bad(
                "at most 32 safe corner handles; synthesis_corner must exist",
            ));
        }
        if self.defines.len() > 256 || self.parameters.len() > 256 || self.collateral.len() > 256 {
            return Err(bad("configuration collection budget exceeded"));
        }
        if self
            .defines
            .keys()
            .chain(self.parameters.keys())
            .any(|k| !handle(k))
            || self.collateral.keys().any(|k| !handle(k))
        {
            return Err(bad(
                "define, parameter and collateral keys must be safe handles",
            ));
        }
        if self
            .defines
            .values()
            .chain(self.parameters.values())
            .any(|v| v.len() > 16384 || v.contains('\0'))
        {
            return Err(bad("oversized or NUL-containing definition/parameter"));
        }
        let l = &self.limits;
        if !(1..=86400).contains(&l.timeout_secs)
            || !(64..=16384).contains(&l.memory_mib)
            || !(1024..=64 * 1024 * 1024).contains(&l.max_log_bytes)
            || !(1..=16 * 1024 * 1024 * 1024).contains(&l.max_file_bytes)
            || l.max_input_bytes < l.max_file_bytes
            || l.max_input_bytes > 64 * 1024 * 1024 * 1024
            || !(1..=100_000).contains(&l.max_paths)
            || !(1..=8).contains(&l.max_state_gib)
            || l.max_state_gib as u64 * 1024 > l.memory_mib
            || !(1..=64).contains(&l.threads)
            || !(1..=100_000_000).contains(&l.max_conflicts)
        {
            return Err(bad("limits outside supported finite resource ranges"));
        }
        if self.physical_steps.len() > 32 {
            return Err(bad("at most 32 physical steps"));
        }
        if self.flow == FlowKind::SynthesisSta && !self.physical_steps.is_empty() {
            return Err(bad("physical_steps require flow classic"));
        }
        let mut ids: BTreeSet<String> = [
            "synthesis",
            "equivalence",
            "sta_pre",
            "equivalence_post",
            "sta_post",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect();
        for step in &self.physical_steps {
            if !handle(&step.id) || !ids.insert(step.id.clone()) {
                return Err(bad(
                    "physical step IDs must be unique safe nonreserved handles",
                ));
            }
            if step.arguments.is_empty()
                || step.arguments.len() > 256
                || step.outputs.is_empty()
                || step.outputs.len() > 64
            {
                return Err(bad(
                    "physical step requires 1..256 argv and 1..64 declared outputs",
                ));
            }
            for arg in &step.arguments {
                if arg.len() > 16384 || arg.contains('\0') {
                    return Err(bad("invalid physical argument"));
                }
            }
            let mut output_paths = BTreeSet::new();
            for output in &step.outputs {
                if !relative_output(&output.path) || !output_paths.insert(&output.path) {
                    return Err(bad("output paths must be unique, normal relative paths"));
                }
            }
        }
        if self.flow == FlowKind::Classic {
            let kinds: Vec<_> = self
                .physical_steps
                .iter()
                .flat_map(|s| s.outputs.iter().map(|o| o.kind))
                .collect();
            if ![ArtifactKind::Netlist, ArtifactKind::Spef, ArtifactKind::Gds]
                .iter()
                .all(|k| kinds.contains(k))
            {
                return Err(bad(
                    "classic requires explicit physical recipes producing netlist, SPEF and GDS; no implicit legacy synthesis/STA",
                ));
            }
            if corners.len() != 1 {
                return Err(bad(
                    "classic currently requires one explicit parasitic corner; multicorner physical association is unsupported",
                ));
            }
        }
        Ok(())
    }

    fn resolve(&mut self, base: &Path) -> Result<()> {
        if self.flow == FlowKind::Rusteda {
            let road = self.road.as_mut().unwrap();
            road.project = fs::canonicalize(base.join(&road.project))?;
            if !road.project.is_dir() {
                return Err(Error::new(
                    "CONFIG_ERROR",
                    "road.project must be a directory",
                ));
            }
            road.manifest = resolve_input(&road.project, &road.manifest)?;
            if !road.manifest.starts_with(&road.project) {
                return Err(Error::new(
                    "CONFIG_ERROR",
                    "road manifest must be within its project",
                ));
            }
            road.driver = resolve_input(base, &road.driver)?;
        }
        for path in &mut self.verilog_files {
            *path = resolve_input(base, path)?;
        }
        for library in &mut self.libraries {
            library.path = resolve_input(base, &library.path)?;
        }
        if self.flow != FlowKind::Rusteda {
            self.sdc_file = resolve_input(base, &self.sdc_file)?;
        }
        for step in &mut self.physical_steps {
            step.script = resolve_input(base, &step.script)?;
        }
        for path in self.collateral.values_mut() {
            *path = resolve_input(base, path)?;
        }
        for path in [
            &mut self.tools.rustsys,
            &mut self.tools.rusteda,
            &mut self.tools.ruststa,
            &mut self.tools.openroad,
            &mut self.tools.klayout,
            &mut self.tools.magic,
            &mut self.tools.netgen,
            &mut self.tools.python,
            &mut self.tools.rustroad,
            &mut self.tools.gnu_time,
            &mut self.tools.podman,
        ] {
            if path.components().count() > 1 && path.is_relative() {
                *path = base.join(&*path);
            }
        }
        if self.flow == FlowKind::Rusteda {
            for path in [
                &mut self.tools.python,
                &mut self.tools.rustroad,
                &mut self.tools.gnu_time,
                &mut self.tools.podman,
            ] {
                *path = crate::engine::resolve_tool(path)?;
            }
        }
        Ok(())
    }

    pub fn input_paths(&self) -> Result<Vec<PathBuf>> {
        if self.flow == FlowKind::Rusteda {
            return crate::integration::input_paths(self);
        }
        let mut paths: BTreeSet<PathBuf> = self.verilog_files.iter().cloned().collect();
        paths.extend(self.libraries.iter().map(|l| l.path.clone()));
        paths.insert(self.sdc_file.clone());
        paths.extend(self.physical_steps.iter().map(|s| s.script.clone()));
        paths.extend(self.collateral.values().cloned());
        Ok(paths.into_iter().collect())
    }

    pub fn replace_paths(&mut self, paths: &BTreeMap<PathBuf, PathBuf>) {
        if self.flow == FlowKind::Rusteda {
            let road = self.road.as_mut().unwrap();
            road.driver = paths[&road.driver].clone();
            // The qualified producer resolves its collateral relative to its
            // project. Originals AND immutable copies remain identity checked.
            return;
        }
        for p in &mut self.verilog_files {
            *p = paths[p].clone();
        }
        for l in &mut self.libraries {
            l.path = paths[&l.path].clone();
        }
        self.sdc_file = paths[&self.sdc_file].clone();
        for s in &mut self.physical_steps {
            s.script = paths[&s.script].clone();
        }
        for p in self.collateral.values_mut() {
            *p = paths[p].clone();
        }
    }
}

pub fn relative_output(path: &Path) -> bool {
    !path.as_os_str().is_empty() && path.components().all(|c| matches!(c, Component::Normal(_)))
}

fn resolve_input(base: &Path, path: &Path) -> Result<PathBuf> {
    let path = fs::canonicalize(if path.is_absolute() {
        path.to_owned()
    } else {
        base.join(path)
    })?;
    if !fs::metadata(&path)?.is_file() {
        return Err(Error::new(
            "CONFIG_ERROR",
            format!("not a regular input: {}", path.display()),
        ));
    }
    Ok(path)
}

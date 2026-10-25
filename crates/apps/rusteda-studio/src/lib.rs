// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! `rusteda-studio`: the workbench of the RustEDA suite (`docs/specs/studio/`).
//!
//! The workbench shows a disk workspace live: it follows `HEAD` as tools commit revisions,
//! and shows the displayed revision's netlist hierarchy, its ranked timing paths, the logic
//! cone of a path or an instance as a schematic, and the RTL lines of what is selected. It
//! reads the workspace only through `rusteda-workspace`, never writes a table, and shares the
//! user's selection with the command-line tools through `ui_state.json` (plan §1.4, §4.2
//! Example 3, §6.1).
//!
//! The crate has two parts:
//!
//! - [`model`]: the headless model, with no graphics dependency, fully tested;
//! - `gui` (feature `gui`, on by default): the front end on the GPUI framework, which renders
//!   the model and forwards the user's actions to it.

pub mod model;

#[cfg(feature = "gui")]
pub mod gui;

pub use model::diag::{Code, Severity, StudioDiag};
pub use model::studio::{Studio, StudioOptions};

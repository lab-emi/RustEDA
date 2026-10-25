// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The headless model of the workbench: everything the panels show, computed without any
//! graphics, so that it is tested on workspaces built in tests (STUDIO-REQ-0240).

pub mod budget;
pub mod cone;
pub mod diag;
pub mod hierarchy;
pub mod layout;
pub mod netlist;
pub mod print;
pub mod rtl;
pub mod scene;
pub mod selection;
pub mod snapshot;
pub mod studio;
pub mod timing;
pub mod watch;

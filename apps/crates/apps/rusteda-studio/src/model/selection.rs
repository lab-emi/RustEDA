// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Selection sharing through `ui_state.json` (`docs/specs/studio/selection.md`; plan §4.2,
//! Example 3; WS-REQ-0043).
//!
//! A selection of the workbench is a set of ranked paths and instances of the displayed
//! revision. Sharing it writes `ui_state.json` through the workspace's API
//! (`Workspace::write_ui_state`, an atomic replacement): a fresh handle `sel<N>`, the displayed
//! revision, and the shell-safe handles `inst<row + 1>` and `path<row + 1>` (WS-REQ-0022). A
//! command-line tool then resolves `--target sel<N>` against that revision only. The workbench
//! reads selections that other programs write in the same way, and never rewrites the file
//! unless the user selects (STUDIO-REQ-0140 to STUDIO-REQ-0147).

use rusteda_workspace::rev::is_handle;
use rusteda_workspace::{BadHandle, Handle, Rev, UiState};

/// A selection of the displayed revision: path rows and instance rows, in selection order,
/// each once (STUDIO-REQ-0140).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Selection {
    /// Ranked path rows (`path<row + 1>`).
    pub paths: Vec<usize>,
    /// Instance rows (`inst<row + 1>`).
    pub instances: Vec<u32>,
}

impl Selection {
    /// Whether nothing is selected.
    pub fn is_empty(&self) -> bool {
        self.paths.is_empty() && self.instances.is_empty()
    }

    /// Selects path `row`: alone, or added to (or, when present, removed from) the selection
    /// with `additive` (STUDIO-REQ-0141).
    pub fn pick_path(&mut self, row: usize, additive: bool) {
        if !additive {
            *self = Selection {
                paths: vec![row],
                instances: Vec::new(),
            };
        } else if let Some(i) = self.paths.iter().position(|&p| p == row) {
            self.paths.remove(i);
        } else {
            self.paths.push(row);
        }
    }

    /// Selects instance `row`, alone or toggled into the selection (STUDIO-REQ-0141).
    pub fn pick_instance(&mut self, row: u32, additive: bool) {
        if !additive {
            *self = Selection {
                paths: Vec::new(),
                instances: vec![row],
            };
        } else if let Some(i) = self.instances.iter().position(|&p| p == row) {
            self.instances.remove(i);
        } else {
            self.instances.push(row);
        }
    }
}

/// The `ui_state.json` of selection `sel<number>` of revision `rev` (STUDIO-REQ-0142): handles
/// `inst<row + 1>` and `path<row + 1>`, every one shell-safe (Rule 4.1).
pub fn to_ui_state(rev: Rev, number: u32, selection: &Selection) -> Result<UiState, BadHandle> {
    let sel = Handle::new("sel", number)?.to_string();
    let instances = selection
        .instances
        .iter()
        .map(|&r| Handle::from_row("inst", r).map(|h| h.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    let paths = selection
        .paths
        .iter()
        .map(|&r| {
            u32::try_from(r)
                .map_err(|_| BadHandle {
                    text: format!("path{r}"),
                    reason: "is out of range".into(),
                })
                .and_then(|r| Handle::from_row("path", r))
                .map(|h| h.to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    for h in instances.iter().chain(&paths).chain(std::iter::once(&sel)) {
        debug_assert!(is_handle(h));
    }
    Ok(UiState::new(rev, sel, instances, paths))
}

/// The number of the next selection: one above both the selection in the file and the last
/// one this workbench wrote, so a new selection never reuses a handle (STUDIO-REQ-0143).
pub fn next_number(current: Option<&UiState>, last_written: u32) -> u32 {
    let in_file = current
        .and_then(|s| Handle::parse_as(&s.selection_handle, "sel").ok())
        .unwrap_or(0);
    in_file.max(last_written).saturating_add(1).max(1)
}

/// What a shared selection means for the displayed revision (STUDIO-REQ-0144).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resolution {
    /// The selection belongs to the displayed revision and every handle names an object.
    Applied(Selection),
    /// The selection belongs to another revision.
    OtherRevision(Rev),
    /// Handles that name nothing in the displayed revision (the others are applied).
    Partial(Selection, Vec<String>),
}

/// Resolves `state` for the displayed revision `rev` with `instances` instance rows and
/// `paths` ranked paths (STUDIO-REQ-0144).
pub fn resolve(state: &UiState, rev: Rev, instances: usize, paths: usize) -> Resolution {
    if state.revision != rev {
        return Resolution::OtherRevision(state.revision);
    }
    let mut sel = Selection::default();
    let mut unknown = Vec::new();
    for h in &state.selected_instances {
        match Handle::parse_as(h, "inst") {
            Ok(n) if (n as usize) <= instances => {
                if !sel.instances.contains(&(n - 1)) {
                    sel.instances.push(n - 1);
                }
            }
            _ => unknown.push(h.clone()),
        }
    }
    for h in &state.selected_paths {
        match Handle::parse_as(h, "path") {
            Ok(n) if (n as usize) <= paths => {
                let row = n as usize - 1;
                if !sel.paths.contains(&row) {
                    sel.paths.push(row);
                }
            }
            _ => unknown.push(h.clone()),
        }
    }
    if unknown.is_empty() {
        Resolution::Applied(sel)
    } else {
        Resolution::Partial(sel, unknown)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // STUDIO-REQ-0140, STUDIO-REQ-0141: a selection holds path and instance rows once each; a
    // plain pick replaces it, an additive pick toggles.
    #[test]
    fn picking() {
        let mut s = Selection::default();
        assert!(s.is_empty());
        s.pick_path(3, false);
        s.pick_instance(7, true);
        s.pick_instance(9, true);
        assert_eq!(s.paths, [3]);
        assert_eq!(s.instances, [7, 9]);
        s.pick_instance(7, true);
        assert_eq!(s.instances, [9]);
        s.pick_path(1, false);
        assert_eq!(
            s,
            Selection {
                paths: vec![1],
                instances: vec![]
            }
        );
        s.pick_path(1, true);
        assert!(s.is_empty());
    }

    // STUDIO-REQ-0142: handles are 1-based and shell-safe, pinned to the revision.
    #[test]
    fn ui_state_handles() {
        let s = Selection {
            paths: vec![0, 4],
            instances: vec![840, 841],
        };
        let u = to_ui_state(Rev(2), 4, &s).unwrap();
        assert_eq!(u.revision, Rev(2));
        assert_eq!(u.selection_handle, "sel4");
        assert_eq!(u.selected_instances, ["inst841", "inst842"]);
        assert_eq!(u.selected_paths, ["path1", "path5"]);
        assert!(u.validate().is_ok());
        assert!(to_ui_state(Rev(2), 0, &s).is_err());
        let big = Selection {
            paths: vec![],
            instances: vec![u32::MAX],
        };
        assert!(to_ui_state(Rev(2), 1, &big).is_err());
    }

    // STUDIO-REQ-0143: the next number is above the file's and our last.
    #[test]
    fn numbering() {
        assert_eq!(next_number(None, 0), 1);
        let u = UiState::new(Rev(1), "sel7", vec![], vec!["path1".into()]);
        assert_eq!(next_number(Some(&u), 3), 8);
        assert_eq!(next_number(Some(&u), 9), 10);
    }

    // STUDIO-REQ-0144: another revision is reported, unknown handles are named and the rest
    // applies.
    #[test]
    fn resolving() {
        let u = UiState::new(
            Rev(2),
            "sel1",
            vec!["inst3".into(), "inst99".into()],
            vec!["path2".into()],
        );
        assert_eq!(
            resolve(&u, Rev(3), 10, 5),
            Resolution::OtherRevision(Rev(2))
        );
        assert_eq!(
            resolve(&u, Rev(2), 10, 5),
            Resolution::Partial(
                Selection {
                    paths: vec![1],
                    instances: vec![2]
                },
                vec!["inst99".into()]
            )
        );
        assert_eq!(
            resolve(&u, Rev(2), 100, 5),
            Resolution::Applied(Selection {
                paths: vec![1],
                instances: vec![2, 98]
            })
        );
    }
}

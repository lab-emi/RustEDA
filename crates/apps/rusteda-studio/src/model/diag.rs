// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Coded diagnostics of the workbench (`docs/specs/studio/diagnostics.md`).
//!
//! Every problem the workbench meets while it reads a workspace, follows its revisions or shares
//! a selection is a [`StudioDiag`] with a code `U0001` to `U0199` (STUDIO-REQ-0200): a missing
//! table, a damaged one, a budget that cut a traversal short, an approximate cross-probe. The
//! workbench never reports an absent value as zero or a skipped analysis as passing (Rule 4.2):
//! what it could not show is named by a diagnostic.

use std::fmt;

/// How serious a diagnostic is (STUDIO-REQ-0201).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Severity {
    /// Context: a table that the revision does not have yet, an approximate result.
    Note,
    /// A result that is incomplete: a budget truncated it, or a selection could not be applied.
    Warning,
    /// Something could not be read or written.
    Error,
}

impl Severity {
    /// `note`, `warning` or `error`.
    pub fn as_str(self) -> &'static str {
        match self {
            Severity::Note => "note",
            Severity::Warning => "warning",
            Severity::Error => "error",
        }
    }
}

/// The diagnostic codes of the workbench (STUDIO-REQ-0200, STUDIO-REQ-0202). The numbers are
/// stable: a code is never reused for another meaning.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Code {
    /// U0001: the directory is not a workspace, or its manifest cannot be read.
    WorkspaceUnreadable,
    /// U0002: the requested revision does not exist.
    UnknownRevision,
    /// U0003: the revision's `meta.json` cannot be read.
    RevisionUnreadable,
    /// U0010: the revision has no netlist table.
    NoNetlist,
    /// U0011: the netlist table cannot be read or is damaged.
    NetlistUnreadable,
    /// U0020: the revision has no timing table (timing not analyzed).
    NoTiming,
    /// U0021: the timing table cannot be read or is damaged.
    TimingUnreadable,
    /// U0022: the path table lists only the first paths (budget `max_path_rows`).
    PathRowsTruncated,
    /// U0030: the revision has no RTL snapshot.
    NoRtl,
    /// U0031: an RTL file cannot be read or exceeds the size budget.
    RtlFileSkipped,
    /// U0040: `ui_state.json` cannot be read (damaged or not of a known schema).
    UiStateUnreadable,
    /// U0041: the shared selection belongs to another revision than the one displayed.
    SelectionOtherRevision,
    /// U0042: `ui_state.json` could not be written.
    UiStateWriteFailed,
    /// U0043: a handle of the shared selection names nothing in the displayed revision.
    SelectionHandleUnknown,
    /// U0050: the logic cone was cut short by a budget.
    ConeTruncated,
    /// U0051: the root of the logic cone is not in the netlist.
    ConeRootUnknown,
    /// U0052: the layout of the logic cone was simplified by a budget.
    LayoutTruncated,
    /// U0060: hierarchy names deeper than the depth budget were folded.
    HierarchyDepthFolded,
    /// U0061: a search listed only the first matches.
    SearchTruncated,
    /// U0070: an RTL cross-probe is approximate (name matching).
    CrossProbeApproximate,
    /// U0071: a cross-probe found no RTL source line.
    CrossProbeNoMatch,
    /// U0080: the workspace manifest disappeared or became unreadable while being watched.
    WatchManifestLost,
    /// U0081: a new revision could not be loaded; the previous one stays displayed.
    ReloadFailed,
    /// U0082: the displayed revision changed (a tool committed or reverted).
    RevisionChanged,
    /// U0090: the graphical front end could not start.
    GuiUnavailable,
    /// U0091: a command-line argument of the workbench is invalid.
    BadArgument,
}

impl Code {
    /// Every code, in number order.
    pub const ALL: [Code; 26] = [
        Code::WorkspaceUnreadable,
        Code::UnknownRevision,
        Code::RevisionUnreadable,
        Code::NoNetlist,
        Code::NetlistUnreadable,
        Code::NoTiming,
        Code::TimingUnreadable,
        Code::PathRowsTruncated,
        Code::NoRtl,
        Code::RtlFileSkipped,
        Code::UiStateUnreadable,
        Code::SelectionOtherRevision,
        Code::UiStateWriteFailed,
        Code::SelectionHandleUnknown,
        Code::ConeTruncated,
        Code::ConeRootUnknown,
        Code::LayoutTruncated,
        Code::HierarchyDepthFolded,
        Code::SearchTruncated,
        Code::CrossProbeApproximate,
        Code::CrossProbeNoMatch,
        Code::WatchManifestLost,
        Code::ReloadFailed,
        Code::RevisionChanged,
        Code::GuiUnavailable,
        Code::BadArgument,
    ];

    /// The number of the code (1 to 199).
    pub fn number(self) -> u16 {
        match self {
            Code::WorkspaceUnreadable => 1,
            Code::UnknownRevision => 2,
            Code::RevisionUnreadable => 3,
            Code::NoNetlist => 10,
            Code::NetlistUnreadable => 11,
            Code::NoTiming => 20,
            Code::TimingUnreadable => 21,
            Code::PathRowsTruncated => 22,
            Code::NoRtl => 30,
            Code::RtlFileSkipped => 31,
            Code::UiStateUnreadable => 40,
            Code::SelectionOtherRevision => 41,
            Code::UiStateWriteFailed => 42,
            Code::SelectionHandleUnknown => 43,
            Code::ConeTruncated => 50,
            Code::ConeRootUnknown => 51,
            Code::LayoutTruncated => 52,
            Code::HierarchyDepthFolded => 60,
            Code::SearchTruncated => 61,
            Code::CrossProbeApproximate => 70,
            Code::CrossProbeNoMatch => 71,
            Code::WatchManifestLost => 80,
            Code::ReloadFailed => 81,
            Code::RevisionChanged => 82,
            Code::GuiUnavailable => 90,
            Code::BadArgument => 91,
        }
    }

    /// The code as text, `U` and four digits (`U0050`).
    pub fn as_string(self) -> String {
        format!("U{:04}", self.number())
    }

    /// The severity a diagnostic of this code has (STUDIO-REQ-0201).
    pub fn severity(self) -> Severity {
        match self {
            Code::NoNetlist
            | Code::NoTiming
            | Code::NoRtl
            | Code::CrossProbeApproximate
            | Code::CrossProbeNoMatch
            | Code::RevisionChanged => Severity::Note,
            Code::PathRowsTruncated
            | Code::SelectionOtherRevision
            | Code::SelectionHandleUnknown
            | Code::ConeTruncated
            | Code::ConeRootUnknown
            | Code::LayoutTruncated
            | Code::HierarchyDepthFolded
            | Code::SearchTruncated
            | Code::RtlFileSkipped
            | Code::WatchManifestLost => Severity::Warning,
            Code::WorkspaceUnreadable
            | Code::UnknownRevision
            | Code::RevisionUnreadable
            | Code::NetlistUnreadable
            | Code::TimingUnreadable
            | Code::UiStateUnreadable
            | Code::UiStateWriteFailed
            | Code::ReloadFailed
            | Code::GuiUnavailable
            | Code::BadArgument => Severity::Error,
        }
    }
}

impl fmt::Display for Code {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "U{:04}", self.number())
    }
}

/// A diagnostic of the workbench: a code, its severity and a message (STUDIO-REQ-0200).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StudioDiag {
    /// The code.
    pub code: Code,
    /// The severity of the code.
    pub severity: Severity,
    /// What happened, with the names involved.
    pub message: String,
}

impl StudioDiag {
    /// A diagnostic of `code` with the code's severity.
    pub fn new(code: Code, message: impl Into<String>) -> StudioDiag {
        StudioDiag {
            code,
            severity: code.severity(),
            message: message.into(),
        }
    }
}

impl fmt::Display for StudioDiag {
    /// `<severity>[U<nnnn>]: <message>`, the line format of the suite's command lines.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{}[{}]: {}",
            self.severity.as_str(),
            self.code,
            self.message
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // STUDIO-REQ-0200, STUDIO-REQ-0202: codes are `U` and four digits in 1 to 199, unique and
    // sorted in their declaration order.
    #[test]
    fn codes_are_unique_and_in_range() {
        let mut last = 0;
        for code in Code::ALL {
            let n = code.number();
            assert!((1..=199).contains(&n), "{code}");
            assert!(n > last, "{code} out of order");
            last = n;
            let text = code.as_string();
            assert_eq!(text.len(), 5);
            assert!(text.starts_with('U'));
            assert_eq!(text, code.to_string());
        }
    }

    // STUDIO-REQ-0201: a diagnostic takes the severity of its code and prints as
    // `<severity>[U<nnnn>]: <message>`.
    #[test]
    fn line_format_and_severity() {
        let d = StudioDiag::new(Code::ConeTruncated, "cone of u1 stopped at 200 nodes");
        assert_eq!(d.severity, Severity::Warning);
        assert_eq!(
            d.to_string(),
            "warning[U0050]: cone of u1 stopped at 200 nodes"
        );
        assert_eq!(
            StudioDiag::new(Code::NoTiming, "x").severity,
            Severity::Note
        );
        assert_eq!(
            StudioDiag::new(Code::UiStateWriteFailed, "x").severity,
            Severity::Error
        );
    }
}

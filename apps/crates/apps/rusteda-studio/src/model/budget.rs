// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The work and memory budgets of the workbench (Rules 3.4 and 9.2; STUDIO-REQ-0003).
//!
//! Every traversal of the workbench is bounded: the hierarchy by its depth, searches by their
//! number of matches, the path table by its rows, logic cones by depth, nodes, edges, net fanout
//! and pin visits, their layout by virtual nodes and sweeps, and the RTL index by file sizes and
//! hits. A budget that is reached ends the traversal with a structured "truncated" result and a
//! diagnostic, never with an unbounded allocation.

/// The budgets of one workbench (STUDIO-REQ-0003). [`Budgets::default`] holds the values the
/// specification names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Budgets {
    /// Hierarchy levels of a name; deeper parts are folded into the last level.
    pub max_hierarchy_depth: usize,
    /// Matches listed by a search.
    pub max_search_results: usize,
    /// Ranked paths listed in the path table.
    pub max_path_rows: usize,
    /// Revisions listed in the revision panel (newest first).
    pub max_revisions: usize,
    /// Fan-in levels of a logic cone.
    pub cone_fanin_depth: u32,
    /// Fan-out levels of a logic cone.
    pub cone_fanout_depth: u32,
    /// Nodes (instances and port bits) of a logic cone.
    pub cone_max_nodes: usize,
    /// Edges of a logic cone.
    pub cone_max_edges: usize,
    /// Loads of one net that a fan-out expansion follows.
    pub cone_max_net_fanout: usize,
    /// Pin and net visits of one cone extraction (the work budget).
    pub cone_max_visits: usize,
    /// Virtual nodes the layout inserts on long edges.
    pub layout_max_virtual: usize,
    /// Ordering sweeps of the layout.
    pub layout_sweeps: usize,
    /// Bytes of one RTL file that the index reads.
    pub rtl_max_file_bytes: u64,
    /// Bytes of all RTL files that the index reads.
    pub rtl_max_total_bytes: u64,
    /// Source lines a cross-probe returns.
    pub rtl_max_hits: usize,
}

impl Default for Budgets {
    fn default() -> Budgets {
        Budgets {
            max_hierarchy_depth: 64,
            max_search_results: 1000,
            max_path_rows: 10_000,
            max_revisions: 64,
            cone_fanin_depth: 3,
            cone_fanout_depth: 2,
            cone_max_nodes: 160,
            cone_max_edges: 1200,
            cone_max_net_fanout: 32,
            cone_max_visits: 200_000,
            layout_max_virtual: 4000,
            layout_sweeps: 12,
            rtl_max_file_bytes: 4 << 20,
            rtl_max_total_bytes: 64 << 20,
            rtl_max_hits: 40,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // STUDIO-REQ-0003: the default budgets are those of the specification.
    #[test]
    fn default_budgets() {
        let b = Budgets::default();
        assert_eq!(
            (
                b.max_hierarchy_depth,
                b.max_search_results,
                b.max_path_rows,
                b.max_revisions
            ),
            (64, 1000, 10_000, 64)
        );
        assert_eq!((b.cone_fanin_depth, b.cone_fanout_depth), (3, 2));
        assert_eq!(
            (
                b.cone_max_nodes,
                b.cone_max_edges,
                b.cone_max_net_fanout,
                b.cone_max_visits
            ),
            (160, 1200, 32, 200_000)
        );
        assert_eq!((b.layout_max_virtual, b.layout_sweeps), (4000, 12));
        assert_eq!(
            (b.rtl_max_file_bytes, b.rtl_max_total_bytes, b.rtl_max_hits),
            (4 << 20, 64 << 20, 40)
        );
    }
}

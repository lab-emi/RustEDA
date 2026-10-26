// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The interactive critical-path table (`docs/specs/studio/paths.md`).
//!
//! The timing table of a revision is read through the shared read-only view
//! [`StaTable`] (WS-REQ-0126 to WS-REQ-0136); the workbench never depends on the timing engine.
//! [`TimingView`] lists the ranked paths with their handles `path<N>` (WS-REQ-0133), kind,
//! corner, slack, startpoint, endpoint and logic levels, the per-corner summary with its WNS and
//! TNS (absent, not zero, without endpoints: WS-REQ-0131 and Rule 4.2), the analysis scope, and
//! the detail of one path with its points (WS-REQ-0134).

use rusteda_workspace::sta::{CheckKind, PointKind, StaTable, Transition};
use serde_json::{Map, Value};

use super::diag::{Code, StudioDiag};
use super::netlist::NetlistView;

/// One corner's summary (STUDIO-REQ-0051).
#[derive(Debug, Clone, PartialEq)]
pub struct CornerSummary {
    /// The corner index.
    pub corner: usize,
    /// The corner name.
    pub name: String,
    /// Worst setup slack in ns; `None` without setup endpoints.
    pub setup_wns_ns: Option<f64>,
    /// Total negative setup slack in ns.
    pub setup_tns_ns: f64,
    /// Endpoints with negative setup slack.
    pub setup_violating: u32,
    /// Worst hold slack in ns; `None` without hold endpoints.
    pub hold_wns_ns: Option<f64>,
    /// Total negative hold slack in ns.
    pub hold_tns_ns: f64,
    /// Endpoints with negative hold slack.
    pub hold_violating: u32,
    /// Checked endpoints.
    pub endpoints: u32,
    /// Endpoints without a constraint.
    pub unconstrained_endpoints: u32,
}

/// One row of the path table (STUDIO-REQ-0050).
#[derive(Debug, Clone, PartialEq)]
pub struct PathRow {
    /// The row of the ranked path; the handle is `path<row + 1>`.
    pub row: usize,
    /// `path<row + 1>`.
    pub handle: String,
    /// The check.
    pub kind: CheckKind,
    /// The corner index.
    pub corner: usize,
    /// The corner name.
    pub corner_name: String,
    /// The slack in ns.
    pub slack_ns: f64,
    /// Whether the slack is the exact `f64` of a materialized path or the `f32` of the rank.
    pub slack_exact: bool,
    /// The first point of the path.
    pub startpoint: String,
    /// The endpoint.
    pub endpoint: String,
    /// Cell arcs on the path.
    pub levels: u32,
}

impl PathRow {
    /// Whether the slack is negative.
    pub fn violating(&self) -> bool {
        self.slack_ns < 0.0
    }
}

/// One point of a path in the detail panel (STUDIO-REQ-0053).
#[derive(Debug, Clone, PartialEq)]
pub struct DetailPoint {
    /// `inst/pin` or a port name.
    pub name: String,
    /// The instance; `None` for a port.
    pub instance: Option<String>,
    /// The instance row in the netlist of the same revision, when the netlist has it.
    pub instance_row: Option<u32>,
    /// The cell of the instance, from the netlist.
    pub cell: Option<String>,
    /// What reaches the point.
    pub kind: PointKind,
    /// Its transition.
    pub transition: Transition,
    /// The arrival in ns, for a materialized path.
    pub arrival_ns: Option<f64>,
    /// The arrival minus the previous point's arrival, for a materialized path.
    pub increment_ns: Option<f64>,
}

/// The detail of one path (STUDIO-REQ-0053).
#[derive(Debug, Clone, PartialEq)]
pub struct PathDetail {
    /// The table row.
    pub row: PathRow,
    /// The required time in ns.
    pub required_ns: Option<f64>,
    /// The check value in ns (setup or hold time of the endpoint).
    pub check_ns: Option<f64>,
    /// Whether the points carry arrival times.
    pub materialized: bool,
    /// The points from the startpoint to the endpoint.
    pub points: Vec<DetailPoint>,
    /// Instances on the path, each once, in path order (`RankedPath::instances`).
    pub instances: Vec<String>,
}

/// The timing table of a revision as the panels show it (STUDIO-REQ-0050 to STUDIO-REQ-0054).
#[derive(Debug)]
pub struct TimingView {
    table: StaTable,
    corners: Vec<CornerSummary>,
    rows: Vec<PathRow>,
    total_paths: usize,
    scope: Map<String, Value>,
}

/// The startpoint of a path: its first point (the convention of the timing tool's reports).
fn startpoint(points: &[rusteda_workspace::sta::PathPoint], endpoint: &str) -> String {
    points
        .first()
        .map_or_else(|| endpoint.to_string(), |p| p.name.clone())
}

/// Cell arcs of a path: its logic levels.
fn levels(points: &[rusteda_workspace::sta::PathPoint]) -> u32 {
    points.iter().filter(|p| p.kind == PointKind::Cell).count() as u32
}

impl TimingView {
    /// Reads the summary, scope and the first `max_rows` ranked paths of `table`
    /// (STUDIO-REQ-0050); a query the table refuses is an error of the whole view.
    pub fn new(
        table: StaTable,
        max_rows: usize,
    ) -> Result<(TimingView, Vec<StudioDiag>), rusteda_workspace::Error> {
        let mut diags = Vec::new();
        let counts = table.counts();
        let mut corners = Vec::new();
        for c in 0..counts.corners as usize {
            let s = table.corner_summary(c)?;
            corners.push(CornerSummary {
                corner: c,
                name: s.corner_name,
                setup_wns_ns: s.setup_wns_ns,
                setup_tns_ns: s.setup_tns_ns,
                setup_violating: s.setup_violating,
                hold_wns_ns: s.hold_wns_ns,
                hold_tns_ns: s.hold_tns_ns,
                hold_violating: s.hold_violating,
                endpoints: s.total_endpoints,
                unconstrained_endpoints: s.unconstrained_endpoints,
            });
        }
        let names = table.corner_names()?;
        let ranks = table.path_ranks()?;
        let total_paths = ranks.len();
        let mut rows = Vec::with_capacity(total_paths.min(max_rows));
        for rank in ranks.iter().take(max_rows) {
            let p = table.path(rank.row)?;
            rows.push(PathRow {
                row: rank.row,
                handle: p.handle(),
                kind: rank.kind,
                corner: rank.corner,
                corner_name: names.get(rank.corner).cloned().unwrap_or_default(),
                slack_ns: p.slack_ns,
                slack_exact: p.slack_exact,
                startpoint: startpoint(&p.points, &p.endpoint_name),
                endpoint: p.endpoint_name.clone(),
                levels: levels(&p.points),
            });
        }
        if total_paths > max_rows {
            diags.push(StudioDiag::new(
                Code::PathRowsTruncated,
                format!("the path table lists the first {max_rows} of {total_paths} ranked paths"),
            ));
        }
        let scope = table.scope()?;
        Ok((
            TimingView {
                table,
                corners,
                rows,
                total_paths,
                scope,
            },
            diags,
        ))
    }

    /// The per-corner summaries, in corner order (STUDIO-REQ-0051).
    pub fn corners(&self) -> &[CornerSummary] {
        &self.corners
    }

    /// The listed rows, in rank order (STUDIO-REQ-0050).
    pub fn rows(&self) -> &[PathRow] {
        &self.rows
    }

    /// All ranked paths of the table, listed or not.
    pub fn total_paths(&self) -> usize {
        self.total_paths
    }

    /// The analysis scope the timing tool recorded (STUDIO-REQ-0052).
    pub fn scope(&self) -> &Map<String, Value> {
        &self.scope
    }

    /// The scope as `key=value` pairs in key order, for the summary bar (STUDIO-REQ-0052).
    pub fn scope_text(&self) -> String {
        self.scope
            .iter()
            .map(|(k, v)| match v {
                Value::String(s) => format!("{k}={s}"),
                other => format!("{k}={other}"),
            })
            .collect::<Vec<_>>()
            .join("  ")
    }

    /// The listed row of ranked path `row`.
    pub fn row(&self, row: usize) -> Option<&PathRow> {
        self.rows.iter().find(|r| r.row == row)
    }

    /// The rows that pass a filter, in rank order (STUDIO-REQ-0054): a corner, the hold family
    /// or the setup family, and negative slack only.
    pub fn filtered(&self, filter: &PathFilter) -> Vec<&PathRow> {
        self.rows
            .iter()
            .filter(|r| filter.corner.is_none_or(|c| c == r.corner))
            .filter(|r| match filter.family {
                Family::All => true,
                Family::Setup => !r.kind.is_hold_family(),
                Family::Hold => r.kind.is_hold_family(),
            })
            .filter(|r| !filter.violating_only || r.violating())
            .collect()
    }

    /// The detail of ranked path `row` (STUDIO-REQ-0053), with cells and instance rows from
    /// `netlist` when the revision has one.
    pub fn detail(
        &self,
        row: usize,
        netlist: Option<&NetlistView>,
    ) -> Result<PathDetail, rusteda_workspace::Error> {
        let p = self.table.path(row)?;
        let names = self.table.corner_names()?;
        let required = self.table.path_required(row).ok();
        let mut points = Vec::with_capacity(p.points.len());
        let mut prev: Option<f64> = None;
        for pt in &p.points {
            let instance_row = pt
                .instance
                .as_deref()
                .and_then(|i| netlist.and_then(|n| n.find_instance(i)));
            let cell = instance_row.and_then(|r| netlist.map(|n| n.instance_cell(r).to_string()));
            let increment = match (pt.arrival_ns, prev) {
                (Some(a), Some(b)) => Some(a - b),
                (Some(_), None) => Some(0.0),
                _ => None,
            };
            prev = pt.arrival_ns.or(prev);
            points.push(DetailPoint {
                name: pt.name.clone(),
                instance: pt.instance.clone(),
                instance_row,
                cell,
                kind: pt.kind,
                transition: pt.transition,
                arrival_ns: pt.arrival_ns,
                increment_ns: increment,
            });
        }
        Ok(PathDetail {
            row: PathRow {
                row,
                handle: p.handle(),
                kind: p.kind,
                corner: p.corner,
                corner_name: names.get(p.corner).cloned().unwrap_or_default(),
                slack_ns: p.slack_ns,
                slack_exact: p.slack_exact,
                startpoint: startpoint(&p.points, &p.endpoint_name),
                endpoint: p.endpoint_name.clone(),
                levels: levels(&p.points),
            },
            required_ns: required.map(|r| r.0),
            check_ns: required.map(|r| r.1),
            materialized: p.materialized,
            instances: p.instances().into_iter().map(str::to_string).collect(),
            points,
        })
    }

    /// The underlying table.
    pub fn table(&self) -> &StaTable {
        &self.table
    }
}

/// Which check family a filter keeps (STUDIO-REQ-0054).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Family {
    /// Every path.
    #[default]
    All,
    /// Setup and recovery.
    Setup,
    /// Hold and removal.
    Hold,
}

/// A filter of the path table (STUDIO-REQ-0054).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PathFilter {
    /// Only this corner.
    pub corner: Option<usize>,
    /// Only this family.
    pub family: Family,
    /// Only negative slack.
    pub violating_only: bool,
}

/// A slack for display: three decimals with its sign, `~` before an `f32` slack, and `n/a` for
/// an absent value (never `0`, Rule 4.2; STUDIO-REQ-0051).
pub fn format_ns(value: Option<f64>, exact: bool) -> String {
    match value {
        None => "n/a".to_string(),
        Some(v) => {
            let v = if v == 0.0 { 0.0 } else { v };
            if exact {
                format!("{v:.3}")
            } else {
                format!("~{v:.3}")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusteda_workspace::sta::{EndpointKind, PathSpec, StaTableBuilder, StoreMode};

    fn table() -> StaTable {
        let mut b = StaTableBuilder::new(StoreMode::Full);
        let tt = b.corner("tt");
        let ss = b.corner("ss");
        b.clock("clk", 1.0, 0.0);
        let a = b.port("a");
        let u1_a = b.pin("u1", "A");
        let u1_y = b.pin("u1", "Y");
        let r_d = b.pin("r1", "D");
        b.instance("u1", "inv").instance("r1", "dff");
        let y = b.port("y");
        b.endpoint(r_d, EndpointKind::FlipFlop);
        b.endpoint(y, EndpointKind::OutputPort);
        b.endpoint_slack(r_d, tt, Some(-0.25), Some(0.1));
        b.endpoint_slack(r_d, ss, Some(-0.5), None);
        b.endpoint_slack(y, tt, Some(0.3), None);
        let pts = |end| {
            vec![
                (a, Transition::Rise, PointKind::Port, 0.0),
                (u1_a, Transition::Rise, PointKind::Net, 0.1),
                (u1_y, Transition::Fall, PointKind::Cell, 0.6),
                (end, Transition::Fall, PointKind::Net, 0.7),
            ]
        };
        for (corner, slack, end) in [(tt, -0.25, r_d), (ss, -0.5, r_d), (tt, 0.3, y)] {
            b.path(PathSpec {
                corner,
                kind: CheckKind::Setup,
                slack_ns: slack,
                required_ns: 0.45,
                points: pts(end),
            });
        }
        b.path(PathSpec {
            corner: tt,
            kind: CheckKind::Hold,
            slack_ns: 0.1,
            required_ns: 0.05,
            points: pts(r_d),
        });
        StaTable::from_bytes(b.to_bytes(&Default::default()).unwrap()).unwrap()
    }

    // STUDIO-REQ-0050: rows in rank order with handle, kind, corner, slack, startpoint,
    // endpoint and levels; the budget lists the first rows and says so.
    #[test]
    fn rows_in_rank_order() {
        let (v, d) = TimingView::new(table(), 100).unwrap();
        assert!(d.is_empty());
        assert_eq!(v.total_paths(), 4);
        let r = &v.rows()[0];
        assert_eq!(r.handle, "path1");
        assert_eq!(r.corner_name, "ss");
        assert_eq!(r.slack_ns, -0.5);
        assert!(r.slack_exact);
        assert_eq!(r.startpoint, "a");
        assert_eq!(r.endpoint, "r1/D");
        assert_eq!(r.levels, 1);
        assert!(r.violating());
        assert_eq!(v.rows()[3].kind, CheckKind::Hold);
        let (v, d) = TimingView::new(table(), 2).unwrap();
        assert_eq!(v.rows().len(), 2);
        assert_eq!(d[0].code, Code::PathRowsTruncated);
    }

    // STUDIO-REQ-0051, STUDIO-REQ-0052: per-corner WNS and TNS, absent WNS shown as n/a, and
    // the analysis scope.
    #[test]
    fn corner_summary_and_scope() {
        let (v, _) = TimingView::new(table(), 100).unwrap();
        let tt = &v.corners()[0];
        assert_eq!(tt.name, "tt");
        assert_eq!(tt.setup_wns_ns, Some(-0.25));
        assert_eq!(tt.setup_tns_ns, -0.25);
        assert_eq!(tt.setup_violating, 1);
        assert_eq!(tt.endpoints, 2);
        let ss = &v.corners()[1];
        assert_eq!(ss.hold_wns_ns, None);
        assert_eq!(format_ns(ss.hold_wns_ns, true), "n/a");
        assert_eq!(format_ns(Some(-0.0), true), "0.000");
        assert_eq!(format_ns(Some(-0.25), false), "~-0.250");
        assert!(v.scope().contains_key("wire_model"));
        assert!(v.scope_text().contains("wire_model=none"));
    }

    // STUDIO-REQ-0053: the detail lists the points with arrivals, increments and cells.
    #[test]
    fn detail_points() {
        let (v, _) = TimingView::new(table(), 100).unwrap();
        let d = v.detail(0, None).unwrap();
        assert_eq!(d.row.handle, "path1");
        assert!(d.materialized);
        assert_eq!(d.points.len(), 4);
        assert_eq!(d.points[0].increment_ns, Some(0.0));
        let inc = d.points[2].increment_ns.unwrap();
        assert!((inc - 0.5).abs() < 1e-12, "{inc}");
        assert_eq!(d.points[2].instance.as_deref(), Some("u1"));
        assert_eq!(d.instances, ["u1", "r1"]);
        assert_eq!(d.required_ns, Some(0.45));
        assert!(v.detail(99, None).is_err());
    }

    // STUDIO-REQ-0054: filters by corner, family and negative slack.
    #[test]
    fn filters() {
        let (v, _) = TimingView::new(table(), 100).unwrap();
        let f = |corner, family, violating_only| PathFilter {
            corner,
            family,
            violating_only,
        };
        assert_eq!(v.filtered(&f(None, Family::All, false)).len(), 4);
        assert_eq!(v.filtered(&f(Some(0), Family::All, false)).len(), 3);
        assert_eq!(v.filtered(&f(None, Family::Hold, false)).len(), 1);
        assert_eq!(v.filtered(&f(None, Family::Setup, true)).len(), 2);
    }
}

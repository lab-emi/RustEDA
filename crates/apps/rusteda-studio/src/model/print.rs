// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The text form of the panels for `--print` (STUDIO-REQ-0222).
//!
//! The text lists what the window shows, in the same order and from the same model: the
//! revision, the hierarchy's first level, the corner summaries with the analysis scope, the
//! path table, the focused path's points, the cone and the cross-probe, then the diagnostics.
//! It is deterministic for a given workspace (no times or absolute paths), so tests and agents
//! can compare it.

use std::fmt::Write as _;

use super::hierarchy::TreeItem;
use super::studio::Studio;
use super::timing::format_ns;

/// The panels of `studio` as text, with at most `max_rows` rows per list.
pub fn summary(studio: &Studio, max_rows: usize) -> String {
    let mut o = String::new();
    let snap = studio.snapshot();
    let i = &snap.info;
    let _ = writeln!(
        o,
        "revision {} (HEAD {}{}){}",
        snap.rev,
        snap.head,
        if studio.follows_head() {
            ", following"
        } else {
            ", pinned"
        },
        i.parent.map_or(String::new(), |p| format!(" parent {p}"))
    );
    let _ = writeln!(o, "  made by: {} ({})", i.command, i.tool);
    let _ = writeln!(o, "  tables: {}", i.tables.join(", "));
    let _ = writeln!(o, "  timing: {}", i.timing);
    if let (Some(nl), Some(h)) = (&snap.netlist, &snap.hierarchy) {
        let _ = writeln!(
            o,
            "hierarchy {}: {} instances, {} nets, {} ports, {} scopes",
            nl.module_name(),
            nl.instance_count(),
            nl.net_count(),
            nl.port_count(),
            h.scopes().len() - 1
        );
        for (d, c) in h.level_counts().iter().enumerate() {
            let _ = writeln!(
                o,
                "  level {d}: {} instances, {} scopes below",
                c.instances, c.scopes
            );
        }
        for r in studio.tree_rows().iter().take(max_rows) {
            if let TreeItem::Scope(_) = r.item {
                let (label, detail) = studio.tree_text(r.item);
                let _ = writeln!(o, "  {}{label}  [{detail}]", "  ".repeat(r.depth as usize));
            }
        }
    }
    if let Some(t) = &snap.timing {
        let _ = writeln!(o, "timing scope: {}", t.scope_text());
        for c in t.corners() {
            let _ = writeln!(
                o,
                "  corner {}: setup WNS {} TNS {} ({} violating), hold WNS {} TNS {} ({} \
                 violating), {} endpoints, {} unconstrained",
                c.name,
                format_ns(c.setup_wns_ns, true),
                format_ns(Some(c.setup_tns_ns), true),
                c.setup_violating,
                format_ns(c.hold_wns_ns, true),
                format_ns(Some(c.hold_tns_ns), true),
                c.hold_violating,
                c.endpoints,
                c.unconstrained_endpoints
            );
        }
        let rows = studio.visible_paths();
        let _ = writeln!(
            o,
            "paths: {} listed of {} ranked",
            rows.len(),
            t.total_paths()
        );
        for r in rows.iter().take(max_rows) {
            let _ = writeln!(
                o,
                "  {:<7} {:<8} {:<6} slack {:>9}  levels {:>3}  {} -> {}",
                r.handle,
                r.kind.as_str(),
                r.corner_name,
                format_ns(Some(r.slack_ns), r.slack_exact),
                r.levels,
                r.startpoint,
                r.endpoint
            );
        }
    }
    if let Some(d) = studio.detail() {
        let _ = writeln!(
            o,
            "focus {}: {} {} slack {} required {}",
            d.row.handle,
            d.row.kind.as_str(),
            d.row.corner_name,
            format_ns(Some(d.row.slack_ns), d.row.slack_exact),
            format_ns(d.required_ns, true)
        );
        for p in d.points.iter().take(max_rows) {
            let _ = writeln!(
                o,
                "  {:<28} {:<10} {:?}/{:?}  arrival {}",
                p.name,
                p.cell.as_deref().unwrap_or("-"),
                p.kind,
                p.transition,
                format_ns(p.arrival_ns, true)
            );
        }
    }
    if let Some(s) = studio.scene() {
        let on_path = s.nodes.iter().filter(|n| n.on_path).count();
        let _ = writeln!(
            o,
            "cone of {}: {} nodes ({} on the path), {} edges, {} layers, {} crossings{}",
            s.root_label,
            s.nodes.len(),
            on_path,
            s.edges.len(),
            s.layers,
            s.crossings,
            if s.truncation.any() {
                format!(", truncated by {}", s.truncation.reasons().join("/"))
            } else {
                String::new()
            }
        );
    }
    if let Some(p) = studio.probe() {
        let _ = writeln!(
            o,
            "rtl of {}: {} lines (approximate)",
            p.subject,
            p.hits.len()
        );
        if let Some(index) = &snap.rtl {
            for h in p.hits.iter().take(max_rows.min(8)) {
                let _ = writeln!(
                    o,
                    "  {}:{} [{}] {}  ({})",
                    index.files[h.file].path,
                    h.line,
                    h.kind.as_str(),
                    index.files[h.file].line(h.line).trim(),
                    h.reason
                );
            }
        }
    }
    match studio.shared() {
        Some(u) => {
            let _ = writeln!(
                o,
                "shared selection: {} of {}: paths [{}] instances [{}]",
                u.selection_handle,
                u.revision,
                u.selected_paths.join(", "),
                u.selected_instances.join(", ")
            );
        }
        None => {
            let _ = writeln!(o, "shared selection: none");
        }
    }
    for d in studio.diagnostics() {
        let _ = writeln!(o, "{d}");
    }
    o
}

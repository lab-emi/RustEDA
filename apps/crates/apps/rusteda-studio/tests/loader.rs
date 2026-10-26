// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Loading a revision as a run of the core job API: phases, progress, observation and
//! cancellation.

mod common;

use std::sync::Mutex;

use common::{design_workspace, scratch};
use rusteda_core::job::{CancelToken, Ctx, Event, JobError, Observer};
use rusteda_studio::model::budget::Budgets;
use rusteda_studio::model::hierarchy::SplitMode;
use rusteda_studio::model::snapshot::{self, PHASES, Snapshot, Which};

/// Records the events of a run, and cancels `token` when `cancel_at` starts.
#[derive(Default)]
struct Record {
    events: Mutex<Vec<Event>>,
    cancel_at: Option<(&'static str, CancelToken)>,
}

impl Observer for Record {
    fn event(&self, e: &Event) {
        if let (Event::PhaseStarted { phase }, Some((at, token))) = (e, &self.cancel_at) {
            if phase == at {
                token.cancel();
            }
        }
        self.events.lock().unwrap().push(e.clone());
    }
}

/// The content of a snapshot, without its load time.
fn content(s: &Snapshot) -> String {
    let names: Vec<&str> = s
        .netlist
        .as_ref()
        .map(|n| {
            (0..n.instance_count() as u32)
                .map(|r| n.instance_name(r))
                .collect()
        })
        .unwrap_or_default();
    format!(
        "{:?} {:?} {:?} {:?} {:?} {:?} {:?} {:?}",
        s.rev,
        s.head,
        s.info,
        names,
        s.hierarchy.as_ref().map(|h| h.scopes().to_vec()),
        s.timing
            .as_ref()
            .map(|t| (t.rows().to_vec(), t.corners().to_vec())),
        s.rtl.as_ref().map(|r| r.modules.clone()),
        s.diagnostics
    )
}

/// The events without the measured seconds.
fn shape(events: &[Event]) -> Vec<String> {
    events
        .iter()
        .map(|e| match e {
            Event::PhaseFinished { phase, .. } => format!("finished {phase}"),
            other => format!("{other:?}"),
        })
        .collect()
}

// STUDIO-REQ-0008, JOB-REQ-0005: a load observed with a token that is never set gives the same
// snapshot as an unobserved load; its phases run in order on the calling thread, and its
// events depend only on the workspace.
#[test]
fn observed_load_equals_unobserved_load() {
    let root = scratch("loader-observed");
    let ws = design_workspace(&root, true);
    let b = Budgets::default();
    let plain = snapshot::load(&ws, Which::Head, &b, SplitMode::Flattened).unwrap();
    let mut shapes = Vec::new();
    for _ in 0..2 {
        let token = CancelToken::new();
        let rec = Record::default();
        let ctx = Ctx::new(Some(&token), Some(&rec));
        let observed =
            snapshot::load_with(&ws, Which::Head, &b, SplitMode::Flattened, &ctx).unwrap();
        assert_eq!(content(&observed), content(&plain));
        let events = rec.events.into_inner().unwrap();
        let started: Vec<&str> = events
            .iter()
            .filter_map(|e| match e {
                Event::PhaseStarted { phase } => Some(*phase),
                _ => None,
            })
            .collect();
        assert_eq!(started, PHASES);
        assert!(events.contains(&Event::Progress {
            phase: "hierarchy",
            done: 12,
            total: 12
        }));
        assert!(events.contains(&Event::Metric {
            name: "instances",
            value: 12.0
        }));
        shapes.push(shape(&events));
    }
    assert_eq!(shapes[0], shapes[1]);
}

// STUDIO-REQ-0008, JOB-REQ-0006: a cancelled load stops at its next checkpoint and returns
// `Cancelled` without a snapshot, whether cancelled before it starts or during a phase.
#[test]
fn cancelled_load_returns_nothing() {
    let root = scratch("loader-cancel");
    let ws = design_workspace(&root, false);
    let b = Budgets::default();
    let token = CancelToken::new();
    token.cancel();
    let r = snapshot::load_with(
        &ws,
        Which::Head,
        &b,
        SplitMode::Flattened,
        &Ctx::new(Some(&token), None),
    );
    assert!(matches!(r, Err(JobError::Cancelled)));
    for at in ["netlist", "hierarchy", "rtl"] {
        let token = CancelToken::new();
        let rec = Record {
            events: Mutex::new(Vec::new()),
            cancel_at: Some((at, token.clone())),
        };
        let ctx = Ctx::new(Some(&token), Some(&rec));
        let r = snapshot::load_with(&ws, Which::Head, &b, SplitMode::Flattened, &ctx);
        assert!(matches!(r, Err(JobError::Cancelled)), "{at}");
        let events = rec.events.into_inner().unwrap();
        let started = events
            .iter()
            .filter(|e| matches!(e, Event::PhaseStarted { .. }))
            .count();
        let at_index = PHASES.iter().position(|p| *p == at).unwrap();
        assert_eq!(
            started,
            at_index + 1,
            "{at}: the run went on to another phase"
        );
    }
    // A failure is not a cancellation.
    let r = snapshot::load_with(
        &ws,
        Which::Rev(rusteda_workspace::Rev(42)),
        &b,
        SplitMode::Flattened,
        &Ctx::NONE,
    );
    assert!(matches!(r, Err(JobError::Failed(_))));
}

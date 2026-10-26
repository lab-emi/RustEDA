// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The workbench window: the model, the live-update loop and the layout of the panels
//! (STUDIO-REQ-0160 to STUDIO-REQ-0166).

use std::sync::{Arc, Mutex};
use std::time::Instant;

use gpui::{
    AsyncApp, ClickEvent, Context, FocusHandle, InteractiveElement, IntoElement, KeyDownEvent,
    ParentElement, Render, StatefulInteractiveElement, Styled, Task, UniformListScrollHandle,
    WeakEntity, Window, div, prelude::FluentBuilder, px,
};

use super::cone_view::ConeViewState;
use super::{panels, theme};
use crate::model::budget::Budgets;
use crate::model::diag::{Severity, StudioDiag};
use crate::model::hierarchy::SplitMode;
use crate::model::snapshot::{self, Snapshot, Which};
use crate::model::studio::Studio;
use crate::model::watch::Watcher;
use rusteda_core::job::{CancelToken, Ctx, Event, JobError, Observer};
use rusteda_workspace::{Rev, Workspace};

/// A revision to load off the render thread.
struct Job {
    ws: Workspace,
    which: Which,
    budgets: Budgets,
    split: SplitMode,
}

/// The phase and progress of the load in flight, as the loader reports them (STUDIO-REQ-0008).
#[derive(Debug, Default)]
struct LoadProgress {
    phase: &'static str,
    done: u64,
    total: u64,
}

/// Receives the loader's events on the loading thread.
struct ProgressObserver(Arc<Mutex<LoadProgress>>);

impl Observer for ProgressObserver {
    fn event(&self, e: &Event) {
        let Ok(mut p) = self.0.lock() else {
            return;
        };
        match e {
            Event::PhaseStarted { phase } => {
                *p = LoadProgress {
                    phase,
                    done: 0,
                    total: 0,
                }
            }
            Event::Progress { done, total, .. } => {
                p.done = *done;
                p.total = *total;
            }
            _ => {}
        }
    }
}

impl Job {
    fn run(
        self,
        token: CancelToken,
        progress: Arc<Mutex<LoadProgress>>,
    ) -> Result<Snapshot, JobError<StudioDiag>> {
        let obs = ProgressObserver(progress);
        let ctx = Ctx::new(Some(&token), Some(&obs));
        snapshot::load_with(&self.ws, self.which, &self.budgets, self.split, &ctx)
    }
}

/// The workbench view (STUDIO-REQ-0163).
pub struct Workbench {
    pub(super) studio: Studio,
    watcher: Watcher,
    /// The cancel token of the load in flight.
    loading: Option<CancelToken>,
    /// A load asked for while another was in flight; it starts when that one ends.
    pending: Option<Which>,
    progress: Arc<Mutex<LoadProgress>>,
    pub(super) search_text: String,
    pub(super) search_focus: FocusHandle,
    pub(super) tree_scroll: UniformListScrollHandle,
    pub(super) paths_scroll: UniformListScrollHandle,
    pub(super) source_scroll: UniformListScrollHandle,
    pub(super) cone: ConeViewState,
    last_source: Option<(usize, u32)>,
    _poll: Task<()>,
}

impl std::fmt::Debug for Workbench {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Workbench")
            .field("rev", &self.studio.snapshot().rev)
            .field("loading", &self.loading.is_some())
            .finish_non_exhaustive()
    }
}

impl Workbench {
    /// The workbench on `studio`, with its polling loop (STUDIO-REQ-0160, STUDIO-REQ-0166).
    pub fn new(studio: Studio, window: &mut Window, cx: &mut Context<Self>) -> Workbench {
        let watcher = Watcher::new(&studio.options().workspace, studio.options().debounce);
        let poll = studio.options().poll;
        let task = cx.spawn(
            async move |this: WeakEntity<Workbench>, cx: &mut AsyncApp| {
                loop {
                    cx.background_executor().timer(poll).await;
                    if this.update(cx, |wb, cx| wb.tick(cx)).is_err() {
                        break;
                    }
                }
            },
        );
        let search_focus = cx.focus_handle();
        search_focus.focus(window);
        Workbench {
            studio,
            watcher,
            loading: None,
            pending: None,
            progress: Arc::new(Mutex::new(LoadProgress::default())),
            search_text: String::new(),
            search_focus,
            tree_scroll: UniformListScrollHandle::new(),
            paths_scroll: UniformListScrollHandle::new(),
            source_scroll: UniformListScrollHandle::new(),
            cone: ConeViewState::default(),
            last_source: None,
            _poll: task,
        }
    }

    /// The model, for tests and embedding.
    pub fn studio(&self) -> &Studio {
        &self.studio
    }

    /// Whether a revision is being loaded in the background.
    pub fn is_loading(&self) -> bool {
        self.loading.is_some()
    }

    fn job(&self, which: Which) -> Job {
        let o = self.studio.options();
        Job {
            ws: self.studio.workspace().clone(),
            which,
            budgets: o.budgets.clone(),
            split: o.split,
        }
    }

    /// One poll of the watcher on the render thread: cheap `stat` calls, and the small reads
    /// of `manifest.json` or `ui_state.json` when they changed; a new revision is loaded in the
    /// background (STUDIO-REQ-0166).
    fn tick(&mut self, cx: &mut Context<Self>) {
        let events = self.watcher.poll(Instant::now());
        if events.is_empty() {
            return;
        }
        let which = self.studio.handle_events(&events);
        if self.studio.options().verbose {
            eprintln!(
                "studio: watch {events:?} -> {}",
                which.map_or_else(|| "no reload".into(), |w| format!("load {w:?}"))
            );
        }
        if let Some(w) = which {
            self.start_load(w, cx);
        }
        cx.notify();
    }

    /// Loads `which` in the background; a load in flight is cancelled and `which` starts when
    /// it has stopped (STUDIO-REQ-0008, STUDIO-REQ-0166).
    pub(super) fn start_load(&mut self, which: Which, cx: &mut Context<Self>) {
        if let Some(token) = &self.loading {
            token.cancel();
            self.pending = Some(which);
            return;
        }
        let token = CancelToken::new();
        self.loading = Some(token.clone());
        let job = self.job(which);
        let progress = self.progress.clone();
        cx.spawn(
            async move |this: WeakEntity<Workbench>, cx: &mut AsyncApp| {
                let loaded = cx
                    .background_executor()
                    .spawn(async move { job.run(token, progress) })
                    .await;
                let _ = this.update(cx, |wb, cx| wb.finish(loaded, cx));
            },
        )
        .detach();
        cx.notify();
    }

    /// Installs a loaded revision (STUDIO-REQ-0161), or starts the load that superseded a
    /// cancelled one.
    fn finish(&mut self, loaded: Result<Snapshot, JobError<StudioDiag>>, cx: &mut Context<Self>) {
        self.loading = None;
        let verbose = self.studio.options().verbose;
        match loaded {
            Ok(s) => {
                if verbose {
                    eprintln!(
                        "studio: showing {} (HEAD {}), loaded in {} ms",
                        s.rev, s.head, s.load_ms
                    );
                }
                self.studio.install(s);
                self.cone.fit();
            }
            Err(JobError::Cancelled) => {
                if verbose {
                    eprintln!("studio: load cancelled by a newer one");
                }
            }
            Err(JobError::Failed(d)) => {
                if verbose {
                    eprintln!("studio: reload failed: {d}");
                }
                self.studio.reload_failed(d);
            }
        }
        if let Some(w) = self.pending.take() {
            self.start_load(w, cx);
        }
        cx.notify();
    }

    /// Pins revision `rev` or, with `None`, follows HEAD again (STUDIO-REQ-0162).
    pub(super) fn show_revision(&mut self, rev: Option<Rev>, cx: &mut Context<Self>) {
        let which = self.studio.set_revision(rev);
        self.start_load(which, cx);
    }

    /// After a selection: the file the workbench wrote is not news (STUDIO-REQ-0146).
    pub(super) fn after_select(&mut self, cx: &mut Context<Self>) {
        self.watcher.mark_ui_state_seen();
        if self.studio.options().verbose {
            if let Some(u) = self.studio.shared() {
                eprintln!(
                    "studio: shared {} of {}: paths [{}] instances [{}]",
                    u.selection_handle,
                    u.revision,
                    u.selected_paths.join(", "),
                    u.selected_instances.join(", ")
                );
            }
        }
        self.cone.fit();
        cx.notify();
    }

    /// Scrolls the source pane to the focused line when it changed.
    fn follow_source(&mut self) {
        let now = self.studio.source().map(|s| (s.file, s.focus_line));
        if now != self.last_source {
            self.last_source = now;
            if let Some((_, line)) = now {
                self.source_scroll.scroll_to_item(
                    line.saturating_sub(1) as usize,
                    gpui::ScrollStrategy::Center,
                );
            }
        }
    }

    /// Typing in the search field (STUDIO-REQ-0164).
    fn on_key(&mut self, ev: &KeyDownEvent, _window: &mut Window, cx: &mut Context<Self>) {
        let k = &ev.keystroke;
        if k.modifiers.control || k.modifiers.platform || k.modifiers.alt {
            return;
        }
        match k.key.as_str() {
            "backspace" => {
                self.search_text.pop();
            }
            "escape" => self.search_text.clear(),
            "enter" | "tab" => {}
            _ => {
                // The typed text, or a single printable key without one (synthetic keystrokes).
                let typed = k.key_char.clone().or_else(|| {
                    let mut chars = k.key.chars();
                    match (chars.next(), chars.next()) {
                        (Some(c), None) if !c.is_control() => Some(if k.modifiers.shift {
                            c.to_ascii_uppercase().to_string()
                        } else {
                            c.to_string()
                        }),
                        _ => None,
                    }
                });
                if let Some(c) = typed {
                    if c.chars().all(|c| !c.is_control()) && self.search_text.len() < 200 {
                        self.search_text.push_str(&c);
                    }
                }
            }
        }
        self.studio.search(&self.search_text);
        cx.notify();
    }

    fn top_bar(&mut self, cx: &mut Context<Self>) -> impl IntoElement {
        let snap = self.studio.snapshot();
        let info = &snap.info;
        let follows = self.studio.follows_head();
        let status = if follows {
            format!("{} - HEAD, following live", snap.rev)
        } else {
            format!("{} - pinned (HEAD {})", snap.rev, snap.head)
        };
        let timing_color = match info.timing.as_str() {
            "met" => theme::good(),
            "violated" => theme::bad(),
            _ => theme::muted(),
        };
        div()
            .flex()
            .flex_row()
            .items_center()
            .gap_4()
            .h(px(34.0))
            .px_3()
            .bg(theme::header())
            .border_b_1()
            .border_color(theme::border())
            .child(div().text_color(theme::accent()).child("RustEDA Studio"))
            .child(
                div().text_color(theme::muted()).child(
                    self.studio
                        .options()
                        .workspace
                        .file_name()
                        .map_or_else(String::new, |n| n.to_string_lossy().into_owned()),
                ),
            )
            .child(div().child(status))
            .child(
                div()
                    .text_color(theme::muted())
                    .child(format!("by {} ({})", info.command, info.tool)),
            )
            .child(
                div()
                    .text_color(timing_color)
                    .child(format!("timing {}", info.timing)),
            )
            .when(!follows, |d| {
                d.child(
                    div()
                        .id("follow-head")
                        .debug_selector(|| "follow-head".into())
                        .px_2()
                        .rounded_sm()
                        .bg(theme::selected_bg())
                        .cursor_pointer()
                        .child("follow HEAD")
                        .on_click(cx.listener(|this, _: &ClickEvent, _, cx| {
                            this.show_revision(None, cx);
                        })),
                )
            })
            .when(self.loading.is_some(), |d| {
                let p = self.progress.lock().map_or_else(
                    |_| String::from("loading"),
                    |p| match p.done.saturating_mul(100).checked_div(p.total) {
                        Some(percent) => format!("loading {} {percent}%", p.phase),
                        None => format!("loading {}", p.phase),
                    },
                );
                d.child(div().text_color(theme::warn()).child(p))
            })
    }

    fn status_bar(&self) -> impl IntoElement {
        let diags = self.studio.diagnostics();
        let count = |s: Severity| diags.iter().filter(|d| d.severity == s).count();
        let last = diags
            .iter()
            .rev()
            .find(|d| d.severity != Severity::Note)
            .or(diags.last())
            .map(|d| d.to_string())
            .unwrap_or_default();
        let shared = match self.studio.shared() {
            Some(u) => format!(
                "shared {} of {}: {}{}",
                u.selection_handle,
                u.revision,
                u.selected_paths.join(" "),
                if u.selected_instances.is_empty() {
                    String::new()
                } else {
                    format!(" {}", u.selected_instances.join(" "))
                }
            ),
            None => "no shared selection".to_string(),
        };
        div()
            .flex()
            .flex_row()
            .items_center()
            .gap_4()
            .h(px(26.0))
            .px_3()
            .bg(theme::header())
            .border_t_1()
            .border_color(theme::border())
            .text_color(theme::muted())
            .child(
                div()
                    .text_color(theme::bad())
                    .child(format!("{} errors", count(Severity::Error))),
            )
            .child(
                div()
                    .text_color(theme::warn())
                    .child(format!("{} warnings", count(Severity::Warning))),
            )
            .child(format!("{} notes", count(Severity::Note)))
            .child(div().flex_1().overflow_hidden().child(last))
            .child(div().text_color(theme::accent()).child(shared))
            .child(format!(
                "poll {} ms",
                self.studio.options().poll.as_millis()
            ))
    }
}

impl Render for Workbench {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.follow_source();
        let top = self.top_bar(cx).into_any_element();
        let status = self.status_bar().into_any_element();
        let hierarchy = panels::hierarchy(self, cx);
        let revisions = panels::revisions(self, cx);
        let summary = panels::summary(self, cx);
        let paths = panels::paths(self, cx);
        let cone = super::cone_view::cone_panel(self, cx);
        let detail = panels::detail(self, cx);
        let source = panels::source(self, cx);
        div()
            .id("workbench")
            .key_context("Workbench")
            .track_focus(&self.search_focus)
            .on_key_down(cx.listener(Self::on_key))
            .size_full()
            .flex()
            .flex_col()
            .bg(theme::bg())
            .text_color(theme::text())
            .text_size(px(12.5))
            .child(top)
            .child(
                div()
                    .flex_1()
                    .min_h_0()
                    .flex()
                    .flex_row()
                    .child(
                        div()
                            .w(px(300.0))
                            .flex()
                            .flex_col()
                            .border_r_1()
                            .border_color(theme::border())
                            .child(div().flex_1().min_h_0().child(hierarchy))
                            .child(div().h(px(190.0)).child(revisions)),
                    )
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .flex()
                            .flex_col()
                            .child(summary)
                            .child(div().h(px(230.0)).child(paths))
                            .child(div().flex_1().min_h_0().child(cone)),
                    )
                    .child(
                        div()
                            .w(px(430.0))
                            .flex()
                            .flex_col()
                            .border_l_1()
                            .border_color(theme::border())
                            .child(div().h(px(330.0)).child(detail))
                            .child(div().flex_1().min_h_0().child(source)),
                    ),
            )
            .child(status)
    }
}

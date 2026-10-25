// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! The graphical front end on the GPUI framework (`docs/specs/studio/gui.md`).
//!
//! The front end renders the headless model ([`crate::model`]) and forwards the user's actions
//! to it; it holds no EDA state of its own. One window shows the workbench ([`Workbench`]): the
//! hierarchy and revisions on the left, the corner summary, the path table and the logic cone
//! in the middle, the path detail and the RTL source on the right, and a status bar. A
//! background loop polls the workspace and loads new revisions off the render thread
//! (STUDIO-REQ-0160 to STUDIO-REQ-0166).

mod cone_view;
mod panels;
mod theme;
mod workbench;

use gpui::{
    App, AppContext, Application, Bounds, TitlebarOptions, WindowBounds, WindowOptions, px, size,
};

use crate::model::diag::{Code, StudioDiag};
use crate::model::studio::Studio;

pub use workbench::Workbench;

/// Opens the workbench window on `studio` and runs until the window closes (STUDIO-REQ-0224);
/// a window that cannot open is a `U0090` diagnostic.
pub fn run(studio: Studio) -> Result<(), StudioDiag> {
    // Without a display server the framework's Linux platform runs without windows and never
    // ends; say so before starting it.
    #[cfg(any(target_os = "linux", target_os = "freebsd"))]
    if std::env::var_os("DISPLAY").is_none() && std::env::var_os("WAYLAND_DISPLAY").is_none() {
        return Err(StudioDiag::new(
            Code::GuiUnavailable,
            "no display: neither DISPLAY nor WAYLAND_DISPLAY is set; use --print, or a virtual \
             display such as xvfb-run",
        ));
    }
    let slot = std::rc::Rc::new(std::cell::RefCell::new(None::<StudioDiag>));
    let inner = slot.clone();
    Application::new().run(move |cx: &mut App| {
        cx.on_window_closed(|cx| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
        let bounds = Bounds::centered(None, size(px(1440.0), px(900.0)), cx);
        let title = format!(
            "RustEDA Studio - {}",
            studio
                .options()
                .workspace
                .file_name()
                .map_or_else(|| ".".into(), |n| n.to_string_lossy().into_owned())
        );
        let opened = cx.open_window(
            WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                titlebar: Some(TitlebarOptions {
                    title: Some(title.into()),
                    ..Default::default()
                }),
                ..Default::default()
            },
            |window, cx| cx.new(|cx| Workbench::new(studio, window, cx)),
        );
        match opened {
            Ok(_) => cx.activate(true),
            Err(e) => {
                *inner.borrow_mut() = Some(StudioDiag::new(
                    Code::GuiUnavailable,
                    format!("the window cannot open: {e}"),
                ));
                // A quit before the event loop runs is lost: quit from inside the loop.
                cx.spawn(async move |cx| {
                    let _ = cx.update(|cx| cx.quit());
                })
                .detach();
            }
        }
    });
    let failure = slot.borrow_mut().take();
    match failure {
        Some(d) => Err(d),
        None => Ok(()),
    }
}

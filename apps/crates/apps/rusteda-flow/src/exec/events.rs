// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Machine-readable progress: one JSON object per line, written whole and at once.
//!
//! Every line has `schema`, `event`, `t` (seconds since the run started) and `unix_time`.
//! Events: `run_started`, `step_started`, `step_finished`, `step_failed`, `run_finished`.
//! A sink that fails to write is dropped with one message on standard error: progress
//! reporting never fails a run.

use serde_json::{Map, Value, json};
use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::Path,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

pub const SCHEMA: &str = "rusteda.flow-exec.event.v1";

#[derive(Debug)]
enum Sink {
    None,
    File(File),
    Stdout,
}

#[derive(Debug)]
pub struct Events {
    sink: Sink,
    started: Instant,
}

impl Events {
    pub fn none(started: Instant) -> Self {
        Self {
            sink: Sink::None,
            started,
        }
    }

    /// `-` selects standard output; any other value names a file that must not exist yet.
    pub fn open(target: &Path, started: Instant) -> std::io::Result<Self> {
        let sink = if target.as_os_str() == "-" {
            Sink::Stdout
        } else {
            Sink::File(
                OpenOptions::new()
                    .append(true)
                    .create_new(true)
                    .open(target)?,
            )
        };
        Ok(Self { sink, started })
    }

    pub fn to_stdout(&self) -> bool {
        matches!(self.sink, Sink::Stdout)
    }

    pub fn emit(&mut self, event: &str, fields: Value) {
        if matches!(self.sink, Sink::None) {
            return;
        }
        let mut line = Map::new();
        line.insert("schema".into(), json!(SCHEMA));
        line.insert("event".into(), json!(event));
        line.insert("t".into(), json!(self.started.elapsed().as_secs_f64()));
        line.insert(
            "unix_time".into(),
            json!(
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_or(0.0, |d| d.as_secs_f64())
            ),
        );
        if let Value::Object(fields) = fields {
            line.extend(fields);
        }
        let mut text = Value::Object(line).to_string();
        text.push('\n');
        let written = match &mut self.sink {
            Sink::File(file) => file.write_all(text.as_bytes()),
            Sink::Stdout => {
                let mut stdout = std::io::stdout().lock();
                stdout
                    .write_all(text.as_bytes())
                    .and_then(|()| stdout.flush())
            }
            Sink::None => Ok(()),
        };
        if let Err(error) = written {
            eprintln!("rusteda flow exec: progress events stopped: {error}");
            self.sink = Sink::None;
        }
    }
}

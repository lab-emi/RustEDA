// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! A JSON reader for the documents that a PDK suite reads: the reports of the tool it runs and
//! cargo's build messages.
//!
//! This file is shared by the RustEDA tool repositories: it, `fetch.rs`, `manifest.rs` and
//! `verification/pdks.toml` are identical in each of them, use only the standard library and
//! each other, and change in all of them at once.

/// A JSON value; object members keep their order.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Json {
    Null,
    Bool(bool),
    Num(f64),
    Str(String),
    List(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

/// Nesting deeper than this is rejected.
const MAX_DEPTH: usize = 128;

impl Json {
    /// Parses one JSON document (surrounding white space allowed).
    pub(crate) fn parse(text: &str) -> Result<Json, String> {
        let mut p = Parser {
            s: text.as_bytes(),
            i: 0,
        };
        let v = p.value(0)?;
        p.ws();
        if p.i == p.s.len() {
            Ok(v)
        } else {
            Err(p.err("trailing characters"))
        }
    }

    /// The member `key` of an object.
    pub(crate) fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(m) => m.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub(crate) fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    pub(crate) fn as_f64(&self) -> Option<f64> {
        match self {
            Json::Num(n) => Some(*n),
            _ => None,
        }
    }

    /// A number that is a non-negative integer.
    pub(crate) fn as_u64(&self) -> Option<u64> {
        let n = self.as_f64()?;
        (n >= 0.0 && n.fract() == 0.0 && n <= 9.0e15).then_some(n as u64)
    }

    pub(crate) fn as_obj(&self) -> Option<&[(String, Json)]> {
        match self {
            Json::Obj(m) => Some(m),
            _ => None,
        }
    }
}

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
}

impl Parser<'_> {
    fn err(&self, msg: &str) -> String {
        format!("JSON at byte {}: {msg}", self.i)
    }

    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }

    fn ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\r' | b'\n')) {
            self.i += 1;
        }
    }

    fn literal(&mut self, word: &str, v: Json) -> Result<Json, String> {
        if self
            .s
            .get(self.i..)
            .unwrap_or_default()
            .starts_with(word.as_bytes())
        {
            self.i += word.len();
            Ok(v)
        } else {
            Err(self.err("unexpected word"))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Json, String> {
        if depth > MAX_DEPTH {
            return Err(self.err("nested too deeply"));
        }
        self.ws();
        match self.peek() {
            Some(b'{') => self.object(depth),
            Some(b'[') => self.list(depth),
            Some(b'"') => self.string().map(Json::Str),
            Some(b't') => self.literal("true", Json::Bool(true)),
            Some(b'f') => self.literal("false", Json::Bool(false)),
            Some(b'n') => self.literal("null", Json::Null),
            Some(c) if c == b'-' || c.is_ascii_digit() => self.number(),
            _ => Err(self.err("expected a value")),
        }
    }

    fn object(&mut self, depth: usize) -> Result<Json, String> {
        self.i += 1;
        let mut members = Vec::new();
        self.ws();
        if self.peek() == Some(b'}') {
            self.i += 1;
            return Ok(Json::Obj(members));
        }
        loop {
            self.ws();
            if self.peek() != Some(b'"') {
                return Err(self.err("expected a member name"));
            }
            let k = self.string()?;
            self.ws();
            if self.peek() != Some(b':') {
                return Err(self.err("expected ':'"));
            }
            self.i += 1;
            let v = self.value(depth + 1)?;
            members.push((k, v));
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    return Ok(Json::Obj(members));
                }
                _ => return Err(self.err("expected ',' or '}'")),
            }
        }
    }

    fn list(&mut self, depth: usize) -> Result<Json, String> {
        self.i += 1;
        let mut items = Vec::new();
        self.ws();
        if self.peek() == Some(b']') {
            self.i += 1;
            return Ok(Json::List(items));
        }
        loop {
            items.push(self.value(depth + 1)?);
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    return Ok(Json::List(items));
                }
                _ => return Err(self.err("expected ',' or ']'")),
            }
        }
    }

    fn hex4(&mut self) -> Result<u32, String> {
        let h = self.s.get(self.i..self.i + 4).unwrap_or_default();
        let v = std::str::from_utf8(h)
            .ok()
            .filter(|h| h.len() == 4)
            .and_then(|h| u32::from_str_radix(h, 16).ok())
            .ok_or_else(|| self.err("bad \\u escape"))?;
        self.i += 4;
        Ok(v)
    }

    fn string(&mut self) -> Result<String, String> {
        self.i += 1;
        let mut out = Vec::new();
        loop {
            let c = self.peek().ok_or_else(|| self.err("unterminated string"))?;
            self.i += 1;
            match c {
                b'"' => break,
                b'\\' => {
                    let e = self.peek().ok_or_else(|| self.err("unterminated string"))?;
                    self.i += 1;
                    let ch = match e {
                        b'"' => '"',
                        b'\\' => '\\',
                        b'/' => '/',
                        b'b' => '\u{8}',
                        b'f' => '\u{c}',
                        b'n' => '\n',
                        b'r' => '\r',
                        b't' => '\t',
                        b'u' => {
                            let hi = self.hex4()?;
                            let code = if (0xd800..0xdc00).contains(&hi)
                                && self.s.get(self.i..self.i + 2) == Some(b"\\u")
                            {
                                self.i += 2;
                                let lo = self.hex4()?;
                                0x10000 + ((hi - 0xd800) << 10) + lo.wrapping_sub(0xdc00)
                            } else {
                                hi
                            };
                            char::from_u32(code).unwrap_or('\u{fffd}')
                        }
                        _ => return Err(self.err("bad escape")),
                    };
                    out.extend_from_slice(ch.encode_utf8(&mut [0; 4]).as_bytes());
                }
                c if c < 0x20 => return Err(self.err("control character in string")),
                c => out.push(c),
            }
        }
        String::from_utf8(out).map_err(|_| self.err("string is not UTF-8"))
    }

    fn number(&mut self) -> Result<Json, String> {
        let start = self.i;
        while self
            .peek()
            .is_some_and(|c| c.is_ascii_digit() || matches!(c, b'-' | b'+' | b'.' | b'e' | b'E'))
        {
            self.i += 1;
        }
        std::str::from_utf8(self.s.get(start..self.i).unwrap_or_default())
            .ok()
            .and_then(|t| t.parse::<f64>().ok())
            .filter(|n| n.is_finite())
            .map(Json::Num)
            .ok_or_else(|| self.err("bad number"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Reports are read member by member: numbers, strings with escapes, nested objects and lists;
    // malformed documents fail.
    #[test]
    fn reads_report_shapes() {
        let j = Json::parse(
            r#" {"format": "demo-report", "cells": {"inv_1": 5, "nand2_1": 4},
                "area": 670.6432000000001, "ok": true, "none": null, "s": "a\"b\\cé😀",
                "list": [1, -2.5e1, [], {}]} "#,
        )
        .unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(j.get("format").and_then(Json::as_str), Some("demo-report"));
        assert_eq!(
            j.get("area").and_then(Json::as_f64),
            Some(670.6432000000001)
        );
        let cells = j.get("cells").and_then(Json::as_obj).unwrap_or_default();
        assert_eq!(cells.len(), 2);
        assert_eq!(cells.first().and_then(|(_, v)| v.as_u64()), Some(5));
        assert_eq!(j.get("ok"), Some(&Json::Bool(true)));
        assert_eq!(j.get("none"), Some(&Json::Null));
        assert_eq!(
            j.get("s").and_then(Json::as_str),
            Some("a\"b\\c\u{e9}\u{1f600}")
        );
        let list = match j.get("list") {
            Some(Json::List(v)) => v.as_slice(),
            _ => &[],
        };
        assert_eq!(list.get(1).and_then(Json::as_f64), Some(-25.0));
        assert_eq!(list.get(1).and_then(Json::as_u64), None);
        assert!(j.get("missing").is_none());
        for bad in [
            "",
            "{",
            "{\"a\" 1}",
            "[1,]",
            "\"x",
            "{} x",
            "nul",
            "[1e999]",
        ] {
            assert!(Json::parse(bad).is_err(), "{bad}");
        }
        let deep = "[".repeat(MAX_DEPTH + 2);
        assert!(Json::parse(&deep).is_err());
    }
}

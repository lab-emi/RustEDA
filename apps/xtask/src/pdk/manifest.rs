// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! The library manifest `verification/pdks.toml` ([`load`]), read with a reader for the part of
//! TOML it uses ([`parse`]): comments, `[[table]]` headers and `key = value` lines whose value is a
//! string, an integer or an array of strings (an array may span lines). Anything else is an error
//! that names the file and line. [`Fields`] takes the typed entries of a table, also for other
//! files written in the same subset.
//!
//! [`assemble`] builds a library that upstream ships as a corner header and per-cell fragments.
//!
//! This file is shared by the RustEDA tool repositories: it, `fetch.rs`, `json.rs` and
//! `verification/pdks.toml` are identical in each of them, use only the standard library and
//! each other, and change in all of them at once.

use std::collections::BTreeMap;
use std::path::Path;

/// The library manifest, relative to the repository root.
pub(crate) const MANIFEST: &str = "verification/pdks.toml";

/// A value of the TOML subset.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Value {
    Str(String),
    Int(u64),
    List(Vec<String>),
}

/// One `[[name]]` table with its entries in file order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Table {
    pub(crate) name: String,
    /// 1-based line of the header.
    pub(crate) line: usize,
    pub(crate) entries: Vec<(String, Value)>,
}

/// A cursor over the text, with the current line for messages.
struct Reader<'a> {
    s: &'a [u8],
    i: usize,
    line: usize,
    path: &'a str,
}

impl Reader<'_> {
    fn err(&self, msg: &str) -> String {
        format!("{}:{}: {msg}", self.path, self.line)
    }

    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }

    fn rest(&self) -> &[u8] {
        self.s.get(self.i..).unwrap_or_default()
    }

    fn bump(&mut self) -> Option<u8> {
        let c = self.peek()?;
        self.i += 1;
        if c == b'\n' {
            self.line += 1;
        }
        Some(c)
    }

    fn blanks(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t')) {
            self.i += 1;
        }
    }

    fn comment(&mut self) {
        if self.peek() == Some(b'#') {
            while !matches!(self.peek(), None | Some(b'\n')) {
                self.i += 1;
            }
        }
    }

    /// Blanks and a comment, then the end of the line or of the text.
    fn end_of_line(&mut self) -> Result<(), String> {
        self.blanks();
        self.comment();
        if self.rest().starts_with(b"\r\n") {
            self.i += 1;
        }
        match self.peek() {
            None => Ok(()),
            Some(b'\n') => {
                self.bump();
                Ok(())
            }
            Some(c) => Err(self.err(&format!("unexpected '{}'", char::from(c)))),
        }
    }

    /// Blanks, comments and line ends (between the elements of an array).
    fn space(&mut self) {
        loop {
            match self.peek() {
                Some(b' ' | b'\t' | b'\r' | b'\n') => {
                    self.bump();
                }
                Some(b'#') => self.comment(),
                _ => return,
            }
        }
    }

    /// A bare key: ASCII letters, digits, `_` and `-`.
    fn key(&mut self) -> Result<String, String> {
        let start = self.i;
        while self
            .peek()
            .is_some_and(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
        {
            self.i += 1;
        }
        match self.s.get(start..self.i) {
            Some(k) if !k.is_empty() => Ok(String::from_utf8_lossy(k).into_owned()),
            _ => Err(self.err("expected a key")),
        }
    }

    /// A basic (`"…"`, with the escapes `\"`, `\\`, `\n`, `\t`, `\r` and `\uXXXX`) or literal
    /// (`'…'`) string on one line.
    fn string(&mut self) -> Result<String, String> {
        let quote = self.bump().ok_or_else(|| self.err("expected a string"))?;
        let mut out = Vec::new();
        loop {
            let c = match self.peek() {
                None | Some(b'\n') => return Err(self.err("unterminated string")),
                Some(c) => c,
            };
            self.i += 1;
            if c == quote {
                break;
            }
            if c != b'\\' || quote == b'\'' {
                out.push(c);
                continue;
            }
            match self.bump() {
                Some(b'"') => out.push(b'"'),
                Some(b'\\') => out.push(b'\\'),
                Some(b'n') => out.push(b'\n'),
                Some(b't') => out.push(b'\t'),
                Some(b'r') => out.push(b'\r'),
                Some(b'u') => {
                    let hex = self.s.get(self.i..self.i + 4).unwrap_or_default();
                    let ch = std::str::from_utf8(hex)
                        .ok()
                        .and_then(|h| u32::from_str_radix(h, 16).ok())
                        .and_then(char::from_u32)
                        .ok_or_else(|| self.err("bad \\u escape"))?;
                    self.i += 4;
                    out.extend_from_slice(ch.encode_utf8(&mut [0; 4]).as_bytes());
                }
                _ => return Err(self.err("unsupported escape in string")),
            }
        }
        String::from_utf8(out).map_err(|_| self.err("string is not UTF-8"))
    }

    /// A non-negative decimal integer; `_` may separate digits.
    fn integer(&mut self) -> Result<u64, String> {
        let start = self.i;
        while self.peek().is_some_and(|c| c.is_ascii_digit() || c == b'_') {
            self.i += 1;
        }
        let text = self.s.get(start..self.i).unwrap_or_default();
        let digits: String = text
            .iter()
            .filter(|c| c.is_ascii_digit())
            .map(|&c| char::from(c))
            .collect();
        let separated = text.first() != Some(&b'_')
            && text.last() != Some(&b'_')
            && !text.windows(2).any(|w| w == b"__");
        match digits.parse::<u64>() {
            Ok(v) if separated => Ok(v),
            _ => Err(self.err("expected a non-negative integer")),
        }
    }

    /// An array of strings; a trailing comma is allowed.
    fn array(&mut self) -> Result<Vec<String>, String> {
        self.bump();
        let mut items = Vec::new();
        loop {
            self.space();
            match self.peek() {
                Some(b']') => {
                    self.bump();
                    return Ok(items);
                }
                Some(b'"' | b'\'') => items.push(self.string()?),
                _ => return Err(self.err("arrays may hold only strings")),
            }
            self.space();
            match self.peek() {
                Some(b',') => {
                    self.bump();
                }
                Some(b']') => {}
                _ => return Err(self.err("expected ',' or ']' in array")),
            }
        }
    }

    fn value(&mut self) -> Result<Value, String> {
        match self.peek() {
            Some(b'"' | b'\'') => self.string().map(Value::Str),
            Some(b'[') => self.array().map(Value::List),
            Some(c) if c.is_ascii_digit() => self.integer().map(Value::Int),
            _ => Err(self.err("expected a string, an integer or an array of strings")),
        }
    }
}

/// Reads the tables of `text` (`path` names the file in errors).
pub(crate) fn parse(path: &str, text: &str) -> Result<Vec<Table>, String> {
    let mut r = Reader {
        s: text.as_bytes(),
        i: 0,
        line: 1,
        path,
    };
    let mut tables: Vec<Table> = Vec::new();
    loop {
        r.blanks();
        match r.peek() {
            None => return Ok(tables),
            Some(b'\r' | b'\n' | b'#') => r.end_of_line()?,
            Some(b'[') => {
                let line = r.line;
                if !r.rest().starts_with(b"[[") {
                    return Err(r.err("only [[table]] headers are supported"));
                }
                r.i += 2;
                r.blanks();
                let name = r.key()?;
                r.blanks();
                if !r.rest().starts_with(b"]]") {
                    return Err(r.err("expected ']]'"));
                }
                r.i += 2;
                r.end_of_line()?;
                tables.push(Table {
                    name,
                    line,
                    entries: Vec::new(),
                });
            }
            Some(_) => {
                let line = r.line;
                let key = r.key()?;
                r.blanks();
                if r.bump() != Some(b'=') {
                    return Err(r.err(&format!("expected '=' after '{key}'")));
                }
                r.blanks();
                let value = r.value()?;
                r.end_of_line()?;
                let Some(t) = tables.last_mut() else {
                    return Err(format!("{path}:{line}: '{key}' outside a [[table]]"));
                };
                if t.entries.iter().any(|(k, _)| *k == key) {
                    return Err(format!("{path}:{line}: duplicate key '{key}'"));
                }
                t.entries.push((key, value));
            }
        }
    }
}

/// The entries of one table, taken by key; keys left over are unknown.
pub(crate) struct Fields<'a> {
    path: &'a str,
    table: &'a Table,
    left: BTreeMap<&'a str, &'a Value>,
}

impl<'a> Fields<'a> {
    /// The entries of `table` of the file `path`.
    pub(crate) fn new(path: &'a str, table: &'a Table) -> Fields<'a> {
        let left = table.entries.iter().map(|(k, v)| (k.as_str(), v)).collect();
        Fields { path, table, left }
    }

    /// `msg` prefixed with the file, the line and the name of the table.
    pub(crate) fn err(&self, msg: &str) -> String {
        format!(
            "{}:{}: [[{}]]: {msg}",
            self.path, self.table.line, self.table.name
        )
    }

    pub(crate) fn opt_str(&mut self, key: &str) -> Result<Option<String>, String> {
        match self.left.remove(key) {
            None => Ok(None),
            Some(Value::Str(s)) => Ok(Some(s.clone())),
            Some(_) => Err(self.err(&format!("'{key}' must be a string"))),
        }
    }

    pub(crate) fn str(&mut self, key: &str) -> Result<String, String> {
        self.opt_str(key)?
            .ok_or_else(|| self.err(&format!("missing '{key}'")))
    }

    pub(crate) fn opt_list(&mut self, key: &str) -> Result<Option<Vec<String>>, String> {
        match self.left.remove(key) {
            None => Ok(None),
            Some(Value::List(v)) => Ok(Some(v.clone())),
            Some(_) => Err(self.err(&format!("'{key}' must be an array of strings"))),
        }
    }

    pub(crate) fn int(&mut self, key: &str) -> Result<u64, String> {
        match self.left.remove(key) {
            None => Err(self.err(&format!("missing '{key}'"))),
            Some(Value::Int(v)) => Ok(*v),
            Some(_) => Err(self.err(&format!("'{key}' must be an integer"))),
        }
    }

    /// Fails on keys that no getter took.
    pub(crate) fn done(self) -> Result<(), String> {
        match self.left.keys().next() {
            Some(k) => Err(self.err(&format!("unknown key '{k}'"))),
            None => Ok(()),
        }
    }
}

/// Where a library file comes from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Source {
    /// Downloaded whole from `url`.
    File { url: String },
    /// Assembled ([`assemble`]) from `header` and `fragments`, paths below `base_url`.
    Assembled {
        base_url: String,
        header: String,
        fragments: Vec<String>,
    },
}

/// One `[[library]]` of the manifest.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Library {
    /// Short name: a column of the QoR table and the directory of the run outputs.
    pub(crate) name: String,
    /// Vendor library name, for example `sky130_fd_sc_hd`.
    pub(crate) library: String,
    /// Process corner, for example `tt_025C_1v80`.
    pub(crate) corner: String,
    /// Vendor repository and the pinned commit every URL points into.
    pub(crate) repo: String,
    pub(crate) commit: String,
    /// SPDX license expression of the library.
    pub(crate) license: String,
    pub(crate) source: Source,
    /// SHA-256 (lower-case hexadecimal) and size in bytes of the library file.
    pub(crate) sha256: String,
    pub(crate) size: u64,
}

impl Library {
    /// The library file's name in the library directory: `<library>__<corner>.lib`.
    pub(crate) fn file_name(&self) -> String {
        format!("{}__{}.lib", self.library, self.corner)
    }
}

fn hex(s: &str, len: usize) -> bool {
    s.len() == len
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// A file or directory name made of ASCII letters, digits, `_`, `-` and `.`, not starting with `.`.
pub(crate) fn plain_name(s: &str) -> bool {
    !s.is_empty()
        && !s.starts_with('.')
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.'))
}

/// A relative `/`-separated path of plain names.
pub(crate) fn plain_path(s: &str) -> bool {
    s.split('/').all(plain_name)
}

fn https(s: &str) -> bool {
    s.starts_with("https://") && !s.bytes().any(|c| c.is_ascii_whitespace() || c == b'"')
}

fn library(path: &str, t: &Table) -> Result<Library, String> {
    let mut f = Fields::new(path, t);
    let name = f.str("name")?;
    let library = f.str("library")?;
    let corner = f.str("corner")?;
    let repo = f.str("repo")?;
    let commit = f.str("commit")?;
    let license = f.str("license")?;
    let sha256 = f.str("sha256")?;
    let size = f.int("size")?;
    let url = f.opt_str("url")?;
    let base_url = f.opt_str("base_url")?;
    let header = f.opt_str("header")?;
    let fragments = f.opt_list("fragments")?;
    for (key, value) in [("name", &name), ("library", &library), ("corner", &corner)] {
        if !plain_name(value) {
            return Err(f.err(&format!("'{key}' must be a plain name: '{value}'")));
        }
    }
    if !hex(&commit, 40) {
        return Err(f.err("'commit' must be 40 lower-case hexadecimal digits"));
    }
    if !hex(&sha256, 64) {
        return Err(f.err("'sha256' must be 64 lower-case hexadecimal digits"));
    }
    if size == 0 {
        return Err(f.err("'size' must be positive"));
    }
    if !https(&repo) || license.trim().is_empty() {
        return Err(f.err("'repo' must be an https URL and 'license' must not be empty"));
    }
    let pinned = |u: &str| https(u) && u.contains(&commit);
    let source = match (url, base_url, header, fragments) {
        (Some(url), None, None, None) => {
            if !pinned(&url) {
                return Err(f.err("'url' must be an https URL that contains the commit"));
            }
            Source::File { url }
        }
        (None, Some(base_url), Some(header), Some(fragments)) => {
            if !pinned(&base_url) || !base_url.ends_with('/') {
                return Err(f.err(
                    "'base_url' must be an https URL that contains the commit and ends with '/'",
                ));
            }
            if fragments.is_empty() {
                return Err(f.err("'fragments' must not be empty"));
            }
            if let Some(p) = std::iter::once(&header)
                .chain(&fragments)
                .find(|p| !plain_path(p))
            {
                return Err(f.err(&format!("'{p}' is not a relative path of plain names")));
            }
            let mut seen = std::collections::BTreeSet::new();
            if let Some(p) = fragments.iter().find(|p| !seen.insert(p.as_str())) {
                return Err(f.err(&format!("fragment '{p}' is listed twice")));
            }
            Source::Assembled {
                base_url,
                header,
                fragments,
            }
        }
        _ => {
            return Err(f.err("give either 'url', or 'base_url' with 'header' and 'fragments'"));
        }
    };
    f.done()?;
    Ok(Library {
        name,
        library,
        corner,
        repo,
        commit,
        license,
        source,
        sha256,
        size,
    })
}

/// The libraries of the manifest text, in file order; names and file names are unique.
pub(crate) fn libraries(path: &str, text: &str) -> Result<Vec<Library>, String> {
    let mut out: Vec<Library> = Vec::new();
    for t in parse(path, text)? {
        if t.name != "library" {
            return Err(format!(
                "{path}:{}: unknown table [[{}]] (expected [[library]])",
                t.line, t.name
            ));
        }
        let l = library(path, &t)?;
        if let Some(other) = out
            .iter()
            .find(|o| o.name == l.name || o.file_name() == l.file_name())
        {
            return Err(format!(
                "{path}:{}: library '{}' repeats the name or file of '{}'",
                t.line, l.name, other.name
            ));
        }
        out.push(l);
    }
    if out.is_empty() {
        return Err(format!("{path}: no [[library]]"));
    }
    Ok(out)
}

/// The libraries of the manifest of the repository at `root`.
pub(crate) fn load(root: &Path) -> Result<Vec<Library>, String> {
    let path = root.join(MANIFEST);
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    libraries(MANIFEST, &text)
}

fn blank(line: &[u8]) -> bool {
    line.iter()
        .all(|c| matches!(c, b' ' | b'\t' | b'\r' | b'\x0b' | b'\x0c'))
}

/// The library assembled from a corner header and its cell fragments: the header without its last
/// line `}` (and the blank lines after it), each remaining header line ending with a newline;
/// then, for each fragment in the given order, a newline and the fragment; then a newline, `}`
/// and a newline. Fails when the header does not end with a line `}`.
pub(crate) fn assemble(header: &[u8], fragments: &[Vec<u8>]) -> Result<Vec<u8>, String> {
    let mut lines: Vec<&[u8]> = header.split(|&c| c == b'\n').collect();
    if header.ends_with(b"\n") {
        lines.pop();
    }
    while lines.last().is_some_and(|l| blank(l)) {
        lines.pop();
    }
    match lines.last() {
        Some(l) if l.first() == Some(&b'}') && blank(l.get(1..).unwrap_or_default()) => {
            lines.pop();
        }
        _ => return Err("the header does not end with a line '}'".into()),
    }
    let size = header.len() + fragments.iter().map(|f| f.len() + 1).sum::<usize>() + 3;
    let mut out = Vec::with_capacity(size);
    for l in lines {
        out.extend_from_slice(l);
        out.push(b'\n');
    }
    for f in fragments {
        out.push(b'\n');
        out.extend_from_slice(f);
    }
    out.extend_from_slice(b"\n}\n");
    Ok(out)
}

#[cfg(test)]
mod tests {
    use std::cmp::Ordering;

    use super::*;

    fn strings(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    // The TOML subset: tables, strings with escapes, integers with separators, arrays over several
    // lines with comments and a trailing comma.
    #[test]
    fn reads_toml_subset() {
        let text = "# head\n\n[[library]]\nname = \"a\\\"b\" # note\nsize = 12_841_637\n\
                    list = [\n  'x', # first\n  \"y\",\n]\n[[index]]\r\nempty = []\r\n";
        let t = parse("m.toml", text).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(t.len(), 2);
        let first = t
            .first()
            .map(|t| (t.name.as_str(), t.line, t.entries.clone()));
        assert_eq!(
            first,
            Some((
                "library",
                3,
                vec![
                    ("name".to_string(), Value::Str("a\"b".into())),
                    ("size".to_string(), Value::Int(12_841_637)),
                    ("list".to_string(), Value::List(strings(&["x", "y"]))),
                ]
            ))
        );
        assert_eq!(
            t.get(1).map(|t| t.entries.clone()),
            Some(vec![("empty".to_string(), Value::List(Vec::new()))])
        );
    }

    // Unsupported or malformed lines fail with the file and line.
    #[test]
    fn rejects_toml_outside_the_subset() {
        for (text, line, msg) in [
            ("[[a]]\nk \"v\"\n", 2, "expected '='"),
            ("[[a]]\nk = \"v\n", 2, "unterminated string"),
            ("k = 1\n", 1, "outside a [[table]]"),
            ("[[a]]\nk = 1\nk = 2\n", 3, "duplicate key"),
            ("[a]\n", 1, "only [[table]] headers"),
            ("[[a]]\nk = true\n", 2, "expected a string"),
            ("[[a]]\nk = [\"x\", 1]\n", 2, "only strings"),
            ("[[a]]\nk = [\"x\"\n\"y\"]\n", 3, "expected ',' or ']'"),
            ("[[a]]\nk = 1_\n", 2, "non-negative integer"),
            ("[[a]]\nk = \"v\" x\n", 2, "unexpected 'x'"),
            ("[[a]]\nk = \"\\q\"\n", 2, "unsupported escape"),
        ] {
            match parse("m.toml", text) {
                Err(e) => assert!(
                    e.starts_with(&format!("m.toml:{line}: ")) && e.contains(msg),
                    "{text:?}: {e}"
                ),
                Ok(t) => panic!("{text:?} parsed: {t:?}"),
            }
        }
    }

    // Typed entries: present, missing, of another type, and keys that no getter took.
    #[test]
    fn typed_fields() {
        let tables = parse("f.toml", "[[t]]\na = \"x\"\nb = 3\nc = [\"y\"]\nd = 1\n")
            .unwrap_or_else(|e| panic!("{e}"));
        let t = tables.first().unwrap_or_else(|| panic!("no table"));
        let mut f = Fields::new("f.toml", t);
        assert_eq!(f.str("a"), Ok("x".into()));
        assert_eq!(f.int("b"), Ok(3));
        assert_eq!(f.opt_list("c"), Ok(Some(strings(&["y"]))));
        assert_eq!(f.opt_str("e"), Ok(None));
        assert_eq!(f.str("e"), Err("f.toml:1: [[t]]: missing 'e'".into()));
        assert_eq!(f.done(), Err("f.toml:1: [[t]]: unknown key 'd'".into()));
        let mut g = Fields::new("f.toml", t);
        assert!(
            g.opt_list("a")
                .is_err_and(|e| e.contains("must be an array"))
        );
        assert!(g.int("c").is_err_and(|e| e.contains("must be an integer")));
        assert!(g.str("d").is_err_and(|e| e.contains("must be a string")));
    }

    const COMMIT: &str = "0123456789abcdef0123456789abcdef01234567";

    fn entry(extra: &str) -> String {
        format!(
            "[[library]]\nname = \"x_tt\"\nlibrary = \"x_lib\"\ncorner = \"tt\"\n\
             repo = \"https://example.org/x\"\ncommit = \"{COMMIT}\"\nlicense = \"Apache-2.0\"\n\
             sha256 = \"{}\"\nsize = 10\n{extra}",
            "ab".repeat(32)
        )
    }

    // A library is downloaded whole or assembled from a header and fragments; the file name
    // follows from the library and the corner.
    #[test]
    fn reads_libraries() {
        let text = format!(
            "{}\n{}",
            entry(&format!(
                "url = \"https://example.org/x/{COMMIT}/x_lib__tt.lib\"\n"
            )),
            entry(&format!(
                "base_url = \"https://example.org/y/{COMMIT}/\"\nheader = \"liberty/h.lib\"\n\
                 fragments = [\"cells/a/a_1.lib\", \"cells/b/b_1.lib\"]\n"
            ))
            .replace("x_tt", "y_tt")
            .replace("x_lib", "y_lib")
        );
        let libs = libraries("p.toml", &text).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(libs.len(), 2);
        let first = libs.first().unwrap_or_else(|| panic!("no library"));
        assert_eq!(first.file_name(), "x_lib__tt.lib");
        assert_eq!(first.size, 10);
        assert!(matches!(&first.source, Source::File { url } if url.ends_with("x_lib__tt.lib")));
        assert_eq!(
            libs.get(1).map(|l| l.source.clone()),
            Some(Source::Assembled {
                base_url: format!("https://example.org/y/{COMMIT}/"),
                header: "liberty/h.lib".into(),
                fragments: strings(&["cells/a/a_1.lib", "cells/b/b_1.lib"]),
            })
        );
    }

    // Every key is validated: presence (a library without its SHA-256 or size is never trusted on
    // first use), type, digests, pinning of URLs to the commit, the choice of one source, plain
    // relative paths and unique names.
    #[test]
    fn rejects_bad_libraries() {
        let url = format!("url = \"https://example.org/x/{COMMIT}/x.lib\"\n");
        let parts =
            format!("base_url = \"https://example.org/y/{COMMIT}/\"\nheader = \"liberty/h.lib\"\n");
        let cases = [
            (entry("").replace("size = 10\n", &url), "missing 'size'"),
            (
                entry(&url).replace(&format!("sha256 = \"{}\"\n", "ab".repeat(32)), ""),
                "missing 'sha256'",
            ),
            (
                entry(&url).replace(&"ab".repeat(32), &"AB".repeat(32)),
                "'sha256' must be 64",
            ),
            (entry(&url).replace(COMMIT, "abc"), "'commit' must be 40"),
            (
                entry(&url).replace("size = 10", "size = 0"),
                "'size' must be positive",
            ),
            (
                entry("url = \"https://example.org/x/main/x.lib\"\n"),
                "contains the commit",
            ),
            (
                entry(&format!("{url}{parts}fragments = [\"a.lib\"]\n")),
                "give either 'url'",
            ),
            (entry(&parts), "give either 'url'"),
            (
                entry(&format!("{parts}fragments = []\n")),
                "must not be empty",
            ),
            (
                entry(&format!("{parts}fragments = [\"../a.lib\"]\n")),
                "not a relative path",
            ),
            (
                entry(&format!("{parts}fragments = [\"/a.lib\"]\n")),
                "not a relative path",
            ),
            (
                entry(&format!("{parts}fragments = [\"a.lib\", \"a.lib\"]\n")),
                "listed twice",
            ),
            (
                entry(&format!("{url}mirror = \"x\"\n")),
                "unknown key 'mirror'",
            ),
            (
                entry(&url).replace("size = 10", "size = \"10\""),
                "'size' must be an integer",
            ),
            (entry(&url).replace("\"x_tt\"", "\"a/b\""), "plain name"),
            (
                format!("{}\n{}", entry(&url), entry(&url)),
                "repeats the name",
            ),
            (
                entry(&url).replace("[[library]]", "[[lib]]"),
                "unknown table [[lib]]",
            ),
            ("# nothing\n".to_string(), "no [[library]]"),
        ];
        for (text, msg) in cases {
            match libraries("p.toml", &text) {
                Err(e) => assert!(e.contains(msg), "{msg}: {e}"),
                Ok(l) => panic!("{msg}: accepted {l:?}"),
            }
        }
    }

    // The header loses its last line `}` and the blank lines after it; each fragment follows a
    // newline in the given order (not sorted); a newline, `}` and a newline close the library.
    // Line bytes, carriage returns included, are kept.
    #[test]
    fn assembly_order() {
        let header = b"library (x) {\r\n  a : 1;\r\n}\r\n\r\n  \n";
        let frags = vec![b"  cell (b) {}\n".to_vec(), b"  cell (a) {}".to_vec()];
        let got = assemble(header, &frags).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(
            String::from_utf8_lossy(&got),
            "library (x) {\r\n  a : 1;\r\n\n  cell (b) {}\n\n  cell (a) {}\n}\n"
        );
        let no_newline = assemble(b"library (x) {\n}", &[]).unwrap_or_default();
        assert_eq!(no_newline, b"library (x) {\n\n}\n");
        assert!(assemble(b"library (x) {\n  a : 1;\n", &frags).is_err());
        assert!(assemble(b"", &frags).is_err());
    }

    /// GNU version order of file names (`sort -V`): digit runs compare as numbers, letters sort
    /// before other characters, the end of a name before anything but `~`, and file suffixes such
    /// as `.lib` count only between names that are equal without them.
    fn version_cmp(a: &str, b: &str) -> Ordering {
        fn order(c: Option<&u8>) -> i32 {
            match c {
                None => -1,
                Some(c) if c.is_ascii_digit() => 0,
                Some(c) if c.is_ascii_alphabetic() => i32::from(*c),
                Some(b'~') => -2,
                Some(c) => i32::from(*c) + 256,
            }
        }
        fn digit(s: &[u8], i: usize) -> bool {
            s.get(i).is_some_and(u8::is_ascii_digit)
        }
        fn compare(a: &[u8], b: &[u8]) -> Ordering {
            let (mut i, mut j) = (0, 0);
            while i < a.len() || j < b.len() {
                while (i < a.len() && !digit(a, i)) || (j < b.len() && !digit(b, j)) {
                    let (x, y) = (order(a.get(i)), order(b.get(j)));
                    if x != y {
                        return x.cmp(&y);
                    }
                    i += 1;
                    j += 1;
                }
                while a.get(i) == Some(&b'0') {
                    i += 1;
                }
                while b.get(j) == Some(&b'0') {
                    j += 1;
                }
                let mut first = Ordering::Equal;
                while digit(a, i) && digit(b, j) {
                    if first == Ordering::Equal {
                        first = a.get(i).cmp(&b.get(j));
                    }
                    i += 1;
                    j += 1;
                }
                if digit(a, i) {
                    return Ordering::Greater;
                }
                if digit(b, j) {
                    return Ordering::Less;
                }
                if first != Ordering::Equal {
                    return first;
                }
            }
            Ordering::Equal
        }
        // The length without the longest suffix matching `(\.[A-Za-z~][A-Za-z0-9~]*)*$`.
        fn stem(s: &[u8]) -> usize {
            let (mut i, mut stem) = (0, 0);
            while i < s.len() {
                i += 1;
                stem = i;
                while s.get(i) == Some(&b'.')
                    && s.get(i + 1)
                        .is_some_and(|c| c.is_ascii_alphabetic() || *c == b'~')
                {
                    i += 2;
                    while s
                        .get(i)
                        .is_some_and(|c| c.is_ascii_alphanumeric() || *c == b'~')
                    {
                        i += 1;
                    }
                }
            }
            stem
        }
        let (a, b) = (a.as_bytes(), b.as_bytes());
        let (sa, sb) = (stem(a), stem(b));
        let r = compare(a.get(..sa).unwrap_or(a), b.get(..sb).unwrap_or(b));
        if r != Ordering::Equal || (sa == a.len() && sb == b.len()) {
            r
        } else {
            compare(a, b)
        }
    }

    // The version order agrees with `sort -V` on the cases the fragment lists contain.
    #[test]
    fn version_order() {
        let sorted = [
            "x__aoi21_1__tt.lib",
            "x__aoi22_4__tt.lib",
            "x__aoi211_1__tt.lib",
            "x__bufz_16__tt.lib",
            "x__buf_1__tt.lib",
            "x__buf_8__tt.lib",
            "x__buf_12__tt.lib",
            "x__endcap__tt.lib",
            "x__fillcap_64__tt.lib",
            "x__filltie__tt.lib",
            "x__fill_1__tt.lib",
            "x__fill_64__tt.lib",
        ];
        for w in sorted.windows(2) {
            if let [a, b] = w {
                assert_eq!(version_cmp(a, b), Ordering::Less, "{a} < {b}");
                assert_eq!(version_cmp(b, a), Ordering::Greater, "{b} > {a}");
            }
        }
        assert_eq!(version_cmp("a.lib", "a.lib"), Ordering::Equal);
        assert_eq!(version_cmp("a.lib", "a1.lib"), Ordering::Less);
        assert_eq!(version_cmp("a01", "a1"), Ordering::Equal);
    }

    // The tracked manifest covers sky130_fd_sc_hd at three corners as whole files and gf180mcu
    // 7-track and 9-track at three corners each as assembled files. Every URL points into the
    // library's repository at its commit and names the library's file; every fragment lies in its
    // cell's directory, the corners of one library list the same cells, and each list is in
    // version order of the file names, so the assembly can be repeated from a clone of the
    // vendor repository.
    #[test]
    fn tracked_manifest() {
        let Some(root) = Path::new(env!("CARGO_MANIFEST_DIR")).parent() else {
            return;
        };
        if !root.join(MANIFEST).is_file() {
            return;
        }
        let libs = load(root).unwrap_or_else(|e| panic!("{e}"));
        let names: Vec<&str> = libs.iter().map(|l| l.name.as_str()).collect();
        assert_eq!(
            names,
            [
                "sky130hd_tt",
                "sky130hd_ss",
                "sky130hd_ff",
                "gf180mcu7t_tt",
                "gf180mcu7t_ss",
                "gf180mcu7t_ff",
                "gf180mcu9t_tt",
                "gf180mcu9t_ss",
                "gf180mcu9t_ff",
            ]
        );
        let mut cells: BTreeMap<&str, Vec<String>> = BTreeMap::new();
        for l in &libs {
            assert_eq!(l.license, "Apache-2.0");
            assert!(
                l.repo.starts_with("https://github.com/fossi-foundation/"),
                "{}",
                l.repo
            );
            let repo = l.repo.trim_start_matches("https://github.com/");
            let raw = format!("https://raw.githubusercontent.com/{repo}/{}/", l.commit);
            match &l.source {
                Source::File { url } => {
                    assert!(url.starts_with(&raw), "{url}");
                    assert!(url.ends_with(&format!("/{}", l.file_name())), "{url}");
                }
                Source::Assembled {
                    base_url,
                    header,
                    fragments,
                } => {
                    assert_eq!(*base_url, raw);
                    assert_eq!(*header, format!("liberty/{}", l.file_name()));
                    let suffix = format!("__{}.lib", l.corner);
                    let base = |p: &str| p.rsplit('/').next().unwrap_or_default().to_string();
                    let mut variants = Vec::new();
                    for p in fragments {
                        let parts: Vec<&str> = p.split('/').collect();
                        let variant = parts
                            .get(2)
                            .and_then(|f| f.strip_prefix(&format!("{}__", l.library)))
                            .and_then(|f| f.strip_suffix(&suffix))
                            .unwrap_or_else(|| panic!("{p}"));
                        let cell = variant
                            .rsplit_once('_')
                            .filter(|(_, d)| d.bytes().all(|c| c.is_ascii_digit()))
                            .map_or(variant, |(c, _)| c);
                        assert_eq!(
                            (parts.len(), parts.first(), parts.get(1)),
                            (3, Some(&"cells"), Some(&cell)),
                            "{p}"
                        );
                        variants.push(variant.to_string());
                    }
                    for w in fragments.windows(2) {
                        if let [a, b] = w {
                            assert_eq!(
                                version_cmp(&base(a), &base(b)),
                                Ordering::Less,
                                "{a} before {b}"
                            );
                        }
                    }
                    let first = cells
                        .entry(l.library.as_str())
                        .or_insert_with(|| variants.clone());
                    assert_eq!(*first, variants, "{}: another cell list", l.name);
                }
            }
        }
    }
}

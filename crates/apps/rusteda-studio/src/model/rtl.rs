// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! RTL cross-probing (`docs/specs/studio/rtl.md`).
//!
//! A revision may carry an `rtl` snapshot: the RTL files a synthesis started from and their
//! `rtl.json` (top module, defines, parameters, files). The netlist made from it is flat, but
//! the names that the synthesis keeps (state nets such as `u_fifo.mem[2][7]`, ports) carry RTL
//! parts: the instance path of the RTL hierarchy and the variable. Cross-probing maps a netlist
//! object to source lines by matching these parts against a light index of the snapshot
//! ([`RtlIndex`]): module definitions, instantiations of modules of the snapshot, generate
//! block labels, and identifier occurrences. It is name matching, not elaboration: every result
//! is flagged approximate (STUDIO-REQ-0120 to STUDIO-REQ-0126).
//!
//! The index is bounded by the size of each file and of all files, and a probe by its number
//! of hits.

use std::collections::{BTreeSet, HashMap};

use super::diag::{Code, StudioDiag};
use super::hierarchy::{SplitMode, level_text, split};

/// What a source line is for a matched identifier (STUDIO-REQ-0123).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum HitKind {
    /// The identifier's declaration.
    Declaration,
    /// An assignment to it.
    Assignment,
    /// The instantiation of a module along the path.
    Instantiation,
    /// A generate block on the path.
    Label,
    /// Any other use.
    Reference,
}

impl HitKind {
    /// `declaration`, `assignment`, `instantiation`, `label` or `reference`.
    pub fn as_str(self) -> &'static str {
        match self {
            HitKind::Declaration => "declaration",
            HitKind::Assignment => "assignment",
            HitKind::Instantiation => "instantiation",
            HitKind::Label => "label",
            HitKind::Reference => "reference",
        }
    }

    fn score(self) -> u32 {
        match self {
            HitKind::Declaration => 30,
            HitKind::Assignment => 20,
            HitKind::Instantiation => 15,
            HitKind::Label => 12,
            HitKind::Reference => 10,
        }
    }
}

/// A token of the light lexer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tok {
    Ident,
    Punct(u8),
    Other,
}

#[derive(Debug, Clone, Copy)]
struct Token {
    tok: Tok,
    start: u32,
    end: u32,
    line: u32,
}

/// One file of the snapshot (STUDIO-REQ-0120).
#[derive(Debug, Clone)]
pub struct RtlFile {
    /// Its path in the snapshot (`/`-separated).
    pub path: String,
    /// Its text; `None` when it was not read (size budget or not UTF-8).
    pub text: Option<String>,
    /// Byte offset of the start of every line.
    line_starts: Vec<usize>,
    tokens: Vec<Token>,
}

impl RtlFile {
    /// A file of the snapshot with its text.
    pub fn new(path: impl Into<String>, text: Option<String>) -> RtlFile {
        let (line_starts, tokens) = match &text {
            Some(t) => (line_starts(t), lex(t)),
            None => (Vec::new(), Vec::new()),
        };
        RtlFile {
            path: path.into(),
            text,
            line_starts,
            tokens,
        }
    }

    /// Lines of the file.
    pub fn line_count(&self) -> usize {
        self.line_starts.len()
    }

    /// Line `line` (1-based) without its line break.
    pub fn line(&self, line: u32) -> &str {
        let Some(text) = &self.text else {
            return "";
        };
        let k = line as usize;
        if k == 0 || k > self.line_starts.len() {
            return "";
        }
        let start = self.line_starts[k - 1];
        let end = self.line_starts.get(k).copied().unwrap_or(text.len());
        text[start..end].trim_end_matches(['\n', '\r'])
    }

    fn text_of(&self, t: &Token) -> &str {
        self.text
            .as_deref()
            .and_then(|s| s.get(t.start as usize..t.end as usize))
            .unwrap_or("")
    }
}

fn line_starts(text: &str) -> Vec<usize> {
    let mut v = vec![0];
    for (i, b) in text.bytes().enumerate() {
        if b == b'\n' && i + 1 < text.len() {
            v.push(i + 1);
        }
    }
    v
}

/// A lexer for identifiers and punctuation that skips comments, strings, numbers and
/// compiler directives' backquotes; escaped identifiers lose their backslash.
fn lex(text: &str) -> Vec<Token> {
    let b = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    let mut line = 1u32;
    while i < b.len() {
        let c = b[i];
        match c {
            b'\n' => {
                line += 1;
                i += 1;
            }
            b'/' if b.get(i + 1) == Some(&b'/') => {
                while i < b.len() && b[i] != b'\n' {
                    i += 1;
                }
            }
            b'/' if b.get(i + 1) == Some(&b'*') => {
                i += 2;
                while i < b.len() && !(b[i] == b'*' && b.get(i + 1) == Some(&b'/')) {
                    if b[i] == b'\n' {
                        line += 1;
                    }
                    i += 1;
                }
                i = (i + 2).min(b.len());
            }
            b'"' => {
                i += 1;
                while i < b.len() && b[i] != b'"' {
                    if b[i] == b'\\' {
                        i += 1;
                    } else if b[i] == b'\n' {
                        line += 1;
                    }
                    i += 1;
                }
                i += 1;
            }
            b'\\' => {
                let start = i + 1;
                i += 1;
                while i < b.len() && !b[i].is_ascii_whitespace() {
                    i += 1;
                }
                out.push(Token {
                    tok: Tok::Ident,
                    start: start as u32,
                    end: i as u32,
                    line,
                });
            }
            c if c.is_ascii_alphabetic() || c == b'_' => {
                let start = i;
                while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_' || b[i] == b'$')
                {
                    i += 1;
                }
                out.push(Token {
                    tok: Tok::Ident,
                    start: start as u32,
                    end: i as u32,
                    line,
                });
            }
            c if c.is_ascii_digit() || c == b'\'' => {
                // Numbers, based literals and their digits.
                i += 1;
                while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_' || b[i] == b'\'')
                {
                    i += 1;
                }
                out.push(Token {
                    tok: Tok::Other,
                    start: i as u32,
                    end: i as u32,
                    line,
                });
            }
            c if c.is_ascii_whitespace() => i += 1,
            c => {
                out.push(Token {
                    tok: Tok::Punct(c),
                    start: i as u32,
                    end: i as u32 + 1,
                    line,
                });
                i += 1;
            }
        }
    }
    out
}

/// A module definition (STUDIO-REQ-0121).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RtlModule {
    /// Its name.
    pub name: String,
    /// The file.
    pub file: usize,
    /// The token range of its body in the file.
    tokens: (usize, usize),
    /// The line of `module`.
    pub line: u32,
}

/// An instantiation of a module of the snapshot (STUDIO-REQ-0121).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RtlInstance {
    /// The instantiated module.
    pub module_type: String,
    /// The instance name.
    pub name: String,
    /// The module that contains it (index into the modules).
    pub parent: usize,
    /// Its line.
    pub line: u32,
}

/// The light index of an RTL snapshot (STUDIO-REQ-0120, STUDIO-REQ-0121).
#[derive(Debug, Clone, Default)]
pub struct RtlIndex {
    /// The top module of `rtl.json`, if any.
    pub top: Option<String>,
    /// The files.
    pub files: Vec<RtlFile>,
    /// The module definitions, in file and line order.
    pub modules: Vec<RtlModule>,
    /// The instantiations of modules of the snapshot.
    pub instances: Vec<RtlInstance>,
    /// Generate block labels: module, label, line.
    pub labels: Vec<(usize, String, u32)>,
}

/// Keywords whose line declares the identifiers on it.
const DECLARATIONS: [&str; 26] = [
    "input",
    "output",
    "inout",
    "wire",
    "reg",
    "logic",
    "bit",
    "var",
    "integer",
    "int",
    "byte",
    "shortint",
    "longint",
    "parameter",
    "localparam",
    "genvar",
    "tri",
    "wand",
    "wor",
    "signed",
    "unsigned",
    "typedef",
    "enum",
    "struct",
    "supply0",
    "supply1",
];

impl RtlIndex {
    /// Indexes `files`, the snapshot's top module being `top` (STUDIO-REQ-0121).
    pub fn new(top: Option<String>, files: Vec<RtlFile>) -> RtlIndex {
        let mut idx = RtlIndex {
            top,
            files,
            ..RtlIndex::default()
        };
        for (f, file) in idx.files.iter().enumerate() {
            let t = &file.tokens;
            let mut k = 0;
            while k < t.len() {
                let word = file.text_of(&t[k]);
                if t[k].tok == Tok::Ident && (word == "module" || word == "macromodule") {
                    let Some(name_tok) = t.get(k + 1).filter(|x| x.tok == Tok::Ident) else {
                        k += 1;
                        continue;
                    };
                    let mut end = k + 2;
                    while end < t.len() && file.text_of(&t[end]) != "endmodule" {
                        end += 1;
                    }
                    idx.modules.push(RtlModule {
                        name: file.text_of(name_tok).to_string(),
                        file: f,
                        tokens: (k + 2, end),
                        line: t[k].line,
                    });
                    k = end + 1;
                } else {
                    k += 1;
                }
            }
        }
        let names: BTreeSet<String> = idx.modules.iter().map(|m| m.name.clone()).collect();
        for (m, module) in idx.modules.iter().enumerate() {
            let file = &idx.files[module.file];
            let t = &file.tokens[module.tokens.0..module.tokens.1];
            for k in 0..t.len() {
                let word = file.text_of(&t[k]);
                if t[k].tok == Tok::Ident && word == "begin" {
                    if let (Some(c), Some(l)) = (t.get(k + 1), t.get(k + 2)) {
                        if c.tok == Tok::Punct(b':') && l.tok == Tok::Ident {
                            idx.labels.push((m, file.text_of(l).to_string(), l.line));
                        }
                    }
                }
                if t[k].tok != Tok::Ident || !names.contains(word) {
                    continue;
                }
                // `type [#( ... )] name [ranges] (`
                let mut j = k + 1;
                if t.get(j).is_some_and(|x| x.tok == Tok::Punct(b'#')) {
                    j += 1;
                    j = skip_balanced(t, j, b'(', b')');
                }
                let Some(name) = t.get(j).filter(|x| x.tok == Tok::Ident) else {
                    continue;
                };
                let mut after = j + 1;
                while t.get(after).is_some_and(|x| x.tok == Tok::Punct(b'[')) {
                    after = skip_balanced(t, after, b'[', b']');
                }
                if t.get(after).is_some_and(|x| x.tok == Tok::Punct(b'(')) {
                    idx.instances.push(RtlInstance {
                        module_type: word.to_string(),
                        name: file.text_of(name).to_string(),
                        parent: m,
                        line: name.line,
                    });
                }
            }
        }
        idx
    }

    /// The module named `name`.
    pub fn module(&self, name: &str) -> Option<usize> {
        self.modules.iter().position(|m| m.name == name)
    }

    /// The module to start from: the snapshot's top, else `fallback` (the netlist's module).
    fn start_module(&self, fallback: &str) -> Option<usize> {
        self.top
            .as_deref()
            .and_then(|t| self.module(t))
            .or_else(|| self.module(fallback))
    }

    /// Occurrences of identifier `ident` in module `m`, classified (STUDIO-REQ-0123).
    fn occurrences(&self, m: usize, ident: &str) -> Vec<(u32, HitKind)> {
        let module = &self.modules[m];
        let file = &self.files[module.file];
        let t = &file.tokens;
        let mut out: Vec<(u32, HitKind)> = Vec::new();
        for k in module.tokens.0..module.tokens.1 {
            if t[k].tok != Tok::Ident || file.text_of(&t[k]) != ident {
                continue;
            }
            let line = t[k].line;
            let text = file.line(line).trim_start();
            let first = text
                .split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
                .next()
                .unwrap_or("");
            let kind = if DECLARATIONS.contains(&first) {
                HitKind::Declaration
            } else if assigns_after(file, t, k) {
                HitKind::Assignment
            } else {
                HitKind::Reference
            };
            if !out.iter().any(|&(l, _)| l == line) {
                out.push((line, kind));
            }
        }
        out
    }
}

/// The index after the group that opens at `j` (or `j` when it does not open one).
fn skip_balanced(t: &[Token], j: usize, open: u8, close: u8) -> usize {
    if t.get(j).is_none_or(|x| x.tok != Tok::Punct(open)) {
        return j;
    }
    let mut depth = 0usize;
    let mut k = j;
    while k < t.len() {
        if t[k].tok == Tok::Punct(open) {
            depth += 1;
        } else if t[k].tok == Tok::Punct(close) {
            depth -= 1;
            if depth == 0 {
                return k + 1;
            }
        }
        k += 1;
    }
    k
}

/// Whether the identifier at `k`, after an optional select, is followed by `=` or `<=`.
fn assigns_after(file: &RtlFile, t: &[Token], k: usize) -> bool {
    let mut j = k + 1;
    while t.get(j).is_some_and(|x| x.tok == Tok::Punct(b'[')) {
        j = skip_balanced(t, j, b'[', b']');
    }
    let at = |j: usize| t.get(j).map(|x| x.tok);
    let text = |j: usize| t.get(j).map_or("", |x| file.text_of(x));
    match at(j) {
        Some(Tok::Punct(b'=')) => text(j + 1) != "=",
        Some(Tok::Punct(b'<')) => at(j + 1) == Some(Tok::Punct(b'=')),
        _ => false,
    }
}

/// An object to probe for: a flattened name and how much its matches count
/// (STUDIO-REQ-0124).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    /// The flattened hierarchical name (escaped, as in the netlist).
    pub name: String,
    /// What it is to the probed object (`instance`, `output net`, `input net`, `port`, `net`).
    pub role: &'static str,
    /// The weight of its matches.
    pub weight: u32,
}

/// A matched source line (STUDIO-REQ-0122).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeHit {
    /// The file (index into [`RtlIndex::files`]).
    pub file: usize,
    /// The line, 1-based.
    pub line: u32,
    /// What the line is.
    pub kind: HitKind,
    /// The module of the line.
    pub module: String,
    /// The candidate that matched and how.
    pub reason: String,
    /// Higher is better.
    pub score: u32,
}

/// The result of a cross-probe (STUDIO-REQ-0122, STUDIO-REQ-0125).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CrossProbe {
    /// The probed object.
    pub subject: String,
    /// The best hits first, at most the budget.
    pub hits: Vec<ProbeHit>,
    /// Always true: matching names is not elaboration (STUDIO-REQ-0125).
    pub approximate: bool,
    /// Whether hits were left out by the budget.
    pub truncated: bool,
}

impl CrossProbe {
    /// The note that the result is approximate, or that nothing matched (STUDIO-REQ-0125).
    pub fn diagnostics(&self) -> Vec<StudioDiag> {
        if self.hits.is_empty() {
            vec![StudioDiag::new(
                Code::CrossProbeNoMatch,
                format!("no RTL line matches {}", self.subject),
            )]
        } else {
            vec![StudioDiag::new(
                Code::CrossProbeApproximate,
                format!(
                    "RTL lines of {} are matched by name and are approximate",
                    self.subject
                ),
            )]
        }
    }
}

/// The RTL parts of a flattened name: its levels with bit and array indices removed
/// (STUDIO-REQ-0124): `u_fifo.mem[2][7]` gives `u_fifo`, `mem`.
pub fn rtl_parts(name: &str) -> Vec<String> {
    split(name, SplitMode::Flattened)
        .into_iter()
        .map(|r| {
            let t = level_text(name, r);
            match t.find('[') {
                Some(0) | None => t,
                Some(i) => t[..i].to_string(),
            }
        })
        .filter(|t| !t.is_empty())
        .collect()
}

/// Probes the snapshot for `candidates` (STUDIO-REQ-0122 to STUDIO-REQ-0125): each name's scope
/// parts are followed from the top module through instantiations and generate labels, and its
/// last part is looked up in the module reached; a part that does not resolve leaves the lookup
/// in the module reached so far, with a lower score. `fallback_top` is the netlist's module name,
/// used when `rtl.json` names no top.
pub fn probe(
    index: &RtlIndex,
    subject: &str,
    candidates: &[Candidate],
    fallback_top: &str,
    max_hits: usize,
) -> CrossProbe {
    let mut best: HashMap<(usize, u32), ProbeHit> = HashMap::new();
    let mut add = |hit: ProbeHit| {
        let key = (hit.file, hit.line);
        match best.get(&key) {
            Some(h) if h.score >= hit.score => {}
            _ => {
                best.insert(key, hit);
            }
        }
    };
    if let Some(top) = index.start_module(fallback_top) {
        for c in candidates {
            let parts = rtl_parts(&c.name);
            let Some((leaf, scopes)) = parts.split_last() else {
                continue;
            };
            let mut m = top;
            let mut resolved = true;
            for part in scopes {
                if let Some(inst) = index
                    .instances
                    .iter()
                    .find(|i| i.parent == m && &i.name == part)
                {
                    add(ProbeHit {
                        file: index.modules[m].file,
                        line: inst.line,
                        kind: HitKind::Instantiation,
                        module: index.modules[m].name.clone(),
                        reason: format!("{} {part} of {}", c.role, c.name),
                        score: c.weight / 2 + HitKind::Instantiation.score(),
                    });
                    match index.module(&inst.module_type) {
                        Some(next) => m = next,
                        None => {
                            resolved = false;
                            break;
                        }
                    }
                } else if let Some(&(_, _, line)) =
                    index.labels.iter().find(|(lm, l, _)| *lm == m && l == part)
                {
                    add(ProbeHit {
                        file: index.modules[m].file,
                        line,
                        kind: HitKind::Label,
                        module: index.modules[m].name.clone(),
                        reason: format!("generate block {part} of {}", c.name),
                        score: c.weight / 2 + HitKind::Label.score(),
                    });
                } else {
                    resolved = false;
                    break;
                }
            }
            let mut idents = vec![leaf.clone()];
            if let Some(base) = leaf.strip_suffix("_reg") {
                idents.push(base.to_string());
            }
            for ident in idents {
                for (line, kind) in index.occurrences(m, &ident) {
                    let penalty = if resolved { 0 } else { c.weight / 2 };
                    add(ProbeHit {
                        file: index.modules[m].file,
                        line,
                        kind,
                        module: index.modules[m].name.clone(),
                        reason: format!("{} {ident} ({})", kind.as_str(), c.name),
                        score: c.weight.saturating_sub(penalty) + kind.score(),
                    });
                }
            }
        }
    }
    let mut hits: Vec<ProbeHit> = best.into_values().collect();
    hits.sort_by(|a, b| {
        b.score
            .cmp(&a.score)
            .then(a.file.cmp(&b.file))
            .then(a.line.cmp(&b.line))
    });
    let truncated = hits.len() > max_hits;
    hits.truncate(max_hits);
    CrossProbe {
        subject: subject.to_string(),
        hits,
        approximate: true,
        truncated,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOP: &str = "module top_hier (
  input  logic       clk_i,
  output logic [7:0] cnt_o
);
  // counter u_fake (...) in a comment is not an instance
  counter #(.W(8)) u_cnt (.clk_i, .cnt_o);
  if (1) begin : g_or
    assign any_o = |cnt_o;
  end
endmodule
";
    const CNT: &str = "module counter #(parameter int W = 8) (
  input  logic clk_i,
  output logic [W-1:0] cnt_o
);
  logic [W-1:0] cnt_q, cnt_d;
  always_ff @(posedge clk_i) cnt_q <= cnt_d;
  assign cnt_o = cnt_q;
  /* cnt_q in a block comment */
  wire \\esc/name ;
endmodule
";

    fn index() -> RtlIndex {
        RtlIndex::new(
            Some("top_hier".into()),
            vec![
                RtlFile::new("src_0_top.sv", Some(TOP.into())),
                RtlFile::new("src_1_counter.sv", Some(CNT.into())),
            ],
        )
    }

    // STUDIO-REQ-0120, STUDIO-REQ-0121: modules, instantiations of snapshot modules (not in
    // comments) and generate labels; lines are 1-based.
    #[test]
    fn index_structure() {
        let i = index();
        let names: Vec<&str> = i.modules.iter().map(|m| m.name.as_str()).collect();
        assert_eq!(names, ["top_hier", "counter"]);
        assert_eq!(i.instances.len(), 1);
        assert_eq!(i.instances[0].name, "u_cnt");
        assert_eq!(i.instances[0].module_type, "counter");
        assert_eq!(i.instances[0].line, 6);
        assert_eq!(i.labels, [(0, "g_or".to_string(), 7)]);
        assert_eq!(i.files[1].line(5).trim(), "logic [W-1:0] cnt_q, cnt_d;");
        assert_eq!(i.files[1].line(99), "");
        assert_eq!(i.files[0].line_count(), 10);
    }

    // STUDIO-REQ-0122 to STUDIO-REQ-0124: a state net's RTL parts lead through the
    // instantiation to the declaration and assignments of the variable, best first.
    #[test]
    fn probe_state_net() {
        let i = index();
        let c = [Candidate {
            name: "u_cnt.cnt_q[3]".into(),
            role: "net",
            weight: 100,
        }];
        let p = probe(&i, "u_cnt.cnt_q[3]", &c, "top_hier", 40);
        assert!(p.approximate);
        let first = &p.hits[0];
        assert_eq!((first.file, first.line), (1, 5));
        assert_eq!(first.kind, HitKind::Declaration);
        assert_eq!(first.module, "counter");
        let kinds: Vec<(u32, HitKind)> = p.hits.iter().map(|h| (h.line, h.kind)).collect();
        assert!(kinds.contains(&(6, HitKind::Assignment)));
        assert!(kinds.contains(&(7, HitKind::Reference)));
        assert!(
            p.hits
                .iter()
                .any(|h| h.kind == HitKind::Instantiation && h.line == 6)
        );
        // Comment lines never match.
        assert!(!p.hits.iter().any(|h| h.file == 1 && h.line == 8));
        assert_eq!(p.diagnostics()[0].code, Code::CrossProbeApproximate);
    }

    // STUDIO-REQ-0124: generate labels, `_reg` suffixes and unresolved
    // scopes; STUDIO-REQ-0125: no match is a note.
    #[test]
    fn probe_variants() {
        let i = index();
        let cand = |name: &str| Candidate {
            name: name.into(),
            role: "net",
            weight: 100,
        };
        let p = probe(&i, "x", &[cand("g_or.any_o")], "top_hier", 40);
        assert!(p.hits.iter().any(|h| h.kind == HitKind::Label));
        assert!(
            p.hits
                .iter()
                .any(|h| h.line == 8 && h.kind == HitKind::Assignment)
        );
        let p = probe(&i, "x", &[cand("u_cnt.cnt_d_reg")], "top_hier", 40);
        assert!(p.hits.iter().any(|h| h.line == 5 && h.file == 1));
        let unresolved = probe(&i, "x", &[cand("u_nope.cnt_o")], "top_hier", 40);
        let resolved = probe(&i, "x", &[cand("cnt_o")], "top_hier", 40);
        assert!(unresolved.hits[0].score < resolved.hits[0].score);
        let none = probe(&i, "g42", &[cand("g42")], "top_hier", 40);
        assert!(none.hits.is_empty());
        assert_eq!(none.diagnostics()[0].code, Code::CrossProbeNoMatch);
        let capped = probe(&i, "x", &[cand("u_cnt.cnt_q")], "top_hier", 2);
        assert_eq!(capped.hits.len(), 2);
        assert!(capped.truncated);
        assert_eq!(rtl_parts("u_fifo.mem[2][7]"), ["u_fifo", "mem"]);
        assert_eq!(
            rtl_parts("gen_lane[2].u_add/g7"),
            ["gen_lane", "u_add", "g7"]
        );
    }

    #[test]
    fn lexer_skips_strings_and_numbers() {
        let f = RtlFile::new("a.sv", Some("x = \"cnt_q\" + 8'hff; // cnt_q\ny".into()));
        let idents: Vec<&str> = f
            .tokens
            .iter()
            .filter(|t| t.tok == Tok::Ident)
            .map(|t| f.text_of(t))
            .collect();
        assert_eq!(idents, ["x", "y"]);
        // An escaped identifier is one identifier without its backslash.
        let f = RtlFile::new("b.sv", Some("wire \\a/b ;".into()));
        let idents: Vec<&str> = f
            .tokens
            .iter()
            .filter(|t| t.tok == Tok::Ident)
            .map(|t| f.text_of(t))
            .collect();
        assert_eq!(idents, ["wire", "a/b"]);
    }
}

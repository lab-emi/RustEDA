// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! `cargo xtask coverage`: the requirement-to-test matrix `docs/verification/requirements.md`
//! (STUDIO-REQ-0250).
//!
//! A requirement is defined by a line of a specification below `docs/specs/` that starts with its
//! bold ID (`**<PREFIX>-REQ-<nnnn>**`) and is covered when a Rust source or test cites it, or when
//! its heading carries `(planned: <WP>)` (listed as `planned (<WP>)` until `<WP>` lands). Every
//! specification file is read, whatever its directory, and every prefix is recognised (STA, CLI,
//! PROTO, JOB, VERILOG, LIBERTY, SDC, COMPUTE, …). A citation is a full ID, optionally followed by
//! further numbers of the same prefix separated by `,`, `/`, `and` or `or`
//! (`DEMO-REQ-0101, 0102 and 0103`, `DEMO-REQ-0080/0081`). A range after a cited number
//! (`0050 to 0054`, `0400-0419`, also with `–`, `…` or `...`, and with the prefix repeated before
//! its last number) cites every ID from its first to its last number within the same prefix
//! ([`RANGE_MAX`] IDs at most; a longer or reversed range cites only its first number). The matrix
//! states this convention ([`CONVENTION`]).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// Requirement definitions: ID → specification path (relative), planned work packages, and IDs
/// defined more than once.
pub(crate) struct Definitions {
    pub(crate) spec: BTreeMap<String, String>,
    pub(crate) planned: BTreeMap<String, String>,
    pub(crate) duplicates: Vec<String>,
}

/// The most IDs a range in a citation may cite; a longer range cites only its first number.
const RANGE_MAX: u32 = 200;
/// The marks that join the first and the last number of a range when the last number follows
/// directly: the en dash, the ellipsis character, three ASCII dots and the hyphen (the word `to`
/// is handled apart).
const RANGE_MARKS: [&str; 4] = ["\u{2013}", "\u{2026}", "...", "-"];

/// The citation convention, printed at the top of the generated matrix.
///
/// The example ID is split in the source so that this file does not cite a requirement by it.
const CONVENTION: &str = concat!(
    "Citation convention: a citation is a full ID (`STA-REQ",
    "-1050`), optionally followed by further\n",
    "numbers of the same prefix separated by `,`, `/`, `and` or `or`. A range after a cited number\n",
    "(`1050 to 1054`, `0400-0419`, also with `\u{2013}`, `\u{2026}` or `...`) cites every ID from its first to its last number\n",
    "within the same prefix; IDs in the range that no specification defines are ignored. A dash marks\n",
    "a range only when the last number follows it directly (`9370 - 9372` is no range). A range of\n",
    "more than 200 IDs is not expanded: only its first number counts as cited.\n"
);

/// True for `<PREFIX>-REQ-<nnnn>` with an upper-case prefix.
fn is_id(s: &str) -> bool {
    let Some((prefix, num)) = s.split_once("-REQ-") else {
        return false;
    };
    let p = prefix.as_bytes();
    p.first().is_some_and(u8::is_ascii_uppercase)
        && p.iter()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
        && num.len() == 4
        && num.bytes().all(|c| c.is_ascii_digit())
}

/// Files below `dir` with extension `ext`, sorted, skipping `target` directories.
fn walk(dir: &Path, ext: &str) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else {
            continue;
        };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                if p.file_name().is_some_and(|n| n != "target") {
                    stack.push(p);
                }
            } else if p.extension().is_some_and(|x| x == ext) {
                out.push(p);
            }
        }
    }
    out.sort();
    out
}

fn rel(root: &Path, p: &Path) -> String {
    let r = p.strip_prefix(root).unwrap_or(p);
    let parts: Vec<String> = r
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect();
    parts.join("/")
}

/// Reads a `(planned: <WP>)` marker immediately after the bold requirement ID on a definition line.
fn planned_marker(rest: &str) -> Option<&str> {
    let rest = rest.trim_start();
    let inner = rest.strip_prefix("(planned:")?.split_once(')')?.0.trim();
    (!inner.is_empty()).then_some(inner)
}

/// Reads the requirement definitions of `docs/specs/**/*.md` below `root`.
pub(crate) fn definitions(root: &Path) -> Result<Definitions, String> {
    let mut spec = BTreeMap::new();
    let mut planned = BTreeMap::new();
    let mut duplicates = BTreeSet::new();
    for p in walk(&root.join("docs/specs"), "md") {
        let text = std::fs::read_to_string(&p).map_err(|e| format!("{}: {e}", p.display()))?;
        let at = rel(root, &p);
        for line in text.lines() {
            let Some((id, rest)) = line.strip_prefix("**").and_then(|s| s.split_once("**")) else {
                continue;
            };
            if !is_id(id) {
                continue;
            }
            if spec.contains_key(id) {
                duplicates.insert(id.to_string());
            } else {
                spec.insert(id.to_string(), at.clone());
                if let Some(wp) = planned_marker(rest) {
                    planned.insert(id.to_string(), wp.to_string());
                }
            }
        }
    }
    Ok(Definitions {
        spec,
        planned,
        duplicates: duplicates.into_iter().collect(),
    })
}

/// Reads exactly four digits at `i`, not followed by another digit.
fn four_digits(b: &[u8], i: usize) -> Option<&str> {
    let d = b.get(i..i + 4)?;
    if d.iter().all(u8::is_ascii_digit) && !b.get(i + 4).is_some_and(u8::is_ascii_digit) {
        std::str::from_utf8(d).ok()
    } else {
        None
    }
}

/// Length of a list separator at `i` (`,`, `/`, ` and `, ` or `, with blanks), or `None`.
fn separator(b: &[u8], i: usize) -> Option<usize> {
    let mut j = i;
    while b.get(j) == Some(&b' ') {
        j += 1;
    }
    let mut seen = false;
    if matches!(b.get(j), Some(b',') | Some(b'/')) {
        j += 1;
        seen = true;
        while b.get(j) == Some(&b' ') {
            j += 1;
        }
    }
    for word in [&b"and "[..], &b"or "[..]] {
        if b.get(j..j + word.len()) == Some(word) && (seen || j > i) {
            j += word.len();
            seen = true;
            while b.get(j) == Some(&b' ') {
                j += 1;
            }
            break;
        }
    }
    seen.then_some(j - i)
}

/// True when a continuation number that ends at `i` belongs to the list: after blanks, the text
/// or line ends; one of `) ; . : ] " ' * `` follows; another separator, a line-ending `and` or
/// `or`, or another ID follows; or a range (`to`, `–`, `…`, `-` and a number or ID) or a qualifier
/// of the cited requirement (`item`, `items`, `in part`) follows. So prose such as
/// `DEMO-REQ-0101, 1024 files` or a date after an ID is not read as a citation.
fn list_goes_on(b: &[u8], i: usize) -> bool {
    let mut j = i;
    while matches!(b.get(j), Some(b' ') | Some(b'\t')) {
        j += 1;
    }
    let rest = b.get(j..).unwrap_or_default();
    let wrapped =
        |w: &[u8]| rest.starts_with(w) && rest.get(w.len()).is_none_or(|c| c.is_ascii_whitespace());
    match rest.first() {
        None => true,
        Some(b'\n' | b'\r' | b')' | b';' | b'.' | b':' | b']' | b'"' | b'\'' | b'*' | b'`') => true,
        Some(_) => {
            separator(b, i).is_some()
                || wrapped(b"and")
                || wrapped(b"or")
                || starts_id(b, j)
                || range_or_qualifier(b, j)
        }
    }
}

/// True when a range (`to`, `–`, `…`, `...` or `-` followed by a number or an ID) or a qualifier of the
/// cited requirement (`item`, `items`, `in part`) starts at `j`.
fn range_or_qualifier(b: &[u8], j: usize) -> bool {
    let rest = b.get(j..).unwrap_or_default();
    let number_or_id = |k: usize| {
        four_digits(b, k).is_some_and(|_| b.get(k + 4) != Some(&b'-')) || starts_id(b, k)
    };
    for mark in RANGE_MARKS {
        if rest.starts_with(mark.as_bytes()) {
            return number_or_id(j + mark.len());
        }
    }
    if rest.starts_with(b"to ") {
        let mut k = j + 3;
        while b.get(k) == Some(&b' ') {
            k += 1;
        }
        return number_or_id(k);
    }
    [&b"item "[..], b"items ", b"in part"]
        .iter()
        .any(|w| rest.starts_with(w))
}

/// After a cited number ending at `i`: the last number of a range that follows (`–0419`,
/// `…0122`, `...0122`, `-0240`, ` to 1054`, each also with `<prefix>-REQ-` before the number) and the
/// index after it; `None` when no range follows. A last number that carries another prefix is
/// left to the scan, which finds that ID itself.
fn range_end(b: &[u8], i: usize, prefix: &str) -> Option<(u32, usize)> {
    let mut j = i;
    while b.get(j) == Some(&b' ') {
        j += 1;
    }
    let rest = b.get(j..)?;
    let mut k = None;
    for mark in RANGE_MARKS {
        if rest.starts_with(mark.as_bytes()) {
            k = Some(j + mark.len());
        }
    }
    if rest.starts_with(b"to ") {
        let mut t = j + 3;
        while b.get(t) == Some(&b' ') {
            t += 1;
        }
        k = Some(t);
    }
    let mut k = k?;
    let repeated = format!("{prefix}-REQ-");
    if b.get(k..k + repeated.len()) == Some(repeated.as_bytes()) {
        k += repeated.len();
    }
    let digits = four_digits(b, k).filter(|_| b.get(k + 4) != Some(&b'-'))?;
    Some((digits.parse().ok()?, k + 4))
}

/// Cites the number `num` (four digits) of `prefix` whose text ends at `after`, together with the
/// range that follows it, if any: every ID from `num` to the range's last number when that is not
/// before `num` and the range has at most [`RANGE_MAX`] IDs, else `num` alone. Returns the index
/// after the range's last number, or `after`.
fn cite(out: &mut BTreeSet<String>, b: &[u8], prefix: &str, num: &str, after: usize) -> usize {
    let Ok(first) = num.parse::<u32>() else {
        return after;
    };
    let Some((last, next)) = range_end(b, after, prefix) else {
        out.insert(format!("{prefix}-REQ-{first:04}"));
        return after;
    };
    let last = if last >= first && last - first < RANGE_MAX {
        last
    } else {
        first
    };
    for n in first..=last {
        out.insert(format!("{prefix}-REQ-{n:04}"));
    }
    next
}

/// True when an upper-case prefix followed by `-REQ-` starts at `i`.
fn starts_id(b: &[u8], i: usize) -> bool {
    let mut j = i;
    while b
        .get(j)
        .is_some_and(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
    {
        j += 1;
    }
    j > i && b.get(i).is_some_and(u8::is_ascii_uppercase) && b.get(j..j + 5) == Some(b"-REQ-")
}

/// Every requirement ID cited in `text`, including list continuations.
pub(crate) fn citations(text: &str) -> BTreeSet<String> {
    let b = text.as_bytes();
    let mut out = BTreeSet::new();
    let mut from = 0;
    while let Some(pos) = text.get(from..).and_then(|s| s.find("-REQ-")) {
        let at = from + pos;
        from = at + 5;
        let mut s = at;
        while s > 0
            && b.get(s - 1)
                .is_some_and(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
        {
            s -= 1;
        }
        let bounded = s == 0 || !b.get(s - 1).is_some_and(|c| c.is_ascii_alphanumeric());
        let Some(prefix) = text.get(s..at) else {
            continue;
        };
        if !bounded
            || !prefix
                .as_bytes()
                .first()
                .is_some_and(u8::is_ascii_uppercase)
        {
            continue;
        }
        let Some(num) = four_digits(b, at + 5) else {
            continue;
        };
        let mut j = cite(&mut out, b, prefix, num, at + 9);
        while let Some(n) = separator(b, j) {
            let Some(num) = four_digits(b, j + n) else {
                break;
            };
            if !list_goes_on(b, j + n + 4) {
                break;
            }
            j = cite(&mut out, b, prefix, num, j + n + 4);
        }
        from = from.max(j);
    }
    out
}

/// The requirement prefix of an ID.
fn prefix(id: &str) -> &str {
    id.split("-REQ-").next().unwrap_or(id)
}

/// Builds the matrix text and the list of uncovered requirements.
pub(crate) fn matrix(
    defs: &Definitions,
    refs: &BTreeMap<String, BTreeSet<String>>,
) -> (String, Vec<String>) {
    let mut out = String::from(
        "# Requirement coverage\n\nGenerated by `cargo xtask coverage`. A requirement is covered when a source or test file cites it.\n\n",
    );
    out.push_str(CONVENTION);
    out.push_str("\n| Requirement | Specification | Cited by |\n|---|---|---|\n");
    let mut gaps = Vec::new();
    let mut per_prefix: BTreeMap<&str, (usize, usize)> = BTreeMap::new();
    for (id, spec) in &defs.spec {
        let by = refs.get(id).map_or_else(String::new, |s| {
            s.iter()
                .map(String::as_str)
                .collect::<Vec<_>>()
                .join("<br>")
        });
        let entry = per_prefix.entry(prefix(id)).or_default();
        entry.0 += 1;
        let cell = if !by.is_empty() {
            by
        } else if let Some(wp) = defs.planned.get(id) {
            format!("planned ({wp})")
        } else {
            gaps.push(id.clone());
            entry.1 += 1;
            String::from("**gap**")
        };
        out.push_str(&format!("| {id} | {spec} | {cell} |\n"));
    }
    out.push_str("\n| Prefix | Requirements | Without a citing test or source |\n|---|---|---|\n");
    for (p, (n, g)) in &per_prefix {
        out.push_str(&format!("| {p} | {n} | {g} |\n"));
    }
    out.push_str(&format!(
        "\n{} requirements, {} without a citing test or source.\n",
        defs.spec.len(),
        gaps.len()
    ));
    (out, gaps)
}

/// The `coverage` command on the repository at `root`.
pub(crate) fn run(root: &Path) -> Result<(), String> {
    let defs = definitions(root)?;
    let mut refs: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for dir in ["crates", "xtask"] {
        for p in walk(&root.join(dir), "rs") {
            let text = std::fs::read_to_string(&p).unwrap_or_default();
            let at = rel(root, &p);
            for id in citations(&text) {
                if defs.spec.contains_key(&id) {
                    refs.entry(id).or_default().insert(at.clone());
                }
            }
        }
    }
    if let Some(common) = crate::foundation::root(root)? {
        for p in walk(&common.join("crates"), "rs") {
            let text = std::fs::read_to_string(&p).map_err(|e| e.to_string())?;
            let at = format!("@common/{}", rel(&common, &p));
            for id in citations(&text) {
                if defs.spec.contains_key(&id) {
                    refs.entry(id).or_default().insert(at.clone());
                }
            }
        }
    }
    let (text, gaps) = matrix(&defs, &refs);
    crate::write(&root.join("docs/verification/requirements.md"), &text)?;
    println!("{} requirements, {} gaps", defs.spec.len(), gaps.len());
    let mut problems = Vec::new();
    if !defs.duplicates.is_empty() {
        problems.push(format!(
            "requirements defined more than once: {}",
            defs.duplicates.join(", ")
        ));
    }
    if !gaps.is_empty() {
        problems.push(format!(
            "requirements without a citing test: {}",
            gaps.join(", ")
        ));
    }
    if problems.is_empty() {
        Ok(())
    } else {
        Err(problems.join("\n"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{Tree, rs, rs_shared};

    fn ids(text: &str) -> Vec<String> {
        citations(text).into_iter().collect()
    }

    // STUDIO-REQ-0250 (the coverage matrix): citations of every prefix, with list continuations,
    // and nothing that only looks like an ID.
    #[test]
    fn citation_forms() {
        assert_eq!(
            ids("// STA-REQ-9101, 9102 and 9103; VERILOG-REQ-9080/9081, JOB-REQ-9008 or 9009"),
            [
                "JOB-REQ-9008",
                "JOB-REQ-9009",
                "STA-REQ-9101",
                "STA-REQ-9102",
                "STA-REQ-9103",
                "VERILOG-REQ-9080",
                "VERILOG-REQ-9081",
            ]
        );
        assert_eq!(
            ids("VERILOG-REQ-9401,9512 CLI-REQ-9300 (SDC-REQ-9601), PROTO-REQ-9201."),
            [
                "CLI-REQ-9300",
                "PROTO-REQ-9201",
                "SDC-REQ-9601",
                "VERILOG-REQ-9401",
                "VERILOG-REQ-9512"
            ]
        );
        assert_eq!(
            ids("LIBERTY-REQ-9001 SDC-REQ-9010 CLI-REQ-9020 JOB-REQ-9030").len(),
            4
        );
        assert!(ids("STA-REQ-91011 sta-REQ-0001 aSTA-REQ-9001 STA-REQ-12").is_empty());
        assert_eq!(ids("STA-REQ-9100, 1000x"), ["STA-REQ-9100"]);
        assert_eq!(ids("STA-REQ-9100, 2 cases"), ["STA-REQ-9100"]);
        assert_eq!(ids("STA-REQ-9106, 9124 files"), ["STA-REQ-9106"]);
        assert_eq!(ids("STA-REQ-9106, 2026-09-29"), ["STA-REQ-9106"]);
        assert_eq!(ids("STA-REQ-9106, 9107 to go"), ["STA-REQ-9106"]);
        assert_eq!(
            ids(
                "(STA-REQ-9043, 9050 to 9053) STA-REQ-9403, 9404 item 7: STA-REQ-9001, 9009 in part"
            ),
            [
                "STA-REQ-9001",
                "STA-REQ-9009",
                "STA-REQ-9043",
                "STA-REQ-9050",
                "STA-REQ-9051",
                "STA-REQ-9052",
                "STA-REQ-9053",
                "STA-REQ-9403",
                "STA-REQ-9404"
            ]
        );
        assert_eq!(
            ids(
                "STA-REQ-9400, 9405\u{2013}9407, 9410\u{2026}9412, 9415-9416 VERILOG-REQ-9100, 9101 and\n"
            ),
            [
                "STA-REQ-9400",
                "STA-REQ-9405",
                "STA-REQ-9406",
                "STA-REQ-9407",
                "STA-REQ-9410",
                "STA-REQ-9411",
                "STA-REQ-9412",
                "STA-REQ-9415",
                "STA-REQ-9416",
                "VERILOG-REQ-9100",
                "VERILOG-REQ-9101"
            ]
        );
        assert_eq!(
            ids("STA-REQ-9106, 9107 and 9108 are covered"),
            ["STA-REQ-9106", "STA-REQ-9107"]
        );
        assert_eq!(
            ids("(STA-REQ-9106, 9107)\n\"VERILOG-REQ-9001/9002\"; JOB-REQ-9008 or 9009:"),
            [
                "JOB-REQ-9008",
                "JOB-REQ-9009",
                "STA-REQ-9106",
                "STA-REQ-9107",
                "VERILOG-REQ-9001",
                "VERILOG-REQ-9002"
            ]
        );
    }

    /// The IDs `<prefix>-REQ-<a>` to `<prefix>-REQ-<b>`, in order.
    fn span(prefix: &str, a: u32, b: u32) -> Vec<String> {
        (a..=b).map(|n| format!("{prefix}-REQ-{n:04}")).collect()
    }

    // STUDIO-REQ-0250 (a range after a cited number cites every ID from its first to its last
    // number): each written form of the convention, also with a full ID at the end.
    #[test]
    fn ranges_cite_every_id() {
        let five = span("STA", 9300, 9304);
        assert_eq!(ids("STA-REQ-9300 to 9304"), five);
        assert_eq!(ids("STA-REQ-9300 to   9304"), five);
        assert_eq!(ids("STA-REQ-9300-9304"), five);
        assert_eq!(ids("STA-REQ-9300\u{2013}9304"), five);
        assert_eq!(ids("STA-REQ-9300\u{2026}9304"), five);
        assert_eq!(ids("STA-REQ-9300...9304"), five);
        assert_eq!(ids("STA-REQ-9300...STA-REQ-9304"), five);
        assert_eq!(ids("(STA-REQ-9300...9304)"), five);
        assert_eq!(ids("STA-REQ-9300 to STA-REQ-9304"), five);
        assert_eq!(ids("(STA-REQ-9300 to 9304)"), five);
        assert_eq!(ids("// STA-REQ-9300 to 9304."), five);
        assert_eq!(
            ids("STA-REQ-9008 to 9011"),
            ["9008", "9009", "9010", "9011"].map(|n| format!("STA-REQ-{n}"))
        );
        assert_eq!(ids("VERILOG-REQ-9080 to 9082"), span("VERILOG", 9080, 9082));
        // The last number of another prefix is not a range; the scan finds that ID itself.
        assert_eq!(
            ids("STA-REQ-9300 to VERILOG-REQ-9302"),
            ["STA-REQ-9300", "VERILOG-REQ-9302"]
        );
    }

    // STUDIO-REQ-0250 (ranges are bounded and never guessed): at most 200 IDs are expanded; a longer,
    // reversed or prose-like range cites only its first number.
    #[test]
    fn range_limits() {
        assert_eq!(ids("STA-REQ-9500 to 9699"), span("STA", 9500, 9699));
        assert_eq!(ids("STA-REQ-9500 to 9699").len(), 200);
        assert_eq!(ids("STA-REQ-9500 to 9700"), ["STA-REQ-9500"]);
        assert_eq!(ids("STA-REQ-9500-9900"), ["STA-REQ-9500"]);
        assert_eq!(ids("STA-REQ-9310 to 9305"), ["STA-REQ-9310"]);
        assert_eq!(ids("STA-REQ-9310 to 9310"), ["STA-REQ-9310"]);
        assert_eq!(ids("STA-REQ-9360 to go"), ["STA-REQ-9360"]);
        assert_eq!(ids("STA-REQ-9360 to 20 cases"), ["STA-REQ-9360"]);
        assert_eq!(ids("STA-REQ-9370 to 2026-09-29"), ["STA-REQ-9370"]);
        assert_eq!(ids("STA-REQ-9370-2026-09-29"), ["STA-REQ-9370"]);
        assert_eq!(ids("STA-REQ-9370 - 9372"), ["STA-REQ-9370"]);
        assert_eq!(
            ids("STA-REQ-9310 to 9305 and STA-REQ-9400 to 9401"),
            ["STA-REQ-9310", "STA-REQ-9400", "STA-REQ-9401"]
        );
    }

    // STUDIO-REQ-0250 (a range is part of a list): lists go on after a range and may contain
    // several ranges, in the first place or after a continuation number.
    #[test]
    fn ranges_in_lists() {
        let mut want = span("STA", 9320, 9322);
        want.push("STA-REQ-9330".into());
        assert_eq!(ids("STA-REQ-9320 to 9322, 9330"), want);
        let mut want = span("STA", 9390, 9391);
        want.push("STA-REQ-9395".into());
        assert_eq!(ids("STA-REQ-9390\u{2013}9391 and 9395"), want);
        let mut want = vec!["STA-REQ-9340".to_string()];
        want.extend(span("STA", 9342, 9344));
        want.extend(span("STA", 9346, 9347));
        assert_eq!(ids("STA-REQ-9340, 9342 to 9344, 9346-9347 files"), want);
        let mut want = vec!["STA-REQ-9350".to_string()];
        want.extend(span("STA", 9352, 9353));
        assert_eq!(ids("STA-REQ-9350, 9352 to STA-REQ-9353"), want);
    }

    // STUDIO-REQ-0250 (the printed convention states the limit that the code applies).
    #[test]
    fn convention_states_the_limit() {
        assert!(CONVENTION.contains(&format!("more than {RANGE_MAX} IDs")));
        assert!(CONVENTION.starts_with("Citation convention:"));
        // A spaced dash is not a range, as the convention says.
        assert!(CONVENTION.contains("no range"));
        assert_eq!(ids("STA-REQ-9370 - 9372"), ["STA-REQ-9370"]);
        assert_eq!(ids("STA-REQ-9370 -9372"), span("STA", 9370, 9372));
        // Three ASCII dots are a range mark like the ellipsis character; prose dots are not.
        assert!(CONVENTION.contains("`...`"));
        assert_eq!(ids("STA-REQ-9370 ... 9372"), ["STA-REQ-9370"]);
        assert_eq!(ids("STA-REQ-9370..."), ["STA-REQ-9370"]);
        assert_eq!(ids("STA-REQ-9370... 9372 files"), ["STA-REQ-9370"]);
        assert_eq!(ids("STA-REQ-9370...2026-09-29"), ["STA-REQ-9370"]);
        assert!(CONVENTION.ends_with('\n'));
    }

    // STUDIO-REQ-0250: definitions of every prefix and in every specification directory, the
    // per-prefix summary, gaps and duplicate detection, on a synthetic tree; sources below a
    // `target` directory are not read.
    #[test]
    fn matrix_on_a_tree() {
        let t = Tree::new("coverage");
        t.file(
            "docs/specs/sta/delay.md",
            "**STA-REQ-9800** Text.\n\n**STA-REQ-9801** Text.\nSee STA-REQ-9802.\n",
        )
        .file(
            "docs/specs/sta/ranges.md",
            "**STA-REQ-9803** Text.\n**STA-REQ-9805** Text.\n**STA-REQ-9800** again\n",
        )
        .file(
            "crates/engines/demo-engine/tests/ranges.rs",
            &rs("// STA-REQ-9803 to 9807\n"),
        )
        .file(
            "docs/specs/core/README.md",
            "**JOB-REQ-9101** Text.\n**JOB-REQ-9102** Text.\n",
        )
        .file(
            "crates/engines/demo-engine/src/prop.rs",
            &rs("// STA-REQ-9800, 9801\n"),
        )
        .file(
            "crates/core/rusteda-core/tests/events.rs",
            &rs_shared("// JOB-REQ-9101\n"),
        )
        .file(
            "crates/core/rusteda-core/target/junk.rs",
            &rs_shared("// JOB-REQ-9102\n"),
        )
        .file("xtask/src/demo.rs", &rs("// JOB-REQ-9101, STA-REQ-9801\n"));
        let e = run(t.root()).unwrap_err();
        assert!(e.contains("defined more than once: STA-REQ-9800"), "{e}");
        assert!(e.contains("without a citing test: JOB-REQ-9102"), "{e}");
        let text = std::fs::read_to_string(t.root().join("docs/verification/requirements.md"))
            .unwrap_or_default();
        assert!(
            text.contains(
                "| STA-REQ-9800 | docs/specs/sta/delay.md | crates/engines/demo-engine/src/prop.rs |"
            ),
            "{text}"
        );
        assert!(
            text.contains(
                "| STA-REQ-9801 | docs/specs/sta/delay.md | crates/engines/demo-engine/src/prop.rs<br>xtask/src/demo.rs |"
            ),
            "{text}"
        );
        assert!(
            text.contains(
                "| JOB-REQ-9101 | docs/specs/core/README.md | crates/core/rusteda-core/tests/events.rs<br>xtask/src/demo.rs |"
            ),
            "{text}"
        );
        assert!(text.contains("| JOB-REQ-9102 | docs/specs/core/README.md | **gap** |"));
        assert!(text.contains("| JOB | 2 | 1 |"));
        assert!(text.contains("| STA | 4 | 0 |"));
        assert!(text.contains("6 requirements, 1 without a citing test or source."));
        // The range covers both requirements it reaches; the ones no specification defines are
        // ignored; the convention is printed above the table.
        assert!(
            text.contains(
                "| STA-REQ-9805 | docs/specs/sta/ranges.md | crates/engines/demo-engine/tests/ranges.rs |"
            ),
            "{text}"
        );
        assert!(
            text.contains(
                "| STA-REQ-9803 | docs/specs/sta/ranges.md | crates/engines/demo-engine/tests/ranges.rs |"
            ),
            "{text}"
        );
        assert!(!text.contains("STA-REQ-9802") && !text.contains("STA-REQ-9804"));
        assert!(text.contains(CONVENTION), "{text}");
        assert!(text.find(CONVENTION) < text.find("| Requirement |"));
    }

    // STUDIO-REQ-0250: a tree without specifications gives an empty matrix, not an error; a new
    // specification directory is read without any configuration.
    #[test]
    fn matrix_without_and_with_new_specifications() {
        let t = Tree::new("coverage-empty");
        run(t.root()).unwrap_or_else(|e| panic!("{e}"));
        let text = std::fs::read_to_string(t.root().join("docs/verification/requirements.md"))
            .unwrap_or_default();
        assert!(text.contains("0 requirements, 0 without a citing test or source."));
        t.file("docs/specs/core/README.md", "**JOB-REQ-9001** Text.\n")
            .file(
                "crates/core/rusteda-core/src/lib.rs",
                &rs_shared("// JOB-REQ-9001\n"),
            );
        run(t.root()).unwrap_or_else(|e| panic!("{e}"));
        let text = std::fs::read_to_string(t.root().join("docs/verification/requirements.md"))
            .unwrap_or_default();
        assert!(
            text.contains(
                "| JOB-REQ-9001 | docs/specs/core/README.md | crates/core/rusteda-core/src/lib.rs |"
            ),
            "{text}"
        );
        assert!(text.contains("1 requirements, 0 without a citing test or source."));
    }

    // STUDIO-REQ-0250: a requirement with `(planned: <WP>)` requires no citing test yet and is
    // listed as `planned (<WP>)` in the matrix; once a test cites it, the citing file is listed.
    #[test]
    fn planned_marker_satisfies_coverage() {
        let t = Tree::new("coverage-planned");
        t.file(
            "docs/specs/sta/delay.md",
            "**STA-REQ-9900** (planned: E3) Lumped wire load.\n**STA-REQ-9901** (planned: E5) Hold relation.\n",
        )
        .file(
            "crates/engines/demo-engine/tests/hold.rs",
            &rs("// STA-REQ-9901\n"),
        );
        run(t.root()).unwrap_or_else(|e| panic!("{e}"));
        let text = std::fs::read_to_string(t.root().join("docs/verification/requirements.md"))
            .unwrap_or_default();
        assert!(
            text.contains("| STA-REQ-9900 | docs/specs/sta/delay.md | planned (E3) |"),
            "{text}"
        );
        assert!(
            text.contains(
                "| STA-REQ-9901 | docs/specs/sta/delay.md | crates/engines/demo-engine/tests/hold.rs |"
            ),
            "{text}"
        );
        assert!(text.contains("| STA | 2 | 0 |"), "{text}");
        assert!(text.contains("2 requirements, 0 without a citing test or source."));
    }
}

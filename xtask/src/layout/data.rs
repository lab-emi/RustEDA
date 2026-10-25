// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA Studio contributors

//! Where data files may be tracked (the layout rules and 10.5): design data only as synthetic fixtures.

use super::Report;

/// Synthetic fixtures: the only place for design data (Rule 7.3).
const FIXTURES: &str = "verification/fixtures/synthetic/";

/// Extensions of design data (HDL sources, netlists including AIGER, BLIF and BENCH logic
/// networks, libraries, constraints, parasitics, layouts, tool scripts, waveform and result
/// dumps, binary netlist and library archives): tracked only as synthetic fixtures written by our
/// own writers (Rule 7.3, 10.5). Network-parameter files (`.s<n>p`) and compressed design data
/// (`.gz`, `.xz`, `.bz2`, `.zst`) count too.
const DESIGN_DATA: [&str; 49] = [
    "lef", "tlef", "def", "lib", "liberty", "db", "v", "sv", "vh", "svh", "vg", "sdc", "sdf",
    "spef", "dspf", "spf", "gds", "gds2", "gdsii", "oas", "oasis", "sp", "spice", "cir", "cdl",
    "tcl", "ckt", "va", "vams", "vhd", "vhdl", "vcd", "raw", "prn", "aig", "aag", "blif", "bench",
    "timing", "ops", "rbin", "rlib", "lyp", "lyt", "lydrc", "lylvs", "drc", "lvs", "tech",
];

/// Compression extensions stripped before checking the inner extension.
const COMPRESSED: [&str; 4] = ["gz", "xz", "bz2", "zst"];

/// True for a network-parameter extension `.s<n>p`.
fn network_ext(ext: &str) -> bool {
    let b = ext.as_bytes();
    b.len() >= 3
        && b.first() == Some(&b's')
        && b.last() == Some(&b'p')
        && b.get(1..b.len() - 1)
            .is_some_and(|d| d.iter().all(u8::is_ascii_digit))
}

/// The inner extension of a file name, stripping one compression extension if present.
fn inner_ext(lower: &str) -> &str {
    let Some((stem, ext)) = lower.rsplit_once('.') else {
        return "";
    };
    if COMPRESSED.contains(&ext)
        && let Some((_, inner)) = stem.rsplit_once('.')
    {
        return inner;
    }
    ext
}

/// Applies the data-placement rules to `files`.
pub(super) fn check(files: &[String], r: &mut Report) {
    for f in files {
        let name = f.rsplit('/').next().unwrap_or("");
        let lower = name.to_ascii_lowercase();
        let ext = inner_ext(&lower);
        if (DESIGN_DATA.contains(&ext) || network_ext(ext) || lower.ends_with(".magicrc"))
            && !f.starts_with(FIXTURES)
        {
            r.errors.push(format!(
                "{f}: design data files are allowed only under {FIXTURES}"
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::Tree;

    fn errors(t: &Tree) -> Vec<String> {
        let mut r = Report::default();
        check(&t.files(), &mut r);
        r.errors
    }

    // Rule 7.3, 10.5: libraries, HDL sources, constraints, netlists (AIGER, BLIF and BENCH
    // included), compressed design data and network-parameter files are tracked only as synthetic
    // fixtures, whatever the letter case of the extension.
    #[test]
    fn design_data_only_as_fixtures() {
        let t = Tree::new("data-design");
        t.file("verification/fixtures/synthetic/sta/lib/wt_rich.lib", "x")
            .file(
                "verification/fixtures/synthetic/sta/netlists/s01_chain4.v",
                "x",
            )
            .file("verification/fixtures/synthetic/net/b.s2p", "x")
            .file("crates/formats/rusteda-formats/tests/cells.lib", "x")
            .file("crates/formats/rusteda-formats/tests/cells.lib.gz", "x")
            .file("crates/formats/rusteda-formats/tests/m.v", "x")
            .file("crates/apps/demo-cli/tests/top.sdc", "x")
            .file("docs/user/sta/example.SV", "x")
            .file("crates/verify/demo-fixtures/src/net.s12p", "x")
            .file("crates/verify/demo-defects/src/deck.sp", "x")
            .file("crates/verify/demo-fixtures/src/notes.md", "x")
            .file("crates/verify/demo-fixtures/src/v", "x")
            .file("docs/specs/sta/table.s2x", "x")
            .file("verification/fixtures/synthetic/aig/adder.aig", "x")
            .file("verification/suites/sta/adder.aig", "x")
            .file("crates/engines/demo-engine/tests/mult.AAG", "x")
            .file("docs/user/sta/top.blif", "x")
            .file("crates/engines/demo-engine/src/blif.rs", "x");
        let e = errors(&t);
        assert_eq!(e.len(), 10, "{e:?}");
        for p in [
            "tests/cells.lib",
            "tests/cells.lib.gz",
            "tests/m.v",
            "top.sdc",
            "example.SV",
            "net.s12p",
            "deck.sp",
            "suites/sta/adder.aig",
            "tests/mult.AAG",
            "sta/top.blif",
        ] {
            assert!(
                e.iter()
                    .any(|m| m.contains(p) && m.contains("only under verification/fixtures/")),
                "{p}: {e:?}"
            );
        }
    }
}

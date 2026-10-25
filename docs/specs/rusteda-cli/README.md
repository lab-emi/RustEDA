# The `rusteda` command-line program (`crates/apps/rusteda-cli`)

## Global interface

**RCLI-REQ-0001** The `rusteda` binary provides generic workspace commands (`init`, `import`, `log`, `show`, `diff`, `revert`, `export`, `completions`) with no tool-specific engine or checker dependencies.

**RCLI-REQ-0002** Global options `-w`/`--workspace <DIR>` (required for every subcommand except `completions`), `--format text|json`, `--threads <N>`, `-q`/`--quiet`, `-v`/`--verbose`, `--color auto|always|never`, and `--progress auto|always|never` are accepted before or after the subcommand name; `-V`/`--version` prints `rusteda <version>`.

**RCLI-REQ-0003** Standard input is never read by any `rusteda` command; commands succeed or fail deterministically when spawned with a closed or null standard input stream.

**RCLI-REQ-0004** In `--format json` mode, every invocation prints exactly one `rusteda.v1` envelope on standard output (with keys in contract order and all standard keys present), including usage errors, `--help`, and `--version` (via pre-scanning `--format` before argument parsing).

**RCLI-REQ-0005** In `--format text` mode, mutating commands begin standard output with `rev<N>  <command>  (base rev<M>)` and end with `next: <command>` lines; diagnostics and errors are written to standard error as `<severity>[<code>]: <location>: <message>` with optional `  help: <hint>` lines.

**RCLI-REQ-0006** Every `next_actions` entry in JSON and text output is a canonical command line with words POSIX-quoted (`=`-prefixed words quoted for `zsh`).

**RCLI-REQ-0007** Exit codes follow contract §7: `0` on success, `1` on usage or input/workspace rejection (`USAGE`, `INPUT_REJECTED`, `NOT_A_WORKSPACE`, `WORKSPACE_EXISTS`, `UNKNOWN_REVISION`, `MISSING_TABLE`, `UNKNOWN_HANDLE`, `SCHEMA_TOO_NEW`, `IO_ERROR`, `BACKEND_UNAVAILABLE`), `2` on internal or corruption failures (`INTERNAL`, `CORRUPT_TABLE`), `4` on `REVISION_CONFLICT`, and `130` on `CANCELLED`.

**RCLI-REQ-0008** Every workspace-changing command (`init`, `import`, `revert`) appends a line to `history.jsonl`, including commands rejected by input validation, usage errors on an existing workspace, or `REVISION_CONFLICT`; pure queries (`log`, `show`, `diff`, `export`, `completions`) never write to `history.jsonl`.

## Workspace initialization and import

**RCLI-REQ-0010** `rusteda init -w <DIR>` initializes a new workspace at `DIR` at `rev0`, failing with `WORKSPACE_EXISTS` if `DIR` already contains a `manifest.json`.

**RCLI-REQ-0011** `rusteda import -w <WS> --base-rev <REV> [-m <TEXT>]` commits one new revision on top of `REV` carrying all tables requested in that invocation (`--lib`, `--rtl`, `--netlist`, `--sdc`), applying carry-forward invalidation to dependent tables from the base revision, and failing with `REVISION_CONFLICT` (exit 4) if `HEAD` is not `REV`.

**RCLI-REQ-0012** `rusteda import --lib <FILE> [--corner <NAME>]` is repeatable; each `--corner` applies to the preceding `--lib` (default corner `"default"`). Each library is validated with `rusteda_formats::liberty::read_text_diags`, replaying reader warnings as `L`-series envelope diagnostics and rejecting syntax or model/unit errors (`INPUT_REJECTED` with `file:line:col`). Validated libraries are stored in `cache/<name>-<h16>.lib` and `.rlib` and recorded in `meta.json` `libraries`.

**RCLI-REQ-0013** Within a revision, `(library_name, corner)` is unique, and defining the same cell name twice within one corner is rejected at import with an `L`-series error diagnostic and `INPUT_REJECTED`.

**RCLI-REQ-0014** `rusteda import --rtl <FILE>... --top <NAME> [-D <NAME>[=<VAL>]]... [-I <DIR>]... [-P <NAME>=<VAL>]... [--no-predefined-macros]` resolves the `` `include `` closure of the given Verilog files hermetically (searching only the including file's directory and `-I` directories, with no CWD fallback and no external absolute or parent-escaping include paths), writes the snapshot files and `rtl.json` (`{top, defines, params, files: [{path, hash}], predefined_macros}`) into the `rtl` directory table, and reports preprocessor/expression errors as `V`-series diagnostics with `file:line:col` locations.

**RCLI-REQ-0021** `rusteda import --rtl <FILE>... --top <NAME>` also takes BLIF files (`.blif`) and AIGER files (`.aag`, `.aig`), the format chosen by the extension, ignoring letter case. All files of one import have one format, and AIGER takes one file; otherwise, and when `-D`, `-I`, `-P` or `--no-predefined-macros` are given with BLIF or AIGER files, the import is a `USAGE` error. BLIF files are validated with `rusteda_formats::blif` with `--top` as the top model, and an AIGER file with `rusteda_formats::aiger`, without properties; reader errors reject the import (`INPUT_REJECTED`) with their `B`- and `G`-series diagnostics and locations, and reader warnings are kept as diagnostics. The files are stored in the `rtl` directory table as given, without include closure.

**RCLI-REQ-0022** `rtl.json` records the format of a BLIF or AIGER snapshot in `format` (`"blif"` or `"aiger"`); for SystemVerilog the field is absent, and a manifest without it is SystemVerilog, so that manifests written before the field read unchanged.

**RCLI-REQ-0019** `rusteda import --netlist <FILE>` reads a structural gate-level Verilog netlist, resolves every instantiated library cell and pin against the revision's libraries across all corners (rejecting unknown cells/pins and cross-corner pin-direction conflicts with `V`-series diagnostics and `file:line:col` locations), drops `pg_pin` connections and supply ports with one aggregated `V`-series warning, encodes the frozen netlist into `netlist.rbin` (`NETLIST` v1) with an empty upstream map, and records the source in `meta.json` `sources`.

**RCLI-REQ-0024** `rusteda import --netlist <FILE>` reads a flat netlist with a bounded streaming reader when it can. A netlist that the streaming reader declines is read with the full Verilog reader only when the file is at most `--max-netlist-ast-mib <MIB>` mebibytes (default 64); a larger one is rejected before the full reader runs (`INPUT_REJECTED`, a `V0300` error that names the budget and the option), and HEAD does not move. The budget does not apply to a netlist that the streaming reader takes, and the option requires `--netlist`. The full reader holds the whole syntax tree in memory: its peak resident memory was measured at about 42 times the file size (1.07 GiB for a 26 MiB flat netlist, against 95 MiB with the streaming reader), so the default bounds it near 2.6 GiB. Estimate the memory before raising the budget.

## Queries, revert, export, and completions

**RCLI-REQ-0015** `rusteda log -w <WS> [--limit <N>]` lists committed revisions newest-first with `rev`, `parent`, `command`, `message`, `tables`, and `created_unix_ms`.

**RCLI-REQ-0016** `rusteda show -w <WS> [--rev <REV>]` (default `HEAD`) reports the revision's metadata (`tables` with hashes and upstreams, `libraries`, `sources`, `dropped`, `summary`, `status`), rendering a missing `sta` status as `timing: "not_analyzed"` and a missing `check` status as `equivalence: "unchecked"`.

**RCLI-REQ-0017** `rusteda diff -w <WS> --from <REV> --to <REV>` reports added, removed, changed, and unchanged table hashes, library changes, numeric summary metric deltas, and status transitions between the two revisions.

**RCLI-REQ-0018** `rusteda revert -w <WS> --base-rev <REV> --to <REV>` moves `HEAD` from `--base-rev` to `--to` under the exclusive workspace lock without creating a new revision directory, logging the revert in `history.jsonl`; `rusteda completions <SHELL>` outputs completion scripts for `bash`, `elvish`, `fish`, `powershell`, and `zsh`.

**RCLI-REQ-0020** `rusteda export -w <WS> [--rev <REV>] --netlist <FILE>` (default `HEAD`) thaws the revision's `netlist` table and writes a deterministic structural Verilog netlist (with escaped identifiers per IEEE 1364-2005 §3.7.1) to `<FILE>` without modifying the workspace or appending to `history.jsonl`.

**RCLI-REQ-0023** `rusteda show` reports the design-rule counts of a timing table as recorded when
its summary states `drv_status`; a timing summary without `drv_status` comes from an analysis that
did not check design rules, so `show` reports `drv_status: "unchecked"` and its
`max_trans_violating` and `max_cap_violating` as `null`, never as zero.

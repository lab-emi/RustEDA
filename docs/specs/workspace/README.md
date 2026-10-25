# rusteda-workspace: disk workspace, revisions, rbin containers and envelope

`rusteda-workspace` (`crates/core/rusteda-workspace`) implements the shared disk workspace of the
RustEDA tools: revisions, tables, the `.rbin` binary container, advisory file locks, the commit
protocol, the library cache and the `rusteda.v1` JSON envelope and text-mode renderer.

| Section | Requirements |
|---|---|
| [Workspace layout and files](#workspace-layout-and-files) | WS-REQ-0001 to 0005 |
| [Hashes](#hashes) | WS-REQ-0010 to 0014 |
| [Revisions and handles](#revisions-and-handles) | WS-REQ-0020 to 0026 |
| [rbin container](#rbin-container) | WS-REQ-0030 to 0039 |
| [Workspace JSON schemas](#workspace-json-schemas) | WS-REQ-0040 to 0046 |
| [Locks](#locks) | WS-REQ-0050 to 0052 |
| [Commit protocol](#commit-protocol) | WS-REQ-0060 to 0079 |
| [Library cache](#library-cache) | WS-REQ-0080 to 0082 |
| [Envelope and text mode](#envelope-and-text-mode) | WS-REQ-0090 to 0097 |
| [Determinism](#determinism) | WS-REQ-0100 |
| [Compiled Liberty libraries (`rlib.md`)](rlib.md) | WS-REQ-0110 to 0117 |
| [Netlist tables (`netlist.md`)](netlist.md) | WS-REQ-0120 to 0125 |
| [Timing table: read-only view and builder (`sta.md`)](sta.md) | WS-REQ-0126 to 0137 |

## Workspace layout and files

**WS-REQ-0001** `init` creates `manifest.json` (head `rev0`, `next_rev` 1, corners `[]`), an empty `workspace.lock`, `cache/`, `revisions/rev0000/meta.json` (rev0, parent null, no tables) and the first `history.jsonl` line.

**WS-REQ-0002** `init` fails with `WORKSPACE_EXISTS` when `manifest.json` exists, and logs the rejected command in that workspace when its manifest can be read; revision directories left by an interrupted `init` are set aside as `revisions/.orphan-*`, and other files are kept.

**WS-REQ-0003** `open` fails with `NOT_A_WORKSPACE` when `manifest.json` is missing.

**WS-REQ-0004** Readers accept their major schema version (any minor version, unknown keys ignored), reject a newer major version with `SCHEMA_TOO_NEW`, and report a malformed file or a foreign schema as `CORRUPT_TABLE`.

**WS-REQ-0005** Workspace JSON is written with keys in the contract's order and sorted maps; `manifest.json`, `meta.json` and `ui_state.json` pretty-printed, history lines compact.

## Hashes

**WS-REQ-0010** Hashes are xxh3-64, written `xxh3:<16 lowercase hex digits>`; parsing accepts only that form.

**WS-REQ-0011** The hash of a file table is the xxh3-64 of its bytes, computed streaming.

**WS-REQ-0012** The hash of a directory table is the xxh3-64 over the records (relative path with `/` separators, NUL, file-hash hex, LF) of its files sorted by path bytes; symbolic links and special files are rejected.

**WS-REQ-0013** The hash of the `libraries` pseudo-table is the list hash: xxh3-64 over one record (name, NUL, corner, NUL, hash hex, LF) per library, in list order.

**WS-REQ-0014** The pseudo-table `libraries.<corner>` hashes the records of the libraries of one corner, in list order, and exists only while the corner has a library; a table computed from one corner names it as its upstream, so a library added for another corner does not drop the table.

## Revisions and handles

**WS-REQ-0020** A revision prints as `rev<N>` unpadded, parses with or without zero padding, and lives in `revisions/rev<N zero-padded to at least 4 digits>`.

**WS-REQ-0021** `HEAD` resolves only on the query side (`--rev`, `--from`, `--to`); the base resolver rejects it with `UNKNOWN_REVISION`, and the two resolvers return different types.

**WS-REQ-0022** Object handles are revision-scoped `<prefix><N>` and match `[A-Za-z0-9_-]+`.

**WS-REQ-0023** A revision exists for readers only if its directory has `meta.json` and its number is below `next_rev`; otherwise `UNKNOWN_REVISION`.

**WS-REQ-0024** Every table path read from `meta.json` is one entry of its revision directory (`[A-Za-z0-9._-]`, not starting with `.`, not `meta.json`) and no two tables share one; otherwise the revision is `CORRUPT_TABLE` before any table is opened.

**WS-REQ-0025** Tables, `meta.json`, `manifest.json`, `history.jsonl`, `ui_state.json` and cached libraries are read only as regular files (or directories, for directory tables), checked without following symbolic links before the open and again on the open handle; a FIFO, device, socket or symbolic link, also as an entry of a directory table (where a name that is not UTF-8 is too), is `CORRUPT_TABLE` (exit 2), never `IO_ERROR`, and never blocks a reader; the cache's lookups do not follow symbolic links either.

**WS-REQ-0026** A directory table is read through checked accessors: `Revision::table_files` lists its files with their hashes after checking the directory hash against `meta.json`, and `Revision::read_table_file` reads one file by its `/`-separated relative path (a path with empty, `.` or `..` components is `INTERNAL`) without following symbolic links; `read_table` on a directory table is `INTERNAL`, not `CORRUPT_TABLE`.

## rbin container

**WS-REQ-0030** The rbin header is little-endian: magic `RUSTEDA\0`, kind (8 ASCII bytes, NUL-padded), format_version u32, flags u32 = 0, content_hash u64 (xxh3-64 of `[header_len, EOF)`), section_count u32, upstream_count u32, header_len u64, upstream entries {name [8], hash u64}, section entries {id u32, elem_size u32, offset u64, len u64}.

**WS-REQ-0031** Sections follow the header in strictly increasing id order at 8-byte aligned offsets with zero padding; the writer's bytes depend only on kind, version, upstream entries and sections.

**WS-REQ-0032** Opening an rbin always validates magic, kind, version, flags, header_len, bounds, alignment, `len % elem_size == 0`, strictly increasing (unique) ids and non-overlapping sections; any failure is `CORRUPT_TABLE` (a newer version: WS-REQ-0039).

**WS-REQ-0033** `section::<T>(id)` returns a zero-copy `&[T]` of the table's bytes (the map, or the aligned buffer of a table read into memory); a missing section or an element-size mismatch is `CORRUPT_TABLE`.

**WS-REQ-0034** The content hash is verified on request, and a mismatch is `CORRUPT_TABLE`.

**WS-REQ-0035** Big-endian targets are rejected at compile time.

**WS-REQ-0036** rbin and table files are created new (never overwritten), written completely and fsynced.

**WS-REQ-0037** The only unsafe code is the read-only memory map, in one module with a module-scoped allow and a SAFETY comment. It maps only files the workspace never writes again, through two entry points: tables of committed revisions (`Revision::open_rbin`) and cached compiled libraries (`LibraryCache::open_rlib`, also behind `Revision::open_library`); no public function maps an arbitrary path, and any other file is read into memory (`Rbin::read`).

**WS-REQ-0038** The header of an rbin table records the table's `meta.json` upstream map: one entry per upstream table holding its `meta.json` hash (for an rbin upstream, the file hash), named by the table name when it has at most 8 bytes, else by its first 3 bytes, `.` and 4 hex digits: the low 16 bits of its xxh3-64, plus one (modulo 2^16) while a name before it in the map (short names first, then long ones in byte order) has the result, so the names of one map never collide (`rbin::upstream_names`); `Txn::add_rbin` writes the entries, the other `add_*` methods reject a table whose rbin header differs (`INTERNAL`), and `open_rbin` reports a difference as `CORRUPT_TABLE`.

**WS-REQ-0039** An rbin format version above the reader's range is `SCHEMA_TOO_NEW` (exit 1, "upgrade the tools"); one below it is `CORRUPT_TABLE`.

## Workspace JSON schemas

**WS-REQ-0040** `manifest.json` holds schema, head, next_rev and corners; corners are the corner names of HEAD's libraries in first-appearance order, rewritten at every commit and revert.

**WS-REQ-0041** `meta.json` holds schema, rev, parent, created_unix_ms, tool, tool_version, command, argv, message, tables {path, hash, upstream}, libraries {name, corner, hash, source, compiled}, sources {table, path, hash}, dropped {table, reason}, summary and status, the last two as `{table: {key: value}}` with keys only for tables present (WS-REQ-0045).

**WS-REQ-0042** `history.jsonl` has one compact line per workspace-changing command, rejected and conflicted ones included, with schema, time_unix_ms, tool, tool_version, command, argv, base_rev, new_rev, head_after, ok, exit, error, metrics, metric_deltas; `metric_deltas` as `{table: {key: delta}}` (WS-REQ-0044); a line torn by a crash is skipped and never merges with the next line.

**WS-REQ-0043** `ui_state.json` (reserved) resolves `--target sel<N>`; `UNKNOWN_HANDLE` when the file is absent, holds another selection or belongs to another revision; a `selection_handle` that is not `sel<N>` or a selected instance or path that is not a handle `<prefix><N>` makes the file `CORRUPT_TABLE` when read and is `INTERNAL` when written.

**WS-REQ-0044** `metric_deltas` holds, per table the command changed, its numeric summary values minus those of the previous version: for a commit, each added table against the same table in the base or, if the base lacks it, in the nearest ancestor that has it (at most 64 revisions back, `DELTA_LOOKBACK`); for a revert, each table of both revisions whose hash differs.

**WS-REQ-0045** Summary and status values belong to a table: keys only for tables present, a `meta.json` breaking that rule (a flat `{"equivalence": "unchecked"}`, a dropped table's key) is `CORRUPT_TABLE` with a reason naming the rule; values are set only for a table the transaction adds (else `INTERNAL`), a carried table's values are copied from the base and a dropped table drops them, so no status outlives the table it describes; a property whose table is absent is unchecked (rendered by `rusteda show`/`diff`), so a tool records a property under the table of the command that checks it.

**WS-REQ-0046** `Txn::set_exit` sets the exit code of a command that commits to 0, 3 (negative verdict) or 5 (inconclusive), recorded in the history line with `ok: true`; any other code is `INTERNAL`.

**WS-REQ-0047** Environment sources of a table (`Txn::add_env_source`, `SourceEntry::env_value`) are recorded in `sources` with path `env:<NAME>=<VALUE>` and hash equal to the xxh3-64 of `<VALUE>`; a source file path starting with `env:` is rejected (`INPUT_REJECTED`).

## Locks

**WS-REQ-0050** Reads of `manifest.json` take a shared lock on `workspace.lock` (read access suffices, so read-only workspaces can be queried); init, commit, revert and the logging of rejected commands take an exclusive lock; every lock opens its own file description; a lock wait interrupted by a signal (`EINTR`) starts again.

**WS-REQ-0051** The lock is internal to the crate: every `Workspace` method takes the lock it needs and releases it before it returns, so no caller can hold it across calls.

**WS-REQ-0052** `Workspace::fork(dst, inv)` copies the workspace into a new or empty directory under the shared lock, so the fork holds HEAD and every revision below `next_rev` and no partial commit: revision files and cached libraries hard-linked (copied where links fail), `history.jsonl` and `ui_state.json` copied, staging and orphan directories left out, a history line of its own; a workspace at `dst` is `WORKSPACE_EXISTS`, a non-empty directory `INPUT_REJECTED`.

**WS-REQ-0053** `Workspace::fork` builds the copy in a hidden sibling directory `.{name}.fork-*` and publishes it by an atomic rename onto `dst` (replacing an empty directory when present), rejecting symbolic links, non-directories, mount points, and paths without a final normal component (`INPUT_REJECTED`).

## Commit protocol

**WS-REQ-0060** `begin` compares HEAD with the base revision under the shared lock and fails with `REVISION_CONFLICT` before any work; it logs that failure, like any other of its failures, as a rejected command with its base revision.

**WS-REQ-0061** New tables are written into `revisions/.staging-<pid>-<nanos>/`; an aborted or dropped transaction removes it.

**WS-REQ-0062** `commit` re-checks HEAD under the exclusive lock; on a conflict it deletes the staging directory and logs `ok: false`, exit 4, error `REVISION_CONFLICT`.

**WS-REQ-0063** The new revision number is `max(next_rev, 1 + highest revision directory)`; revision directories at or above `next_rev` (left by an interrupted commit) are renamed to `.orphan-*`.

**WS-REQ-0064** Carry-forward: the new revision starts from the base's tables; a carried table survives only if each upstream hash equals that table's hash in the new revision, otherwise it is dropped and listed in `dropped` with the reason; repeated to a fixpoint.

**WS-REQ-0065** Carried tables are hard-linked from the base revision; a copy is the fallback, recorded nowhere.

**WS-REQ-0066** Durability order: files and directories fsynced, staging renamed to `rev<NNNN>`, `revisions/` fsynced, `manifest.json` replaced via temporary file, fsync, rename and directory fsync, history line appended and fsynced (and its directory, when the file is new), then unlock.

**WS-REQ-0067** Committed revisions are immutable: no file of a committed revision is written again.

**WS-REQ-0068** `revert` takes the exclusive lock, checks HEAD, sets HEAD to the target, logs a history line and creates no revision; a damaged `meta.json` of the old HEAD does not block it.

**WS-REQ-0069** Every workspace-changing command gets one history line: `init`, `fork`, `commit` and `revert` log their own outcome, success or failure, and `begin` its failure, a `REVISION_CONFLICT` included; the caller logs only failures before `begin` (`log_rejected`) and between `begin` and `commit` (`Txn::reject`).

**WS-REQ-0070** Entries of `revisions/` starting with `.staging-` or `.orphan-` are ignored.

**WS-REQ-0071** Every upstream hash of a table added by a transaction must hold in the new revision; otherwise the commit fails with `INTERNAL`.

**WS-REQ-0072** Table names match `[A-Za-z0-9_-]+` and are not `libraries`; table paths are single entries of the revision directory, not `meta.json`; a staging directory holds only the transaction's tables.

**WS-REQ-0073** A library entry has a cache name (1 to 128 characters of `[A-Za-z0-9._+-]`, not starting with `.`) and a corner of `[A-Za-z0-9_-]+` (else `INPUT_REJECTED`), its `compiled` is its cache path `cache/<name>-<h16>.rlib` (else `INTERNAL`), and (name, corner) is unique within a revision; an entry of `meta.json` that breaks this is `CORRUPT_TABLE`.

**WS-REQ-0074** Sources of carried tables are carried; sources of new tables are recorded by the transaction.

**WS-REQ-0075** While a transaction is open, `Txn::upstream` gives the hashes the named tables will have in the new revision; a table the transaction drops is reported as `MISSING_TABLE` with the drop reason.

**WS-REQ-0076** The rename of `manifest.json` is the commit point of `init`, `fork`, `commit` and `revert`: a failure before it is an error and leaves HEAD unchanged; after it the result is a success, a failed directory fsync or history append is a warning of it (`W0102`, `W0101`), and no failure line is written.

**WS-REQ-0077** A staging file is written by the tool or is a hard link to a file of the base revision: `add_file` and `add_dir` refuse (`INTERNAL`) a regular file with more than one link whose (device, inode) is not that of a file of the base revision, so no name outside the revisions reaches the bytes of a committed table; directory tables hold regular files and directories with UTF-8 names only (else `INTERNAL`).

**WS-REQ-0078** A table that enters a revision as an rbin file (`add_file`, `add_bytes`: its bytes start with the magic) must pass the checks of every open except kind and version, which the tool owns, and its content hash; otherwise `INTERNAL`, and nothing is committed. `add_file` reads the file once for both hashes.

**WS-REQ-0079** `Txn::reject` writes the history line before it removes the staging directory; `abort`, `reject` and a dropped transaction remove read-only subdirectories too, and a staging directory that cannot be removed stays behind as `.staging-*`, ignored by readers (WS-REQ-0070), without costing the command its line.

## Library cache

**WS-REQ-0080** `cache/<name>-<h16>.lib` holds the library verbatim and `cache/<name>-<h16>.rlib` its compiled form; both are written atomically (temporary file, rename), the `.rlib` only as a valid rbin file (structure and content hash; else `INTERNAL`, from `put_library` and `recompile` alike), and nothing is compiled when both exist as regular files and the `.rlib` is valid; an invalid or symlinked `.rlib` is compiled again.

**WS-REQ-0081** A cached library is found by name and hash, or by hash alone, as two regular files.

**WS-REQ-0082** `LibraryCache::open_rlib(name, hash, kind, versions)` maps a cached `.rlib` and validates its header; `Revision::open_library(entry, kind, versions)` maps the compiled form of one of the revision's libraries (another entry is `INTERNAL`).

**WS-REQ-0083** `LibraryCache::put_library` and `recompile` validate that the compiled `.rlib` matches the requested rbin `kind` and `versions`; a cached `.rlib` of an older format version or another kind is recompiled from the cached `.lib`, whereas a newer format version fails with `SCHEMA_TOO_NEW`.

**WS-REQ-0084** `LibraryCache::open_rlib_or_recompile` and `Revision::open_library_or_recompile` open a compiled library and automatically recompile it once from the verbatim cached `.lib` when the `.rlib` is missing, damaged, or of an older format version.

## Envelope and text mode

**WS-REQ-0090** The envelope has the keys schema, ok, command, workspace, base_rev, rev, new_rev, scope, completeness, metrics, tool-specific fields in insertion order, diagnostics, error, next_actions; standard keys are always present; scope, completeness and metrics are JSON objects, set through setters that refuse anything else (`INTERNAL`).

**WS-REQ-0091** Nested envelope values keep the key order of the struct, `JsonObject` or text they were made from, also as fields of a serialized struct (`JsonObject` and `RawJson` implement `Serialize` in that order, numbers written from their values); maps (`BTreeMap`, `JsonMap`, `serde_json::Value`) serialize their keys sorted, a `HashMap` in arbitrary order; an object with two equal keys (compared unescaped) or a lone surrogate escape is refused (`INTERNAL`).

**WS-REQ-0092** Envelopes are printed pretty, in the layout of `serde_json::to_string_pretty` (two-space indents, every array element on its own line, numbers as serialized), or compact; plan Example 1 is illustrative in its whitespace and number text, binding in its keys, their order and their values.

**WS-REQ-0093** Error codes and exit codes: USAGE, INPUT_REJECTED, NOT_A_WORKSPACE, WORKSPACE_EXISTS, UNKNOWN_REVISION, MISSING_TABLE, UNKNOWN_HANDLE, SCHEMA_TOO_NEW, IO_ERROR, BACKEND_UNAVAILABLE exit 1; REVISION_CONFLICT 4; INTERNAL, CORRUPT_TABLE 2; CANCELLED 130; negative verdict 3 and inconclusive 5 with `ok: true`.

**WS-REQ-0094** A diagnostic is {severity, code, location {file, line, col} or null, message, help}; its code is a `DiagCode`, a letter of `ABCDGLSTVW` (WS-REQ-0190) and four digits, checked when it is made (at compile time for a constant, `INTERNAL` for text), also when a diagnostic is read back.

**WS-REQ-0095** Text mode: a mutation starts with `rev<N>  <command>  (base rev<M>)`, follow-ups are `next: <command>` lines, diagnostics go to stderr as `<severity>[<code>]: <location>: <text>` plus `  help: ...`.

**WS-REQ-0096** `next_actions` are POSIX-quoted command lines; words starting with `=` are quoted.

**WS-REQ-0097** Diagnostic codes of the crate use the letter W, for errors about the workspace only: `W0002` INPUT_REJECTED, `W0003` NOT_A_WORKSPACE, `W0004` WORKSPACE_EXISTS, `W0005` UNKNOWN_REVISION, `W0006` MISSING_TABLE, `W0007` UNKNOWN_HANDLE, `W0008` SCHEMA_TOO_NEW, `W0009` IO_ERROR, `W0010` REVISION_CONFLICT, `W0012` CORRUPT_TABLE (`ErrorCode::diag_code`); USAGE, INTERNAL, CANCELLED and BACKEND_UNAVAILABLE are the application's errors and print with the application's `A` code (`Error::to_diagnostic(app_code)`). Text mode prints an error as the diagnostic `error[<code>]: <message>` plus its help, while JSON keeps the envelope code; `W0101` (history line not written) and `W0102` (manifest not durable) are warnings of a completed command; `W0200` and above are left to the `rusteda` command line.

## Determinism

**WS-REQ-0100** Identical inputs give byte-identical rbin files, `manifest.json` and `meta.json` (except `created_unix_ms`).

**WS-REQ-0190** The area letters of diagnostic codes (WS-REQ-0094) include `B` for the BLIF reader (BLIF-REQ-0062) and `G` for the AIGER reader (AIGER-REQ-0032), so that their messages keep their codes in envelopes: `DiagCode::LETTERS` is `ABCDGLSTVW`.

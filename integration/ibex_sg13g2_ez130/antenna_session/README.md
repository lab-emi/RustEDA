# Single-process antenna repair

Freeze this helper set for each run with `freeze.py --workspace DEST --base-rev
empty`. Bind the resulting manifest, runner, legacy parser, field reader and
verifier in the backend inputs; add the frozen `antenna-session-policy.json` as
`files.antenna_session_policy`. The explicit policy selects this protocol.

The runner invokes one `repair-antenna-groups` process. Its lineage distinguishes
that capture from typed in-process operations. The independent verifier imports
no controller implementation. Its v3 config binds `controller_inputs` through a
producer FileId; a lineage cannot choose its own unpinned policy or workspace.

Lineage v3 (RustROAD summary v1) retains every raw report and one real revision
per mutating attempt. Lineage v4 (RustROAD summary v2) records the same attempts
as logical revisions: the session edits routes in memory, validates the final
state once and commits only that revision, so only the initial and final
revisions exist on disk (`materialized_revisions`). Each logical revision is
bound by the distinct physical-source fingerprint its check reports name.
Check reports hold only their violated rows (`check_rows: violated`); coverage
rows, worst-net lists and verdict counts still describe every evaluated row,
and the verifier checks them against that inventory. A failing v4 session
commits nothing. Re-freeze the helper set after updating the RustROAD binary.

The original grouped flow remains available without the session policy. Native
antenna status covers the supported subset; complete foundry antenna, DRC, LVS,
timing and equivalence gates remain required.

Frozen run16 replay (lineage v3): all 439 raw reports and 52 revisions match, including final
design, inventory and model hashes. The independent verifier passed the real
lineage and 42 rejection controls. These are replay gates, not a new full-flow
qualification. Resource evidence is recorded in the superproject handoff.

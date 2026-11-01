# Single-process antenna repair

Freeze this helper set for each run with `freeze.py --workspace DEST --base-rev
empty`. Bind the resulting manifest, runner, legacy parser, field reader and
verifier in the backend inputs; add the frozen `antenna-session-policy.json` as
`files.antenna_session_policy`. The explicit policy selects this protocol.

The runner invokes one `repair-antenna-groups` process. Its lineage distinguishes
that capture from typed in-process operations and retains all original raw
reports and real revisions. The independent verifier imports no controller
implementation. Its v3 config binds `controller_inputs` through a producer
FileId; a lineage cannot choose its own unpinned policy or workspace.

The original grouped flow remains available without the session policy. Native
antenna status covers the supported subset; complete foundry antenna, DRC, LVS,
timing and equivalence gates remain required.

Frozen run16 replay: all 439 raw reports and 52 revisions match, including final
design, inventory and model hashes. The independent verifier passed the real
lineage and 42 rejection controls. These are replay gates, not a new full-flow
qualification. Resource evidence is recorded in the superproject handoff.

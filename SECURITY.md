# Security policy

RustEDA Studio reads workspaces and RTL files that may come from untrusted sources. Readers are
expected to reject malformed input with a diagnostic and never panic, hang or exhaust memory
without bound; the workbench writes no file but the shared selection `ui_state.json`.

To report a vulnerability, contact the maintainers privately (see the repository profile) and
include a minimal input that triggers the problem. Please do not open a public issue for
security reports. We aim to acknowledge reports within seven days.

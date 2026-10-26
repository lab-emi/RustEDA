# Synthetic fixtures

Inputs of the tests of the shared crates (`rusteda-formats`, `rusteda-cli`), which read them from
`verification/fixtures/synthetic/` of the repository they run in. They are copied unchanged from
the tool repositories of the suite (the RTL files with this repository's copyright line), and
re-copied with the shared crates. Everything here is synthetic: invented cells, values and
designs that model no real process or product.

| Directory | Contents |
|---|---|
| `liberty/` | Liberty libraries exercising the extensions of the Liberty reader |
| `sdc/` | SDC files exercising the SDC reader and its Tcl subset |
| `rtl/` | a small RTL design with an include file, for the RTL import of `rusteda import` |
| `synth/lib/` | the synthetic cell libraries the command-line tests import |

The workbench's own tests build their workspaces in memory with the shared crates and use none
of these files.

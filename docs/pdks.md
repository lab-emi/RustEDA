# PDK and SRAM setup

RustEDA uses external PDK data, which is not distributed or relicensed by this
repository. The local installations below are under `~/pdks`:

| Process | Root | Library selection |
| --- | --- | --- |
| SKY130A | `~/pdks/sky130A` | `./rusteda library sky130_fd_sc_hd --corner tt` |
| GF180MCU-D | `~/pdks/gf180mcuD` | `./rusteda library gf180mcu_fd_sc_mcu7t5v0 --corner tt` |
| IHP SG13G2 | `~/pdks/ihp-open-pdk` | `./rusteda library sg13g2_stdcell --corner tt` |
| IHP SG13G2 / EZ130 8T | `~/pdks/ez130_8t_v1p1` | `./rusteda library ez130_8t --corner tt` |

The SKY130 and GF180 installations use Ciel/open_pdks revision
`f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7`. IHP is v0.3.0 at commit
`5cccb161f7492697cfa52eb14dc03beb00bdca9e`; EZ130 is v1.1. The selector
checks [pinned Liberty hashes](../integration/library-catalog.json). It covers
TT/SS/FF for HD, HDLL, HS, MS, LS, GF180 7T and 9T, IHP and EZ130; SKY130 LP
has SS/FF, and HVL has TT. A missing corner is an error, not a substituted model.
Other voltage and temperature corners remain available in the PDK directories.

The default roots can be changed with `--root` or `RUSTEDA_SKY130_ROOT`,
`RUSTEDA_GF180_ROOT`, `RUSTEDA_IHP_ROOT`, and `RUSTEDA_EZ130_ROOT`, respectively.
These commands select data for direct engine invocations; they do not alter the
separately pinned RustEDA flow physical-flow manifest or its qualification.

## SRAM collateral

```sh
./rusteda sram sky130 --corner tt
./rusteda sram sky130 --source openram --corner tt
./rusteda sram gf180 --corner ss
./rusteda sram ihp --corner ff
```

Each command reports a representative macro's installed views and SHA-256
identities. `--macro NAME` selects another macro in the same library, and
`--root DIR` overrides that library's root. Missing views or corners are errors.
The printed hashes identify files but are not independent verification.

- SKY130: `~/pdks/sram-libraries/sram22-sky130` contains published SRAM22
  macros. GDS files are compressed; decompress a selected `.gds.gz` before a
  consumer requiring plain GDS. The provider reports silicon measurements for
  named configurations at 1.8 V and 25 MHz; this does not certify new generated
  configurations or other operating conditions. Its catalog also warns about
  SPICE includes and some Verilog/LEF write-mask interface differences.
  A freshly generated `sram22_64x32m4w8` GDS did not have the same SHA-256 as
  the published catalog GDS, so its name alone does not bind it to the
  provider's silicon-test evidence.
  The Ciel PDK also contains prebuilt OpenRAM macros under
  `sky130A/libs.ref/sky130_sram_macros`; the selector's TT/SS/FF choices for
  these use their 1.8 V, 25°C models, which are not the same PVT points as
  the selected standard-cell SS/FF corners.
- GF180: `~/pdks/gf180mcuD/libs.ref/gf180mcu_fd_ip_sram` contains four
  foundry prebuilt SRAM macros with multi-corner views.
- IHP: `~/pdks/ihp-open-pdk/ihp-sg13g2/libs.ref/sg13g2_sram` contains prebuilt
  one- and two-port macros. Functional simulation requires the companion
  behavioral file reported by the selector. The open PDK is a preview.

`~/pdks/sram-compilers/sram22-install/bin/sram22` is the open SRAM22 generator
for SKY130. Its open build generates interpolated Liberty timing; commercial
characterization and Calibre verification are separate. OpenRAM 1.2.48 is
installed with the SKY130 SRAM build space and can be run through
`~/pdks/sram-compilers/openram/bin/openram`; see its included README and
`sky130-install.log`. The launcher sets `OPENRAM_HOME`, `OPENRAM_TECH`, and a
default `PDK_ROOT`. OpenRAM's public
documentation does not support GF180 SRAM generation, and the installed IHP and
GF180 macro libraries are prebuilt IP rather than open SRAM compilers.

None of these selectors proves macro pin consistency, timing accuracy, DRC,
LVS, or RustEDA RTL-to-GDS integration. The qualified `./rusteda flow` remains
limited to its existing SKY130/GF180 small-block cases without SRAM macros.

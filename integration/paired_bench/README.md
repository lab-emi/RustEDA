# Paired elapsed-time benchmark

`bench.py` compares RustEDA with LibreLane on one design and PDK under a protocol that
answers three questions a single timed pair cannot:

1. **Was the reference flow given the same tuning opportunity?** The plan lists every
   LibreLane arm as an overlay on the published recipe and fixes the selection rule before
   any sample runs. The first mutating command freezes the plan in the workspace; a later
   command with different plan bytes stops with `PLAN_CHANGED`.
2. **How long does each flow take to reach the same signoff, not just a GDS?** Every sample
   runs its producer and then the shared gates (`../ibex_host_benchmark/signoff_layout.py`)
   on that producer's own layout, in two scopes with the same CPUs, memory and zero swap.
   Each endpoint is the producer time plus the wall time of the endpoint's gates. If any of
   those gates fails or cannot render a verdict, the endpoint has no time.
3. **Is the lead stable?** The confirmation phase repeats the selected LibreLane arm and
   RustEDA in alternating AB/BA blocks after a quiet-host gate. A lead is reported only when
   every reference sample is slower than every candidate sample over at least three blocks.

## Endpoints

| Endpoint | Gates |
| --- | --- |
| `gds` | none: producer start to final GDS |
| `physical_signoff` | foundry DRC, antenna DRC, latch-up, device LVS |
| `common_signoff` | physical gates, OpenRCX extraction, TT/SS timing with electrical limits, vendor simulation, density |
| `full_signoff` | common gates and logic equivalence |

On SKY130A and GF180MCU D the gates are those of `integration/ibex_pdks/gates` (`signoff_layout.py
--profile`): there is no separate latch-up gate, because the tap-distance rules are part of the
foundry DRC, so `physical_signoff` is DRC, antenna and LVS.

The logic gate cannot judge LibreLane's IHP netlists yet: Yosys renames 431 flip-flops and
merges duplicated registers, and the checker requires a unique named RTL counterpart for each
state element. That gate then reports `unverified`, which is a checker limitation, not a
LibreLane failure. `common_signoff` therefore excludes it, and `full_signoff` reports it.

## Verdicts

`report.json` gives, per endpoint, every sample's seconds, the median, range and spread for
each arm, the paired ratio for each block, the ratio of medians, and a conservative range
(fastest reference / slowest candidate to slowest reference / fastest candidate).

| Verdict | Meaning |
| --- | --- |
| `candidate_faster_in_every_sample` | at least three blocks and the conservative range is above 1 |
| `reference_faster_in_every_sample` | at least three blocks and the conservative range is below 1 |
| `overlap_lead_not_established` | the samples overlap |
| `insufficient_repeats` | fewer than three blocks |
| `not_reached` | some samples did not reach the endpoint; no ratio is given |

Each sample also records the quiet-gate outcome, foreign CPU on the host and on the allowed
CPUs, package temperature and throttle time, cgroup memory and swap peaks, and output
identities. GDS identities are also compared with date records masked. Samples with foreign
load above the plan's flags, swap use or a quiet-gate timeout are listed under `flags`.

## Use

```bash
python3 integration/paired_bench/bench.py check --plan integration/paired_bench/plans/ihp-same-signoff.json --workspace .work/paired-bench-20261117/ihp
```

```bash
integration/paired_bench/run_campaign.sh integration/paired_bench/plans/ihp-same-signoff.json .work/paired-bench-20261117/ihp
```

`check` verifies every pinned input, renders each LibreLane configuration and its difference
from the baseline arm, prints the schedules and refuses a run whose memory or disk budget does
not fit (at least 100 GiB must remain free). `run_campaign.sh` holds `.work/scale.lock` and
runs screening, `select`, confirmation and `summarize` one sample at a time. A rerun resumes
it. Mutating commands take `--base-rev` (the workspace `HEAD`, `empty` at first) and refuse
a partial sample unless `--discard-partial` moves it aside.

Two operations change a frozen workspace, and `report.json` lists both:

- `amend --reason` may add arms and append screening samples before the selection. Every
  field already declared stays identical: resources, gates, endpoints, the rule, existing arms
  and the confirmation phase. The previous plan is kept as `plan-v<n>.json`.
- `invalidate --sample --reason` moves a sample aside as `<id>.invalid-<n>` so it can rerun.
  It is meant for harness defects. The moved sample is kept.

`tests/test_paired_bench.py` runs the whole sample path with fake producers and gates.

## Plans

`plans/ihp-same-signoff.json` compares the IHP SG13G2 Ibex arms on CPUs 24–27 with 4 GiB and
no swap:

| Arm | Change from the published LibreLane recipe |
| --- | --- |
| `librelane-recipe` | none; must render byte-for-byte JSON-equal to the published 7.73× configuration |
| `librelane-antenna-default` | GRT and DRT antenna repair back to LibreLane defaults (margin 10, three iterations each), as in the SKY130 and GF180MCU references |
| `librelane-defaults` | the antenna defaults, plus LibreLane's default post-GRT resizer setup and hold margins and TNS setting |
| `librelane-drt-jumpers` | the antenna defaults with jumpers instead of diodes after detailed routing (added by amendment) |
| `rusteda` | the qualified `profile-aligned-power.json` manifest, unchanged |

The selected LibreLane arm has the fewest seconds to `common_signoff`. If no arm reaches it,
the selection is the fastest GDS among arms that pass every physical gate. Screening samples
are not reused in the confirmation blocks.

`plans/sky130-same-signoff.json` (CPUs 30–31) and `plans/gf180-same-signoff.json` (CPUs 22–23)
apply the same protocol to the SKY130A and GF180MCU D pairs, with 4 GiB and no swap. Their
published LibreLane configurations already use LibreLane's defaults for the IHP-screened knobs,
so the screen runs the opposite direction:

| Arm | Change from the published LibreLane configuration |
| --- | --- |
| `librelane-recipe` | none; must render JSON-equal to the published configuration |
| `librelane-antenna-tuned` | the IHP recipe's antenna repair: GRT and DRT margin 50, 6 GRT and 4 DRT iterations |
| `librelane-closure-tuned` | that antenna repair plus the IHP recipe's post-GRT resizer setup and hold margins (1.0 and 0.2 ns) and full TNS repair |
| `librelane-drt-jumpers` | jumpers instead of diodes after detailed routing |
| `rusteda` | the published producer, unchanged (below) |

The SKY130A gates are the tracked `integration/ibex_pdks/sky130hd/gates.json`. The GF180MCU D
gates are `.work/three-pdk-20261114/gf180/matched-gates/gates.json`, the profile behind the
published GF180 verdicts: the tracked `gf180mcu/gates.json` pins rule decks of an older PDK build
that the local PDK tree no longer has.

On GF180MCU D every LibreLane arm ends in the floating-fill emitter that the RustEDA producer
runs (`integration/ibex_pdks/gf180mcu/native/floating_fill.py`, the `-fill` arm names), on its
own final GDS and DEF and inside the timed producer, so both flows are judged on density with
fill. Both pass `below_target=emit`: on LibreLane's layout the top metal reaches 26 % of the die,
below the emitter's 30 % target, and the foundry density rules decide. The fill is GDS geometry
only; OpenRCX, the timing gate and the power figures do not see it.

The SKY130A and GF180MCU D LibreLane arms run with `TMPDIR=/tmp`, where the published runs kept
Yosys's ABC temporary files. With `TMPDIR` on the ext4 `/home` volume, Yosys 0.70's parallel ABC
mapping hung in all three runs: every ABC process had written its result and waited for the
next command, while Yosys waited for their replies. With `/tmp` (tmpfs), synthesis finished in
92 s and wrote the published netlist. The IHP arms use Yosys 0.62, which does not hang: in a
control run, moving its temporary files to `/tmp` changed synthesis time by under 0.5 s and
wrote the same netlist (`docs/benchmarks/evidence/ibex-three-pdk/ihp-tmpdir-synthesis.json`).

### Producers

The published SKY130A and GF180MCU D RustEDA samples ran one-off recipes that write into their
own `.work` directories. `producers/` runs each into the sample directory instead:

- `producers/sky130_rusteda.py --out DIR` copies every input the 1,675.78 s SKY130A run (r02)
  had at launch, checked against r02's own pin lists, into DIR and replaces only the lines that
  derived the repository and campaign directories from the script's location (six one-line
  edits, each asserted to occur once, recorded in `DIR/port.json`). The stages, binaries,
  arguments and deadlines are r02's. The run requires CPUs 30 and 31, as r02's did.
- `producers/gf180.py rusteda --out DIR` copies the 637.13 s GF180MCU D run's three launch
  inputs unchanged (they write next to themselves) and replaces its scratch fill scripts with
  the tracked emitter, which reproduces that run's filled GDS after masking dates. The run
  requires CPUs 22 and 23. `producers/gf180.py librelane --run RUN --out DIR -- ARGV` runs
  LibreLane, then the same emitter.

Each RustEDA arm lists the published output hashes in `expected_outputs`, so every sample
records whether it reproduced them.

<!-- SPDX-License-Identifier: Apache-2.0 -->
# SKY130 horizontal V3 plan generator

This Linux helper uses Python 3.11 or later and RustROAD's `antenna-inventory` and
`check-antenna-batch` commands. It reads a routed SKY130 Ibex workspace and
produces a `rustroad.antenna-via-offsets.v1` plan for moving lower V3 cuts along
the original M3 tracks while retaining the V4 sites and M5 geometry. Candidate
generation uses native cut-weight checks, route geometry, technology and actual
power routing. Its output is a typed proposal and the evidence for its selection.

Provide a producer JSON receipt with an `artifacts` object containing `gds`,
`def` and `netlist` records. Each record gives `path`, `sha256` and `bytes`.
Pass the matching routed format-six workspace and its routing configuration.
The supplied sidewall model is for the pinned SKY130A HD technology LEF
recorded in that model. The scout uses a fixed 2,048-network batch size, a
maximum of 64 modeled V3/V4 sites and displacements of one or two M4 tracks.
The threshold in the command is the one used on the grouped Ibex observation.

Set the variables to local paths. Use a new output directory outside the
source workspace and two unoccupied cores in the run scope.

```bash
runner_dir=/path/to/horizontal-v3-selector
road_bin=/path/to/rustroad
routed_workspace=/path/to/routed-workspace
producer_receipt=/path/to/producer.json
routing_configuration=/path/to/routing.json
plan_output=/path/to/new-output
core_pair=20,21

native_hash="$(sha256sum "$road_bin" | cut -d' ' -f1)"
systemd-run --user --scope -p "AllowedCPUs=$core_pair" -p MemoryMax=4G \
  -p MemorySwapMax=0 timeout --signal=TERM --kill-after=10s 210s \
  env PYTHONDONTWRITEBYTECODE=1 python3 "$runner_dir/generate.py" \
  --workspace "$routed_workspace" --producer "$producer_receipt" \
  --routing-config "$routing_configuration" --out "$plan_output" \
  --native "$road_bin" --native-sha256 "$native_hash" \
  --model "$runner_dir/sky130-contact-sidewall-axes.json" \
  --model-sha256 17a51dd593c66c8fc6ff3e1d32f9c9c86e32c98f288a652b0a27cebd6571dd8e \
  --threshold 0.9785932721712537
```

The output plan is `scout/lower-offset-plan.json`. `result.json` binds the
input and output identities and records stage times. The model path remains
bound to the exact literal path recorded by RustROAD. Moving the same model
bytes to a new path requires freshly generating the plan at that path.

The plan contains static proposals. The RustROAD hard validator and
independent physical and electrical checks determine which edits qualify.

## Ranked metal plan

On a routed SKY130 input, `ranked_metal.py` uses the same native batch
checker to score partial metal violations from local interconnect through
M4. It takes the largest eligible ratio for each net at
`other_native = 1.0261111111111112`. It ranks descending by score, breaks
ties by canonical name and sorts names within each consecutive pair.

Use the same resource scope as above and a separate output directory.
The ranked metal selector uses the native tool and model flags from the
horizontal selector, omitting the routing configuration and threshold
flags. The threshold is fixed for this recipe.

```bash
metal_output=/path/to/new-metal-output
systemd-run --user --scope -p "AllowedCPUs=$core_pair" -p MemoryMax=4G \
  -p MemorySwapMax=0 timeout --signal=TERM --kill-after=10s 210s \
  env PYTHONDONTWRITEBYTECODE=1 python3 "$runner_dir/ranked_metal.py" \
  --workspace "$routed_workspace" --producer "$producer_receipt" \
  --out "$metal_output" --native "$road_bin" --native-sha256 "$native_hash" \
  --model "$runner_dir/sky130-contact-sidewall-axes.json" \
  --model-sha256 17a51dd593c66c8fc6ff3e1d32f9c9c86e32c98f288a652b0a27cebd6571dd8e
```

The generated `plan.json` is a one-round
`rustroad.antenna-group-repair.v1` plan. Its model and report fingerprint
are bound to the just-completed native batch. The plan names a separate
`checked` directory for a later repair transaction.

## Apply each plan on its source revision

Use `repair-antenna-groups` with the grouped plan and the revision recorded
by the metal selector.

```bash
"$road_bin" --workspace "$routed_workspace" repair-antenna-groups \
  --base-rev "$routed_revision" --plan "$metal_output/plan.json"
```

Export the resulting GDS, DEF and netlist and supply their identities in
a producer receipt for the new revision. Run the horizontal generator
on that revision and its matching routing configuration. For the local
repair used in the benchmark, enable bounded pad-gap fill before the full
geometry check.

```bash
jq '. + {fill_aligned_pad_notch: true, eager_lower_pad_gap_fill: true}' \
  "$plan_output/scout/lower-offset-plan.json" > "$plan_output/repair-plan.json"
"$road_bin" --workspace "$routed_workspace" repair-via-offsets \
  --base-rev "$grouped_revision" --plan "$plan_output/repair-plan.json"
```

Run each repair in the same guarded resource scope. The RustROAD commands
acquire the workspace lock and bind proposals to the recorded route and
model. Export the committed revision before running the independent gates.

# Bounded aggregation of coverage hierarchy dependencies

The T1 final-layout coverage attempt stopped before geometry with
`COVERAGE_BUDGET:hierarchy_edges`: the old hierarchy stored one adjacency tuple
for every raw instance record, exhausting250,000 entries. The failed attempt and
all its source/config/report files remain immutable. Its independent foundry
maximal pass is not a coverage certificate.

The hierarchy now stores one dictionary entry for each **distinct parent/child
cell pair**, summing that pair's occurrence multiplicities. Every raw instance
record is still read and its transform, child and array multiplicity validated.
No cell, layer, excluded subtree, unreachable component or transform is pruned.
Native recursive geometry traversal is unchanged and still processes all actual
instance transforms. Aggregation changes count representation, not geometry.

## Explicit budget-scope derivation

No CEILINGS value or configuration field is raised or added. The work receipt
states the following scopes explicitly:

| Counter | Bound | Meaning |
| --- | --- | --- |
| `cells` | existing16,384 | All native cell records, including unreachable cells. |
| `hierarchy_edges` | existing250,000 | Distinct stored parent/child dependency pairs; charged before a new dictionary entry. |
| `hierarchy_instance_records` | existing `instance_visits` ceiling4,000,000 | Separate raw-record scan count, charged before inspecting each instance. This is not the expanded-count counter. |
| `instance_visits` | existing4,000,000 | Exact expanded full-hierarchy occurrences including the root. |

This is a declared budget-scope change from raw adjacency storage to aggregated
dependency storage, with a newly visible scan counter derived from the existing
visit ceiling. It does not label a raw scan as free, overwrite it with expanded
counts, or claim old/new `hierarchy_edges` work values mean the same thing.
`hierarchy_budget_scope` in every result describes the changed representation.
Input config shape and all numerical ceilings stay unchanged. A raw-record,
dependency, cycle, expanded-count or later geometry failure remains unverified.

Each raw multiplicity is positive and at most the existing visit ceiling L.
At most L raw records are read, so a stored edge sum is bounded by L²
(16,000,000,000,000, fewer than44 bits at the existing ceiling). Check the sum
before addition. Do **not** cap the sum at L: an unreachable parent can have
several valid arrays whose combined multiplicity exceeds L without contributing
any reachable occurrence. During reachable DP, compare the multiplier to
`(L-current_child_count)//parent_count` before multiplication; accepted count
values always remain at most L. The final sum of full counts is also checked.

Kahn indegrees increase and decrease once per distinct dependency, regardless
of multiplicity. Since all multiplicities are positive, deduplication preserves
the underlying directed graph and all cycles, including unreachable cycles.
The DP identity is `sum(count[parent]*m_i)=count[parent]*sum(m_i)`, with exact
nonnegative integers. Full counts always include macro well subtrees; selected
counts stop propagation only when the original parent is excluded. Other paths
to the same child remain represented. No startpoint/path list is materialized.

## Upfront memory and time estimates

Hierarchy allocation is O(cells+distinct_edges), time is O(raw_records+cells+
distinct_edges). Raw instance wrappers are transient, never retained in a list.
A conservative Python hierarchy estimate is:

- 250,000 stored dependencies at240 B/edge, including integer keys/values,
  dictionary occupancy and old/new resize headroom:60,000,000 B.
- 16,384 cells at1,024 B/cell for native wrapper references, cell/edge/count maps,
  indegrees, queue/order and bookkeeping:16,777,216 B.
- 16 MiB transient interpreter/bookkeeping margin:16,777,216 B.

Total93,554,432 B is below128 MiB. Exact Python allocator and native cell-wrapper
costs remain subject to the process guard; this is not a generic admission proof
for KLayout Boolean intermediates. Existing placed geometry measured about0.9 GiB;
new final-layout geometry remains unchecked. Preserve the unchanged4 GiB RAM,
1 GiB swap and600 s real-check guard and GNU-time peak measurement. No cap is
increased if a later geometry stage cannot fit.

Tiny controls estimate<64 MiB, one worker, at most80 six-node DAGs with an
independently bounded explicit occurrence oracle (4096 visits/pending entries,
strict increasing node IDs prevent cycles). Other controls stream at most13 raw
records and use small configurable limits; none creates4M records or250k edges.
Run them only on CPU10,12 under1 GiB/60 s with GNU time. They cover parallel edges,
mixed arrays/singletons, all-transform inspection, reconvergence and exclusions,
cycles, unreachable invalid records, unique/raw/expanded budget boundaries and
unreachable multiplicity sums above the expanded cap.

The22 tiny controls passed under1 GiB/256 MiB swap/60 s onCPU10,12:
0.08 s whole test process,25,792 KiB peak RSS (test body0.037 s). A separate
bounded syntax/source check confirmed identical CEILINGS and identical ASTs for
TiePoints, certify_box, identity, inspect_region and geometry_check. That check
took0.03 s/18,928 KiB. These are not native-GDS or physical-signoff results.

Source/test review precedes final-GDS replay. A later replay needs a fresh output
directory, pinned new checker/config and original GDS identities, unchanged
limits/guard and independently retained negative controls. It must not write into
or relabel the failed T1 run. No pass is established by this source change alone.

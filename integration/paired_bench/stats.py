# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Repeated-sample statistics for paired elapsed-time comparisons.

A comparison is reported per endpoint (time to GDS, time to a signoff gate set). A ratio is
`reference / candidate` (above 1: the candidate is faster). Verdicts are deliberately
conservative: a lead is called stable only when every reference sample is slower than every
candidate sample over at least `min_blocks` counterbalanced blocks, so the claim does not rest
on one measurement, a mean, or a favourable pairing.
"""
from __future__ import annotations

from math import comb
from statistics import mean, median, stdev

MIN_BLOCKS = 3


def describe(values: list[float]) -> dict:
    """n, median, min, max, mean, sample standard deviation and relative spread of seconds."""
    if not values:
        return dict(n=0)
    mid = median(values)
    return dict(n=len(values), median=round(mid, 3), min=round(min(values), 3), max=round(max(values), 3),
                mean=round(mean(values), 3), stdev=round(stdev(values), 3) if len(values) > 1 else None,
                spread_pct=round(100 * (max(values) - min(values)) / mid, 2) if mid else None,
                values=[round(v, 3) for v in values])


def sign_test_one_sided(wins: int, n: int) -> float | None:
    """P(at least `wins` of `n` paired ratios above 1 | each side equally likely): exact binomial."""
    if n == 0:
        return None
    return sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def compare(reference: dict[str, float | None], candidate: dict[str, float | None], *,
            min_blocks: int = MIN_BLOCKS) -> dict:
    """Compare per-block seconds (block id -> seconds, None = endpoint not reached in that sample).

    Paired ratios use blocks that hold both arms. The conservative range divides the fastest and
    slowest reference samples by the slowest and fastest candidate samples."""
    missing = dict(reference=sorted(b for b, v in reference.items() if v is None),
                   candidate=sorted(b for b, v in candidate.items() if v is None))
    ref = {b: v for b, v in reference.items() if v is not None}
    cand = {b: v for b, v in candidate.items() if v is not None}
    result = dict(reference=describe(list(ref.values())), candidate=describe(list(cand.values())))
    if missing["reference"] or missing["candidate"] or not ref or not cand:
        result.update(verdict="not_reached", missing=missing, ratio_of_medians=None, paired_ratios={},
                      conservative_ratio_range=None)
        return result
    paired = {b: ref[b] / cand[b] for b in sorted(set(ref) & set(cand))}
    wins = sum(r > 1 for r in paired.values())
    low, high = min(ref.values()) / max(cand.values()), max(ref.values()) / min(cand.values())
    if len(paired) < min_blocks:
        verdict = "insufficient_repeats"
    elif low > 1:
        verdict = "candidate_faster_in_every_sample"
    elif high < 1:
        verdict = "reference_faster_in_every_sample"
    else:
        verdict = "overlap_lead_not_established"
    result.update(verdict=verdict, blocks=len(paired), min_blocks=min_blocks,
                  ratio_of_medians=round(median(ref.values()) / median(cand.values()), 4),
                  paired_ratios={b: round(r, 4) for b, r in paired.items()},
                  paired_ratio_min=round(min(paired.values()), 4) if paired else None,
                  paired_ratio_max=round(max(paired.values()), 4) if paired else None,
                  conservative_ratio_range=[round(low, 4), round(high, 4)],
                  candidate_faster_blocks=wins,
                  sign_test_one_sided_p=round(sign_test_one_sided(wins, len(paired)), 4) if paired else None)
    return result


def alternating_blocks(first: str, second: str, blocks: int) -> list[list[str]]:
    """Counterbalanced order: AB, BA, AB, ... so drift (thermal, page cache) does not favour one arm."""
    return [[first, second] if b % 2 == 0 else [second, first] for b in range(blocks)]

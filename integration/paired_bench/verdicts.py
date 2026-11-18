# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Normalized gate verdicts from a `signoff_layout.py` summary (`rusteda.host-shared-signoff.v1`).

Each gate maps to one of `passed`, `failed`, `unverified`, `completed` (a gate without a pass/fail
verdict, such as OpenRCX extraction, that produced its output) or `not_run`. Nothing absent is
reported as passed or as zero violations.
"""
from __future__ import annotations

COMPLETED = {"extracted_not_qualified"}


def _status(report) -> str | None:
    if not isinstance(report, dict):
        return None
    if isinstance(report.get("status"), str):
        return report["status"]
    summary = report.get("summary")   # latch-up and logic nest their verdict
    if isinstance(summary, dict) and isinstance(summary.get("status"), str):
        return summary["status"]
    return None


def _detail(name: str, report) -> dict:
    if not isinstance(report, dict):
        return {}
    if name in ("drc", "antenna"):
        return {"violations": report.get("violations")}
    if name == "timing":
        corners = report.get("corners") or {}
        return {"corners": {c: {k: v.get(k) for k in ("setup_ws_ns", "hold_ws_ns", "setup_violations",
                                                       "hold_violations", "slew_violations", "cap_violations",
                                                       "fanout_violations")}
                            for c, v in corners.items() if isinstance(v, dict)},
                "failed_items": report.get("failed_items")}
    if name == "logic":
        summary = report.get("summary") or {}
        worker = summary.get("worker") or {}
        return {"error": worker.get("error") or summary.get("error")}
    return {}


def gate(name: str, record: dict | None) -> dict:
    """Verdict of one gate record `{exit_code, report}` from the signoff summary."""
    if record is None:
        return dict(verdict="not_run")
    report = record.get("report")
    status, code = _status(report), record.get("exit_code")
    if status in COMPLETED and code == 0:
        verdict = "completed"
    elif status == "passed" and code == 0:
        violations = report.get("violations") if name in ("drc", "antenna") else 0
        verdict = "passed" if violations == 0 else "failed" if violations else "unverified"
    elif status == "failed":
        verdict = "failed"
    else:   # undecided, unverified, missing report, or a passed report with a failed exit
        verdict = "unverified"
    return dict(verdict=verdict, status=status, exit_code=code, **_detail(name, report))


def gates(summary: dict | None, names: list[str]) -> dict[str, dict]:
    found = (summary or {}).get("gates") or {}
    resources = (summary or {}).get("resources") or {}
    out = {}
    for name in names:
        row = gate(name, found.get(name))
        row["wall_seconds"] = (resources.get(name) or {}).get("wall_seconds")
        out[name] = row
    return out


def endpoint(producer_seconds: float | None, rows: dict[str, dict], names: list[str]) -> dict:
    """Seconds to reach a gate set: producer + the gates' own wall times, only when every gate in the
    set passed (or completed). Otherwise `seconds` is None and `blocking` names the gates."""
    blocking = {n: rows.get(n, {}).get("verdict", "not_run") for n in names
                if rows.get(n, {}).get("verdict") not in ("passed", "completed")}
    if producer_seconds is None:
        return dict(seconds=None, blocking={"producer": "failed"} | blocking)
    if blocking:
        return dict(seconds=None, blocking=blocking)
    walls = [rows[n].get("wall_seconds") for n in names]
    if any(w is None for w in walls):
        return dict(seconds=None, blocking={n: "no_wall_time" for n in names if rows[n].get("wall_seconds") is None})
    return dict(seconds=round(producer_seconds + sum(walls), 3), gate_seconds=round(sum(walls), 3), blocking={})

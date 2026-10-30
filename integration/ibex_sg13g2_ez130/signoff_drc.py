#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run a pinned complete IHP foundry deck and retain its independent findings.

The caller supplies a memory-limited scope and wall-clock deadline. The prepared
deck is checked against its complete provenance inventory before and after use.
No geometry is interpreted here. XML counting is streamed, capped at 500,000
findings/64 MiB; expected controller memory <64 MiB. KLayout uses two threads and
is estimated below 4 GiB for this hierarchical Ibex design.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET


def identity(path):
    path = path.resolve(strict=True)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return dict(path=str(path), sha256=digest, bytes=path.stat().st_size)


def verify_profile(profile, table="main"):
    if profile.stat().st_size > 2 * 1024**2:
        raise ValueError("deck provenance byte budget exceeded")
    data = json.loads(profile.read_bytes())
    files = data["files"]
    if not 1 <= len(files) <= 4096 or data["original_views_modified"] is not False:
        raise ValueError("invalid prepared foundry deck inventory")
    total = 0
    for name, spec in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe deck inventory path")
        path = profile.parent / "klayout" / relative
        record = identity(path)
        total += record["bytes"]
        if total > 256 * 1024**2 or record["sha256"] != spec["prepared_sha256"]:
            raise ValueError("foundry deck identity or byte budget differs")
    if table not in ("main", "antenna"):
        raise ValueError("unknown foundry rule table")
    relative = "ihp-sg13g2.drc" if table == "main" else "rule_decks/antenna.drc"
    deck = profile.parent / "klayout/tech/drc" / relative
    return deck, identity(profile), identity(deck)


def findings(path):
    if not path.is_file() or path.stat().st_size > 64 * 1024**2:
        raise ValueError("missing or oversized DRC report")
    categories = {}
    samples = {}
    total = 0
    root = None
    for event, node in ET.iterparse(path, events=("start", "end")):
        if root is None:
            root = node.tag
            if root != "report-database":
                raise ValueError("unexpected KLayout report root")
        if event == "end" and node.tag == "item":
            category = node.findtext("category")
            cell = node.findtext("cell")
            if not category or not cell:
                raise ValueError("malformed DRC finding")
            total += 1
            if total > 500000:
                raise ValueError("DRC finding count budget exceeded")
            categories[category] = categories.get(category, 0) + 1
            if len(samples.setdefault(category, [])) < 3:
                samples[category].append(dict(cell=cell, values=[v.text for v in node.findall("values/value")]))
            node.clear()
    return total, categories, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gds", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--table", choices=("main", "antenna"), default="main")
    parser.add_argument("--top", default="ibex_sram_bench")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    if not args.top.isascii() or not args.top.replace("_", "").isalnum():
        raise ValueError("unsafe top-cell handle")
    output = args.workspace.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "workspace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.base_rev != "empty" or any(p.name != "workspace.lock" for p in output.iterdir()):
            raise ValueError("REVISION_CONFLICT: DRC workspace must be empty")
        tick = time.monotonic()
        deck, profile_id, deck_id = verify_profile(args.profile.resolve(strict=True), args.table)
        gds = identity(args.gds)
        tool = identity(args.tool)
        command = [tool["path"], "klayout", "-b", "-r", str(deck)]
        switches = ["no_recommended=true"] if args.table == "main" else []
        for item in ("threads=2", "run_mode=deep", *switches, "topcell=" + args.top,
                     "input=" + gds["path"], "report=" + str(output / "drc.lyrdb"),
                     "log=" + str(output / "deck.log")):
            command.extend(["-rd", item])
        (output / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (output / "stdout.log").open("xb") as log:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        status = "failed"
        report = dict(status=status, complete=False, violations=None, engine_exit_code=result.returncode,
                      deck_sha256=deck_id["sha256"], profile=profile_id, gds=gds, tool=tool,
                      disabled_rules=["recommended"] if args.table == "main" else [],
                      scope="complete_foundry_" + args.table,
                      wall_seconds=time.monotonic() - tick)
        if result.returncode == 0:
            if verify_profile(args.profile.resolve(strict=True), args.table)[1:] != (profile_id, deck_id) or identity(args.gds) != gds:
                raise ValueError("DRC source changed during execution")
            total, categories, samples = findings(output / "drc.lyrdb")
            report.update(status="passed" if total == 0 else "failed", complete=True,
                          violations=total, categories=categories, samples=samples,
                          raw_report=identity(output / "drc.lyrdb"))
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "HEAD").write_text("rev1\n")
        print(json.dumps(report))
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

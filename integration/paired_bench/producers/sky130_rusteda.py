#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Run the published SKY130A RustEDA RTL-to-GDS producer into a new output directory.

    sky130_rusteda.py --out DIR [--check]

The published 1,675.78 s SKY130A sample ran a one-off recipe that lives in, and writes into,
`.work/ppa-closure-20261115/sky-rtl-horizontal-closed-pilot-r02` (r02). Its scripts find the
repository and the campaign directory from their own location, so they cannot run anywhere else.

This driver copies every input r02 had at launch into DIR, byte for byte, and replaces only those
location lookups with values from `DIR/paths.json` (`PATCHES`, each applied exactly once). No
step, binary, argument or other input changes: the stages, their order and their deadlines are
r02's `run.py`, which then runs from DIR with the repository root as working directory.

The inputs are r02's own pin lists. `producer-inputs.sha256` (pinned below) names the scripts and
binaries the launcher checked; the `closed-inputs.sha256` it pins names the sealed data the
producer re-checks after it starts. Every entry is verified before anything is copied. An entry
inside r02 is copied to the same relative path under DIR. One of them, the hold policy
`measured-load-eco/policy.json`, was regenerated in place by r02's run (it is rewritten by the
producer's first ECO step before any use), so the launch-time bytes are taken from the identical
pinned copy of the round-two campaign. The copied `closed-inputs.sha256` names DIR instead of
r02; nothing else in it changes.

`--check` verifies the pins and exits. Without it the driver writes DIR/port.json (sources,
patches, resulting hashes) and replaces itself with r02's `run.py` under this interpreter. The
caller provides the CPU set (r02 requires CPUs 30 and 31), memory scope and the RustEDA
worker ownership (`RUSTEDA_WORKER_ID`, `RUSTEDA_OWNERSHIP`), as r02's `launch.sh` did.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / ".work/ppa-closure-20261115"
SOURCE = CAMPAIGN / "sky-rtl-horizontal-closed-pilot-r02"
PRODUCER_PINS = ("producer-inputs.sha256", "b90d5a21450f2be8cae95835e8c683e9503a385810c6dac939143ccac5510e94")
CLOSED_PINS = "closed-inputs.sha256"
# r02's launch-time hold policy; r02's own copy was rewritten by its first ECO step.
REPLACED = {"measured-load-eco/policy.json": CAMPAIGN / "sky-round-two-pgo-calibrated-r02/measured-load-eco/policy.json"}
# r02 created these empty directories before launch; the stages write into them.
EMPTY_DIRS = ("stages", "warm")
PATHS_LOOKUP = 'json.loads((Path(__file__).resolve().parent/"paths.json").read_text())'
PATCHES = {
    "flow_utils.py": [
        ("REPO=Path(__file__).resolve().parents[3]",
         f'PATHS={PATHS_LOOKUP}\nREPO,CAMPAIGN=Path(PATHS["root"]),Path(PATHS["campaign"])'),
    ],
    "produce-warm.py": [
        ("from flow_utils import parallel_stages, gate_cmd",
         "from flow_utils import parallel_stages, gate_cmd, REPO, CAMPAIGN"),
        ("campaign = case.parent", "campaign = CAMPAIGN"),
        ("root = case.parents[2]", "root = REPO"),
    ],
    "tail.py": [
        ("CAMPAIGN=HERE.parent", "from flow_utils import CAMPAIGN"),
    ],
    "derive-ss.py": [
        ('baseline = Path(__file__).resolve().parents[1] / "sky-two-vertical-channels-fixed"',
         'from flow_utils import CAMPAIGN\nbaseline = CAMPAIGN / "sky-two-vertical-channels-fixed"'),
    ],
    "select-fresh.py": [
        ("campaign = Path(__file__).resolve().parents[1]", "from flow_utils import CAMPAIGN\ncampaign = CAMPAIGN"),
    ],
    "local-tail.py": [
        ("DATA = MODULE.parent", 'DATA = Path(__import__("json").loads((MODULE / "paths.json").read_text())["campaign"])'),
    ],
}


class PortError(Exception):
    pass


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(path: Path) -> dict:
    path = Path(path)
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def pins(path: Path) -> list[tuple[str, Path]]:
    rows = []
    for line in path.read_text().splitlines():
        digest, name = line.split(None, 1)
        if len(digest) != 64 or not Path(name).is_absolute():
            raise PortError(f"PIN_FORMAT: {path}: {line!r}")
        rows.append((digest, Path(name)))
    return rows


def plan() -> tuple[dict[str, tuple[str, Path]], list[dict]]:
    """Verify every pin; return the files to copy (relative path -> (sha, source)) and the externals."""
    listed = SOURCE / PRODUCER_PINS[0]
    if sha256(listed) != PRODUCER_PINS[1]:
        raise PortError(f"PIN_CHANGED: {listed}")
    rows = pins(listed)
    closed = [(d, p) for d, p in rows if p == SOURCE / CLOSED_PINS]
    if len(closed) != 1 or sha256(closed[0][1]) != closed[0][0]:
        raise PortError(f"PIN_CHANGED: {SOURCE / CLOSED_PINS}")
    rows += pins(SOURCE / CLOSED_PINS)
    copies, externals = {}, []
    for digest, path in rows:
        if path.is_relative_to(SOURCE):
            rel = str(path.relative_to(SOURCE))
            source = REPLACED.get(rel, path)
            if rel in copies and copies[rel][0] != digest:
                raise PortError(f"PIN_CONFLICT: {rel}")
            copies[rel] = (digest, source)
        else:
            externals.append((digest, path))
    for rel, (digest, source) in copies.items():
        if sha256(source) != digest:
            raise PortError(f"PIN_MISMATCH: {source} is not {digest} ({rel})")
    seen = []
    for digest, path in externals:
        actual = sha256(path)
        if actual != digest:
            raise PortError(f"PIN_MISMATCH: {path} is {actual}, pinned {digest}")
        seen.append({"path": str(path), "sha256": digest})
    missing = set(PATCHES) - set(copies)
    if missing:
        raise PortError(f"PATCH_TARGET_UNPINNED: {sorted(missing)}")
    return copies, seen


def materialize(out: Path, copies: dict[str, tuple[str, Path]], externals: list[dict]) -> dict:
    out.mkdir(parents=False)
    for name in EMPTY_DIRS:
        (out / name).mkdir()
    records = {}
    for rel, (digest, source) in sorted(copies.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(target) != digest:
            raise PortError(f"COPY_DIFFERS: {target}")
        records[rel] = {"source": str(source), "sha256": digest}
    for rel, edits in PATCHES.items():
        text = (out / rel).read_text()
        for old, new in edits:
            if text.count(old) != 1:
                raise PortError(f"PATCH_NOT_UNIQUE: {rel}: {old!r} occurs {text.count(old)} times")
            text = text.replace(old, new)
        (out / rel).write_text(text)
        records[rel]["patched_sha256"] = sha256(out / rel)
        records[rel]["patches"] = [{"old": o, "new": n} for o, n in edits]
    closed = out / CLOSED_PINS
    text = closed.read_text()
    lines = text.splitlines()
    moved = sum(1 for line in lines if f" {SOURCE}/" in line)
    closed.write_text(text.replace(f" {SOURCE}/", f" {out}/"))
    records[CLOSED_PINS]["retargeted_sha256"] = sha256(closed)
    records[CLOSED_PINS]["retargeted_entries"] = moved
    for digest, path in pins(closed):
        if sha256(path) != digest:
            raise PortError(f"RETARGETED_PIN_FAILS: {path}")
    paths = out / "paths.json"
    paths.write_text(json.dumps({"root": str(ROOT), "campaign": str(CAMPAIGN)}, indent=2) + "\n")
    receipt = {"schema": "rusteda.sky130-producer-port.v1", "source": str(SOURCE), "out": str(out),
               "driver": identity(Path(__file__).resolve()), "interpreter": sys.executable,
               "producer_pins": {"path": str(SOURCE / PRODUCER_PINS[0]), "sha256": PRODUCER_PINS[1]},
               "copied": records, "externals_verified": externals, "paths": json.loads(paths.read_text()),
               "empty_dirs": list(EMPTY_DIRS)}
    (out / "port.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="verify the pins and exit")
    args = parser.parse_args(argv)
    out = args.out.absolute()
    try:
        copies, externals = plan()
        if args.check:
            print(json.dumps({"status": "pins_verified", "copies": len(copies), "externals": len(externals)}))
            return 0
        if out.exists():
            raise PortError(f"OUTPUT_EXISTS: {out}")
        materialize(out, copies, externals)
    except PortError as error:
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return 2
    sys.stdout.flush()
    os.chdir(ROOT)
    os.execv(sys.executable, [sys.executable, str(out / "run.py")])


if __name__ == "__main__":
    raise SystemExit(main())

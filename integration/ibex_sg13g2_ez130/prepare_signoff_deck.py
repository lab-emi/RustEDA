# SPDX-License-Identifier: Apache-2.0
"""Prepare the official IHP #1025 off-grid fix without changing PDK/IP files.

This is a bounded file-copy operation, not DRC qualification. The caller must run
the same prepared deck on both implementations and retain the original reports.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

BASE = "5cccb161f7492697cfa52eb14dc03beb00bdca9e"
FIX = "a6a7e6ccf95b480f786039890b40670db6a54419"
REL = Path("tech/drc/rule_decks/geometry/3_1_offgrid.drc")
OLD_SHA = "8622d8aae0069b6183d3f59458c2abaa243b459347be26dc1d9ede8fe8676896"
NEW_SHA = "dc5eb236c1229efb48df98b256a1b3fd3b08cb793db021e4fee889756f292f1d"
MAX_FILES = 4096
MAX_BYTES = 256 * 1024 * 1024


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=30)


def prepare(source, workspace, base_rev):
    if base_rev != "empty":
        raise ValueError("REVISION_CONFLICT: preparation requires --base-rev empty")
    source = source.resolve(strict=True)
    if git(source, "rev-parse", "HEAD").decode().strip() != BASE:
        raise ValueError("IHP base revision mismatch")
    tree = source / "ihp-sg13g2/libs.tech/klayout"
    if digest(tree / REL) != OLD_SHA:
        raise ValueError("original off-grid deck differs from the pinned base")
    replacement = git(source, "show", f"{FIX}:ihp-sg13g2/libs.tech/klayout/{REL}")
    if len(replacement) > 65536 or hashlib.sha256(replacement).hexdigest() != NEW_SHA:
        raise ValueError("official fixed deck identity mismatch")
    files = []
    total = 0
    # Check the work budget before materializing the copy. Hashing is streamed.
    for path in tree.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"unexpected symlink in signoff inputs: {path}")
        if path.is_file():
            files.append(path)
            total += path.stat().st_size
        if len(files) > MAX_FILES or total > MAX_BYTES:
            raise ValueError("signoff input file/byte budget exceeded")
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "workspace.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(p.name != "workspace.lock" for p in workspace.iterdir()):
            raise ValueError("REVISION_CONFLICT: preparation workspace is nonempty")
        target = workspace / "klayout"
        shutil.copytree(tree, target)
        (target / REL).write_bytes(replacement)
        identities = {}
        for path in files:
            relative = path.relative_to(tree)
            identities[str(relative)] = {
                "original_sha256": digest(path),
                "prepared_sha256": digest(target / relative),
            }
        changed = [p for p, v in identities.items()
                   if v["original_sha256"] != v["prepared_sha256"]]
        if changed != [str(REL)]:
            raise ValueError("unexpected changes while preparing signoff deck")
        shutil.copyfile(source / "LICENSE", workspace / "IHP-LICENSE")
        record = {
            "status": "prepared_not_qualified",
            "base_pdk_commit": BASE,
            "official_fix_commit": FIX,
            "official_pr": "https://github.com/IHP-GmbH/IHP-Open-PDK/pull/1025",
            "only_changed_file": str(REL),
            "files": identities,
            "copied_bytes": total,
            "original_views_modified": False,
            "runset": str(target / "tech/drc/ihp-sg13g2.drc"),
        }
        (workspace / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
        (workspace / "HEAD").write_text("rev1\n")
    return {"status": record["status"], "new_rev": "rev1", "runset": record["runset"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdk-source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.pdk_source, args.workspace.resolve(), args.base_rev)))

#!/usr/bin/env python3
"""Reject active references to the retired repositories, preserving frozen evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 10_000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FINDINGS = 256
RETIRED = r"(?:rustlane|rusteda-studio)"
BOUNDARY = r"(?=$|[\s/\\\"'`?#<>@]|\.git\b|\.exe\b)"
RULES = {
    "retired_repository": re.compile(
        r"(?:www\.)?github\.com[/:]lab-emi/" + RETIRED + BOUNDARY, re.I),
    "retired_action": re.compile(
        r"\buses\s*:\s*[\"']?lab-emi/" + RETIRED + r"(?:/[^\s@]+)?@", re.I),
    "retired_clone": re.compile(
        r"\bgh\s+repo\s+clone\s+lab-emi/" + RETIRED + BOUNDARY, re.I),
    "retired_checkout": re.compile(r"\bmodules[/\\]" + RETIRED + BOUNDARY, re.I),
    "retired_build_directory": re.compile(r"\btarget[/\\]" + RETIRED + BOUNDARY, re.I),
    "retired_executable": re.compile(
        r"\btarget[/\\](?:debug|release)[/\\]" + RETIRED + BOUNDARY, re.I),
}
# Exact hashes prevent a future dependency from being hidden in a historical file.
HISTORICAL_SHA256 = {
    "integration/evidence/phase3.json":
        "6be39f59322c67aac8a8d6f99683d243aa786750c6e5222a76fa0ef89fb729f9",
    "integration/evidence/closeout-remote.json":
        "371c3952a9873266c56d1a9e2a2d72aa0a35d04582a93e1c39af30df9fe4ef40",
    "apps/docs/flow/verification/flow-results.json":
        "b923049da31679f9cc9bacb32e075d94c8a56faf7185413042b650e6ffbce4fc",
}


def audit(root: Path) -> dict:
    listing = subprocess.run(
        ["git", "ls-files", "--recurse-submodules", "-z"], cwd=root,
        capture_output=True, check=True, timeout=30,
    ).stdout
    if len(listing) > 4 * 1024 * 1024:
        raise ValueError("PATH_BUDGET: Git inventory exceeds 4 MiB")
    names = [p.decode() for p in listing.split(b"\0") if p]
    if len(names) > MAX_FILES:
        raise ValueError("FILE_BUDGET: too many tracked files")
    findings, historical = [], []
    total = 0

    def record(destination: list, row: dict) -> None:
        if len(findings) + len(historical) >= MAX_FINDINGS:
            raise ValueError("FINDING_BUDGET: too many references")
        destination.append(row)

    for name in names:
        path = root / name
        for rule, pattern in RULES.items():
            if pattern.search(name):
                record(findings, dict(path=name, line=0, rule=rule))
        if path.is_symlink():
            data = path.readlink().as_posix().encode()
        elif path.is_file():
            size = path.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError(f"BYTE_BUDGET: {name}")
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"BYTE_BUDGET: {name}")
        else:
            continue
        pinned = HISTORICAL_SHA256.get(name)
        frozen = pinned is not None and hashlib.sha256(data).hexdigest() == pinned
        if pinned and not frozen:
            record(findings, dict(path=name, line=0, rule="historical_record_changed"))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue  # Binary evidence and artwork are not dependency declarations.
        if "\0" in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            line = unquote(line.replace(r"\/", "/"))
            for rule, pattern in RULES.items():
                if pattern.search(line):
                    row = dict(path=name, line=number, rule=rule)
                    record(historical if frozen else findings, row)
    return dict(ok=not findings, scope="tracked root files and initialized submodules",
                files_scanned=len(names), bytes_scanned=total,
                active_dependencies=findings, preserved_historical_references=historical)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = audit(args.root.resolve())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = dict(ok=False, error=str(error))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay a recorded extraction against a private workspace with hash/RSS gates."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=1, choices=range(1, 17))
    parser.add_argument("--cpus", default="24-31")
    parser.add_argument("--timeout", type=int, default=1800, choices=range(1, 3601))
    args = parser.parse_args()
    source = args.source_run.resolve(strict=True)
    binary = args.binary.resolve(strict=True)
    out = args.out.resolve()
    # An existing destination, symlink, or nesting inside the evidence is refused.
    if args.out.is_symlink() or out.exists() or out == source or source in out.parents:
        parser.error("output must be a new directory outside the source run")
    command = json.loads((source / "logs/be_extract.command.json").read_text())["argv"]
    timer = Path(command[0]).resolve(strict=True)
    expected = json.loads((source / "logs/be_extract.stdout").read_text())["output"]
    old_binary = next(i for i, arg in enumerate(command) if arg == "--workspace") - 1
    argv = command[old_binary:]
    source_workspace = Path(argv[argv.index("--workspace") + 1]).resolve(strict=True)
    profile = Path(argv[argv.index("--rc-profile") + 1]).resolve(strict=True)
    if source not in source_workspace.parents or source not in profile.parents:
        parser.error("recorded inputs must belong to the source run")
    # Copies retain bytes, never hardlink files back into the published evidence.
    if any(p.is_symlink() for p in source_workspace.rglob("*")):
        parser.error("source workspace contains a symlink")
    out.mkdir(parents=True)
    subprocess.run(["free", "-h"], check=True)
    subprocess.run(["df", "-h", str(out)], check=True)
    head = sha256(source_workspace / "HEAD")
    copy_started = time.monotonic()
    workspace = out / "road"
    subprocess.run(["cp", "-a", "--reflink=auto", str(source_workspace), str(workspace)], check=True)
    revision = (workspace / "HEAD").read_text().strip()
    if not revision.startswith("rev") or not revision[3:].isdigit():
        parser.error("invalid copied workspace revision")
    revision_files = sorted((source_workspace / "revisions" / revision).iterdir())
    if len(revision_files) > 64 or any(not path.is_file() for path in revision_files):
        parser.error("unexpected revision file count or type")
    snapshot = {path.name: sha256(path) for path in revision_files}
    for name, expected_hash in snapshot.items():
        if sha256(workspace / "revisions" / revision / name) != expected_hash:
            parser.error("private snapshot copy differs from the source")
    shutil.copy2(profile, out / "rc-profile.json")
    copy_seconds = time.monotonic() - copy_started
    argv[0] = str(binary)
    for flag, value in [("--workspace", workspace), ("--rc-profile", out / "rc-profile.json"),
                        ("--out", out / "native.nom.spef")]:
        argv[argv.index(flag) + 1] = str(value)
    argv[1:1] = ["--threads", str(args.threads)]
    guard = ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=8G",
             "-p", "MemorySwapMax=2G", "-p", f"AllowedCPUs={args.cpus}",
             "timeout", "--signal=TERM", "--kill-after=10s", str(args.timeout),
             str(timer), "-v", "-o", str(out / "resource.time")]
    (out / "command.json").write_text(json.dumps({"argv": guard + argv,
        "binary_sha256": sha256(binary), "timer_sha256": sha256(timer), "expected": expected,
        "estimated_peak_bytes": 3_716_407_476, "corners": 1,
        "threads": args.threads, "copy_seconds": copy_seconds,
        "snapshot_sha256": snapshot}, indent=2) + "\n")
    started = time.monotonic()
    with (out / "stdout.json").open("w") as stdout, (out / "stderr.log").open("w") as stderr:
        result = subprocess.run(guard + argv, cwd=out, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, check=False)
    elapsed = time.monotonic() - started
    output = out / "native.nom.spef"
    actual = sha256(output) if output.is_file() else None
    unchanged = head == sha256(source_workspace / "HEAD") and all(
        sha256(source_workspace / "revisions" / revision / name) == digest
        and sha256(workspace / "revisions" / revision / name) == digest
        for name, digest in snapshot.items())
    receipt = {"status": "passed" if result.returncode == 0 and actual == expected["sha256"]
               and unchanged else "failed", "exit_code": result.returncode,
               "wall_seconds": elapsed, "copy_seconds": copy_seconds,
               "sha256": actual, "expected_sha256": expected["sha256"],
               "source_snapshot_unchanged": unchanged, "full_flow_status": "unchecked",
               "resource_usage": str(out / "resource.time")}
    (out / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

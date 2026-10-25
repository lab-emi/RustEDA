#!/usr/bin/env python3
"""Measure a child when GNU time is unavailable; run under the caller's resource guards."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a child command is required")
    started = time.monotonic()
    result = subprocess.run(command, check=False)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak = usage.ru_maxrss / 1024 if sys.platform == "darwin" else usage.ru_maxrss
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "measurement": "resource.getrusage(RUSAGE_CHILDREN)",
        "rss_scope": "maximum_individual_child_rss_not_aggregate",
        "wall_seconds": round(time.monotonic() - started, 6),
        "maximum_resident_set_size_kib": int(peak),
        "user_seconds": usage.ru_utime,
        "system_seconds": usage.ru_stime,
        "exit_code": result.returncode,
    }, indent=2) + "\n")
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    sys.exit(main())

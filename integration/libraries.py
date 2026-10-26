"""Resolve a pinned standard-cell library for direct engine commands."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .toolchain import digest, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rusteda library", description=__doc__)
    catalog = read_json(Path(__file__).with_name("library-catalog.json"))
    if catalog.get("schema") != "rusteda.library-catalog.v1":
        print("library unavailable: unsupported catalog schema", file=sys.stderr)
        return 1
    libraries = catalog["libraries"]
    parser.add_argument("name", choices=tuple(libraries))
    parser.add_argument("--corner", choices=("tt", "ff", "ss"), default="tt")
    parser.add_argument("--root", type=Path, help="Override the selected PDK or library root")
    args = parser.parse_args(argv)
    library = libraries[args.name]
    spec = library["corners"].get(args.corner)
    if spec is None:
        print(f"library unavailable: {args.name} has no pinned {args.corner} corner", file=sys.stderr)
        return 1
    root = args.root or Path(os.environ.get(library["env"], Path.home() / "pdks" / library["root"]))
    path = (root / spec["path"]).resolve()
    try:
        if digest(path) != spec["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {path}")
    except (OSError, ValueError) as error:
        print(f"library unavailable: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0

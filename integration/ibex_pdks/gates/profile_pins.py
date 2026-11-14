#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Check, or rewrite, the SHA-256 pins of a gate profile.

  profile_pins.py --profile <gates.json> --pdk-root <root> [--prepared <dir>]            check
  profile_pins.py --profile <gates.json> --pdk-root <root> [--prepared <dir>] --write    re-pin

The pinned set is every file the profile names (macro views, collateral, Magic start-up and
technology files, decks, rule files, vendor models) plus every file matching a deck's `includes`
globs. `--write` is for moving a profile to another PDK build on purpose; the gates themselves
never re-pin, they refuse to run on a mismatch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def consumed(profile) -> list:
    data, paths, globs = profile.data, [], []
    for macro in data["macros"].values():
        paths += [macro["gds"], macro["lef"], *macro["liberty"].values()]
    collateral = data["collateral"]
    paths += [*collateral["liberty"].values(), collateral["tech_lef"], *collateral["cell_lefs"]]
    if "magic" in data:
        paths += [data["magic"]["magicrc"], *data["magic"]["tech_files"]]
    gates = data["gates"]
    for gate in ("drc", "antenna"):
        for engine in gates[gate]["engines"]:
            if engine["engine"] == "klayout":
                paths.append(engine["deck"])
                globs += engine.get("includes", [])
    paths.append(gates["extraction"]["rules"])
    paths += [gates["lvs"]["netgen_setup"], *gates["lvs"]["cell_spice"], *gates["simulation"]["vendor"]]
    return sorted(set(paths) | set(profile.expand(globs)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--pdk-root", type=Path, required=True)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    profile = common.Profile(args.profile, args.pdk_root, args.prepared, pinned=not args.write)
    wanted = consumed(profile)
    actual = {path: common.identity(profile.locate(path))["sha256"] for path in wanted}
    if args.write:
        profile.data["files"] = actual
        args.profile.write_text(json.dumps(profile.data, indent=2) + "\n")
        print(json.dumps(dict(status="written", files=len(actual))))
        return 0
    differing = sorted(path for path in set(actual) | set(profile.pins) if actual.get(path) != profile.pins.get(path))
    print(json.dumps(dict(status="matches" if not differing else "differs", files=len(actual), differing=differing[:40])))
    return 0 if not differing else 1


if __name__ == "__main__":
    raise SystemExit(main())

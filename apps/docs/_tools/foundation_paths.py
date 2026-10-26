# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA Studio contributors
"""Resolve relocated first-party source links without weakening link checks."""
import json
from pathlib import Path
import subprocess
import tomllib

SHARED = ("crates/core/rusteda-core", "crates/core/rusteda-workspace", "crates/core/rusteda-compute", "crates/formats/rusteda-formats", "crates/apps/rusteda-cli")
_CACHE = {}

def foundation(root):
    root = root.resolve()
    if root in _CACHE:
        return _CACHE[root]
    data = tomllib.loads((root / "Cargo.toml").read_text())
    dep = data.get("workspace", {}).get("dependencies", {}).get("rusteda-core", {})
    revision = dep.get("rev")
    if not revision:
        _CACHE[root] = None
        return None
    result = subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked", "--offline"], cwd=root, capture_output=True, check=True, timeout=30)
    if len(result.stdout) > 16 * 1024 * 1024:
        raise ValueError("Cargo metadata exceeds 16 MiB")
    packages = json.loads(result.stdout)["packages"]
    matches = [p for p in packages if p["name"] == "rusteda-core"]
    if len(matches) != 1:
        raise ValueError("expected one canonical foundation")
    base = Path(matches[0]["manifest_path"]).resolve().parents[3]
    _CACHE[root] = (base, revision)
    return _CACHE[root]

def relocated(root, target):
    root, target = root.resolve(), target.resolve()
    if not target.is_relative_to(root):
        return None
    rel = target.relative_to(root).as_posix()
    if not any(rel == p or rel.startswith(p + "/") for p in SHARED):
        return None
    resolved = foundation(root)
    if resolved is None:
        return None
    base, revision = resolved
    candidate = base / rel
    if not candidate.exists():
        return None
    kind = "tree" if candidate.is_dir() else "blob"
    return candidate, f"https://github.com/lab-emi/RustEDA-Common/{kind}/{revision}/{rel}"

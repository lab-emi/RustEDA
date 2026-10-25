"""Explicit local tool setup for the RustEDA integration flow (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SCHEMA = "rusteda.toolchain.v1"


def read_json(path, limit=2*1024**2):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"missing or oversized JSON: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("JSON byte budget exceeded")
    return json.loads(data)


def digest(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 1024**3:
        raise ValueError(f"missing or oversized file: {path}")
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name+".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, allow_nan=False); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def executable(value):
    if not value:
        return None
    p = Path(value) if Path(value).is_absolute() else Path(shutil.which(str(value)) or value)
    return p.resolve() if p.is_file() and os.access(p, os.X_OK) else None


def verify(lock, *, recovery=False):
    """Cheap pinned file check; the explicit tools-verify command also probes PDK/images."""
    if lock.get("schema") != SCHEMA or lock.get("status") != "ready":
        raise ValueError("toolchain is not ready; run dev tools configure")
    errors = []
    for name, spec in lock["programs"].items():
        if recovery and name != "rustlane":
            continue
        p = executable(spec["path"])
        if p is None or digest(p) != spec["sha256"]:
            errors.append(name)
    for name in (() if recovery else ("tool_manifest", "signoff_manifest")):
        spec = lock[name]
        if digest(spec["path"]) != spec["sha256"]:
            errors.append(name)
    if errors:
        raise ValueError("toolchain identity changed: "+", ".join(errors))
    return lock


def probe(root, project, programs, manifest_path):
    out=Path(root)/".work/integration/identity";out.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix="probe-",dir=out))
    environment=dict(os.environ,RUSTEDA_GNU_TIME=programs["gnu_time"]["path"])
    checked=subprocess.run([programs["python"]["path"],str(project/"scripts/signoff/manifest.py"),
                            "verify","--manifest",str(manifest_path),"--out",str(work)],
                           cwd=project,env=environment,capture_output=True,text=True,timeout=180)
    identity=read_json(work/"identity.json") if (work/"identity.json").exists() else dict(
        status="unverified",error=checked.stderr[-2000:])
    return checked.returncode==0 and identity["status"]=="verified",identity


def configure(root, args):
    project = (root/"modules/RustROAD").resolve()
    pins_path = project/"fixtures/flow/rtl2gds-tools.json"
    manifest_path = project/"fixtures/flow/signoff-pinned.json"
    pins = read_json(pins_path)
    overrides = dict(value.split("=", 1) for value in args.tool)
    allowed = set(pins["tools"]) | {"python", "rustlane", "rustroad", "podman"}
    if set(overrides)-allowed:
        raise ValueError("unknown tool override")
    programs, aliases, errors = {}, [], []
    for name, spec in pins["tools"].items():
        if "path" not in spec:
            continue
        alias = project/spec["path"]
        candidate = overrides.get(name) or (str(alias) if alias.is_file() else shutil.which(
            {"gnu_time":"time", "opensta":"sta"}.get(name, name)))
        p = executable(candidate)
        if p is None:
            errors.append(f"{name}: unavailable (use --tool {name}=PATH)"); continue
        actual = digest(p)
        if actual != spec["sha256"]:
            errors.append(f"{name}: differs from qualified SHA-256"); continue
        programs[name] = dict(path=str(p), sha256=actual, version=spec.get("version"))
        if not alias.exists():
            if alias.is_symlink():
                errors.append(f"{name}: dangling alias; preserve it and resolve explicitly"); continue
            if not alias.is_relative_to(project/".work/tools"):
                errors.append(f"unsafe tool alias {alias}"); continue
            aliases.append((alias,p))
        elif alias.resolve() != p:
            errors.append(f"{name}: existing alias differs; no existing file will be replaced")
    defaults = {
        "python":sys.executable,
        "rustlane":str(root/(".work/target/RustLane/release/rustlane" if
                             (root/".work/target/RustLane/release/rustlane").is_file() else
                             ".work/target/RustLane/debug/rustlane")),
        "rustroad":str(root/".work/target/RustROAD/release/rustroad"),
        "podman":shutil.which("podman"),
    }
    for name, default in defaults.items():
        p = executable(overrides.get(name) or default)
        if p is None:
            errors.append(f"{name}: unavailable (use --tool {name}=PATH)"); continue
        programs[name] = dict(path=str(p), sha256=digest(p))
    result = dict(schema=SCHEMA, status="unverified", project=str(project), programs=programs,
                  tool_manifest=dict(path=str(pins_path), sha256=digest(pins_path)),
                  signoff_manifest=dict(path=str(manifest_path), sha256=digest(manifest_path)), errors=errors)
    if not errors:
        for alias, target in aliases:
            alias.parent.mkdir(parents=True, exist_ok=True); alias.symlink_to(target)
        valid,result["identity"]=probe(root,project,programs,manifest_path)
        result["status"]="ready" if valid else "unverified"
    write_json(args.output, result)
    return dict(ok=result["status"] == "ready", status=result["status"], configuration=str(args.output),
                errors=errors, identity=result.get("identity",{}).get("status"))


def main(argv=None, root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    conf = sub.add_parser("configure")
    conf.add_argument("--output", type=Path, default=root/".work/integration/toolchain.json")
    conf.add_argument("--tool", action="append", default=[], metavar="NAME=PATH")
    check = sub.add_parser("verify")
    check.add_argument("--config", type=Path, default=root/".work/integration/toolchain.json")
    a = p.parse_args(argv)
    if a.action == "configure":
        return configure(root,a)
    lock = verify(read_json(a.config))
    valid,identity=probe(root,Path(lock["project"]),lock["programs"],lock["signoff_manifest"]["path"])
    return dict(ok=valid,status="ready" if valid else "unverified",configuration=str(a.config),identity=identity)


if __name__ == "__main__":
    try:
        result = main()
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        result = dict(ok=False,status="unverified",error=str(e))
    print(json.dumps(result))
    raise SystemExit(0 if result["ok"] else 3)

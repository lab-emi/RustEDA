#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Compose the native Ibex recipe of a PDK profile for a host.

This driver reads a native PDK profile (`rusteda.native-pdk-profile.v1`, a directory of
data files under `integration/ibex_pdks/<profile>/native/`; `integration/ibex_pdks/NATIVE.md`
describes it) and a host map, resolves every file the profile names, writes the two composer
inputs (`rusteda.native-frontend-inputs.v1`, `rusteda.native-backend-inputs.v1`) and calls
the recipe's composers `native_frontend.py build` and `native_backend.py` with the profile.
Every file and tool is recorded by absolute path, SHA-256 and size; the composers re-verify
them, and a profile reference that states a SHA-256 is refused when the file differs.

Without `--profile` (and without a `profile` entry in the host map) the profile is the IHP
SG13G2/EZ130 one, and the result is the published recipe: the same two input files and the
same `rusteda.ihp-flow.v1` manifest, byte for byte, as before profiles existed. Any other
profile composes a `rusteda.native-flow.v1` manifest for `rusteda flow exec`.

The full sixteen-gate composer (`native_flow.py`) also requires the
component-qualification evidence of the original host
(`rusteda.ihp-component-qualification.v1`). No generator for it is published
and it must not be fabricated, so this driver stops at the backend prefix
manifest. Logic equivalence and vendor simulation are run separately with the shared gates.

Usage (all paths absolute or relative to the working directory):

    python3 integration/ibex_host_benchmark/compose_native.py \\
        --host host.json --workspace .work/ibex-host/compose --base-rev empty \\
        [--profile integration/ibex_pdks/sky130hd/native]

Host map: `prepared` (the prepare.py workspace), `threads`, `cpu_affinity`, `tools`, and the
directories the profile's file references name: `roots` (`{"pdk": "/path/to/sky130A"}`) or,
as the IHP host maps have them, top-level entries (`ez130_root`, `ihp_root`, ...). Optional:
`profile`, `recipe_dir` (the directory of the pinned reference sources and IHP policies,
default `integration/ibex_sg13g2_ez130`), `parallel_signoff`, `backend_steps`.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
# The composers run are the ones beside this driver; `recipe_dir` of the host map names the
# pinned reference sources (it may be a patched host copy).
COMPOSERS = Path(__file__).resolve().parents[1] / "ibex_sg13g2_ez130"
PROFILES = Path(__file__).resolve().parents[1] / "ibex_pdks"
DEFAULT_PROFILE = "ihp_sg13g2_ez130"
# Default file names of the policy files of a profile directory.
POLICIES = dict(floorplan="floorplan.json", physical="physical.json", routing="routing.json",
                selection="selection.json", interfaces="interfaces.json",
                rc_estimate="rc-estimate-policy.json", contract="contract.json",
                antenna_group="antenna-group-policy.json", antenna_session="antenna-session-policy.json")
# Record order of the backend inputs of the published IHP recipe (the order is part of the
# bytes of its composer input and manifest). Another profile records its files in the order
# they are resolved: physical inputs, macro LEFs, antenna policies, referee files.
IHP_BACKEND_ORDER = (
    "physical_prepare", "rc_builder", "reference_extract", "timing_runner", "postroute_runner",
    "lvs_runner", "drc_runner", "macro_contract", "std_gds", "ram_gds", "layer_map", "rules_nom",
    "magic_tech", "magic_extract", "ram_lef", "drc_profile", "drc_deck", "antenna_deck", "lvs_deck",
    "std_cdl", "ram_cdl", "filler_cdl", "sram_qualification", "lvs_interfaces", "antenna_runner",
    "antenna_fields", "contract", "antenna_legacy", "antenna_verifier", "antenna_source_manifest",
    "antenna_group_policy", "antenna_session_policy")


def fail(message: str):
    raise SystemExit("compose_native: " + message)


def identity(path: Path) -> dict:
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}


def load_composer(name: str):
    path = COMPOSERS / name
    spec = importlib.util.spec_from_file_location(path.stem + "_for_compose", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resolver:
    """Resolves the file references of a profile: `<root>:<relative path>`, optionally with a
    pinned SHA-256 (`{"path": ..., "sha256": ...}`)."""

    def __init__(self, host: dict, profile_dir: Path, recipe: Path, prepared: Path, sources: Path):
        self.host = host
        self.builtin = dict(profile=profile_dir, recipe=recipe, prep=prepared, repo=sources)

    def root(self, name: str) -> Path | None:
        if name in self.builtin:
            return self.builtin[name]
        value = self.host.get("roots", {}).get(name, self.host.get(name))
        return Path(value).resolve() if isinstance(value, str) else None

    def known(self, reference) -> bool:
        text = reference["path"] if isinstance(reference, dict) else reference
        return self.root(text.split(":", 1)[0]) is not None

    def path(self, reference, what: str) -> Path:
        pinned = None
        if isinstance(reference, dict):
            if set(reference) != {"path", "sha256"}:
                fail(f"{what}: a pinned reference has exactly path and sha256")
            reference, pinned = reference["path"], reference["sha256"]
        if not isinstance(reference, str) or ":" not in reference:
            fail(f"{what}: file reference must be '<root>:<relative path>'")
        name, relative = reference.split(":", 1)
        root = self.root(name)
        if root is None:
            fail(f"{what}: the host map names no root '{name}' (roots.{name})")
        parts = [part for part in relative.split("/") if part]
        if any(part in (".", "..") for part in parts) or relative.startswith("/"):
            fail(f"{what}: relative path must stay below its root: {reference}")
        path = root.joinpath(*parts)
        if not path.is_file():
            fail(f"{what}: no such file: {path}")
        if pinned is not None and identity(path)["sha256"] != pinned:
            fail(f"{what}: {path} differs from the SHA-256 the profile pins")
        return path


def combined_lef(sources: list[Path], target: Path) -> Path:
    """One LEF with every cell and macro MACRO: the first file without its final END LIBRARY,
    then the MACRO blocks of the others, then END LIBRARY (what `rustroad import-pdk --cell-lef`
    and the planning helpers read)."""
    def body(text: str, path: Path) -> str:
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines or lines[-1].split() != ["END", "LIBRARY"]:
            fail(f"combined LEF: {path} does not end with END LIBRARY")
        return "\n".join(lines[:-1]) + "\n"

    out = [body(sources[0].read_text(), sources[0])]
    for path in sources[1:]:
        lines, name, block = path.read_text().splitlines(), None, []
        found = 0
        for line in lines:
            tokens = line.split()
            if name is None and len(tokens) == 2 and tokens[0] == "MACRO":
                name, block = tokens[1], []
            if name is not None:
                block.append(line)
                if tokens == ["END", name]:
                    out.append("\n".join(block) + "\n")
                    name, found = None, found + 1
        if name is not None or not found:
            fail(f"combined LEF: no complete MACRO block in {path}")
    out.append("END LIBRARY\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", type=Path, required=True, help="host path map (see host.example.json)")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-rev", required=True)
    parser.add_argument("--profile", help="native PDK profile: a directory, its profile.json, or the name of a "
                                          "directory under integration/ibex_pdks [default: the host map's "
                                          "`profile`, else the IHP SG13G2/EZ130 profile]")
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="superproject checkout whose module sources are pinned as reference files "
                             "[default: this repository]")
    args = parser.parse_args()
    if args.base_rev != "empty" or (args.workspace.exists() and any(args.workspace.iterdir())):
        raise SystemExit("REVISION_CONFLICT: composition requires --base-rev empty and an empty workspace")
    host = json.loads(args.host.read_text())
    chosen = args.profile or host.get("profile") or DEFAULT_PROFILE
    profile_path = Path(chosen)
    if not profile_path.exists() and re.fullmatch(r"[A-Za-z0-9_.-]+", chosen):
        profile_path = PROFILES / chosen / "native"
    if profile_path.is_dir():
        profile_path = profile_path / "profile.json"
    profile_path = profile_path.resolve()
    frontend = load_composer("native_frontend.py")
    try:
        profile = frontend.load_profile(profile_path)
        backend = load_composer("native_backend.py").BackendProfile(profile)
    except (ValueError, KeyError, TypeError) as error:
        fail(f"PDK profile {profile_path}: {error}")
    data = profile.data
    sources = args.source_root.resolve()
    recipe = Path(host.get("recipe_dir", COMPOSERS)).resolve()   # pinned reference sources (or a host copy)
    prep = Path(host["prepared"]).resolve()                       # prepare.py workspace
    refs = Resolver(host, profile_path.parent, recipe, prep, sources)
    road = sources / "modules/RustROAD/scripts"
    work = args.workspace.resolve()
    work.mkdir(parents=True, exist_ok=True)

    def policy(name: str) -> Path:
        return refs.path(data.get("policies", {}).get(name, "profile:" + POLICIES[name]), "policies." + name)

    corner = {c["name"]: c for c in data["corners"]}
    macro_lefs = {m["id"] + "_lef": refs.path(m["lef"], f"macros.{m['id']}.lef") for m in profile.macros}
    cell_lefs = [refs.path(ref, "lef.cells") for ref in data["lef"]["cells"]]
    if data["lef"].get("combined") is not None:
        cell_lef = refs.path(data["lef"]["combined"], "lef.combined")
    else:
        cell_lef = combined_lef([*cell_lefs, *macro_lefs.values()], work / "collateral/combined-cells.lef")
    fe_files = {"rtl": prep / "rtl/design.v", "sdc": prep / "constraints.sdc"}
    for group in profile.groups:
        for name in profile.corners:
            fe_files[group + "_" + name] = refs.path(corner[name]["liberty"][group], f"corners.{name}.liberty.{group}")
    fe_files.update({
        "tech_lef": refs.path(data["lef"]["technology"], "lef.technology"), "cell_lef": cell_lef,
        "std_lef": cell_lefs[0],
        "floorplan": policy("floorplan"), "physical": policy("physical"),
        "routing": policy("routing"), "interfaces": policy("interfaces"),
        "ecos": recipe / "native_ecos.py", "frontend": recipe / "native_frontend.py",
        "prepare": recipe / "native_frontend_inputs.py",
        "delay_helper": road / "build_delay_cells.py", "sizing_helper": road / "build_sizing_profile.py",
        "liberty_reader": road / "liberty_lite.py",
        "pin_caps_source": sources / "modules/RustEDA-Common/crates/formats/rusteda-formats/examples/export_pin_caps.rs",
        "producer": recipe / "producer.py", "estimator": recipe / "estimate_spef.py",
        "rc_policy": policy("rc_estimate"),
    })
    if not profile.legacy:
        # The executor and the planning helpers read the profile and its contract.
        fe_files.update({"profile": profile_path, "contract": policy("contract")})
    tools = host["tools"]
    fe_tools = {k: tools[k] for k in ("rustsys", "rusteda", "ruststa", "rustroad", "python",
                                      "gnu_time", "xxhsum", "pin_caps")}
    threads = int(host["threads"])
    scope = {"threads": threads, "cpu_affinity": list(host["cpu_affinity"]), "max_log_bytes": 64 << 20,
             "memory_mib": 8192, "max_memory_bytes": 8 << 30, "max_stdout_artifact_bytes": 1536 << 20,
             "wire_model": "ideal_diagnostic", "cppr": "off"}
    selection = json.loads(policy("selection").read_text())
    fe = {"schema": "rusteda.native-frontend-inputs.v1",
          "files": {k: identity(v) for k, v in fe_files.items()},
          "tools": {k: identity(Path(v)) for k, v in fe_tools.items()},
          "scope": scope, "selection": selection}
    (work / "frontend-inputs.json").write_text(json.dumps(fe, indent=2) + "\n")
    python = sys.executable
    subprocess.run([python, str(COMPOSERS / "native_frontend.py"), "build", "--config", str(work / "frontend-inputs.json"),
                    "--workspace", str(work / "fe"), "--base-rev", "empty", "--profile", str(profile_path)], check=True)

    # The four backend design steps run as RustROAD flow-step commands unless host.json
    # says "backend_steps": "python" (the IHP recipe's helpers; needs "rc_builder" too).
    backend_steps = host.get("backend_steps", "native")
    be_files = {}
    gds = [refs.path(ref, "gds.cells") for ref in data["gds"]["cells"]]
    gds += [refs.path(m["gds"], f"macros.{m['id']}.gds") for m in profile.macros]
    be_files.update(zip(backend.gds_keys, gds))
    layer_map, rc = data["gds_layer_map"], data["rc"]
    if backend.projected:
        be_files["layer_map"] = refs.path(layer_map["klayout"], "gds_layer_map.klayout")
    else:
        be_files["gds_map"] = refs.path(layer_map["tracked"], "gds_layer_map.tracked")
    if backend.rc_built:
        be_files["rules_nom"] = refs.path(rc["rules"], "rc.rules")
        be_files["magic_tech"] = refs.path(rc["magic_tech"], "rc.magic_tech")
        if backend.magic_extract:
            be_files["magic_extract"] = refs.path(rc["magic_extract"], "rc.magic_extract")
        pdk_tree = rc["magic_tech"]
    else:
        be_files["rc_profile"] = refs.path(rc["profile"], "rc.profile")
        pdk_tree = data["lef"]["technology"]
    if backend.referee_steps:
        be_files.update(macro_lefs)
    for key, name in (("antenna_group_policy", "antenna_group"), ("antenna_session_policy", "antenna_session")):
        reference = data.get("policies", {}).get(name, "profile:" + POLICIES[name])
        # An IHP host map without the frozen antenna session has no grouped policies.
        if not profile.legacy or refs.known(reference):
            be_files[key] = refs.path(reference, "policies." + name)
    for key, reference in data.get("referee_files", {}).items():
        be_files[key] = refs.path(reference, "referee_files." + key)
    if profile.legacy:
        # The Python step helpers of the IHP recipe, pinned unless the steps are native commands.
        be_files.update({
            "physical_prepare": recipe / "native_physical_inputs.py",
            "rc_builder": Path(host["rc_builder"]) if backend_steps == "python" else None,
            "postroute_runner": recipe / "native_postroute.py", "antenna_runner": recipe / "native_antenna.py",
            "antenna_fields": recipe / "report_fields.py"})
        if host.get("antenna_frozen"):
            # The grouped single-process antenna session (R20 lineage): helpers frozen by
            # antenna_session/freeze.py, plus the qualified integer group policy.
            frozen = Path(host["antenna_frozen"]).resolve()
            be_files.update({
                "antenna_runner": frozen / "native_antenna_groups.py", "antenna_legacy": frozen / "native_antenna.py",
                "antenna_fields": frozen / "report_fields.py", "antenna_verifier": frozen / "verify.py",
                "antenna_source_manifest": frozen / "manifest.json"})
        if backend_steps == "native":
            # No Python step helper is pinned: the session, its verifier, the post-route
            # runner and the physical-input preparation are commands of the pinned rustroad.
            for key in ("physical_prepare", "rc_builder", "postroute_runner", "antenna_runner", "antenna_fields",
                        "antenna_legacy", "antenna_verifier", "antenna_source_manifest"):
                be_files.pop(key, None)
        be_files = {key: be_files[key] for key in IHP_BACKEND_ORDER if key in be_files}
    elif backend_steps != "native":
        fail("this profile needs backend_steps native (the Python step helpers are the IHP recipe's)")
    referee_tools = ("appimage", "opensta", "yosys") if backend.referee_steps else ()
    parallel = {"main", "antenna", "lvs"} <= set(backend.referee_steps)
    be = {"schema": "rusteda.native-backend-inputs.v1",
          "frontend_fragment": identity(work / "fe/fragment.json"),
          "files": {k: identity(v) for k, v in be_files.items()},
          "tools": {k: identity(Path(tools[k])) for k in referee_tools},
          # The directory above the PDK tree (the tree is the root of the profile's Magic
          # technology reference; the IHP recipe's physical-inputs step joins ihp-sg13g2 to it).
          "pdk_root": str(refs.root((pdk_tree["path"] if isinstance(pdk_tree, dict) else pdk_tree).split(":", 1)[0]).parent),
          # The producer's foundry checks (be_main, be_antenna -> be_lvs) run as two concurrent
          # two-core lanes on a four-CPU host (producer.signoff_parallel_group); host.json can opt out.
          "parallel_signoff": bool(host.get("parallel_signoff", threads == 4)) and parallel,
          "backend_steps": backend_steps}
    (work / "backend-inputs.json").write_text(json.dumps(be, indent=2) + "\n")
    subprocess.run([python, str(COMPOSERS / "native_backend.py"), "--inputs", str(work / "backend-inputs.json"),
                    "--workspace", str(work / "be"), "--base-rev", "empty", "--profile", str(profile_path)], check=True)
    manifest = json.loads((work / "be/prefix-manifest.json").read_text())
    print(json.dumps({"status": "composed_unqualified", "manifest": str(work / "be/prefix-manifest.json"),
                      "profile": str(profile_path), "pdk": manifest["pdk"], "library": manifest["library"],
                      "steps": len(manifest["steps"]), "stop_after": manifest["steps"][-1]["id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""GF180MCU D Ibex producers that end in the same floating-fill emitter.

    gf180.py rusteda   --out DIR
    gf180.py librelane --run RUN_DIR --out DIR -- LIBRELANE_ARGV...

Both write `DIR/fill/ibex_sram_bench.gds`, the routed GDS with the Metal2 and top-metal floating
fill of `integration/ibex_pdks/gf180mcu/native/floating_fill.py`, run with the campaign's pinned
KLayout (`reference-tools/eda klayout`). The emitter reads the routed GDS, its DEF and the SRAM
LEF; the fill is GDS geometry only (OpenRCX, Magic and the timing gate do not see it). Both flows
pass `below_target=emit`: a layer the fill cannot lift above the emitter's 30 % geometric target is
filled as far as it goes and reported, and the foundry density rules decide.

`rusteda` is the published 637.13 s producer
(`.work/three-pdk-20261114/gf180/gf-density-legal-closure-20261115/matched-filled-producer-22-23`):
its three launch inputs (`run-integrated.py`, `cold/manifest.json`, `warm/replay.py`) are copied
byte for byte into DIR, where they write their outputs (they locate themselves, nothing else), and
run as that producer ran them: the cold flow, both corrective warm reroutes and their prerequisite
extracted analyses. Its fill step is the tracked emitter in place of the scratch scripts the
published run used; the tracked emitter reproduces that run's filled GDS record for record after
masking dates. The caller provides CPUs 22 and 23 (the copied script requires them), the memory
scope and the RustEDA worker ownership, with `RUSTEDA_OWNERSHIP=DIR/cold/ownership`.

`librelane` runs LIBRELANE_ARGV unchanged, then the same emitter on `RUN_DIR/final/gds` and
`RUN_DIR/final/def`. `DIR/producer.json` records both stages' seconds and exit codes. A failure of
either stage fails the producer.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / ".work/three-pdk-20261114"
EDA = CAMPAIGN / "reference-tools/eda"
EMITTER = ROOT / "integration/ibex_pdks/gf180mcu/native/floating_fill.py"
MACRO_LEF = CAMPAIGN / "pdks/gf180mcuD/libs.ref/gf180mcu_fd_ip_sram/lef/gf180mcu_fd_ip_sram__sram512x8m8wm1.lef"
TOP = "ibex_sram_bench"
SOURCE = CAMPAIGN / "gf180/gf-density-legal-closure-20261115/matched-filled-producer-22-23"
COPIES = {
    "run-integrated.py": "51ab67a65305131d49b5dfe9ca0435af559713160be401436abc2e9366bec2f2",
    "cold/manifest.json": "4699aa0021890fb470dbeab03a4280a187a1afd475d9a5ce667fb8f37a4cf12a",
    "warm/replay.py": "bbcb1ae3a3070df90e47e2dd1beb244ba782ac0ca5c80dc40e4eaa07a4d60e4f",
}
PINS = {
    EMITTER: "08b8645b80becd047d6be61104248b4c8d1ee432a16eb60b45f16e2259ceb423",
    MACRO_LEF: "f30453be8a3919de697ad6f9828cc0e31602ed5ad7cc462dd85b7d54e47872af",
}
FILL_DEADLINE = 600


class ProducerError(Exception):
    pass


def sha256(path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(path) -> dict:
    path = Path(path)
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def verify(pins: dict) -> None:
    for path, digest in pins.items():
        actual = sha256(path)
        if actual != digest:
            raise ProducerError(f"PIN_MISMATCH: {path} is {actual}, pinned {digest}")


def stage(name: str, argv: list, record: dict, *, stdout=None, stderr=None, timeout=None) -> int:
    """Run one stage; stdout/stderr go to new files when given, else to this process's streams."""
    start = time.monotonic()
    with contextlib.ExitStack() as stack:
        out = stack.enter_context(open(stdout, "xb")) if stdout else None
        err = stack.enter_context(open(stderr, "xb")) if stderr else None
        try:
            code = subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                  timeout=timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            code = 124
    record["stages"].append({"stage": name, "seconds": round(time.monotonic() - start, 3), "exit_code": code})
    print(json.dumps(record["stages"][-1]), flush=True)
    return code


def fill(gds: Path, def_: Path, out: Path, record: dict) -> int:
    out.mkdir()
    code = stage("floating-fill", [EDA, "klayout", "-b", "-r", EMITTER, "-rd", f"input_gds={gds}",
                                   "-rd", f"input_def={def_}", "-rd", f"macro_lef={MACRO_LEF}",
                                   "-rd", f"output_gds={out / f'{TOP}.gds'}", "-rd", "below_target=emit"],
                 record, stdout=out / "emitter.json", stderr=out / "emitter.log", timeout=FILL_DEADLINE)
    record["fill"] = {"input_gds": identity(gds), "input_def": identity(def_), "emitter": identity(EMITTER),
                      "macro_lef": identity(MACRO_LEF)}
    return code


def finish(out: Path, record: dict, code: int) -> int:
    record["exit_code"] = code
    (out / "producer.json").write_text(json.dumps(record, indent=2) + "\n")
    return code


def rusteda(out: Path) -> int:
    verify(PINS | {SOURCE / rel: digest for rel, digest in COPIES.items()})
    out.mkdir()
    for rel in COPIES:
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / rel, target)
    verify({out / rel: digest for rel, digest in COPIES.items()})
    record = {"schema": "rusteda.gf180-filled-producer.v1", "flow": "rusteda", "source": str(SOURCE),
              "copied": COPIES, "driver": identity(Path(__file__).resolve()), "stages": []}
    code = stage("cold-and-warm", [sys.executable, out / "run-integrated.py", ROOT], record)
    if code == 0:
        code = fill(out / "warm/load/eval/gds.gds", out / "warm/load/eval/def.def", out / "fill", record)
    return finish(out, record, code)


def librelane(run: Path, out: Path, argv: list[str]) -> int:
    verify(PINS)
    out.mkdir()
    record = {"schema": "rusteda.gf180-filled-producer.v1", "flow": "librelane", "argv": argv,
              "driver": identity(Path(__file__).resolve()), "stages": []}
    code = stage("librelane", argv, record)
    if code == 0:
        code = fill(run / f"final/gds/{TOP}.gds", run / f"final/def/{TOP}.def", out / "fill", record)
    return finish(out, record, code)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[argv.index("--") + 1:] if "--" in argv else []
    own = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flow", choices=("rusteda", "librelane"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run", type=Path, help="librelane: the run directory (runs/<tag>)")
    args = parser.parse_args(own)
    out = args.out.absolute()
    try:
        if out.exists():
            raise ProducerError(f"OUTPUT_EXISTS: {out}")
        if args.flow == "rusteda":
            if command or args.run:
                raise ProducerError("USAGE: rusteda takes only --out")
            return rusteda(out)
        if not command or args.run is None:
            raise ProducerError("USAGE: librelane needs --run and -- LIBRELANE_ARGV")
        return librelane(args.run.absolute(), out, command)
    except ProducerError as error:
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Helpers shared by the profile-driven signoff gates.

Nothing here interprets a design. The helpers pin files by SHA-256, load a gate
profile (`integration/ibex_pdks/<profile>/gates.json`), read the few LEF and DEF
facts the gates need (footprints, classes, pin uses, placements) and run one
external referee tool under GNU time with a wall-clock limit and a log cap.
"""
from __future__ import annotations

from decimal import Decimal, DecimalException
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

MIB = 1 << 20
PROFILE_SCHEMA = "rusteda.ibex-gate-profile.v1"
ORIENTATIONS = ("N", "S", "W", "E", "FN", "FS", "FW", "FE")


class GateError(ValueError):
    """An input, a pin or a tool result the gate refuses."""


def require(condition, message):
    if not condition:
        raise GateError(message)


def identity(path, cap=4 << 30) -> dict:
    path = Path(path).resolve(strict=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= cap, f"FILE_BYTE_BUDGET: {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino),
            f"FILE_CHANGED_WHILE_HASHING: {path}")
    return dict(path=str(path), sha256=digest, bytes=after.st_size)


def verify(record, label="input") -> Path:
    """Check a pinned record {path, sha256, bytes}; return the path."""
    require(isinstance(record, dict) and set(record) == {"path", "sha256", "bytes"}, f"RECORD_KEYS: {label}")
    require(identity(record["path"]) == record, f"STALE_INPUT: {label}")
    return Path(record["path"])


def read_json(path, cap=64 * MIB):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= cap, f"JSON_BYTE_BUDGET: {path}")
    return json.loads(path.read_text())


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def fresh_workspace(path, base_rev) -> Path:
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    require(base_rev == "empty" and not any(path.iterdir()), "REVISION_CONFLICT: empty workspace required")
    return path


class Profile:
    """A gate profile with every consumed deck and collateral file pinned by SHA-256.

    A path in the profile is relative to the PDK root of the host map, or, with the prefix
    `prepared:`, to the prepared benchmark inputs. `file()` returns an identity record only for
    a pinned path whose bytes match the pin.
    """

    def __init__(self, path, pdk_root, prepared=None, pinned=True):
        self.path = Path(path).resolve(strict=True)
        self.dir = self.path.parent
        self.data = read_json(self.path, 4 * MIB)
        require(self.data.get("schema") == PROFILE_SCHEMA, "PROFILE_SCHEMA")
        self.root = Path(pdk_root).resolve(strict=True)
        self.prepared = Path(prepared).resolve(strict=True) if prepared else None
        self.pins = self.data["files"]
        require(isinstance(self.pins, dict) and (not pinned or 1 <= len(self.pins) <= 4096), "PROFILE_PIN_INVENTORY")
        self._records = {}

    def __getitem__(self, key):
        return self.data[key]

    def locate(self, relative) -> Path:
        base, name = self.root, relative
        if relative.startswith("prepared:"):
            require(self.prepared is not None, "PROFILE_NEEDS_PREPARED_INPUTS")
            base, name = self.prepared, relative[len("prepared:"):]
        require(name and not Path(name).is_absolute() and ".." not in Path(name).parts, f"PROFILE_UNSAFE_PATH: {relative}")
        return base / name

    def file(self, relative) -> dict:
        require(isinstance(relative, str) and relative in self.pins, f"PROFILE_UNPINNED_FILE: {relative}")
        if relative not in self._records:
            target = self.locate(relative)
            require(target.is_file(), f"PROFILE_FILE_MISSING: {relative}")
            record = identity(target)
            require(record["sha256"] == self.pins[relative], f"PROFILE_PIN_MISMATCH: {relative}")
            self._records[relative] = record
        return self._records[relative]

    def expand(self, patterns) -> list:
        """Paths under the PDK root matching any glob, as they are on disk."""
        result = set()
        for pattern in patterns:
            head = pattern.split("*", 1)[0].rsplit("/", 1)[0]
            base = self.root / head
            require(base.is_dir(), f"PROFILE_GLOB_BASE: {pattern}")
            result.update(str(p.relative_to(self.root)) for p in base.rglob("*")
                          if p.is_file() and fnmatch.fnmatchcase(str(p.relative_to(self.root)), pattern))
        return sorted(result)

    def matching(self, patterns) -> list:
        """Pinned paths matching the globs; refuses a file on disk that matches but is not pinned."""
        on_disk = self.expand(patterns)
        pinned = sorted(p for p in self.pins if any(fnmatch.fnmatchcase(p, pattern) for pattern in patterns))
        require(on_disk == pinned, f"PROFILE_INVENTORY_DIFFERS: {patterns}")
        return pinned

    def verify_all(self) -> dict:
        return {relative: self.file(relative) for relative in sorted(self.pins)}

    def macro_instances(self) -> list:
        return [name for spec in self.data["macros"].values() for name in spec["instances"]]


def dbu(value, units) -> int:
    try:
        number = Decimal(str(value)) * units
    except DecimalException as error:
        raise GateError("GEOMETRY_NUMBER") from error
    require(number.is_finite() and abs(number) <= 1 << 50 and number == number.to_integral_value(),
            f"GEOMETRY_NOT_INTEGER_DBU: {value}")
    return int(number)


def lef_macros(paths) -> dict:
    """LEF facts per master: class tokens (upper case), size in um (Decimal), pins {name: {direction, use}}."""
    macros = {}
    for path in paths:
        path = Path(path)
        require(path.stat().st_size <= 64 * MIB, "LEF_BYTE_BUDGET")
        current, pin = None, None
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if current is None:
                start = re.fullmatch(r"MACRO\s+(\S+)", line)
                if start:
                    current = start[1]
                    require(current not in macros and len(macros) < 8192, f"LEF_DUPLICATE_MASTER: {current}")
                    macros[current] = dict(cls=[], size=None, pins={})
                continue
            entry = macros[current]
            if pin is None:
                if re.fullmatch(r"END\s+" + re.escape(current), line):
                    require(entry["size"] is not None, f"LEF_MASTER_WITHOUT_SIZE: {current}")
                    current = None
                    continue
                match = re.fullmatch(r"CLASS\s+(.*?)\s*;", line)
                if match:
                    entry["cls"] = match[1].upper().split()
                    continue
                match = re.fullmatch(r"SIZE\s+([0-9.eE+-]+)\s+BY\s+([0-9.eE+-]+)\s*;", line)
                if match:
                    entry["size"] = (Decimal(match[1]), Decimal(match[2]))
                    continue
                match = re.fullmatch(r"PIN\s+(\S+)", line)
                if match:
                    pin = match[1]
                    entry["pins"][pin] = dict(direction=None, use="SIGNAL")
                continue
            if re.fullmatch(r"END\s+" + re.escape(pin), line):
                pin = None
                continue
            match = re.fullmatch(r"(DIRECTION|USE)\s+(.*?)\s*;", line)
            if match:
                entry["pins"][pin][match[1].lower()] = match[2].upper()
        require(current is None and pin is None, f"LEF_UNFINISHED: {path}")
    require(macros, "LEF_EMPTY")
    return macros


def def_design(path) -> dict:
    """DEF facts: top, DBU per um, die box, components {name: {master, status, x, y, orientation}}."""
    path = Path(path)
    require(path.stat().st_size <= 512 * MIB, "DEF_BYTE_BUDGET")
    top, units, die, declared, components = None, None, None, None, {}
    active, statement = False, ""
    with path.open(errors="replace") as stream:
        for line in stream:
            if not active:
                if line.startswith("DESIGN "):
                    require(top is None, "DEF_DUPLICATE_DESIGN")
                    top = line.split()[1]
                elif line.startswith("UNITS DISTANCE MICRONS "):
                    require(units is None, "DEF_DUPLICATE_UNITS")
                    units = int(line.split()[3])
                elif line.startswith("DIEAREA "):
                    numbers = [int(v) for v in re.findall(r"-?\d+", line)]
                    require(die is None and len(numbers) == 4, "DEF_DIEAREA_RECTANGLE_REQUIRED")
                    die = numbers
                elif line.startswith("COMPONENTS "):
                    require(declared is None, "DEF_DUPLICATE_COMPONENTS")
                    declared = int(line.split()[1])
                    require(0 < declared <= 2_000_000, "DEF_COMPONENT_BUDGET")
                    active = True
                continue
            if line.startswith("END COMPONENTS"):
                require(not statement.strip(), "DEF_UNFINISHED_COMPONENT")
                active = False
                break
            statement += line
            require(len(statement) <= 1 << 20, "DEF_STATEMENT_BUDGET")
            if ";" not in line:
                continue
            head = re.match(r"\s*-\s+(\S+)\s+(\S+)", statement)
            place = re.search(r"\+\s+(FIXED|PLACED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", statement)
            require(head is not None and place is not None and place[4] in ORIENTATIONS,
                    "DEF_COMPONENT_UNSUPPORTED: " + statement.strip()[:120])
            require(head[1] not in components, f"DEF_DUPLICATE_COMPONENT: {head[1]}")
            components[head[1]] = dict(master=head[2], status=place[1], x=int(place[2]), y=int(place[3]),
                                       orientation=place[4])
            statement = ""
    require(not active and declared is not None and len(components) == declared, "DEF_COMPONENT_COUNT")
    require(top is not None and units is not None and die is not None, "DEF_HEADER")
    return dict(top=top, units=units, die=die, components=components)


def placed_box(component, size_um, units) -> list:
    """Bounding box [x0, y0, x1, y1] in DBU of a placed master (DEF origin is the lower-left corner)."""
    width, height = (dbu(v, units) for v in size_um)
    if component["orientation"] in ("W", "E", "FW", "FE"):
        width, height = height, width
    return [component["x"], component["y"], component["x"] + width, component["y"] + height]


def run_tool(argv, log_path, time_path, gnu_time, seconds, env=None, cwd=None, log_cap=256 * MIB) -> dict:
    """Run one external tool under GNU time; kill its process group at the deadline or log cap."""
    command = [str(gnu_time), "-v", "-o", str(time_path), *map(str, argv)]
    tick = time.monotonic()
    with Path(log_path).open("xb") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=env, cwd=cwd)
        reason = None
        try:
            while process.poll() is None:
                if time.monotonic() - tick > seconds:
                    reason = "deadline"
                elif Path(log_path).stat().st_size > log_cap:
                    reason = "log byte budget"
                if reason:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=30)
                    break
                time.sleep(0.2)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=30)
            raise
    text = Path(time_path).read_text() if Path(time_path).is_file() else ""
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    return dict(command=command, exit_code=process.returncode, killed=reason,
                wall_seconds=round(time.monotonic() - tick, 3), peak_rss_kib=int(rss[1]) if rss else None)


def macro_boxes(design, lef, masters) -> dict:
    """Placed bounding box per macro instance of the given masters, in DBU."""
    result = {}
    for name, component in design["components"].items():
        if component["master"] in masters:
            require(component["master"] in lef, f"MACRO_WITHOUT_LEF: {component['master']}")
            result[name] = dict(master=component["master"], orientation=component["orientation"],
                                box=placed_box(component, lef[component["master"]]["size"], design["units"]))
    return result


def magic_setup(config, tools):
    """Verify the pinned Magic collateral of a plan; return (magicrc path, environment additions)."""
    magicrc = verify(config["magicrc"], "magicrc")
    for record in config["tech_files"]:
        verify(record, "tech file")
    lefs = [verify(record, "abstract LEF") for record in config["abstract_lefs"]]
    cells = list(config["abstract_cells"])
    require(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cell) for cell in cells), "ABSTRACT_CELL_HANDLE")
    require(all(":" not in str(path) for path in lefs), "ABSTRACT_LEF_PATH")
    env = dict(os.environ, PDK_ROOT=str(Path(config["pdk_root"]).resolve(strict=True)),
               GATE_ABSTRACT_LEFS=":".join(map(str, lefs)), GATE_ABSTRACT_CELLS=":".join(cells))
    return magicrc, env


def magic_facts(log, abstract_cells) -> dict:
    """Facts Magic printed about its own run; refuses a missing top or a macro that is not abstract."""
    require(not re.search(r"^GATE_TOP_MISSING", log, re.M), "TOP_CELL_MISSING")
    require(not re.search(r"^GATE_ABSTRACT_MISSING", log, re.M), "ABSTRACT_MACRO_NOT_IN_LAYOUT")
    require(re.search(r"^GATE_DONE\s*$", log, re.M) is not None, "MAGIC_RUN_INCOMPLETE")
    seen = dict(re.findall(r"^GATE_ABSTRACT (\S+) (\S*)\s*$", log, re.M))
    require(set(seen) == set(abstract_cells) and all(value.upper() == "TRUE" for value in seen.values()),
            "MACRO_NOT_ABSTRACT")
    facts = {name.lower(): value.strip() for name, value in re.findall(r"^GATE_([A-Z]+) (.*)$", log, re.M)
             if name not in ("ABSTRACT",)}
    require("tech" in facts, "MAGIC_TECH_FACT")
    return facts

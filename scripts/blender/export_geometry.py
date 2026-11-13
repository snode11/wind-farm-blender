"""Export local OpenFAST design geometry as a deterministic, stdlib-only asset.

No solver, NumPy, PyVista or Blender installation is needed. Source checksums
make the packaged geometry auditable without depending on a sibling checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?")
_spec = importlib.util.spec_from_file_location(
    '_wfrl_export_atomic_json', ROOT / 'blender_frontend/wfrl_blender/atomic_json.py')
atomic_json = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atomic_json)


def write_export_files(outputs):
    """Publish related UTF-8 files together, restoring old files on write failure.

    Each replacement is atomic. Rollback covers caught failures, not process
    termination or concurrent writers; callers must own the output directory.
    """
    paths = [Path(path) for path in outputs]
    if not paths or len({path.parent for path in paths}) != 1:
        raise ValueError('Export files must share one output directory')
    with tempfile.TemporaryDirectory(prefix='.wfrl-export-', dir=paths[0].parent) as directory:
        staged, backups = {}, {}
        for index, (path, text) in enumerate(zip(paths, outputs.values())):
            staged[path] = Path(directory) / f'{index}.new'
            atomic_json.write_text(staged[path], text)
            if path.exists():
                backups[path] = Path(directory) / f'{index}.old'
                shutil.copy2(path, backups[path])
                shutil.copymode(path, staged[path])
        replaced = []
        try:
            for path in paths:
                os.replace(staged[path], path)
                replaced.append(path)
        except BaseException:
            for path in reversed(replaced):
                if path in backups:
                    os.replace(backups[path], path)
                else:
                    path.unlink()
            raise


def nums(line):
    return [float(n.replace("D", "E").replace("d", "e")) for n in NUMBER.findall(line)]


def scalar(lines, key):
    for line in lines:
        if key in line.split():
            return nums(line.split(key)[0])[0]
    raise ValueError(f"Missing scalar {key}")


def table(lines, header, columns, count):
    result = []
    active = False
    for line in lines:
        if not active:
            active = header in line
            continue
        fields = line.split('!', 1)[0].split()
        if not fields or fields[0].startswith('('):
            continue
        try:
            row = [float(value.replace('D', 'E').replace('d', 'e')) for value in fields[:columns]]
        except ValueError as exc:
            raise ValueError(f'Invalid numeric row in {header} table: {line}') from exc
        if len(row) != columns or not all(math.isfinite(value) for value in row):
            raise ValueError(f'Invalid or nonfinite row in {header} table: {line}')
        result.append(row)
        if len(result) == count:
            return result
    raise ValueError(f"Incomplete {header} table")


def export(template, output, shell=None, source=None):
    shell = shell or ROOT / "wfrl/turbines/nrel5mw.yaml"
    sources = {}

    def source_path(path):
        path = path.absolute()
        return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()

    def read(path):
        raw = path.read_bytes()
        sources[path.relative_to(template).as_posix()] = hashlib.sha256(raw).hexdigest()
        return raw.decode("latin-1").splitlines()

    base = template / "5MW_Baseline"
    ed = read(template / "FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat")
    ad = read(base / "AD.dat")
    blade = read(base / "NRELOffshrBsline5MW_AeroDyn_blade.dat")
    airfoils = []
    for line in ad:
        match = re.search(r'"([^"]*Airfoils/([^"]+))"', line)
        if not match:
            continue
        polar = base / "Airfoils" / match.group(2)
        polar_lines = read(polar)
        coord_name = next(re.search(r'@\s*"([^"]+)"', ln).group(1)
                          for ln in polar_lines if "NumCoords" in ln and "@" in ln)
        coords = read(polar.parent / Path(coord_name).name)
        pairs = []
        for line in coords:
            fields = line.split('!', 1)[0].split()
            if not fields or 'NumCoords' in fields:
                continue
            try:
                pair = [float(value.replace('D', 'E').replace('d', 'e')) for value in fields[:2]]
            except ValueError as exc:
                raise ValueError(f'Invalid airfoil coordinate in {polar.name}: {line}') from exc
            if len(pair) != 2 or not all(math.isfinite(value) for value in pair):
                raise ValueError(f'Nonfinite or missing airfoil coordinate in {polar.name}: {line}')
            pairs.append(pair)
        airfoils.append({"name": polar.stem, "reference": pairs[0], "coordinates": pairs[1:]})
    shell_raw = shell.read_bytes()
    shell_lines = shell_raw.decode().splitlines()
    shell_dimensions = {}
    for key in ("length", "width", "height", "spinner_radius", "nacelle_x_bias"):
        line = next(ln for ln in shell_lines if re.match(rf"\s*{key}:", ln))
        shell_dimensions[key] = float(line.split(":", 1)[1].split("#")[0])
    result = {
        "schema_version": 1,
        "source": source or (f"Local FAST.Farm template {source_path(template)}; "
                             f"shell dimensions from {source_path(shell)}"),
        "source_sha256": dict(sorted(sources.items())),
        "shell_source_sha256": hashlib.sha256(shell_raw).hexdigest(),
        "units": {"length": "m", "angle": "deg"},
        "scalars": {key: scalar(ed, key) for key in
                    ("TipRad", "HubRad", "PreCone(1)", "OverHang", "ShftTilt", "TowerHt", "Twr2Shft")},
        "blade_columns": ["span", "curve", "sweep", "curve_angle", "twist", "chord", "airfoil_id"],
        "blade_stations": table(blade, "BlSpn", 7, int(scalar(blade, "NumBlNds"))),
        "tower_columns": ["elevation", "diameter"],
        "tower_stations": table(ad, "TwrElev", 2, int(scalar(ad, "NumTwrNds"))),
        "airfoils": airfoils,
        "shell": shell_dimensions,
    }
    assert len(result["blade_stations"]) == 19 and len(airfoils) == 8
    assert all(len(af["coordinates"]) == 399 for af in airfoils)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json.write_text(output, json.dumps(result, separators=(",", ":"),
                                             ensure_ascii=False, allow_nan=False) + "\n")
    print(f"Exported {output}: {output.stat().st_size} bytes, {len(sources)} source hashes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=ROOT / "projects/nrel5mw-reference/template")
    parser.add_argument("--output", type=Path, default=ROOT / "blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json")
    parser.add_argument("--shell", type=Path, default=None,
                        help="Turbine shell YAML; defaults to wfrl/turbines/nrel5mw.yaml")
    parser.add_argument("--source", default=None, help="Override the recorded source description")
    args = parser.parse_args()
    export(args.template, args.output, args.shell, args.source)

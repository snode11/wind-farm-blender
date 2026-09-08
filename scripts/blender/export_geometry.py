"""Export local OpenFAST design geometry as a deterministic, stdlib-only asset.

No solver, NumPy, PyVista or Blender installation is needed. Source checksums
make the packaged geometry auditable without depending on a sibling checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?")


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
        row = nums(line)
        if len(row) >= columns:
            result.append(row[:columns])
            if len(result) == count:
                return result
    raise ValueError(f"Incomplete {header} table")


def export(template, output):
    sources = {}

    def read(path):
        raw = path.read_bytes()
        sources[str(path.relative_to(template))] = hashlib.sha256(raw).hexdigest()
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
        pairs = [nums(ln)[:2] for ln in coords
                 if ln.strip() and not ln.lstrip().startswith("!") and len(nums(ln)) >= 2]
        airfoils.append({"name": polar.stem, "reference": pairs[0], "coordinates": pairs[1:]})
    shell_path = ROOT / "wfrl/turbines/nrel5mw.yaml"
    shell_raw = shell_path.read_bytes()
    shell_lines = shell_raw.decode().splitlines()
    shell = {}
    for key in ("length", "width", "height", "spinner_radius", "nacelle_x_bias"):
        line = next(ln for ln in shell_lines if re.match(rf"\s*{key}:", ln))
        shell[key] = float(line.split(":", 1)[1].split("#")[0])
    result = {
        "schema_version": 1,
        "source": "Local FAST.Farm NREL 5MW template; shell dimensions from wfrl/turbines/nrel5mw.yaml",
        "source_sha256": dict(sorted(sources.items())),
        "shell_source_sha256": hashlib.sha256(shell_raw).hexdigest(),
        "units": {"length": "m", "angle": "deg"},
        "scalars": {key: scalar(ed, key) for key in
                    ("TipRad", "HubRad", "PreCone(1)", "OverHang", "ShftTilt", "TowerHt")},
        "blade_columns": ["span", "curve", "sweep", "curve_angle", "twist", "chord", "airfoil_id"],
        "blade_stations": table(blade, "BlSpn", 7, int(scalar(blade, "NumBlNds"))),
        "tower_columns": ["elevation", "diameter"],
        "tower_stations": table(ad, "TwrElev", 2, int(scalar(ad, "NumTwrNds"))),
        "airfoils": airfoils,
        "shell": shell,
    }
    assert len(result["blade_stations"]) == 19 and len(airfoils) == 8
    assert all(len(af["coordinates"]) == 399 for af in airfoils)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"Exported {output}: {output.stat().st_size} bytes, {len(sources)} source hashes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=ROOT.parent / "wfcrl-env-me/wfcrl/simulators/fastfarm/inputs/template")
    parser.add_argument("--output", type=Path, default=ROOT / "blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json")
    args = parser.parse_args()
    export(args.template, args.output)

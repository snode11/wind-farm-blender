"""Generate a manifest-backed TurbSim library for the NREL 5MW turb3_row layout."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wfrl.inflow import (bts_statistics, file_sha256, load_turbulence_manifest,
                         template_dir, turbulence_dir, turbulence_key, turbulence_name)

DEFAULT_TURBSIM = "/opt/anaconda3/envs/wfrl-mac/bin/turbsim"


def replace_parameters(source, parameters):
    """Replace exactly one value per TurbSim label, preserving line endings."""
    for label, value in parameters.items():
        pattern = re.compile(r'^(\s*)(?:"[^"\r\n]*"|\S+)([ \t]+'
                             + re.escape(label) + r'\b[^\r\n]*)', re.MULTILINE)
        source, count = pattern.subn(lambda match: match[1] + str(value) + match[2], source)
        if count != 1:
            raise ValueError(f"模板参数 {label} 必须恰好出现一次，实际 {count}")
    return source


def _positive(value, label):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} 必须为有限正数")
    return value


def generation_parameters(wind_speed, ti, seed, *, duration=300.0, time_step=0.1,
                          grid_spacing=10.0, simulation_seconds=60.0, scale_iec=1):
    """Set the grid and target longitudinal sigma at the actual 90 m hub height."""
    speed, ti, seed = turbulence_key(wind_speed, ti, seed)
    for value, label in ((duration, "duration"), (time_step, "time_step"),
                         (grid_spacing, "grid_spacing"), (simulation_seconds, "simulation_seconds")):
        _positive(value, label)
    if scale_iec not in (0, 1, 2):
        raise ValueError("scale_iec 必须为 0 / 1 / 2")
    # The 90 m layer lies on the grid; the tall box also contains wake meandering.
    width, height, bottom, hub = 680.0, 340.0, 10.0, 90.0
    for length in (width, height, hub - bottom):
        if not math.isclose(length / grid_spacing, round(length / grid_spacing), abs_tol=1e-8):
            raise ValueError("grid_spacing 必须整除 680、340 和 80 m，以保留轮毂层网格点")
    ny, nz = int(round(width / grid_spacing)) + 1, int(round(height / grid_spacing)) + 1
    if ny * nz > 20000 or duration / time_step > 100000:
        raise ValueError("网格/时间采样过大；本脚本限制 20000 个空间点和 100000 个时间点")
    artificial_hub = bottom + height / 2
    center_speed = speed * (artificial_hub / hub) ** 0.2
    # IEC 61400-1 Ed.3 NTM: sigma1 = Iref * (0.75 * Vhub + 5.6).
    target_ti = (100.0 * {"A": .16, "B": .14, "C": .12}[ti] * (.75 * speed + 5.6) / speed
                 if isinstance(ti, str) else ti)
    # TurbSim defines IECturbc at HubHt, which is the artificial grid centre.
    corrected_ti = target_ti * speed / center_speed
    parameters = {
        "RandSeed1": seed, "RandSeed2": -75384093,
        "Echo": "False", "WrADFF": "True", "WrBHHTP": "False", "WrFHHTP": "False",
        "WrADHH": "False", "WrBLFF": "False", "WrADTWR": "False", "WrFMTFF": "False",
        "WrACT": "False", "ScaleIEC": scale_iec,
        "NumGrid_Y": ny, "NumGrid_Z": nz, "GridWidth": width, "GridHeight": height,
        "HubHt": artificial_hub, "RefHt": hub, "TimeStep": time_step,
        "AnalysisTime": duration, "UsableTime": '"ALL"',
        "URef": speed, "IECturbc": f'"{corrected_ti:.12g}"',
        "IECstandard": '"1-ED3"', "IEC_WindType": '"NTM"', "TurbModel": '"IECKAI"',
        "WindProfileType": '"PL"', "PLExp": '"0.2"', "HFlowAng": 0, "VFlowAng": 0,
        "SCMod1": '"IEC"', "SCMod2": '"IEC"', "SCMod3": '"IEC"',
        "InCDec1": '"12.0 0.00035273"', "InCDec2": '"12.0 0.00035273"',
        "InCDec3": '"12.0 0.00035273"', "CohExp": 0,
    }
    from openfast_toolbox.fastfarm import fastFarmBoxExtent

    scene = _layout()
    try:
        grid = fastFarmBoxExtent(
            np.linspace(-width / 2, width / 2, ny), np.linspace(bottom, bottom + height, nz),
            np.array([0., time_step, duration]), speed, 87.6, 126., scene["x"], scene["y"],
            Cmeander=1.9, chord_max=5, extent_X=1.2, extent_YZ=1.2)
    except Exception as exc:
        raise ValueError(f"FAST.Farm 网格不适用；请减小 time_step 或 grid_spacing: {exc}") from exc
    coverage = check_coverage(grid, [-width / 2, width / 2], [bottom, bottom + height],
                              duration, center_speed, simulation_seconds)
    return parameters, target_ti, coverage


def _layout():
    import yaml

    scene = yaml.safe_load((ROOT / "scenes" / "turb3_row.yaml").read_text(encoding="utf-8"))
    return {"x": [t["x"] for t in scene["layout"]], "y": [t["y"] for t in scene["layout"]]}


def check_coverage(grid, y_range, z_range, duration, advection_speed, simulation_seconds):
    """Check the actual low/high domains, including the eight-diameter wake extent."""
    for axis, bounds in (("Y", y_range), ("Z", z_range)):
        low_min = float(grid[f"{axis}0_Low"])
        low_max = low_min + (grid[f"N{axis}_Low"] - 1) * grid[f"d{axis}_Low"]
        high_min = np.asarray(grid[f"{axis}0_High"])
        high_max = high_min + (grid[f"N{axis}_High"] - 1) * grid[f"d{axis}_High"]
        if (low_min < bounds[0] - 1e-5 or low_max > bounds[1] + 1e-5
                or np.any(high_min < low_min - 1e-5) or np.any(high_max > low_max + 1e-5)):
            raise ValueError(f"BTS 的 {axis} 范围未覆盖 turb3_row 的 FAST.Farm 低/高分辨率域")
    x_span = (grid["NX_Low"] - 1) * grid["dX_Low"]
    required = simulation_seconds + x_span / advection_speed
    if duration + 1e-4 < required:
        raise ValueError(f"duration={duration:g}s 不足，至少需要 {required:.2f}s "
                         f"（{simulation_seconds:g}s 算例加完整低分辨率域平流时间）")
    return {"layout": "turb3_row", "layout_coordinates_m": _layout(),
            "simulation_seconds": simulation_seconds, "required_duration_s": float(required),
            "advection_speed_mps": float(advection_speed),
            "low_domain": {k: float(v) for k, v in grid.items() if k.endswith("_Low")},
            "high_domain": {k: np.asarray(v).tolist() for k, v in grid.items() if k.endswith("_High")}}


def _version(output):
    match = re.search(r"\b(?:TurbSim|OpenFAST)-v([^\s)]+)", output)
    if not match:
        raise RuntimeError("TurbSim 未返回可识别的版本号")
    return "TurbSim-v" + match.group(1)


def _write_manifest(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".manifest-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def generate_library(*, wind_speeds, intensities, seeds, output_dir=None,
                     turbsim=DEFAULT_TURBSIM, template=None, scratch_root="/private/tmp",
                     duration=300.0, time_step=0.1, grid_spacing=10.0,
                     simulation_seconds=60.0, scale_iec=1, threads=2, timeout=3600.0):
    """Generate all requested combinations without replacing existing inputs or boxes."""
    from openfast_toolbox.fastfarm import fastFarmTurbSimExtent
    from openfast_toolbox.io import TurbSimFile

    _positive(timeout, "timeout")
    if type(threads) is not int or threads < 1:
        raise ValueError("threads 必须为正整数")
    executable = Path(turbsim).expanduser().resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"找不到 TurbSim: {executable}")
    template = Path(template or Path(template_dir()) / "90m_08mps.inp").resolve()
    source = template.read_bytes().decode("utf-8")
    combinations = [turbulence_key(*values) for values in itertools.product(wind_speeds, intensities, seeds)]
    if not combinations or len(set(combinations)) != len(combinations):
        raise ValueError("生成列表为空或含重复组合")
    plans = []
    for speed, ti, seed in combinations:
        parameters, target_ti, _ = generation_parameters(
            speed, ti, seed, duration=duration, time_step=time_step, grid_spacing=grid_spacing,
            simulation_seconds=simulation_seconds, scale_iec=scale_iec)
        plans.append((speed, ti, seed, parameters, target_ti, replace_parameters(source, parameters)))
    root = turbulence_dir(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".generation.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"已有生成任务或未清理的锁文件: {lock}") from exc
    os.close(descriptor)
    try:
        manifest = load_turbulence_manifest(root)
        for speed, ti, seed, *_ in plans:
            name = turbulence_name(speed, ti, seed)
            if (any(entry["name"] == name for entry in manifest["boxes"])
                    or any((root / (name + ext)).exists() for ext in (".bts", ".inp", ".sum"))):
                raise FileExistsError(f"拒绝覆盖已有组合 {name}；请使用其他输出目录")
        with tempfile.TemporaryDirectory(prefix="wfrl-turbsim-", dir=scratch_root) as temporary:
            work = Path(temporary)
            env = dict(os.environ, OMP_NUM_THREADS=str(threads))
            version_run = subprocess.run([str(executable), "-v"], cwd=work, env=env,
                                         capture_output=True, text=True, encoding="utf-8",
                                         errors="replace", timeout=30, check=True)
            version = _version(version_run.stdout + version_run.stderr)
            executable_hash = file_sha256(executable)
            for speed, ti, seed, parameters, target_ti, rendered in plans:
                name = turbulence_name(speed, ti, seed)
                inp = work / (name + ".inp")
                inp.write_bytes(rendered.encode("utf-8"))
                print(f"Generating {name} (90 m target TI={target_ti:.3f}%)", flush=True)
                run = subprocess.run([str(executable), str(inp)], cwd=work, env=env,
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=timeout)
                bts = inp.with_suffix(".bts")
                summary = inp.with_suffix(".sum")
                output = run.stdout + run.stderr
                if run.returncode or not bts.is_file() or not summary.is_file():
                    raise RuntimeError(f"TurbSim 生成失败 {name} (exit={run.returncode}):\n{output[-6000:]}")
                if _version(output) != version:
                    raise RuntimeError("TurbSim 运行版本与预检版本不符")
                statistics = bts_statistics(bts, z_hub=90.)
                ts = TurbSimFile(str(bts))
                layout = _layout()
                grid = fastFarmTurbSimExtent(str(bts), 87.6, 126., layout["x"], layout["y"],
                                            Cmeander=1.9, chord_max=5, extent_X=1.2,
                                            extent_YZ=1.2, meanUAtHubHeight=True)
                coverage = check_coverage(grid, statistics["y_range_m"], statistics["z_range_m"],
                                          statistics["time_span_s"], float(ts["uRef"]), simulation_seconds)
                entry = {
                    "name": name, "file": bts.name, "wind_speed_mps": speed, "ti": ti, "seed": seed,
                    "target_hub_ti_percent": target_ti, "input_parameters": parameters,
                    "input_file": inp.name, "input_sha256": file_sha256(inp),
                    "summary_file": summary.name, "summary_sha256": file_sha256(summary),
                    "template_file": template.name, "template_sha256": file_sha256(template),
                    "sha256": file_sha256(bts), "size_bytes": bts.stat().st_size,
                    "turbsim_version": version, "turbsim_executable_sha256": executable_hash,
                    "statistics": statistics, "coverage": coverage,
                }
                published = []
                try:
                    for artifact in (inp, summary, bts):
                        destination = root / artifact.name
                        with destination.open("xb") as stream, artifact.open("rb") as original:
                            published.append(destination)
                            shutil.copyfileobj(original, stream)
                    manifest["boxes"].append(entry)
                    _write_manifest(root / "manifest.json", manifest)
                except BaseException:
                    for destination in published:
                        destination.unlink(missing_ok=True)
                    raise
                print(f"Saved {bts.name}: U90={statistics['mean_speed_mps']:.4f} m/s, "
                      f"TI90={statistics['mean_ti_percent']:.3f}%, "
                      f"{entry['size_bytes']} bytes", flush=True)
                for artifact in (inp, summary, bts):
                    artifact.unlink()
        return manifest
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wind-speeds", nargs="+", type=float, required=True, help="m/s at 90 m")
    intensity = parser.add_mutually_exclusive_group(required=True)
    intensity.add_argument("--ti", nargs="+", type=float, help="TI in percent at 90 m (8 means 8%%)")
    intensity.add_argument("--iec-class", nargs="+", choices=("A", "B", "C"), help="IEC Ed.3 NTM at 90 m")
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--turbsim", default=DEFAULT_TURBSIM)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--scratch-root", default="/private/tmp")
    parser.add_argument("--duration", type=float, default=300.0, help="periodic analysis duration, seconds")
    parser.add_argument("--time-step", type=float, default=0.1)
    parser.add_argument("--grid-spacing", type=float, default=10.0, help="y/z spacing, metres")
    parser.add_argument("--simulation-seconds", type=float, default=60.0, help="domain coverage budget")
    parser.add_argument("--scale-iec", type=int, choices=(0, 1, 2), default=1)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=3600.0, help="seconds per generated box")
    args = vars(parser.parse_args(argv))
    args["intensities"] = args.pop("ti") or args.pop("iec_class")
    args.pop("iec_class", None)
    try:
        generate_library(**args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

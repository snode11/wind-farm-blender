"""Verify YAML BTS selection through SceneRuntime, FastFarmDriver and native MPI."""
from __future__ import annotations

import argparse
from contextlib import chdir
from dataclasses import fields
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--box", required=True, help="library name, bts:U,TI,seed, or absolute path")
    parser.add_argument("--seconds", type=int, choices=range(30, 61, 3), default=30)
    parser.add_argument("--layout", choices=("single", "turb3_row"), default="single")
    parser.add_argument("--fastfarm", default="/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm")
    parser.add_argument("--mpiexec", help="MPI launcher; defaults to the Python environment or PATH")
    parser.add_argument("--library-dir", type=Path)
    parser.add_argument("--scratch-root", default="/private/tmp")
    parser.add_argument("--timeout", type=float, default=180., help="worker timeout, seconds")
    parser.add_argument("--work-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not np.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout 必须为有限正数")
    if args.work_dir is None:
        from wfrl.inflow import turbulence_dir
        from wfrl.mpi_runtime import launcher_environment

        local_mpiexec = Path(sys.executable).with_name("mpiexec")
        launcher = args.mpiexec or (str(local_mpiexec) if local_mpiexec.is_file() else shutil.which("mpiexec"))
        if launcher is None:
            parser.error("找不到 mpiexec；请用 --mpiexec 指定与 mpi4py/FAST.Farm 匹配的启动器")

        with tempfile.TemporaryDirectory(prefix="wfrl-bts-smoke-",
                                         dir=Path(args.scratch_root).resolve()) as temporary:
            work = Path(temporary)
            command = [launcher, "-n", "1", sys.executable, str(Path(__file__).resolve()), "--box", args.box,
                       "--seconds", str(args.seconds), "--fastfarm", str(Path(args.fastfarm).resolve()),
                       "--layout", args.layout, "--work-dir", str(work)]
            if args.library_dir is not None:
                command += ["--library-dir", str(args.library_dir.resolve())]
            env = dict(launcher_environment(), PYTHONDONTWRITEBYTECODE="1", MPLCONFIGDIR=str(work / "mpl"),
                       WFRL_TURBULENCE_DIR=str(turbulence_dir(args.library_dir)),
                       WFCRL_FASTFARM_EXECUTABLE=str(Path(args.fastfarm).resolve()),
                       OMP_NUM_THREADS="1", PYTHONUNBUFFERED="1")
            code = run_worker(command, work, env, args.timeout)
            if code == 0:
                print((work / "result.json").read_text(encoding="utf-8"), flush=True)
        if code:
            parser.exit(code, "FAST.Farm 读取检查未完成；请检查 MPI/求解器输出。\n")
        return

    run_solver(args)


def run_worker(command, work, env, timeout):
    """Supervise only this worker's descendants, including detached MPI daemons."""
    import psutil

    owned = {}
    log = Path(work) / "solver.log"
    with log.open("wb") as stream, subprocess.Popen(
            command, cwd=work, env=env, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True) as process:
        owner = psutil.Process(process.pid)
        deadline = time.monotonic() + timeout
        code = None
        try:
            while process.poll() is None:
                try:
                    for child in owner.children(recursive=True):
                        owned[(child.pid, child.create_time())] = child
                except psutil.NoSuchProcess:
                    pass
                if time.monotonic() >= deadline:
                    code = 124
                    break
                time.sleep(.02)
            if code is None:
                code = process.returncode
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            _, alive = psutil.wait_procs(list(owned.values()), timeout=10.)
            for child in alive:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=5.)
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=5.)
            if alive:
                raise RuntimeError(f"本检查的进程未退出: {[child.pid for child in alive]}")
        if code:
            print(log.read_text(encoding="utf-8", errors="replace")[-6000:], file=sys.stderr)
            if code == 124:
                print(f"FAST.Farm/MPI 超时（{timeout:g}s）；本检查的进程已停止。", file=sys.stderr)
        return code


def short_scene(scene):
    """Use the YAML inputs with zero warmup for an explicitly short smoke case."""
    from wfrl.scene.schema import Scene

    class ShortScene(Scene):
        def make_case(self, max_iter=200):
            case = super().make_case(max_iter)
            case.t_init = 0
            return case

    return ShortScene(**{field.name: getattr(scene, field.name) for field in fields(scene)})


def solver_process(farm):
    """Identify the native process by this job's exact farm input argument."""
    import psutil

    farm = Path(farm).resolve()
    matches = []
    for process in psutil.process_iter(["cmdline"]):
        args = process.info["cmdline"] or []
        if args and Path(args[0]).name.startswith("FAST.Farm"):
            for argument in args[1:]:
                if farm.parent.parent.name not in argument:
                    continue
                try:
                    candidate = Path(argument)
                    if not candidate.is_absolute():
                        candidate = Path(process.cwd()) / candidate
                    if candidate.resolve() == farm:
                        matches.append(process)
                        break
                except psutil.NoSuchProcess:
                    continue
    if len(matches) != 1:
        raise RuntimeError(f"无法唯一识别本算例的 FAST.Farm 进程: {farm}, {[p.pid for p in matches]}")
    return matches[0]


def wait_solver_exit(process, log, timeout=60.):
    """Require native process exit and its normal-termination marker before final output reads."""
    import psutil

    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired as exc:
        raise TimeoutError(f"FAST.Farm 尚未退出: PID {process.pid}") from exc
    if "FAST.Farm terminated normally." not in Path(log).read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("FAST.Farm 进程退出但没有正常结束标记")


def statistics(values):
    values = np.asarray(values, dtype=float)
    valid = values[np.isfinite(values)]
    if not valid.size:
        raise RuntimeError("没有有效的遥测样本")
    return dict(samples=int(valid.size), missing=int(values.size - valid.size), mean=float(valid.mean()),
                std=float(valid.std()), min=float(valid.min()), max=float(valid.max()))


def native_summary(path, seconds):
    """Read a closed native output and require its complete final timestamp."""
    from wfrl.solver_outputs import OutputReader

    reader = OutputReader(path)
    reader.advance(seconds + 1.)
    if reader.error or not reader.interval or reader.partial or reader.future:
        raise RuntimeError(f"原生输出不完整: {path}: {reader.error}")
    data = np.array([[float(value.replace("D", "E")) for value in row] for _, row in reader.interval])
    if abs(data[-1, 0] - seconds) > 5.1e-5:
        raise RuntimeError(f"原生输出末时刻 {data[-1, 0]} 与 TMax={seconds} 不符")
    result = {"last_time_s": float(data[-1, 0]), "rows": len(data), "channels": {}}
    for name, unit in (("GenPwr", "kW"), ("Wind1VelX", "m/s"), ("RotSpeed", "rpm")):
        if name in reader.names:
            index = reader.names.index(name)
            if reader.units[index] != unit:
                raise RuntimeError(f"{name} 单位不匹配: {reader.units[index]}")
            result["channels"][name] = dict(unit=unit, **statistics(data[:, index]))
    return result


def run_solver(args):
    """Run inside the scratch directory owned by the supervising process."""

    from wfrl.mpi_runtime import configure_local_mpi
    from wfrl.inflow import load_turbulence_manifest

    configure_local_mpi()
    from wfrl import paths
    from wfrl.scene import load_scene, SceneRuntime
    import yaml

    with chdir(args.work_dir):
        work = args.work_dir
        paths.RUNS = str(work / "runs")
        scene_data = load_scene(ROOT / "scenes" / "turb3_row.yaml").to_dict()
        if args.layout == "single":
            scene_data["layout"] = scene_data["layout"][:1]
        scene_data["sensors"] = []
        scene_data["inflow"]["turbulence"] = args.box
        yaml_path = work / "scene.yaml"
        yaml_path.write_text(yaml.safe_dump(scene_data), encoding="utf-8")
        runtime = SceneRuntime(short_scene(load_scene(yaml_path)), max_steps=args.seconds // 3)
        frames, raw_frames = [], []
        native_process = None
        try:
            runtime.reset()
            driver = runtime.driver
            interface = driver.env.mdp.interface
            farm = Path(interface._simul_file).resolve()
            native_process = solver_process(farm)
            while True:
                m = runtime.last_measure
                frames.append({"solver_time_s": m["solver_clock"]["value"],
                               "power_mw": np.asarray(m["power"]).tolist(),
                               "hub_speed_mps": np.asarray(m["wind_speed"]).tolist()})
                raw_frames.append({"power_w": interface.current_measures[:, interface.measure_map["power"]].tolist(),
                                   "hub_speed_mps": interface.current_measures[:, interface.measure_map["wind_speed"]].tolist()})
                if runtime.done:
                    break
                runtime.step(yaw_delta=np.zeros(driver.n))
        finally:
            runtime.close(purge=False, strict=True)
        if native_process is None:
            raise RuntimeError("没有取得求解器进程身份")
        wait_solver_exit(native_process, work / "solver.log")
        record = interface.wind_record
        if record["status"] != "completed":
            raise RuntimeError("FAST.Farm MPI 未正常完成")
        manifest_entry = next((box for box in load_turbulence_manifest()["boxes"]
                               if box["sha256"] == record["loaded_inputs"]["wind_file"]["sha256"]), None)
        turbines = []
        for index in range(driver.n):
            power = [frame["power_mw"][index] for frame in frames]
            wind = [frame["hub_speed_mps"][index] for frame in frames]
            if (not np.isfinite(power[1:]).all() or not np.isfinite(wind).all()
                    or not np.isfinite([frame["power_w"][index] for frame in raw_frames]).all()):
                raise RuntimeError(f"T{index + 1} 在线 MPI/Driver 遥测无效")
            turbines.append({"id": f"T{index + 1}", "driver_power_mw": statistics(power),
                             "driver_hub_speed_mps": statistics(wind),
                             "mpi_power_w": statistics([f["power_w"][index] for f in raw_frames]),
                             "mpi_hub_speed_mps": statistics([f["hub_speed_mps"][index] for f in raw_frames]),
                             "native_output": native_summary(farm.with_name(f"{farm.stem}.T{index + 1}.out"), args.seconds)})
        report = {
            "status": "completed", "bts_request": args.box, "layout": args.layout,
            "bts": record["selected_wind_file"]["path"],
            "loaded_sha256": record["loaded_inputs"]["wind_file"]["sha256"],
            "simulation_seconds": args.seconds, "mpi_exchanges": len(frames),
            "solver_times_s": [frame["solver_time_s"] for frame in frames],
            "yaml_selection_preserved": record["requested"]["wind_time_series"] == args.box,
            "solver_pid": native_process.pid, "solver_exited_before_native_read": True,
            "cleanup_confirmed": driver.cleanup_confirmed,
            "manifest_statistics": ({key: manifest_entry["statistics"][key] for key in
                                     ("mean_speed_mps", "mean_ti_percent", "centerline", "time_span_s")}
                                    if manifest_entry else None),
            "turbines": turbines,
            "scope": "zero-warmup YAML/Driver/MPI reading check; startup transient; initial Driver power can be missing",
        }
        (work / "result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                                     allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

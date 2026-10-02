"""Isolated prescribed-speed BeamDyn solves with lossless VTP retention.

Run from the repository root with ``python -m scripts.lidar.run_dual_beam_validation``.
No template, unloaded reference, controller or existing result is modified.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import lzma
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.lidar.run_physics import replace

DEFAULT_TEMPLATE = ROOT / 'results/prebend-20260918/C'
DEFAULT_REFERENCE = ROOT / 'results/prebend-20260918/C-rest'
DEFAULT_EXECUTABLE = Path('/opt/anaconda3/envs/wfrl-mac/bin/openfast')
SURFACE_KINDS = ('Blade1', 'Blade2', 'Blade3', 'Tower', 'Nacelle', 'Hub')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def validate_parameters(wind, duration, fps, dt, rpm, pitch, azimuth, evaluation_start):
    if not all(math.isfinite(v) for v in (wind, duration, fps, dt, rpm, pitch, azimuth, evaluation_start)):
        raise ValueError('All physical parameters must be finite')
    if not (3 <= wind <= 20 and duration > evaluation_start >= 18 and fps > 0
            and int(fps) == fps and 0 < dt <= .00625 and 0 < rpm <= 12.1
            and 0 <= pitch <= 90 and 0 <= azimuth < 360):
        raise ValueError('Invalid wind/duration/output grid/rotor/pitch/azimuth/evaluation window')
    if not math.isclose(1 / (fps * dt), round(1 / (fps * dt)), abs_tol=1e-8):
        raise ValueError('1/fps must be an integer multiple of DT')
    if fps * dt > 1 or not math.isclose(duration * fps, round(duration * fps), abs_tol=1e-8):
        raise ValueError('Duration must end on the configured output grid')


def input_inventory(directory):
    directory = Path(directory)
    paths = list((directory / 'FarmInputs').glob('*.fst')) + list((directory / 'FarmInputs').glob('*.dat'))
    paths += [p for p in (directory / '5MW_Baseline').rglob('*') if p.is_file()]
    return [dict(path=str(p.relative_to(directory)), sha256=digest(p), bytes=p.stat().st_size)
            for p in sorted(paths)]


def prepare(output, *, template=DEFAULT_TEMPLATE, reference=DEFAULT_REFERENCE,
            executable=DEFAULT_EXECUTABLE, wind=12., duration=30., fps=40,
            dt=.00625, rpm=9., pitch=0., azimuth=0., evaluation_start=18.):
    validate_parameters(wind, duration, fps, dt, rpm, pitch, azimuth, evaluation_start)
    output, template, reference = map(lambda p: Path(p).resolve(), (output, template, reference))
    executable = Path(shutil.which(str(executable)) or executable).resolve()
    if output.exists():
        raise FileExistsError(output)
    # Validate everything before creating the task-owned directory.
    config_path = template / 'probe-config.json'
    reference_config = reference / 'probe-config.json'
    config = json.loads(config_path.read_text())
    rest = json.loads(reference_config.read_text())
    if (config.get('structural_module') != 'BeamDyn' or not rest.get('unloaded')
            or rest.get('structural_module') != 'BeamDyn'
            or config.get('reference_curve') != rest.get('reference_curve')):
        raise ValueError('Matching independently unloaded BeamDyn reference is required')
    names = ('FFTest_WT1.fst', 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat', 'InflowWind.dat')
    for name in names:
        if not (template / 'FarmInputs' / name).is_file():
            raise FileNotFoundError(template / 'FarmInputs' / name)
    baseline_inputs = [p for p in (template / '5MW_Baseline').rglob('*') if p.is_file()
                       and (p.suffix.lower() == '.dat' or p.name.endswith('_coords.txt'))]
    if not baseline_inputs:
        raise ValueError('Template has no baseline physical inputs')
    for name in ('NRELOffshrBsline5MW_BeamDyn.dat', 'NRELOffshrBsline5MW_BeamDyn_Blade.dat'):
        if digest(template / '5MW_Baseline' / name) != digest(reference / '5MW_Baseline' / name):
            raise ValueError('Loaded and unloaded BeamDyn input hashes differ: ' + name)
    solver_hash = digest(executable)
    source_hashes = {str(p.relative_to(template)): digest(p) for p in baseline_inputs}
    source_hashes.update({f'FarmInputs/{name}': digest(template / 'FarmInputs' / name) for name in names})
    output.mkdir(parents=True, exist_ok=False)
    (output / 'FarmInputs').mkdir()
    for name in names:
        shutil.copy2(template / 'FarmInputs' / name, output / 'FarmInputs' / name)
    for source in baseline_inputs:
        destination = output / source.relative_to(template)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    farm = output / 'FarmInputs'
    replace(farm / names[0], dict(TMax=duration, DT=dt, CompElast=2, CompServo=0,
            CompAero=2, CompInflow=1, Gravity=9.80665, WrVTK=2, VTK_type=1,
            VTK_fields='False', VTK_fps=fps, DT_Out=1 / fps, TStart=0,
            OutFileFmt=1, OutFmt='"ES16.8E3"', SumPrint='True',
            **{f'BDBldFile({b})': '"../5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat"' for b in (1, 2, 3)}))
    replace(farm / names[1], dict(GenDOF='False', DrTrDOF='False', YawDOF='False',
            RotSpeed=rpm, Azimuth=azimuth, NacYaw=0, FlapDOF1='False',
            FlapDOF2='False', EdgeDOF='False', TwFADOF1='True', TwFADOF2='True',
            TwSSDOF1='True', TwSSDOF2='True', **{f'BlPitch({b})': pitch for b in (1, 2, 3)}))
    replace(farm / names[2], dict(WindType=1, HWindSpeed=wind, PLExp=0))
    inputs = input_inventory(output)
    config.update(duration_s=duration, dt_s=dt, unloaded=False, wind_mps=wind,
                  fps=fps, rpm=rpm, pitch_deg=pitch, azimuth_deg=azimuth,
                  inputs={r['path']: r['sha256'] for r in inputs},
                  scope='Independent full-window single-turbine prescribed-speed numerical validation; no ServoDyn or MAPPO')
    save_json(output / 'probe-config.json', config)
    run_config = dict(schema='wfrl.dual-beam-validation-run.v1', status='PREPARED_ONLY',
            duration_s=duration, fps=fps, dt_s=dt, wind_mps=wind, rpm=rpm,
            pitch_deg=pitch, azimuth_deg=azimuth,
            evaluation_window_s=[evaluation_start, duration], reference_path=str(reference),
            template_path=str(template), executable=str(executable), solver_sha256=solver_hash,
            template_config_sha256=digest(config_path), template_input_hashes=source_hashes,
            reference_config_sha256=digest(reference_config), inputs=inputs,
            controller='Prescribed rotor speed/fixed pitch; CompServo=0; no policy checkpoint',
            telemetry_boundary='Electrical generator power/torque unavailable because ServoDyn is disabled',
            archive='Lossless xz preset 6; original byte hashes verified by decompression before plain-file removal',
            provenance_boundary='Independent numerical run with deterministic steady wind; no random seed, device or field validation')
    save_json(output / 'run-config.json', run_config)
    return output


class SurfaceArchiver:
    """Only archive newly generated files inside this task-owned run directory."""
    def __init__(self, run):
        self.run = Path(run).resolve()
        self.previous = {}
        self.surfaces = []

    def _compress_surface(self, candidate):
        path, signature = candidate
        stat = path.stat()
        # OpenFAST writes each frame once. Stable size alone is insufficient:
        # require both the closing tag and a fully parseable XML document.
        with path.open('rb') as stream:
            stream.seek(max(0, stat.st_size - 128))
            if b'</VTKFile>' not in stream.read():
                return None
        try:
            if ET.parse(path).getroot().tag != 'VTKFile':
                return None
        except ET.ParseError:
            return None
        original_hash = digest(path)
        compressed = path.with_name(path.name + '.xz')
        if compressed.exists():
            raise FileExistsError(compressed)
        temporary = compressed.with_name(compressed.name + '.tmp')
        with path.open('rb') as source, lzma.open(temporary, 'wb', preset=6) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        check = hashlib.sha256()
        with lzma.open(temporary, 'rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                check.update(chunk)
        final_stat = path.stat()
        if ((final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns) != signature
                or check.hexdigest() != original_hash):
            raise ValueError('Surface changed or lossless verification failed: ' + str(path))
        entry = dict(path=str(path.relative_to(self.run)),
                compressed_path=str(compressed.relative_to(self.run)),
                sha256=original_hash, bytes=stat.st_size,
                compressed_sha256=digest(temporary), compressed_bytes=temporary.stat().st_size)
        return path, temporary, compressed, entry

    def scan(self):
        candidates = sorted((self.run / 'FarmInputs/vtk').glob('*.vtp'))
        current, ready = {}, []
        for path in candidates:
            if path.is_symlink():
                raise ValueError('Refusing symlink surface: ' + str(path))
            stat = path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            current[path] = signature
            if self.previous.get(path) == signature:
                ready.append((path, signature))
        completed = []
        try:
            # liblzma releases the GIL; each worker owns one temporary file.
            # Inventory and original-file removal remain on the main thread.
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(self._compress_surface, candidate) for candidate in ready]
                first_error = None
                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception as exc:
                        first_error = first_error or exc
                        continue
                    if result is None:
                        continue
                    path, temporary, compressed, entry = result
                    temporary.replace(compressed)
                    self.surfaces.append(entry)
                    completed.append(path)
                if first_error is not None:
                    raise first_error
        finally:
            if completed:
                # Persist one batch before removing any verified original.
                self.surfaces.sort(key=lambda row: row['path'])
                self.write_inventory()
                for path in completed:
                    path.unlink()
                    current.pop(path, None)
            # Interrupted/failed worker results retain their original VTP.
            for path, _ in ready:
                temporary = path.with_name(path.name + '.xz.tmp')
                if temporary.exists():
                    temporary.unlink()
            self.previous = current

    def write_inventory(self, **extra):
        save_json(self.run / 'compressed-surfaces.json', dict(
                schema='wfrl.lossless-openfast-surfaces.v1', surfaces=self.surfaces,
                original_bytes=sum(r['bytes'] for r in self.surfaces),
                compressed_bytes=sum(r['compressed_bytes'] for r in self.surfaces), **extra))


def read_output_clock(path):
    """Parse native ASCII rows strictly, without importing training dependencies."""
    lines = Path(path).read_text(encoding='latin-1').splitlines()
    headers = [(index, line.split()) for index, line in enumerate(lines)
               if line.split()[:1] == ['Time'] and len(line.split()) > 1]
    if len(headers) != 1:
        raise ValueError('Native output must have one unique Time channel header')
    index, names = headers[0]
    if len(names) != len(set(names)) or index + 1 >= len(lines):
        raise ValueError('Invalid native output channel header')
    if len(lines[index + 1].split()) != len(names):
        raise ValueError('Native output channel/units column counts differ')
    times = []
    for line in lines[index + 2:]:
        tokens = line.split()
        if not tokens:
            continue
        if len(tokens) != len(names):
            raise ValueError('Malformed native output numerical row column count')
        try:
            values = [float(value) for value in tokens]
        except ValueError as error:
            raise ValueError('Malformed native output numerical value') from error
        if not all(math.isfinite(value) for value in values):
            raise ValueError('Nonfinite native output numerical value')
        times.append(values[0])
    if not times or any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError('Native output time axis must be nonempty and strictly increasing')
    return times


def audit_frames(run, surfaces, config):
    indexed = {}
    ground = []
    pattern = re.compile(r'^FFTest_WT1\.(' + '|'.join(SURFACE_KINDS) + r')Surface\.(\d+)\.vtp$')
    for row in surfaces:
        name = Path(row['path']).name
        match = pattern.fullmatch(name)
        if match:
            kind, frame = match[1], int(match[2])
            if (kind, frame) in indexed:
                raise ValueError('Duplicate surface frame: ' + name)
            indexed[kind, frame] = row
        elif name == 'FFTest_WT1.GroundSurface.vtp':
            ground.append(row)
        else:
            raise ValueError('Unexpected surface filename: ' + name)
    expected = range(round(config['duration_s'] * config['fps']) + 1)
    missing = [dict(kind=kind, frame=frame) for frame in expected for kind in SURFACE_KINDS
               if (kind, frame) not in indexed]
    extra = [dict(kind=kind, frame=frame) for kind, frame in indexed if frame not in expected]
    if missing or extra or len(ground) != 1:
        raise ValueError(f'Incomplete full-run surface clock: missing={missing[:12]}, extra={extra[:12]}, ground={len(ground)}')
    # Check the actual numerical clock; zero-padding width is never assumed.
    times = read_output_clock(Path(run) / 'FarmInputs/FFTest_WT1.out')
    if len(times) != len(expected):
        raise ValueError('Numerical output does not contain the complete configured 0-duration clock')
    maximum_error = max(abs(time_value - index / config['fps']) for index, time_value in enumerate(times))
    if maximum_error > 5.1e-5:
        raise ValueError('Numerical output does not contain the complete configured 0-duration clock')
    return dict(status='COMPLETE', kinds=list(SURFACE_KINDS), frame_count=len(expected),
            first_frame=0, last_frame=len(expected) - 1, ground_count=1,
            time_mapping='frame i is solver output time i/fps; verified against native .out clock',
            max_printed_time_error_s=maximum_error)


def directory_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file())


def terminate_process(process):
    if process.poll() is not None:
        return
    process.terminate()
    # A stopped POSIX process must be continued to handle SIGTERM.
    if os.name == 'posix':
        process.send_signal(signal.SIGCONT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def execute(run, *, poll_seconds=1., budget_bytes=1_000_000_000, budget_root=None):
    run = Path(run).resolve()
    if not math.isfinite(poll_seconds) or poll_seconds <= 0 or budget_bytes <= 0:
        raise ValueError('Polling interval and storage budget must be positive')
    if os.name != 'posix':
        raise RuntimeError('This bounded-storage runner requires POSIX SIGSTOP/SIGCONT backpressure')
    config = json.loads((run / 'run-config.json').read_text())
    if config['status'] != 'PREPARED_ONLY':
        raise ValueError('Only a fresh PREPARED_ONLY run can execute')
    root = Path(budget_root or run).resolve()
    if not run.is_relative_to(root):
        raise ValueError('Budget root must contain this new run')
    executable = Path(config['executable'])
    if digest(executable) != config['solver_sha256']:
        raise ValueError('Solver changed after preparation')
    archiver = SurfaceArchiver(run)
    status = dict(status='FAILED', exit_code=None, executable=str(executable),
                  sha256=config['solver_sha256'], storage_budget_bytes=budget_bytes,
                  budget_root=str(root), peak_budget_root_bytes=directory_bytes(root))
    process = None
    started = time.monotonic()
    config['status'] = 'RUNNING'
    save_json(run / 'run-config.json', config)
    try:
        if status['peak_budget_root_bytes'] >= budget_bytes:
            raise RuntimeError('Storage budget already exhausted before solver launch')
        with (run / 'solver.log').open('wb') as log:
            process = subprocess.Popen([str(executable), 'FFTest_WT1.fst'], cwd=run / 'FarmInputs',
                    env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'},
                    stdout=log, stderr=subprocess.STDOUT)
            while process.poll() is None:
                time.sleep(poll_seconds)
                stopped = process.poll() is None
                if stopped:
                    try:
                        process.send_signal(signal.SIGSTOP)
                    except ProcessLookupError:
                        stopped = False
                try:
                    size = directory_bytes(root)
                    status['peak_budget_root_bytes'] = max(status['peak_budget_root_bytes'], size)
                    if size >= budget_bytes:
                        raise RuntimeError('Storage budget reached; solver stopped and all evidence retained')
                    # Two scans while paused establish that closed files cannot
                    # change underneath compression, without slowing the solver clock.
                    archiver.scan()
                    archiver.scan()
                    size = directory_bytes(root)
                    if size >= budget_bytes:
                        raise RuntimeError('Storage budget reached; solver stopped and all evidence retained')
                    status['storage_warning'] = 'NEAR_BUDGET' if size >= .9 * budget_bytes else None
                finally:
                    if stopped and process.poll() is None:
                        try:
                            process.send_signal(signal.SIGCONT)
                        except ProcessLookupError:
                            pass
            status['exit_code'] = process.returncode
        archiver.scan()
        archiver.scan()
        if process.returncode:
            raise RuntimeError(f'OpenFAST exited with code {process.returncode}; see solver.log')
        if 'OpenFAST terminated normally.' not in (run / 'solver.log').read_text(errors='replace'):
            raise ValueError('Normal solver termination was not recorded')
        audit = audit_frames(run, archiver.surfaces, config)
        archiver.write_inventory(frame_audit=audit, status='COMPLETE')
        status.update(status='COMPLETE', frame_audit=audit)
        config['status'] = 'SOLVER_COMPLETE'
    except Exception as exc:
        if process is not None:
            terminate_process(process)
            status['exit_code'] = process.returncode
        status['error'] = f'{type(exc).__name__}: {exc}'
        config['status'] = 'FAILED'
        # Retain every finished file even on a solver or budget failure.
        try:
            archiver.scan()
            archiver.scan()
        except Exception as archive_error:
            status['archive_error'] = f'{type(archive_error).__name__}: {archive_error}'
        archiver.write_inventory(status='FAILED')
        raise
    finally:
        status.update(elapsed_wall_s=time.monotonic() - started,
                      retained_budget_root_bytes=directory_bytes(root),
                      compressed_surface_count=len(archiver.surfaces))
        save_json(run / 'exit-status.json', status)
        save_json(run / 'run-config.json', config)
    return status


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--template', type=Path, default=DEFAULT_TEMPLATE)
    p.add_argument('--reference', type=Path, default=DEFAULT_REFERENCE)
    p.add_argument('--executable', type=Path, default=DEFAULT_EXECUTABLE)
    p.add_argument('--wind', type=float, default=12.)
    p.add_argument('--duration', type=float, default=30.)
    p.add_argument('--fps', type=int, default=40)
    p.add_argument('--dt', type=float, default=.00625)
    p.add_argument('--rpm', type=float, default=9.)
    p.add_argument('--pitch', type=float, default=0.)
    p.add_argument('--azimuth', type=float, default=0.)
    p.add_argument('--evaluation-start', type=float, default=18.)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--budget-root', type=Path)
    p.add_argument('--storage-budget-bytes', type=int, default=1_000_000_000)
    a = p.parse_args()
    run = prepare(a.output, template=a.template, reference=a.reference, executable=a.executable,
                  wind=a.wind, duration=a.duration, fps=a.fps, dt=a.dt, rpm=a.rpm,
                  pitch=a.pitch, azimuth=a.azimuth, evaluation_start=a.evaluation_start)
    if not a.prepare_only:
        execute(run, budget_bytes=a.storage_budget_bytes, budget_root=a.budget_root)
    print(run, flush=True)


if __name__ == '__main__':
    main()

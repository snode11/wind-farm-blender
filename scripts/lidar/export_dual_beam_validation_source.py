"""Export an independently executed single-turbine engineering screen as v3.

Every exported sample comes from a declared raw solver index and actual VTP
bytes, optionally XZ compressed. There is no time interpolation, synthetic
amplitude, policy replay, or reuse of the historical B2 measurements.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import lzma
import os
from pathlib import Path
import re

import numpy as np
from scipy.spatial.transform import Rotation

from wfrl.blender_bridge.blade_flex_export import fit_sections
from wfrl.camera_video.data import blade_root_frame
from wfrl.lidar.physics import read_surface

ROOT = Path(__file__).resolve().parents[2]
TIME_TOLERANCE_S = 5.1e-5
SURFACE_TOLERANCE_M = .002
COMPONENT_TOLERANCE_M = .005
EFFECTIVE_LENGTH_M = 63.0079360081
BEAM_FILES = ('NRELOffshrBsline5MW_BeamDyn.dat',
              'NRELOffshrBsline5MW_BeamDyn_Blade.dat')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                   separators=(',', ':'))+'\n')


def exact_output_grid(columns, fps, duration_s):
    """Accept rounded ASCII Time only after checking the complete raw clock."""
    if fps not in (40, 80) or not np.isfinite(duration_s) or duration_s <= 0:
        raise ValueError('Expected declared 40/80 Hz positive-duration run')
    last_index = round(duration_s*fps)
    if abs(last_index/fps-duration_s) > 1e-9:
        raise ValueError('Run duration is not on the declared sampling grid')
    expected = np.arange(last_index+1, dtype=float)/fps
    raw = np.asarray(columns.get('Time', []), float)
    if (raw.shape != expected.shape or not np.isfinite(raw).all()
            or np.any(np.diff(raw) <= 0)):
        raise ValueError('Incomplete, duplicated or nonfinite raw output clock')
    error = float(np.max(abs(raw-expected)))
    if error > TIME_TOLERANCE_S:
        raise ValueError('Raw output clock differs from declared sample indices')
    for name, values in columns.items():
        array = np.asarray(values, float)
        if array.shape != expected.shape or not np.isfinite(array).all():
            raise ValueError('Missing/nonfinite raw samples: '+name)
    return expected, dict(raw_sample_count=len(raw), max_ascii_time_error_s=error,
                         tolerance_s=TIME_TOLERANCE_S,
                         method='complete zero-origin raw clock checked, then index/fps; no interpolation')


def read_output(path):
    """Strict ASCII parser: malformed numerical rows cannot disappear silently."""
    lines = Path(path).read_text(encoding='latin-1').splitlines()
    headers = [(i, line.split()) for i, line in enumerate(lines)
               if line.split()[:1] == ['Time'] and len(line.split()) > 1]
    if len(headers) != 1:
        raise ValueError('Ambiguous/missing raw output channel header')
    header, names = headers[0]
    units = lines[header+1].split()
    if len(names) != len(set(names)) or len(units) != len(names):
        raise ValueError('Invalid raw output channel names/units')
    rows = []
    for line in lines[header+2:]:
        tokens = line.split()
        if not tokens:
            continue
        if len(tokens) != len(names):
            raise ValueError('Malformed/missing raw output numerical row')
        try:
            rows.append([float(value) for value in tokens])
        except ValueError as error:
            raise ValueError('Malformed raw output numerical row') from error
    if not rows:
        raise ValueError('No raw output samples')
    values = np.asarray(rows, float)
    return {name: values[:, i] for i, name in enumerate(names)}, dict(zip(names, units))


def _relative_file(folder, name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or str(path) in ('', '.'):
        raise ValueError('Surface inventory path must be run-relative')
    resolved = (folder/path).resolve()
    if not resolved.is_relative_to(folder.resolve()):
        raise ValueError('Surface inventory path escapes run')
    return resolved


class SurfaceInventory:
    """Verify compressed and original bytes before parsing one actual surface."""
    def __init__(self, run):
        self.run = Path(run).resolve()
        self.path = self.run/'compressed-surfaces.json'
        content = json.loads(self.path.read_text())
        entries = content.get('surfaces')
        if not isinstance(entries, list):
            raise ValueError('Missing compressed surface inventory')
        self.entries, self.used = {}, {}
        for entry in entries:
            name = entry.get('path', entry.get('name'))
            if not isinstance(name, str) or name in self.entries:
                raise ValueError('Ambiguous/invalid surface inventory entry')
            _relative_file(self.run, name)
            for field in ('compressed_path', 'sha256', 'bytes',
                          'compressed_sha256', 'compressed_bytes'):
                if field not in entry:
                    raise ValueError('Incomplete compressed surface inventory entry')
            _relative_file(self.run, entry['compressed_path'])
            self.entries[name] = dict(entry, path=name)

    def surface(self, kind, frame):
        pattern = re.compile(r'FFTest_WT1\.'+re.escape(kind)+r'Surface\.(\d+)\.vtp$')
        matches = [entry for name, entry in self.entries.items()
                   if (match := pattern.fullmatch(Path(name).name))
                   and int(match[1]) == frame]
        if len(matches) != 1:
            raise ValueError(f'Ambiguous/missing actual {kind} surface at frame {frame}')
        entry = matches[0]
        compressed = _relative_file(self.run, entry['compressed_path']).read_bytes()
        if (len(compressed) != entry['compressed_bytes']
                or hashlib.sha256(compressed).hexdigest() != entry['compressed_sha256']):
            raise ValueError('Compressed surface integrity mismatch: '+entry['path'])
        try:
            raw = lzma.decompress(compressed)
        except lzma.LZMAError as error:
            raise ValueError('Invalid XZ surface: '+entry['path']) from error
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Original surface integrity mismatch: '+entry['path'])
        self.used[entry['path']] = entry
        return read_surface(BytesIO(raw))


def _input_values(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        tokens = line.split()
        if len(tokens) >= 2:
            result[tokens[1]] = tokens[0].strip('"')
    return result


def _beam_curve(path):
    lines = Path(path).read_text().splitlines()
    starts = [i+2 for i, line in enumerate(lines) if line.split()[:1] == ['kp_xr']]
    if len(starts) != 1:
        raise ValueError('Missing BeamDyn reference coordinates')
    try:
        return np.asarray([[float(v) for v in line.split()[:4]]
                           for line in lines[starts[0]:starts[0]+49]])
    except ValueError as error:
        raise ValueError('Invalid BeamDyn reference coordinates') from error


def validate_reference(run, reference, config):
    """Compare actual structure and declared curve; a loaded rest is rejected."""
    run, reference = Path(run).resolve(), Path(reference).resolve()
    # Absolute paths in the original declaration are historical namespaces.
    # A relocated copy is identified by frozen bytes, not its former mount point.
    if digest(reference/'probe-config.json') != config.get('reference_config_sha256'):
        raise ValueError('Independent reference config differs from frozen run hash')
    inventory_path = reference/'reference-inventory.json'
    if not inventory_path.is_file():
        raise ValueError('Frozen independent reference inventory is required')
    expected_inventory = config.get('reference_inventory_sha256')
    declaration_path = reference.parent/'protocol-declaration.json'
    if expected_inventory is None and declaration_path.is_file():
        expected_inventory = json.loads(declaration_path.read_text()).get('reference_inventory_sha256')
    if not expected_inventory or digest(inventory_path) != expected_inventory:
        raise ValueError('Reference inventory differs from frozen pre-solver hash')
    inventory = json.loads(inventory_path.read_text())
    required_reference = {'probe-config.json', 'FarmInputs/FFTest_WT1.fst',
        'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat',
        *(f'5MW_Baseline/{name}' for name in BEAM_FILES),
        *(f'FarmInputs/vtk/FFTest_WT1.{kind}Surface.0.vtp'
          for kind in ('Blade1', 'Blade2', 'Blade3', 'Nacelle', 'Tower'))}
    if not required_reference.issubset(inventory):
        raise ValueError('Frozen reference inventory misses required physics/surfaces')
    for name, record in inventory.items():
        path = _relative_file(reference, name)
        if digest(path) != record['sha256'] or path.stat().st_size != record['bytes']:
            raise ValueError('Frozen reference file integrity mismatch: '+name)
    loaded = json.loads((run/'probe-config.json').read_text())
    rest = json.loads((reference/'probe-config.json').read_text())
    if (loaded.get('structural_module') != 'BeamDyn'
            or rest.get('structural_module') != 'BeamDyn'
            or loaded.get('unloaded') or not rest.get('unloaded')):
        raise ValueError('A loaded BeamDyn run and separate unloaded reference are required')
    curve = _beam_curve(run/'5MW_Baseline'/BEAM_FILES[0])
    other = _beam_curve(reference/'5MW_Baseline'/BEAM_FILES[0])
    if (curve.shape != (49, 4) or not np.isfinite(curve).all()
            or not np.array_equal(curve, other)
            or not np.allclose(curve, loaded.get('reference_curve'), atol=1e-9, rtol=0)
            or not np.allclose(curve, rest.get('reference_curve'), atol=1e-9, rtol=0)
            or digest(run/'5MW_Baseline'/BEAM_FILES[1])
            != digest(reference/'5MW_Baseline'/BEAM_FILES[1])):
        raise ValueError('Independent reference structure/curve mismatch')
    fst = _input_values(reference/'FarmInputs/FFTest_WT1.fst')
    ed = _input_values(reference/'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat')
    if (float(fst.get('Gravity', 'nan')) != 0 or fst.get('CompInflow') != '0'
            or float(ed.get('RotSpeed', 'nan')) != 0):
        raise ValueError('Reference is not zero-gravity/still-air/stationary')
    return loaded, rest, curve


def _load_scalars(run, reference):
    scalars = json.loads((ROOT/'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json').read_text())['scalars']
    keys = ('TipRad', 'HubRad', 'OverHang', 'TowerHt', 'Twr2Shft', 'ShftTilt', 'PreCone(1)')
    for folder in (run, reference):
        inputs = _input_values(folder/'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat')
        if any(not np.isclose(float(inputs.get(key, 'nan')), scalars[key], atol=1e-8, rtol=0)
               for key in keys):
            raise ValueError('Actual turbine geometry differs from root-frame scalars')
    return scalars


def export(run, reference, output, start=None, end=None):
    run, reference, output = map(lambda p: Path(p).resolve(), (run, reference, output))
    if output.exists():
        raise ValueError('Output directory already exists')
    config = json.loads((run/'run-config.json').read_text())
    exit_path = run/'exit-status.json'
    completion = json.loads(exit_path.read_text()) if exit_path.is_file() else {}
    if (config.get('status') != 'SOLVER_COMPLETE' or completion.get('status') != 'COMPLETE'
            or completion.get('exit_code') != 0):
        raise ValueError('Successful complete runner execution is not proven')
    for item in config.get('inputs', []):
        path = _relative_file(run, item['path'])
        if digest(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('Actual solver input integrity mismatch: '+item['path'])
    loaded, rest, curve = validate_reference(run, reference, config)
    raw_path = run/'FarmInputs/FFTest_WT1.out'
    columns, units = read_output(raw_path)
    complete_times, clock = exact_output_grid(columns, config['fps'], config['duration_s'])
    log_path = run/'solver.log'
    if not log_path.is_file() or 'OpenFAST terminated normally.' not in log_path.read_text(errors='replace'):
        raise ValueError('Complete solver execution is not proven')
    start = complete_times[0] if start is None else float(start)
    end = complete_times[-1] if end is None else float(end)
    indices = np.arange(round(start*config['fps']), round(end*config['fps'])+1)
    if (len(indices) < 2 or start < 0 or end > config['duration_s']
            or abs(indices[0]/config['fps']-start) > 1e-9
            or abs(indices[-1]/config['fps']-end) > 1e-9):
        raise ValueError('Export window must be completely covered on the declared grid')
    times = complete_times[indices]
    channels = ('YawPzn', 'Azimuth', 'RotSpeed', 'BlPitch1', 'BlPitch2', 'BlPitch3')
    if any(name not in columns for name in channels):
        raise ValueError('Missing actual rigid-pose channels')
    expected_units = dict(Time='(s)', RotSpeed='(rpm)',
                          **{name: '(deg)' for name in channels if name != 'RotSpeed'})
    if any(units.get(name) != unit for name, unit in expected_units.items()):
        raise ValueError('Unexpected raw clock/rigid-pose channel units')
    for blade in (1, 2, 3):
        for axis in 'xyz':
            if units.get(f'B{blade}TipTD{axis}r') != '(m)':
                raise ValueError('Missing BeamDyn tip channel or unexpected displacement unit')
    poses = np.column_stack([columns[name][indices] for name in channels])
    poses[:, 1] = np.degrees(np.unwrap(np.radians(columns['Azimuth'])))[indices]
    scalars = _load_scalars(run, reference)
    inventory = SurfaceInventory(run)
    reference_hashes = {}

    def rest_surface(kind):
        candidates = [p for p in (reference/'FarmInputs/vtk').glob(f'FFTest_WT1.{kind}Surface.*.vtp')
                      if int(p.stem.rsplit('.', 1)[1]) == 0]
        if len(candidates) != 1:
            raise ValueError('Ambiguous/missing unloaded reference '+kind)
        path = candidates[0]
        reference_hashes[str(path)] = digest(path)
        return read_surface(path)

    blade_refs, blade_triangles = [], None
    for blade in (1, 2, 3):
        points, triangles = rest_surface(f'Blade{blade}')
        if len(points) % 19:
            raise ValueError('Expected 19 ordered AeroDyn sections')
        if blade_triangles is not None and not np.array_equal(triangles, blade_triangles):
            raise ValueError('Reference blade topologies differ')
        blade_refs.append(points.reshape(19, -1, 3))
        blade_triangles = triangles
    refs = np.asarray(blade_refs)
    nacelle_ref, nacelle_triangles = rest_surface('Nacelle')
    tower_ref, tower_triangles = rest_surface('Tower')
    heights = np.unique(tower_ref[:, 2])
    groups = [np.flatnonzero(tower_ref[:, 2] == height) for height in heights]
    if len({len(group) for group in groups}) != 1:
        raise ValueError('Unequal reference tower contours')
    rings = np.asarray(groups)
    tip_local = np.array([curve[-1, 0], curve[-1, 1], scalars['TipRad']])
    if not np.isclose(np.linalg.norm(tip_local), EFFECTIVE_LENGTH_M, atol=1e-9, rtol=0):
        raise ValueError('Unloaded effective tip length differs from frozen calibration')
    rest_nacelle = np.column_stack((np.eye(3), np.zeros(3)))
    reference_tips = [hub+axes@tip_local for hub, axes in
                      (blade_root_frame(scalars, [0.]*6, b, rest_nacelle) for b in (1, 2, 3))]
    transforms = np.empty((len(times), 1, 3, 19, 3, 4), float)
    nacelles = np.empty((len(times), 1, 3, 4), float)
    towers = np.empty((len(times), 1, len(heights), 3, 4), float)
    components, motion = [], []
    max_surface, max_support, max_nacelle_channel = 0., 0., 0.
    for row, frame in enumerate(indices):
        nacelle, triangles = inventory.surface('Nacelle', int(frame))
        if nacelle.shape != nacelle_ref.shape or not np.array_equal(triangles, nacelle_triangles):
            raise ValueError('Actual nacelle topology differs from reference')
        nr, nd, fit = fit_sections(nacelle_ref[None], nacelle[None])
        nacelles[row, 0] = np.column_stack((nr[0], nd[0]))
        tower, triangles = inventory.surface('Tower', int(frame))
        if tower.shape != tower_ref.shape or not np.array_equal(triangles, tower_triangles):
            raise ValueError('Actual tower topology differs from reference')
        tr, td, tower_fit = fit_sections(tower_ref[rings], tower[rings])
        towers[row, 0] = np.concatenate((tr, td[:, :, None]), axis=2)
        max_support = max(max_support, fit, tower_fit)
        # YawBrTD* channels describe the tower-top yaw bearing, not the shaft
        # centre. Twr2Shft remains in blade_root_frame's independent hub mapping.
        top_reference = np.array([0., 0., scalars['TowerHt']])
        top = nr[0]@top_reference+nd[0]
        if all(name in columns for name in ('YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt')):
            numeric_top = top_reference+np.array([columns[name][frame] for name in ('YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt')])
            max_nacelle_channel = max(max_nacelle_channel, float(np.linalg.norm(top-numeric_top)))
        component_row = []
        for blade in (1, 2, 3):
            points, triangles = inventory.surface(f'Blade{blade}', int(frame))
            if points.shape != refs[blade-1].reshape(-1, 3).shape or not np.array_equal(triangles, blade_triangles):
                raise ValueError('Actual blade topology differs from independent reference')
            r, d, fit = fit_sections(refs[blade-1], points.reshape(19, -1, 3))
            max_surface = max(max_surface, fit)
            transforms[row, 0, blade-1] = np.concatenate((r, d[:, :, None]), axis=2)
            actual_tip = r[-1]@reference_tips[blade-1]+d[-1]
            hub, axes = blade_root_frame(scalars, poses[row], blade, nacelles[row, 0])
            component_row.append(axes.T@(actual_tip-(hub+axes@tip_local)))
        components.append(component_row)
        motion.append(dict(time_s=float(times[row]), yaw_deg=float(poses[row, 0]),
            azimuth_deg=float(poses[row, 1]), rotor_speed_rpm=float(poses[row, 2]),
            pitch_deg=poses[row, 3:].tolist(), nacelle_transform=nacelles[row, 0].tolist(),
            nacelle_position_m=top.tolist(),
            nacelle_orientation_deg=Rotation.from_matrix(nr[0]).as_euler('xyz', degrees=True).tolist()))
        if row % 200 == 0:
            print(f'Actual single-turbine VTP samples checked: {row}/{len(times)}', flush=True)
    simulation = np.stack([np.column_stack([columns[f'B{blade}TipTD{axis}r'][indices] for axis in 'xyz'])
                           for blade in (1, 2, 3)], axis=1)
    error = np.asarray(components)-simulation
    max_component = float(np.max(abs(error)))
    if max(max_surface, max_support, max_nacelle_channel) > SURFACE_TOLERANCE_M or max_component > COMPONENT_TOLERANCE_M:
        raise ValueError(f'Original interface tolerances failed: blade={max_surface}, support={max_support}, '
                         f'nacelle={max_nacelle_channel}, tip_components={max_component}')
    output.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(output/'geometry.npz', times=times, poses=poses[:, None], transforms=transforms)
    np.savez_compressed(output/'tower-motion.npz', times=times, heights=heights, transforms=towers, nacelle=nacelles)
    np.savez_compressed(output/'reference-surfaces.npz', blades=refs.reshape(3, -1, 3),
                        triangles=blade_triangles, tower=tower_ref, tower_triangles=tower_triangles)
    reference_payload = dict(schema='wfrl.blade-reference.v1', structural_module='BeamDyn',
        model=loaded['model'], curve=curve.tolist(), tip_local_m=tip_local.tolist(),
        definition='x=-(z/61.5)^2; y=0; metres; z from blade root',
        reference_state='independent zero-load stationary solve', precone_deg=scalars['PreCone(1)'],
        source_note='Independent engineering operating screen; NREL 5MW derivative; no real-machine equivalence claim',
        sources=loaded.get('structural_source', {}))
    save_json(output/'blade-reference.json', reference_payload)
    raw_source = dict(path=os.path.relpath(raw_path, output), original_path=str(raw_path),
                      path_reference='source output directory', sha256=digest(raw_path))
    save_json(output/'deflection-t1.json', dict(schema='wfrl.tip-deflection.t1.v3', turbine_id='T1', unit='m',
        reference='BeamDyn structural tip; independent unloaded prebend', frame='BeamDyn pitched root xyz',
        geometry_sha256=digest(output/'geometry.npz'), tower_motion_sha256=digest(output/'tower-motion.npz'),
        reference_sha256=digest(output/'blade-reference.json'), times=times.tolist(), poses=poses.tolist(),
        simulation=simulation.tolist(), scalars=scalars, sources={'T1': raw_source}))
    telemetry = {}
    for name, channel, unit, scale in (('power', 'GenPwr', 'MW', .001), ('torque', 'GenTq', 'N m', 1000.)):
        if channel in columns:
            if units.get(channel) != {'GenPwr': '(kW)', 'GenTq': '(kN-m)'}[channel]:
                raise ValueError('Unexpected raw telemetry unit: '+channel)
            telemetry[name] = dict(values=(columns[channel][indices]*scale).tolist(), unit=unit, source_channel=channel)
    save_json(output/'telemetry.json', dict(schema='wfrl.farm-telemetry.v1', geometry_sha256=digest(output/'geometry.npz'),
        times=times.tolist(), sources={'T1': raw_source}, turbines={'T1': telemetry},
        status='AVAILABLE_SOURCE_CHANNELS' if telemetry else 'UNAVAILABLE_SOURCE_CHANNELS',
        reason=None if telemetry else config.get('telemetry_boundary', 'Generator power/torque channels absent from actual solver output')))
    statistics = dict(expected_samples=0, valid_samples=0, valid_ratio=None, mae_m=None, max_abs_error_m=None,
        p95_abs_error_m=None, max_positive_bias_m=None, passage_count=0, missed_passage_count=0,
        status='NOT_COMPUTED_SOURCE_GEOMETRY_ONLY')
    save_json(output/'data.json', {'T1': dict(motion=motion, measurements=[], cumulative=[], statistics=statistics)})
    save_json(output/'source-surfaces.json', dict(schema='wfrl.compressed-source-surfaces.v1', run_path=str(run),
        reference_path=str(reference), run_package=os.path.relpath(run, output),
        reference_package=os.path.relpath(reference, output), path_reference='source output directory',
        reference_inventory_sha256=digest(reference/'reference-inventory.json'),
        compressed_inventory_sha256=digest(inventory.path),
        surfaces=list(inventory.used.values()), reference_surfaces=reference_hashes))
    source_run = dict(config, status='SOLVER_COMPLETE_FULL_RAW_CLOCK_VERIFIED', case_dir=str(run),
        unloaded_reference=str(reference), blade_model='beamdyn-prebend', blade_config=loaded,
        run_package=os.path.relpath(run, output), reference_package=os.path.relpath(reference, output),
        path_reference='source output directory',
        reference_inventory_sha256=digest(reference/'reference-inventory.json'),
        run_config_sha256=digest(run/'run-config.json'), raw_output=raw_source,
        solver_log_sha256=digest(log_path), exit_status_sha256=digest(exit_path),
        probe_config_sha256=digest(run/'probe-config.json'), compressed_inventory_sha256=digest(inventory.path),
        full_raw_clock=clock, exported_grid_indices=[int(indices[0]), int(indices[-1])],
        exported_window_s=[float(times[0]), float(times[-1])],
        boundary='Independent engineering operating screen; prescribed RPM and pitch; no policy; numerical simulation only; no field validation')
    save_json(output/'source-run.json', source_run)
    save_json(output/'interface-audit.json', dict(status='PASSED_ORIGINAL_INTERFACE_TOLERANCES',
        max_surface_fit_error_m=max_surface, max_support_fit_error_m=max_support,
        max_nacelle_channel_error_m=max_nacelle_channel, max_component_error_m=max_component,
        max_component_by_blade_m=np.max(abs(error), axis=(0, 2)).tolist(),
        surface_tolerance_m=SURFACE_TOLERANCE_M, component_tolerance_m=COMPONENT_TOLERANCE_M,
        reference_tip_world_m=np.asarray(reference_tips).tolist(), effective_length_m=float(np.linalg.norm(tip_local)),
        raw_clock=clock, exported_sample_count=len(times), no_interpolation=True,
        mapping='Independent unloaded sections fitted to actual VTP; terminal section transform applied once; rigid root frame independently computed',
        physical_validation='NOT_PERFORMED'))
    manifest = dict(schema='wfrl.farm-flex-review.v3', status='REVIEW_ONLY', source='OpenFAST',
        turbine_ids=['T1'], layout_m=[[0., 0., 0.]], source_fps=config['fps'],
        segment=dict(start_s=float(times[0]), end_s=float(times[-1])), includes_rigid_motion=True,
        tower_model='elastodyn-flexible', structural_module='BeamDyn', model=loaded['model'],
        provenance_label='independent engineering operating screen; prescribed rpm/no policy',
        run_path=str(run), run_config_sha256=digest(run/'run-config.json'),
        run_package=os.path.relpath(run, output), reference_package=os.path.relpath(reference, output),
        path_reference='source output directory',
        probe_config_sha256=digest(run/'probe-config.json'),
        reference_frame='independent zero-load stationary solve', interface_only=False,
        performance_status='PENDING_ACCEPTANCE', physical_validation='NOT_PERFORMED',
        max_surface_fit_error_m=max_surface, max_support_fit_error_m=max_support,
        max_component_error_m=max_component, measurement_window_deg=3,
        replay=dict(threshold_m=7., hysteresis_m=.1, max_hold_s=5., passage_margin=1.25),
        calibration=dict(origin_m=[-2., 0., 87.6]),
        clearance_definition='Terminal contour surface minimum to same-global-height moving tower section; not whole-blade minimum',
        files={p.name: digest(p) for p in output.iterdir() if p.is_file()})
    save_json(output/'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run'); parser.add_argument('reference'); parser.add_argument('output')
    parser.add_argument('--start', type=float); parser.add_argument('--end', type=float)
    args = parser.parse_args()
    result = export(args.run, args.reference, args.output, args.start, args.end)
    print(json.dumps({key: result[key] for key in ('segment', 'source_fps', 'max_component_error_m')}))

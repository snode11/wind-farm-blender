"""Check a saved v3 source against retained originating OpenFAST files.

This is a same-run provenance check, not an independent performance validation.
Missing raw surfaces remain explicit evidence gaps; retained files with a wrong
hash or inconsistent values fail the check. No solver is started or file copied.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.data import blade_root_frame
from wfrl.lidar.physics import read_surface

# Existing exporter interface checks, not newly chosen performance thresholds.
POSE_TOLERANCE = .002
TIP_COMPONENT_TOLERANCE_M = .005
TIME_TOLERANCE_S = 1e-8
SAVED_NUMBER_TOLERANCE = 1e-9
SPOTCHECK_TIMES_S = (117., 132., 147., 162., 177.)


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def read_output(path):
    """Read every numeric row and preserve the original channel unit declarations."""
    lines = Path(path).read_text(encoding='latin-1').splitlines()
    header = next((i for i, line in enumerate(lines)
                   if line.split() and line.split()[0] == 'Time'), None)
    if header is None or header + 1 >= len(lines):
        raise ValueError('OpenFAST channel/unit header is missing')
    names, units = lines[header].split(), lines[header + 1].split()
    if len(names) != len(set(names)) or len(units) != len(names):
        raise ValueError('OpenFAST channel/unit header is malformed')
    rows = []
    for line_number, line in enumerate(lines[header + 2:], header + 3):
        if not line.strip():
            continue
        values = line.split()
        if len(values) != len(names):
            raise ValueError(f'OpenFAST row {line_number} has an incorrect column count')
        rows.append([float(value) for value in values])
    if not rows:
        raise ValueError('OpenFAST numeric rows are missing')
    array = np.asarray(rows, float)
    columns = dict(zip(names, array.T))
    if not np.isfinite(columns['Time']).all() or np.any(np.diff(columns['Time']) <= 0):
        raise ValueError('OpenFAST time axis is not finite and increasing')
    return columns, dict(zip(names, units)), len(rows)


def input_value(path, key):
    for line in Path(path).read_text().splitlines():
        words = line.split()
        if len(words) > 1 and words[1] == key:
            return words[0].strip('"')
    raise ValueError(f'Missing input parameter {key}: {path}')


def require_unit(units, channel, expected):
    if units.get(channel) != expected:
        raise ValueError(f'Incorrect original unit for {channel}: {units.get(channel)}; expected {expected}')


def difference(actual, expected, tolerance, label):
    actual, expected = np.asarray(actual, float), np.asarray(expected, float)
    if actual.shape != expected.shape or not np.isfinite(actual).all() or not np.isfinite(expected).all():
        raise ValueError(f'{label}: shape or finite-value mismatch')
    maximum = float(np.max(np.abs(actual - expected), initial=0.))
    if maximum > tolerance:
        raise ValueError(f'{label}: maximum difference {maximum:g} exceeds {tolerance:g}')
    return maximum


def verify_numeric_turbine(tid, columns, units, geometry, nacelles, payload, telemetry,
                           scalars, tip_local, turbine_index, deflection=None):
    """Check all saved timestamps, poses, telemetry and structural tip components."""
    times = geometry['times']
    require_unit(units, 'Time', '(s)')
    indices = np.searchsorted(columns['Time'], times)
    if np.any(indices >= len(columns['Time'])):
        raise ValueError('Saved segment extends beyond original output')
    time_difference = difference(columns['Time'][indices], times, TIME_TOLERANCE_S, 'source timestamps')
    channels = ('YawPzn', 'Azimuth', 'RotSpeed', 'BlPitch1', 'BlPitch2', 'BlPitch3')
    for channel in channels:
        require_unit(units, channel, '(rpm)' if channel == 'RotSpeed' else '(deg)')
    azimuth = np.degrees(np.unwrap(np.radians(columns['Azimuth'])))
    precise = np.column_stack([columns['YawPzn'], azimuth, columns['RotSpeed'],
                              columns['BlPitch1'], columns['BlPitch2'], columns['BlPitch3']])[indices]
    poses = geometry['poses'][:, turbine_index]
    pose_difference = difference(precise, poses, POSE_TOLERANCE, 'original output versus geometry pose')
    motion = payload['motion']
    difference([row['time_s'] for row in motion], times, TIME_TOLERANCE_S, 'motion timestamps')
    saved_pose = [[row['yaw_deg'], row['azimuth_deg'], row['rotor_speed_rpm'], *row['pitch_deg']]
                  for row in motion]
    motion_difference = difference(precise, saved_pose, SAVED_NUMBER_TOLERANCE, 'original output versus saved motion')
    tip_channels = [[f'B{blade}TipTD{axis}r' for axis in 'xyz'] for blade in (1, 2, 3)]
    for group in tip_channels:
        for channel in group:
            require_unit(units, channel, '(m)')
    simulation = np.stack([np.column_stack([columns[name][indices] for name in group])
                           for group in tip_channels], axis=1)
    if not np.isfinite(simulation).all():
        raise ValueError('Nonfinite original structural tip components')
    if deflection is not None:
        difference(deflection['times'], times, TIME_TOLERANCE_S, 'deflection timestamps')
        difference(deflection['simulation'], simulation, SAVED_NUMBER_TOLERANCE, 'original output versus deflection sidecar')
    rest_nacelle = np.column_stack((np.eye(3), np.zeros(3)))
    rest_tips = []
    for blade in (1, 2, 3):
        hub, axes = blade_root_frame(scalars, [0.] * 6, blade, rest_nacelle)
        rest_tips.append(hub + axes @ tip_local)
    transforms = geometry['transforms'][:, turbine_index, :, -1]
    actual = np.einsum('nbij,bj->nbi', transforms[..., :3], rest_tips) + transforms[..., 3]
    components = np.empty_like(simulation)
    for index in range(len(times)):
        for blade in (1, 2, 3):
            hub, axes = blade_root_frame(scalars, poses[index], blade, nacelles[index, turbine_index])
            components[index, blade - 1] = axes.T @ (actual[index, blade - 1] - hub - axes @ tip_local)
    component_difference = difference(components, simulation, TIP_COMPONENT_TOLERANCE_M,
                                      'terminal section geometry versus original BeamDyn components')
    telemetry_differences, original_telemetry = {}, {}
    for name, channel, source_unit, saved_unit, scale in (
            ('power', 'GenPwr', '(kW)', 'MW', .001),
            ('torque', 'GenTq', '(kN-m)', 'N m', 1000.)):
        saved = telemetry['turbines'][tid][name]
        if saved.get('source_channel') != channel or saved.get('unit') != saved_unit:
            raise ValueError(f'Incorrect telemetry contract: {name}')
        require_unit(units, channel, source_unit)
        values = columns[channel][indices] * scale
        telemetry_differences[name] = difference(values, saved['values'], SAVED_NUMBER_TOLERANCE,
                                               'original output versus telemetry ' + name)
        original_telemetry[name] = values
    spotchecks = []
    for time in SPOTCHECK_TIMES_S:
        selected = np.flatnonzero(abs(times - time) <= TIME_TOLERANCE_S)
        if not len(selected):
            continue
        index = int(selected[0])
        spotchecks.append(dict(time_s=float(times[index]), original_output_sample_index=int(indices[index]),
                               raw_pose=precise[index].tolist(), power_mw=float(original_telemetry['power'][index]),
                               torque_n_m=float(original_telemetry['torque'][index]),
                               max_tip_component_difference_m=float(np.max(abs(components[index] - simulation[index])))))
    return dict(passed=True, saved_samples_checked=len(times), original_segment_sample_indices=indices[[0, -1]].tolist(),
                max_timestamp_difference_s=time_difference, max_pose_difference=pose_difference,
                pose_channel_max_differences={name: float(np.max(abs(precise[:, index] - poses[:, index])))
                                             for index, name in enumerate(channels)},
                max_saved_motion_difference=motion_difference, telemetry_max_differences=telemetry_differences,
                max_tip_component_difference_m=component_difference,
                max_tip_component_difference_by_blade_m=np.max(abs(components - simulation), axis=(0, 2)).tolist(),
                structural_frame='BeamDyn pitched root xyz; terminal total transform applied once to independent rest tip',
                spotchecks=spotchecks)


def verify_provenance(source, repo_root=ROOT, provenance_root=None):
    source, repo_root = Path(source).resolve(), Path(repo_root).resolve()
    provenance_root = Path(provenance_root).resolve() if provenance_root is not None else None
    report = dict(schema='wfrl.dual-beam-source-provenance.v1', source_package=str(source),
                  scope='same-run raw-file provenance and exporter interface consistency; no independent performance acceptance',
                  failures=[], evidence_gaps=[], hash_checks=[], numeric_checks={}, reference_geometry=[],
                  provenance_root=str(provenance_root) if provenance_root is not None else None,
                  portable_copy_inventory=[],
                  tolerances=dict(pose=POSE_TOLERANCE, structural_tip_components_m=TIP_COMPONENT_TOLERANCE_M,
                                  timestamps_s=TIME_TOLERANCE_S, saved_numbers=SAVED_NUMBER_TOLERANCE),
                  space_time_convergence=dict(status='NOT_COMPLETED', note='No temporal or spatial convergence solve is performed.'),
                  independent_complete_dataset=dict(status='NOT_SUPPLIED', note='A second complete independent dataset is missing from this one-source audit.'))

    def gap(kind, note, **details):
        report['evidence_gaps'].append(dict(kind=kind, note=note, **details))

    def resolve_raw(path):
        original = Path(path).resolve()
        if provenance_root is None:
            return original
        try:
            relative = original.relative_to(repo_root)
        except ValueError as error:
            raise ValueError('Recorded raw path is outside the repository mapping: ' + str(original)) from error
        return provenance_root / relative

    def retain(kind, original, resolved, checksum):
        try:
            relative = str(original.relative_to(repo_root))
        except ValueError:
            relative = None
        if not any(item['recorded_path'] == str(original) for item in report['portable_copy_inventory']):
            report['portable_copy_inventory'].append(dict(kind=kind, recorded_path=str(original),
                                                         resolved_path=str(resolved), repository_relative_path=relative,
                                                         sha256=checksum, size_bytes=resolved.stat().st_size))

    def auxiliary(kind, original):
        resolved = resolve_raw(original)
        if resolved.is_file():
            retain(kind, original, resolved, digest(resolved))
        return resolved

    def check_hash(kind, path, expected):
        original = Path(path).resolve()
        path = original if kind == 'source_package' else resolve_raw(original)
        record = dict(kind=kind, path=str(path), recorded_path=str(original), resolved_path=str(path),
                      expected_sha256=expected)
        if not path.is_file():
            record['status'] = 'MISSING'
            gap(kind, 'Original file is not retained; this check is unavailable.', path=str(path))
        else:
            actual = digest(path)
            record.update(actual_sha256=actual, size_bytes=path.stat().st_size,
                          status='MATCH' if actual == expected else 'MISMATCH')
            if actual != expected:
                report['failures'].append(f'Hash mismatch: {kind}: {path}')
            elif kind != 'source_package':
                retain(kind, original, path, actual)
        report['hash_checks'].append(record)
        return record['status'] == 'MATCH'

    try:
        manifest = json.loads((source / 'manifest.json').read_text())
        if (manifest.get('schema') != 'wfrl.farm-flex-review.v3'
                or manifest.get('status') != 'REVIEW_ONLY' or manifest.get('structural_module') != 'BeamDyn'
                or not manifest.get('includes_rigid_motion') or manifest.get('tower_model') != 'elastodyn-flexible'):
            raise ValueError('Expected a v3 BeamDyn source including rigid and flexible motion')
        report['source_manifest_sha256'] = digest(source / 'manifest.json')
        for name, expected in manifest['files'].items():
            if Path(name).name != name:
                raise ValueError('Package inventory must use package-local names')
            if not check_hash('source_package', source / name, expected):
                raise ValueError('Source package integrity is not intact: ' + name)
        run = json.loads((source / 'source-run.json').read_text())
        telemetry = json.loads((source / 'telemetry.json').read_text())
        deflection = json.loads((source / 'deflection-t1.json').read_text())
        reference = json.loads((source / 'blade-reference.json').read_text())
        case = Path(run['case_dir'])
        case = case.resolve() if case.is_absolute() else (repo_root / case).resolve()
        farm = case / 'FarmInputs'
        rest = Path(run['unloaded_reference'])
        rest = rest.resolve() if rest.is_absolute() else (repo_root / rest).resolve()
        report.update(originating_case=str(case), unloaded_reference=str(rest), segment=manifest['segment'],
                      resolved_originating_case=str(resolve_raw(case)), resolved_unloaded_reference=str(resolve_raw(rest)),
                      source_fps=manifest['source_fps'], turbine_ids=manifest['turbine_ids'],
                      input_inventory_boundary='source-run.input_hashes and blade_input_hashes are checked against the originating run; blade_config.inputs describes an earlier single-turbine interface model and is not the originating-run inventory')
        for name, expected in run['input_hashes'].items():
            if Path(name).name != name:
                raise ValueError('Originating FAST input hashes must use local filenames')
            check_hash('originating_FAST_input', farm / name, expected)
        for name, expected in run['blade_input_hashes'].items():
            if Path(name).name != name:
                raise ValueError('BeamDyn input hashes must use local filenames')
            check_hash('originating_BeamDyn_input', case / '5MW_Baseline' / name, expected)
        wind_input = auxiliary('wind_path_input', farm / 'InflowWind.dat')
        if wind_input.is_file():
            check_hash('originating_wind', (farm / input_value(wind_input, 'FileName_BTS')).resolve(), run['wind_sha256'])
        else:
            gap('originating_wind', 'InflowWind input is missing; original wind path cannot be resolved.')
        for tid in manifest['turbine_ids']:
            recorded_fst = farm / f'FFTest_WT{tid[1:]}.fst'
            fst = resolve_raw(recorded_fst)
            if not fst.is_file():
                gap('originating_controller', 'FAST input is missing; controller path cannot be resolved.', turbine_id=tid)
                continue
            recorded_servo = (farm / input_value(fst, 'ServoFile')).resolve()
            servo = auxiliary('controller_path_input', recorded_servo)
            if not servo.is_file():
                gap('originating_controller', 'ServoDyn input is missing; controller path cannot be resolved.', turbine_id=tid)
                continue
            check_hash('originating_controller', (recorded_servo.parent / input_value(servo, 'DLL_FileName')).resolve(), run['controller_sha256'])
        surfaces = json.loads((source / 'source-surfaces.json').read_text())
        counts, missing_examples, mismatches = Counter(), [], []
        for name, expected in surfaces.items():
            path = Path(name)
            kind = 'absolute_reference' if path.is_absolute() else 'relative_loaded'
            original = (path if path.is_absolute() else farm / path).resolve()
            path = resolve_raw(original)
            counts[kind + '_indexed'] += 1
            if not path.is_file():
                counts[kind + '_missing'] += 1
                if len(missing_examples) < 10:
                    missing_examples.append(str(path))
            elif digest(path) == expected:
                counts[kind + '_matched'] += 1
                retain('raw_source_surface', original, path, expected)
            else:
                counts[kind + '_mismatched'] += 1
                mismatches.append(str(path))
                report['failures'].append('Raw surface hash mismatch: ' + str(path))
        report['raw_surface_hashes'] = dict(indexed_files=len(surfaces), counts=dict(counts),
                                           missing_path_examples=missing_examples, mismatched_paths=mismatches)
        if sum(value for key, value in counts.items() if key.endswith('_missing')):
            gap('raw_source_surfaces', 'Missing loaded/reference VTP prevents full raw-surface reconstruction verification.',
                counts={key: value for key, value in counts.items() if key.endswith('_missing')})
        with np.load(source / 'reference-surfaces.npz', allow_pickle=False) as archive:
            frozen_reference = {key: archive[key] for key in archive.files}
        for name, points, triangles in [(f'Blade{blade}', frozen_reference['blades'][blade - 1], frozen_reference['triangles'])
                                        for blade in (1, 2, 3)] + [('Tower', frozen_reference['tower'], frozen_reference['tower_triangles'])]:
            reference_folder = rest / 'FarmInputs/vtk'
            candidates = sorted(resolve_raw(reference_folder).glob(f'FFTest_WT1.{name}Surface.*.vtp'))
            path = next((path for path in candidates if int(path.stem.rsplit('.', 1)[1]) == 0), None)
            if path is None:
                gap('reference_geometry', 'Independent rest VTP is missing.', surface=name)
                continue
            actual_points, actual_triangles = read_surface(path)
            # NPZ contains these exact original double-precision reference points.
            residual = difference(actual_points, points, 0., 'independent rest ' + name)
            if not np.array_equal(actual_triangles, triangles):
                raise ValueError('Independent rest topology differs: ' + name)
            checksum = digest(path)
            original = reference_folder / path.name
            retain('reference_geometry', original, path, checksum)
            report['reference_geometry'].append(dict(surface=name, path=str(path), recorded_path=str(original),
                                                      resolved_path=str(path), sha256=checksum,
                                                      points=len(actual_points), max_coordinate_difference_m=residual,
                                                      topology_identical=True))
        with np.load(source / 'geometry.npz', allow_pickle=False) as archive:
            geometry = {key: archive[key] for key in archive.files}
        with np.load(source / 'tower-motion.npz', allow_pickle=False) as archive:
            nacelles, tower_times = archive['nacelle'], archive['times']
        difference(tower_times, geometry['times'], TIME_TOLERANCE_S, 'tower timestamps')
        difference(telemetry['times'], geometry['times'], TIME_TOLERANCE_S, 'telemetry timestamps')
        payload = json.loads((source / 'data.json').read_text())
        for index, tid in enumerate(manifest['turbine_ids']):
            output = farm / f'Case.{tid}.out'
            expected = telemetry['sources'][tid]['sha256']
            if tid == 'T1' and deflection['sources']['T1']['sha256'] != expected:
                raise ValueError('Telemetry and deflection raw-output hashes differ')
            if not check_hash('originating_output', output, expected):
                continue
            try:
                columns, units, rows = read_output(resolve_raw(output))
                check = verify_numeric_turbine(tid, columns, units, geometry, nacelles, payload[tid], telemetry,
                                               deflection['scalars'], np.asarray(reference['tip_local_m'], float),
                                               index, deflection if tid == 'T1' else None)
                check.update(original_numeric_rows=rows, original_time_range_s=columns['Time'][[0, -1]].tolist())
                report['numeric_checks'][tid] = check
            except (ValueError, KeyError, IndexError) as error:
                report['numeric_checks'][tid] = dict(passed=False, error=str(error))
                report['failures'].append(f'Original numeric consistency failed: {tid}: {error}')
    except (ValueError, KeyError, IndexError, OSError, TypeError) as error:
        report['failures'].append(str(error))
    report['passed'] = not report['failures']
    report['status'] = ('FAILED_INTEGRITY' if not report['passed'] else
                        'SAME_RUN_VERIFIED_WITH_EVIDENCE_GAPS' if report['evidence_gaps'] else 'SAME_RUN_VERIFIED')
    report['saved_samples_checked'] = sum(check.get('saved_samples_checked', 0) for check in report['numeric_checks'].values())
    report['max_tip_component_difference_m'] = max((check.get('max_tip_component_difference_m', 0.)
                                                   for check in report['numeric_checks'].values()), default=None)
    return report


def markdown_report(report):
    lines = ['# 双束 TLS 来源与原始输出核验', '', f"状态：`{report['status']}`。这是同源来源核验，不构成独立性能或设备验收。", '',
             f"原始运行：`{report.get('originating_case', 'unavailable')}`。", '',
             '|机组|完整保存时刻核对|原始输出行数|最大 pose 差|最大结构 tip 分量差 / m|',
             '|---|---:|---:|---:|---:|']
    for tid, check in report['numeric_checks'].items():
        if check['passed']:
            lines.append(f"|{tid}|{check['saved_samples_checked']}|{check['original_numeric_rows']}|{check['max_pose_difference']:.8g}|{check['max_tip_component_difference_m']:.8g}|")
        else:
            lines.append(f"|{tid}|FAILED|—|—|—|")
    lines += ['', '检查原始 Time、yaw/azimuth/rpm/三叶片 pitch、GenPwr/GenTq 单位转换、BeamDyn pitched-root xyz tip 分量及独立静止参考 VTP。',
              '结构 tip 使用 exporter 原有 0.005 m 接口容差；pose 使用原有 0.002 容差。两者均非新设性能门槛。', '',
              '保留的源包、FAST/BeamDyn 输入、controller、wind、原始 .out 与 VTP 哈希逐项核验；缺失 VTP 单列证据缺口，存在却哈希不符则失败。', '']
    if report.get('raw_surface_hashes'):
        lines.append('原始 VTP：`' + json.dumps(report['raw_surface_hashes']['counts'], ensure_ascii=False) + '`。')
    lines += ['', '独立完整第二工况未提供；空间与时间收敛未完成。本工具未启动求解器、未复制原始数据，也未核定现场输入可用性。', '']
    for gap in report['evidence_gaps']:
        lines.append('- 证据缺口 `' + gap['kind'] + '`：' + gap['note'])
    for failure in report['failures']:
        lines.append('- 核验失败：' + failure)
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New report directory containing provenance.json and report.md')
    parser.add_argument('--repo-root', type=Path, default=ROOT, help='Base for originating relative case paths')
    parser.add_argument('--provenance-root', type=Path, help='Root containing retained raw files at their recorded repository-relative paths')
    args = parser.parse_args(argv)
    report = verify_provenance(args.source, args.repo_root, args.provenance_root)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report['evidence_tool_sha256'] = digest(Path(__file__))
    (output / 'provenance.json').write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    (output / 'report.md').write_text(markdown_report(report))
    print(report['status'], output)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

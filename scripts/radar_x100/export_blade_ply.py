"""Export a 33 Hz calibrated X100 detection sequence from the T1 Down camera position.

Run in Blender:
  blender --background --factory-startup --python scripts/radar_x100/export_blade_ply.py -- --output DIR
      [--package PACKAGE] [--start S] [--end S | --duration S] [--fps 33] [--blade-model KEY] [--seed N]

The radar is installed at the current T1 Down camera pose with the camera's rectangular field of
view and uses the detection parameters fitted to the measured drone PLY sequence
(wfrl.radar_x100.calibration). Outputs:
  DIR/recording/                   x100-recording.v1 with sensor-axis points, velocity and diagnostics;
  DIR/ply/{index:012d}_{frame}_RawPoints.ply   vendor PLY layout and axes (+Y boresight, +X right, +Z up);
  DIR/index.json                   per-frame source time, point count and false-alarm count;
  DIR/summary.json                 installation, field of view, configuration and frame statistics.
Only turbine geometry is observed; ground, sea and other clutter are not modelled.
"""
from pathlib import Path
import argparse
import importlib
import json
import math
import os
import sys
import tempfile
import uuid
from dataclasses import asdict
from fractions import Fraction

import numpy as np

# Sensor (+X boresight, +Y left, +Z up) to vendor (+X right, +Y boresight, +Z up).
VENDOR_FROM_SENSOR = np.array(((0., -1., 0.), (1., 0., 0.), (0., 0., 1.)))


def sample_times(start, end, fps):
    if not all(math.isfinite(x) for x in (start, end, fps)) or start > end or fps <= 0:
        raise ValueError('Invalid export grid interval or rate')
    first, last, rate = (Fraction(str(value)) for value in (start, end, fps))
    for sequence in range(int((last - first) * rate) + 1):
        yield float(first + sequence / rate)


def vendor_points(points):
    out = points.copy()
    xyz = np.column_stack((points['x'], points['y'], points['z'])).astype(float) @ VENDOR_FROM_SENSOR.T
    out['x'], out['y'], out['z'] = xyz.T
    out['label'] = 0
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--start', type=float, help='Absolute source time; defaults to source start')
    parser.add_argument('--end', type=float, help='Absolute source time; defaults to start + duration')
    parser.add_argument('--duration', type=float, default=10., help='Seconds exported when --end is omitted')
    parser.add_argument('--fps', type=float, default=33.)
    parser.add_argument('--blade-model', help='Replay blade shape key, e.g. rootkink')
    parser.add_argument('--seed', type=int, default=100)
    parser.add_argument('--first-frame-number', type=int, default=0)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if (not math.isfinite(args.duration) or args.duration <= 0
            or args.seed < 0 or args.first_frame_number < 0):
        raise ValueError('Invalid export duration, seed or first frame number')
    module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
    if module == 'wfrl_blender':
        root = Path(__file__).resolve().parents[2]
        sys.path[:0] = [str(root / 'blender_frontend'), str(root)]
    addon = importlib.import_module(module)
    if module == 'wfrl_blender':
        addon.register()
    try:
        farm = importlib.import_module(module + '.farm_flex')
        api = importlib.import_module(module + '.radar_x100.core')
        scene_api = importlib.import_module(module + '.radar_x100.scene')
        import bpy
        addon.load_demo_scene(args.package or farm.default_package())
        scene = bpy.context.scene
        if args.blade_model:
            scene.wfrl_blade_model = args.blade_model
            if farm.active_for(scene).model != args.blade_model:
                raise ValueError('Blade model switch failed: ' + str(scene.get('wfrl_blade_model_error', '')))
        location, rotation, field_of_view = scene_api.down_camera_installation(scene)
        sampler = scene_api.SceneSampler(farm.active_for(scene), mount='NACELLE',
            mount_from_sensor=scene_api.installation_matrix(location, rotation), field_of_view=field_of_view,
            field_of_view_shape='PROJECTIVE')
        config = api.calibration.calibrated_config(azimuth_limit_deg=field_of_view[0],
                                                   elevation_limit_deg=field_of_view[1],
                                                   field_of_view_shape='PROJECTIVE')
        args.start = sampler.interval[0] if args.start is None else args.start
        args.end = min(sampler.interval[1], args.start + args.duration) if args.end is None else args.end
        if (not all(math.isfinite(x) for x in (args.start, args.end, args.fps))
                or not sampler.interval[0] <= args.start < args.end <= sampler.interval[1]
                or not 0 < args.fps <= 120 or (args.end - args.start) * args.fps > 100000):
            raise ValueError('Invalid export source interval or sample budget')
        from wfrl.camera_video.publication import commit_exclusive
        destination = args.output
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.x100-export-', dir=destination.parent) as directory:
            args.output = Path(directory) / 'export'
            args.output.mkdir(parents=True, exist_ok=False)
            ply_dir = args.output / 'ply'
            ply_dir.mkdir()
            description = dict(source_kind='ENGINEERING_SIMULATION', source_grid_fps=args.fps,
                               interval_s=[args.start, args.end], calibration_id=sampler.calibration_id,
                               mount_preset='DOWN_CAMERA', field_of_view_deg=list(field_of_view),
                               field_of_view_shape='PROJECTIVE',
                               blade_model=farm.active_for(scene).model)
            sink = api.recording.Recorder(args.output / 'recording', description=description)
            session, rows = str(uuid.uuid4()), []
            try:
                for sequence, t in enumerate(sample_times(args.start, args.end, args.fps)):
                    budget = 16384
                    with sampler.sampling_transaction():
                        frame = api.simulation.simulate_detections(sampler.sample, t, sensor_id='X100-T1',
                            session_id=session, sequence=sequence, interval_s=sampler.interval,
                            integration_budget=budget, output_point_limit=api.schema.PROFILE['max_points'],
                            sampling_seed=args.seed, noise_seed=args.seed,
                            config=config, visibility=sampler.visibility, material_at=sampler.material_sample,
                            sampling_plan=sampler.sampling_plan(budget, seed=args.seed))
                    if frame is None:
                        raise ValueError(f'No source sample at {t} s')
                    if frame.model_diagnostics is None:
                        raise ValueError(f'Missing detection diagnostics at {t} s')
                    frame.provenance['calibration_id'] = sampler.calibration_id
                    sink.append(frame, t - args.start)
                    number = args.first_frame_number + sequence
                    name = f'{sequence:012d}_{number}_RawPoints.ply'
                    (ply_dir / name).write_bytes(api.ply.encode_ply(vendor_points(frame.points)))
                    diagnostics = frame.model_diagnostics
                    rows.append(dict(file=name, sequence=sequence, frame=number, source_time_s=t,
                                     points=len(frame.points), moving=int(frame.points['ms'].sum()),
                                     false_alarms=(int(diagnostics.values['false_alarm'].sum())
                                                   if diagnostics is not None else None)))
                sink.close()
            except Exception as exc:
                sink.fail(str(exc))
                raise
            counts = np.array([row['points'] for row in rows])
            summary = dict(frames=len(rows), fps=args.fps, interval_s=[args.start, args.end],
                installation=dict(preset='DOWN_CAMERA', frame='T1 YawRoot', location_m=list(location),
                                  rotation_xyz_deg=list(rotation)),
                field_of_view_half_deg=dict(azimuth=field_of_view[0], elevation=field_of_view[1]),
                field_of_view_shape='PROJECTIVE',
                vendor_axes='+X right, +Y boresight, +Z up (x_v = -y_s, y_v = x_s, z_v = z_s)',
                max_points=api.schema.PROFILE['max_points'], blade_model=description['blade_model'],
                points_per_frame=dict(mean=float(counts.mean()), min=int(counts.min()), max=int(counts.max())),
                false_alarms_per_frame=float(np.mean([row['false_alarms'] for row in rows])),
                detection_config={k: v for k, v in asdict(config).items()},
                limitations=['Turbine geometry only: no ground, sea, nacelle-housing or other clutter',
                             'Blade surface RCS density is not calibrated (no blade measurement)',
                             'Elevation half angle follows the camera and exceeds the observed hardware +/-22.9 deg'
                             if field_of_view[1] > 22.9 else 'Field of view follows the camera'])
            (args.output / 'index.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
            (args.output / 'summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding='utf-8')
            commit_exclusive(args.output, destination)
        args.output = destination
        print('X100_BLADE_EXPORT_PASS', json.dumps(dict(frames=len(rows), output=str(args.output),
                                                        points_mean=summary['points_per_frame'])))
    finally:
        if module == 'wfrl_blender':
            addon.unregister()


if __name__ == '__main__':
    main()

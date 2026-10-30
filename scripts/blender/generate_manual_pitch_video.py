"""Generate and await the rootkink B1 20-degree, 180-second Down video."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True,
                    help='MP4 output path; an existing video is never overwritten')
parser.add_argument('--detach', action='store_true', help='Return after launch and print the queryable job path')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:])
output = args.output.expanduser().absolute()

import bpy

sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]

import wfrl_blender as addon
from wfrl_blender import manual_control as control, manual_geometry, manual_scene, manual_video, manual_video_job
from wfrl_blender.turbine_geometry import blade_mesh

assert bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
addon.register()
scene = manual_scene.create(bpy.context)
assert scene['wfrl_manual_blade_model'] == 'rootkink'
manual_geometry.set_detail(scene, 'CLOSEUP')
vertices, faces = blade_mesh(subdiv=8, ring_points=96, model='rootkink')
maximum_error = 0.0
for entry in scene.wfrl_manual_turbines:
    for index in range(1, 4):
        mesh = getattr(entry, f'blade{index}_object').data
        assert mesh['wfrl_manual_blade_model'] == 'rootkink'
        assert len(mesh.vertices) == len(vertices)
        assert tuple(tuple(face.vertices) for face in mesh.polygons) == tuple(faces)
        maximum_error = max(maximum_error, max(
            abs(actual.co[axis] - expected[axis])
            for actual, expected in zip(mesh.vertices, vertices) for axis in range(3)))
assert maximum_error < 1e-4

scene.wfrl_manual_pitch_rate_deg_s = 0
entry = control._t1(scene)
entry.yaw_deg = 0
entry.azimuth_deg = 180
entry.pitch_deg = 0
entry.individual_pitch = False
entry.rpm = 9
trial = scene.wfrl_manual_trial
trial.select_b1, trial.select_b2, trial.select_b3 = True, False, False
trial.target_b1_deg, trial.target_b2_deg, trial.target_b3_deg = 20, 0, 0
trial.duration_s = 4
trial.interpolation = 'LINEAR'
scene.wfrl_manual_video_hold_s = 176
control.confirm_trial(scene, now=0)
control.trial_action(scene, 'PAUSE', now=0)
start, spec = control.trial_configuration(scene)
assert spec.start_pitches == (0, 0, 0)
assert spec.targets == (20, 0, 0) and spec.selected == (True, False, False)
assert spec.interpolation == 'LINEAR' and spec.rpm == 9 and spec.duration_s == 4
assert spec.pose_at(1).pitches_deg == (5, 0, 0)
assert spec.pose_at(4).pitches_deg == spec.pose_at(180).pitches_deg == (20, 0, 0)
manual_scene.down_view(bpy.context)
output.parent.mkdir(parents=True, exist_ok=True)
job_path = manual_video.launch(scene, output)
record = dict(
    schema='wfrl.manual-pitch-delivery.v1',
    output_mp4=str(output), job_path=str(job_path), job_id=json.loads(job_path.read_text())['job_id'],
    addon_file=addon.__file__,
    blade_model='rootkink', checked_blades=9, maximum_mesh_coordinate_error_m=maximum_error,
    geometry_sha256=hashlib.sha256((Path(addon.__file__).parent / 'assets/rootkink_geometry.json').read_bytes()).hexdigest(),
    rpm=9, start_azimuth_deg=180, interpolation='LINEAR', pitch_rate_deg_s=5,
    transition_s=4, hold_s=176, duration_s=180, frame_count=4500,
    resolution=[1920, 1080], fps=25, camera='T1 Down',
    start_pitches_deg=[0, 0, 0], target_pitches_deg=[20, 0, 0],
)
(job_path.parent / 'delivery.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
print('WFRL_MANUAL_PITCH_DELIVERY ' + json.dumps(record, ensure_ascii=False), flush=True)
if not args.detach:
    try:
        result = manual_video_job.wait(job_path)
    except KeyboardInterrupt:
        result = manual_video_job.cancel(job_path)
    print('WFRL_MANUAL_VIDEO_RESULT ' + json.dumps(result, ensure_ascii=False), flush=True)
    code = {'COMPLETE': 0, 'FAILED': 1, 'CANCELLED': 130, 'LOST': 3}.get(result['state'], 1)
    if code:
        raise SystemExit(code)

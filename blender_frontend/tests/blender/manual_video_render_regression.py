"""Short native export covering deterministic poses, encoded media and isolation."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
from wfrl_blender import cameras, manual_control as control, manual_scene
from wfrl_blender.manual_video_render import render_job

OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
WIDTH = int(os.environ.get('WFRL_TEST_WIDTH', '320'))
HEIGHT = int(os.environ.get('WFRL_TEST_HEIGHT', '180'))
SAMPLES = int(os.environ.get('WFRL_TEST_SAMPLES', '4'))
control.register()
scene = manual_scene.create(bpy.context)
entries = {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}
entries['T1'].azimuth_deg = 180
entries['T1'].rpm = 9
entries['T1'].individual_pitch = True
entries['T1'].pitch_b1_deg, entries['T1'].pitch_b2_deg, entries['T1'].pitch_b3_deg = 3, 7, 11
entries['T2'].yaw_deg, entries['T3'].pitch_deg = 21, 17
scene.wfrl_manual_trial.duration_s = .16
scene.wfrl_manual_trial.interpolation = 'LINEAR'
scene.wfrl_manual_trial.target_b1_deg = 20
control.confirm_trial(scene, now=0)
control.trial_action(scene, 'PAUSE', now=0)
start, trial = control.trial_configuration(scene)
scene.view_layers[0].update()
other_before = {entry.turbine_id: [tuple(value for row in getattr(entry, field).matrix_world for value in row)
                 for field in control._POINTER_FIELDS] for entry in scene.wfrl_manual_turbines if entry.turbine_id != 'T1'}
hidden_before = {obj.name: obj.hide_render for obj in scene.objects}
snapshot = OUT / 'snapshot.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(snapshot))
job = dict(schema='wfrl.manual-video-job.v2', job_id=uuid.uuid4().hex,
           control_token=uuid.uuid4().hex + uuid.uuid4().hex, snapshot=str(snapshot), scene_name=scene.name,
           configuration=scene.wfrl_manual_trial.configuration, output_mp4=str(OUT / 'delivered.mp4'),
           width=WIDTH, height=HEIGHT, fps=25, hold_s=.04, samples=SAMPLES,
           ffmpeg=shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg',
           ffprobe=shutil.which('ffprobe') or '/opt/homebrew/bin/ffprobe')
(OUT / 'job.json').write_text(json.dumps(job))
started = time.monotonic()
report = render_job(OUT / 'job.json')
elapsed = time.monotonic() - started
scene = bpy.data.scenes[job['scene_name']]
assert not bpy.app.timers.is_registered(control._timer)
assert all(not entry.running for entry in scene.wfrl_manual_turbines)
assert (report['width'], report['height'], report['frame_count']) == (WIDTH, HEIGHT, 5)
assert trial.interpolation == 'LINEAR'
assert control.trial_configuration(scene)[1].interpolation == 'LINEAR'
assert json.loads((OUT / 'status.json').read_text())['state'] == 'COMPLETE'
assert (OUT / 'delivered.mp4').read_bytes() == (OUT / 'video.mp4').read_bytes()
with (OUT / 'frames.csv').open() as handle:
    rows = list(csv.DictReader(handle))
for index, row in enumerate(rows):
    expected = trial.pose_at(index / 25)
    assert float(row['time_s']) == index / 25
    assert abs(float(row['azimuth_deg']) - expected.azimuth_deg) < 1e-8
    for blade, pitch in enumerate(expected.pitches_deg, 1):
        assert abs(float(row[f'pitch_b{blade}_deg']) - pitch) < 1e-8
assert abs(float(rows[-1]['pitch_b1_deg']) - 20) < 1e-8
assert abs(float(rows[1]['pitch_b1_deg']) - 7.25) < 1e-6
assert all(float(row['pitch_b2_deg']) == 7 and float(row['pitch_b3_deg']) == 11 for row in rows)
scene.view_layers[0].update()
other_after = {entry.turbine_id: [tuple(value for row in getattr(entry, field).matrix_world for value in row)
                 for field in control._POINTER_FIELDS] for entry in scene.wfrl_manual_turbines if entry.turbine_id != 'T1'}
assert other_before == other_after
assert all(scene.objects[name].hide_render == hidden for name, hidden in hidden_before.items())
camera = scene.camera
assert camera.parent == control._t1(scene).yaw_object
assert camera.name.endswith('.Camera.T1.Down')
assert not scene.render.use_motion_blur
assert scene.render.pixel_aspect_x == scene.render.pixel_aspect_y == 1
assert scene.get('wfrl_manual_blade_model') == 'rootkink'
published = (OUT / 'delivered.mp4').read_bytes()
try:
    render_job(OUT / 'job.json')
except (ValueError, FileExistsError):
    pass
else:
    raise AssertionError('Existing output was accepted')
assert (OUT / 'delivered.mp4').read_bytes() == published
assert json.loads((OUT / 'status.json').read_text())['state'] == 'COMPLETE'
for label, changes in (('mismatched-configuration', {'configuration': '{}'}),
                       ('missing-ffmpeg', {'ffmpeg': str(OUT / 'missing-ffmpeg')}),
                       ('fractional-frame', {'hold_s': .01})):
    failed = OUT / label
    failed.mkdir()
    failed_job = dict(job, output_mp4=str(failed / 'must-not-exist.mp4'), **changes)
    (failed / 'job.json').write_text(json.dumps(failed_job))
    try:
        render_job(failed / 'job.json')
    except ValueError:
        pass
    else:
        raise AssertionError(f'{label} was accepted')
    assert json.loads((failed / 'status.json').read_text())['state'] == 'FAILED'
    assert not Path(failed_job['output_mp4']).exists()
for label in ('camera-track-to', 'rotor-driver', 'root-action', 'yaw-driver',
              'pitch-parent-constraint', 'blade-driver', 'lens-driver'):
    failed = OUT / label
    failed.mkdir()
    bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False, use_scripts=False)
    scene = bpy.data.scenes[job['scene_name']]
    bpy.context.window.scene = scene
    entry = control._t1(scene)
    owner = scene['wfrl_manual_owner']
    camera = cameras.ensure_gimbal(scene, 'T1', yaw_object=entry.yaw_object,
                                   name=owner + '.Camera.T1.Down', owner=owner)
    if label == 'camera-track-to':
        constraint = camera.constraints.new('TRACK_TO')
        constraint.target = entry.blade1_object
    elif label == 'pitch-parent-constraint':
        constraint = entry.blade1_object.parent.constraints.new('COPY_ROTATION')
        constraint.target = entry.rotor_object
    elif label == 'root-action':
        entry.yaw_object.parent.keyframe_insert(data_path='rotation_euler', frame=1)
    elif label == 'lens-driver':
        camera.data.driver_add('lens').driver.expression = '50.0'
    else:
        obj = {'rotor-driver': entry.rotor_object, 'yaw-driver': entry.yaw_object,
               'blade-driver': entry.blade1_object}[label]
        obj.driver_add('rotation_euler', 0).driver.expression = '0.4'
    injected_snapshot = failed / 'snapshot.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(injected_snapshot))
    failed_job = dict(job, snapshot=str(injected_snapshot),
                      output_mp4=str(failed / 'must-not-exist.mp4'))
    (failed / 'job.json').write_text(json.dumps(failed_job))
    try:
        render_job(failed / 'job.json')
    except ValueError as exc:
        assert '约束、动作或驱动' in str(exc) or '镜头含动作或驱动' in str(exc), str(exc)
    else:
        raise AssertionError(f'{label} was accepted')
    assert json.loads((failed / 'status.json').read_text())['state'] == 'FAILED'
    assert not Path(failed_job['output_mp4']).exists()
    assert not (failed / 'master_frames').exists()
result = dict(status='PASS', source=str(ROOT / 'blender_frontend/wfrl_blender'),
              renderer_source_sha256=hashlib.sha256((ROOT / 'blender_frontend/wfrl_blender/manual_video_render.py').read_bytes()).hexdigest(),
              blender=bpy.app.version_string, elapsed_render_encode_s=elapsed,
              frame_count=5, resolution=[WIDTH, HEIGHT], fps=25, samples=SAMPLES,
              checks=['deterministic poses', 'frozen unselected blades', 'stationary T2/T3',
                      'fixed own Down', 'no geometry hidden', 'strict H264 PTS decode',
                      'exact duration', 'no output overwrite', 'failed attempt status',
                      'mismatched configuration rejected', 'missing encoder rejected',
                      'fractional frame rejected', 'camera tracking rejected',
                      'rotor/root/yaw/pitch-parent/blade animation or constraint rejected',
                      'lens driver rejected'])
(OUT / 'regression.json').write_text(json.dumps(result, indent=2))
print('WFRL_MANUAL_VIDEO_REGRESSION ' + json.dumps(result), flush=True)

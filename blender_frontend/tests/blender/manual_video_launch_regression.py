"""Native background export launch, isolation, cancellation and completed MP4."""
import importlib
import csv
import json
import os
from pathlib import Path
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
from wfrl_blender import manual_control as control, manual_video as video

assert bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
full = os.environ.get('WFRL_VIDEO_FULL_SAMPLE') == '1'
addon.register()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
scene = bpy.context.scene
t1 = control._t1(scene)
t1.rpm, t1.azimuth_deg = 9, 148
scene.wfrl_manual_trial.duration_s = 8 if full else .08
scene.wfrl_manual_video_hold_s = 7 if full else .04
scene.wfrl_manual_trial.target_b1_deg = 20
control.confirm_trial(scene)
control.trial_action(scene, 'PAUSE')
_start, frozen = control.trial_configuration(scene)
control.trial_action(scene, 'END')
t1.yaw_deg, t1.azimuth_deg, t1.rpm = -31, 223, 17
t1.pitch_deg = 61
scene.wfrl_manual_trial.duration_s = .16
scene.wfrl_manual_trial.target_b1_deg = -10
before = control.pose_snapshot(scene)
config = scene.wfrl_manual_trial.configuration
filepath = bpy.data.filepath
camera = scene.camera
output = OUT / 'T1-Down-9RPM-1080p25.mp4'
linked_output, missing_target = OUT / 'existing-link.mp4', OUT / 'missing-target.mp4'
linked_output.symlink_to(missing_target)
try:
    video.launch(scene, linked_output)
except ValueError:
    pass
else:
    video.cancel(scene)
    video._poll()
    raise AssertionError('A pre-existing dangling output link was accepted')
assert linked_output.is_symlink() and not missing_target.exists()
assert bpy.ops.wfrl.manual_video(filepath=str(output)) == {'FINISHED'}
job_path = Path(scene['wfrl_manual_video_job'])
assert video.active(scene)
try:
    video.launch(scene, OUT / 'duplicate.mp4')
except ValueError:
    pass
else:
    raise AssertionError('Concurrent export accepted')
assert control.pose_snapshot(scene) == before
assert scene.wfrl_manual_trial.configuration == config and scene.camera == camera
assert bpy.data.filepath == filepath
deadline = time.monotonic() + 600
while video.active(scene) and time.monotonic() < deadline:
    time.sleep(.2)
assert not video.active(scene), 'Export did not finish within the test deadline'
video._poll()
result = video.status(scene)
assert result['state'] == 'COMPLETE', result
assert output.is_file()
with (job_path.parent / 'frames.csv').open() as handle:
    rows = list(csv.DictReader(handle))
assert len(rows) == (375 if full else 3)
for index, row in enumerate(rows):
    expected = frozen.pose_at(index / 25)
    assert float(row['time_s']) == index / 25 and float(row['rpm']) == frozen.rpm
    assert abs(float(row['azimuth_deg']) - expected.azimuth_deg) < 1e-8
    for blade, pitch in enumerate(expected.pitches_deg, 1):
        assert abs(float(row[f'pitch_b{blade}_deg']) - pitch) < 1e-8
assert control.pose_snapshot(scene) == before and scene.camera == camera
assert bpy.data.filepath == filepath
try:
    video.launch(scene, output)
except ValueError:
    pass
else:
    raise AssertionError('Existing output accepted')
cancel_output = OUT / 'cancelled.mp4'
video.launch(scene, cancel_output)
video.unregister()
video.register()
assert bpy.app.timers.is_registered(video._poll)
video.cancel(scene)
video._poll()
assert video.status(scene)['state'] == 'CANCELLED'
assert not cancel_output.exists()
assert control.pose_snapshot(scene) == before
scene['wfrl_manual_video_job'] = str(job_path)
report = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
              launched_via_product_operator=True, source_scene_preserved=True,
              source_filepath_preserved=True, concurrent_job_rejected=True,
              overwrite_rejected=True, cancellation=True,
              dangling_output_link_rejected=True,
              frozen_configuration_after_end_and_parameter_edits=True,
              all_frame_poses_checked=True,
              polling_restored_after_registration=True,
              output_mp4=str(output), job=str(job_path), completed=result)
(OUT / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print('MANUAL_VIDEO_LAUNCH_PASS ' + json.dumps(report), flush=True)
addon.unregister()

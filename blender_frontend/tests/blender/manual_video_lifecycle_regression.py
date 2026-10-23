"""Native video task ownership through undo, reload and add-on registration."""
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
from wfrl_blender import cameras, manual_control as control, manual_video as video
from wfrl_blender import manual_video_job as jobs

assert bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
report = dict(addon_file=addon.__file__, blender=bpy.app.version_string)
job_path = None
addon.register()
try:
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message='Factory scene')
    assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
    scene = bpy.context.scene
    assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action='CONFIRM') == {'FINISHED'}
    assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action='PAUSE') == {'FINISHED'}
    before_export = OUT / 'before-export.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(before_export))
    bpy.ops.ed.undo_push(message='Confirmed trial')
    output = OUT / 'cancelled.mp4'
    job_path = video.launch(scene, output)
    assert video.active(scene)
    initial_status = video.status(scene)
    assert bpy.ops.ed.undo() == {'FINISHED'}
    scene = bpy.context.scene
    assert jobs.query(job_path)['active']
    assert video.active(scene), 'Undo lost the running task association'
    assert video.status(scene).get('total_frames') == initial_status['total_frames']
    try:
        video.launch(scene, OUT / 'duplicate.mp4')
    except ValueError as exc:
        assert '已有视频' in str(exc)
    else:
        raise AssertionError('Undo allowed a second task for the same scene')
    assert bpy.ops.ed.redo() == {'FINISHED'}
    scene = bpy.context.scene
    assert video.active(scene), 'Redo lost the running task association'
    scene.name = 'Renamed manual scene'
    assert video.active(scene)
    copy = scene.copy()
    try:
        assert not video.active(copy), 'A copied scene claimed its source task'
    finally:
        bpy.data.scenes.remove(copy)
    bpy.ops.wm.open_mainfile(filepath=str(before_export))
    scene = bpy.context.scene
    assert video.active(scene), 'Reloading a pre-export save lost the running task association'
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'running-task.blend'))
    video._JOBS.clear()
    video._SCENE_JOBS.clear()
    bpy.ops.wm.open_mainfile(filepath=str(OUT / 'running-task.blend'))
    scene = bpy.context.scene
    assert video.active(scene), 'Reload lost the running task association'
    addon.unregister()
    addon.register()
    assert video.active(scene)
    assert bpy.app.timers.is_registered(video._poll)
    video.cancel(scene)
    video.cancel(scene)
    video._poll()
    assert video.status(scene)['state'] == 'CANCELLED'
    assert not output.exists()
    assert not video._JOBS
    entry = control._t1(scene)
    owner = scene['wfrl_manual_owner']
    camera = cameras.ensure_gimbal(scene, 'T1', yaw_object=entry.yaw_object,
                                   name=owner + '.Camera.T1.Down', owner=owner)
    constraint = camera.constraints.new('TRACK_TO')
    constraint.target = entry.blade1_object
    failed_output = OUT / 'failed.mp4'
    job_path = video.launch(scene, failed_output)
    camera.constraints.remove(constraint)
    deadline = time.monotonic() + 30
    while video.active(scene) and time.monotonic() < deadline:
        time.sleep(.1)
    assert not video.active(scene), 'Failed renderer did not exit'
    video._poll()
    failed = video.status(scene)
    assert failed['state'] == 'FAILED' and '约束、动作或驱动' in failed['error'], failed
    assert not failed_output.exists() and not video._JOBS
    job_path = video.launch(scene, OUT / 'retry-cancelled.mp4')
    video.cancel(scene)
    video._poll()
    assert video.status(scene)['state'] == 'CANCELLED'
    report.update(status='PASS', undo_redo_task_retained=True, duplicate_task_rejected=True,
                  renamed_scene_retained=True, copied_scene_independent=True,
                  pre_export_save_reload_retained=True,
                  reload_retained=True, full_addon_registration_retained=True,
                  cancellation=True, cancellation_idempotent=True, controller_memory_reset=True,
                  failed_renderer_status_retained=True, retry_after_failure=True,
                  job=str(job_path))
except BaseException:
    report.update(status='FAILED', error=traceback.format_exc())
    raise
finally:
    if job_path is not None and str(job_path) in video._JOBS:
        bpy.context.scene['wfrl_manual_video_job'] = str(job_path)
        if hasattr(video, '_SCENE_JOBS'):
            video._SCENE_JOBS[bpy.context.scene.session_uid] = str(job_path)
        video.cancel(bpy.context.scene)
        video._poll()
    addon.unregister()
    (OUT / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print('MANUAL_VIDEO_LIFECYCLE_PASS ' + json.dumps(report), flush=True)

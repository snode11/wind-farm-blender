"""Installed video controller recovery across Blender processes using a non-media child."""
import importlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
MODULE = os.environ['WFRL_ADDON_MODULE']
addon = importlib.import_module(MODULE)
assert Path(addon.__file__).resolve().parent == Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).resolve()
assert MODULE in bpy.context.preferences.addons
ui = importlib.import_module(MODULE + '.manual_video')
jobs = importlib.import_module(MODULE + '.manual_video_job')
phase = os.environ['WFRL_VIDEO_RECOVERY_MODE']
python = os.environ['WFRL_VIDEO_PYTHON']
report = {'module_file': addon.__file__, 'blender': bpy.app.version_string,
          'scope': 'Installed controller, configured project, non-media child, fresh-process recovery and cancellation'}
if phase == 'create':
    OUT.mkdir(parents=True, exist_ok=False)
    bpy.context.preferences.addons[MODULE].preferences.project_dir = str(ROOT)
    assert ui._workspace_root() == ROOT
    ui._preflight_output(ROOT, OUT / 'unused.mp4')
    scene = bpy.context.scene
    scene['wfrl_manual_owner'] = uuid.uuid4().hex
    snapshot = OUT / 'snapshot.fixture'
    snapshot.write_text('Non-media input, no rendering')
    job_path = OUT / 'job.json'
    job = dict(schema='wfrl.manual-video-job.v2', job_id=uuid.uuid4().hex,
               control_token=uuid.uuid4().hex + uuid.uuid4().hex, snapshot=str(snapshot),
               scene_name=scene.name, output_mp4=str(OUT / 'unused.mp4'), frame_count=0,
               render_command=[python, '-c', 'import time; time.sleep(120)'])
    jobs.write_json(job_path, job)
    jobs.write_json(OUT / 'scene-binding.json', dict(scene_name=scene.name, scene_owner=scene['wfrl_manual_owner']))
    jobs.start(job_path, python=python, project_root=ROOT,
               preflight_check=lambda destination: ui._preflight_output(ROOT, destination))
    scene['wfrl_manual_video_job'] = str(job_path)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'controller.blend'), check_existing=False)
    assert ui.status(scene)['active']
    report['active_before_exit'] = True
elif phase == 'recover':
    assert not ui._JOBS and not ui._SCENE_JOBS
    bpy.ops.wm.open_mainfile(filepath=str(OUT / 'controller.blend'))
    scene = bpy.context.scene
    record = ui.status(scene)
    assert record['active'], record
    job_path = OUT / 'job.json'
    started = time.monotonic()
    pending = ui.cancel(scene)
    elapsed = time.monotonic() - started
    assert elapsed < .2 and pending['cancel_requested'] and not pending['final'], pending
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = ui.status(scene)
        if record.get('final'):
            break
        time.sleep(.1)
    assert record['state'] == 'CANCELLED' and record['final'] and not record['active'], record
    assert not (OUT / 'unused.mp4').exists()
    report.update(recovered_in_fresh_process=True, cancellation=record['state'], final=record['final'],
                  cancel_call_s=elapsed)
else:
    raise ValueError(phase)
report['status'] = 'PASS'
(OUT / (phase + '.json')).write_text(json.dumps(report, indent=2) + '\n')
print('MANUAL_VIDEO_INSTALLED_' + phase.upper() + '_PASS', flush=True)

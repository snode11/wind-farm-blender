"""History export ownership with native scenes and extension registration.

Run in an isolated Blender profile with --background --factory-startup,
--python-exit-code 1, and a new WFRL_TEST_OUTPUT directory. No solver is used.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import charts
from wfrl_blender.operators import history_export

OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
assert not bpy.data.filepath and '--factory-startup' in sys.argv
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
checks = []


def complete_worker():
    job = charts.export_job
    job.thread.join(5)
    assert not job.thread.is_alive(), 'History export timed out'
    return job.poll()


def start(scene, name):
    bpy.context.window.scene = scene
    path = OUT / name
    assert bpy.ops.wfrl.export_history(filepath=str(path)) == {'FINISHED'}
    return path


addon.register()
source = bpy.context.scene
other = bpy.data.scenes.new('HistoryExport.Other')
other['wfrl_history_export_status'] = 'other scene status'

path = start(source, 'history.json')
assert complete_worker() == 'COMPLETE'
bpy.context.window.scene = other
assert history_export._poll_export() is None
assert source['wfrl_history_export_status'] == f'COMPLETE / {path}'
assert other['wfrl_history_export_status'] == 'other scene status'
json.loads(path.read_text())
checks.append('completion_stays_with_source_scene')

start(source, 'missing/history.json')
assert complete_worker() == 'FAILED'
bpy.context.window.scene = other
assert history_export._poll_export() is None
assert source['wfrl_history_export_status'].startswith('FAILED /')
assert other['wfrl_history_export_status'] == 'other scene status'
checks.append('failure_stays_with_source_scene')

first = start(source, 'first.json')
assert complete_worker() == 'COMPLETE'
second = start(other, 'second.json')
assert source['wfrl_history_export_status'] == f'COMPLETE / {first}'
assert complete_worker() == 'COMPLETE'
assert history_export._poll_export() is None
assert other['wfrl_history_export_status'] == f'COMPLETE / {second}'
checks.append('consecutive_exports_preserve_both_owners')

path = start(source, 'reenabled.json')
addon.unregister()
assert not bpy.app.timers.is_registered(history_export._poll_export)
assert complete_worker() == 'COMPLETE'
bpy.context.window.scene = other
addon.register()
assert bpy.app.timers.is_registered(history_export._poll_export)
assert history_export._poll_export() is None
assert source['wfrl_history_export_status'] == f'COMPLETE / {path}'
assert other['wfrl_history_export_status'] == f'COMPLETE / {second}'
checks.append('reenabling_resumes_completion_for_source')

start(source, 'deleted-owner.json')
assert complete_worker() == 'COMPLETE'
bpy.context.window.scene = other
bpy.data.scenes.remove(source)
assert history_export._poll_export() is None
assert other['wfrl_history_export_status'] == f'COMPLETE / {second}'
checks.append('deleted_owner_cannot_write_into_other_scene')

saved = OUT / 'replacement.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
start(other, 'closed-owner.json')
assert complete_worker() == 'COMPLETE'
assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}
assert bpy.app.timers.is_registered(history_export._poll_export)
assert history_export._poll_export() is None
assert bpy.context.scene['wfrl_history_export_status'] == f'COMPLETE / {second}'
checks.append('file_reload_preserves_saved_scene_status')

addon.unregister()
result = {'status': 'PASS', 'checks': checks, 'addon': addon.__file__,
          'blender': bpy.app.version_string,
          'source_sha256': hashlib.sha256(Path(history_export.__file__).read_bytes()).hexdigest()}
(OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print('HISTORY_EXPORT_REGRESSION_PASS', flush=True)

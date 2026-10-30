"""An unavailable standalone replay must fail before writing animation frames."""
import importlib
import json
import os
from pathlib import Path
import sys
import uuid

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
worker = importlib.import_module('wfrl_blender.animation_render_worker')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.register()
scene = bpy.context.scene
scene['wfrl_scene_kind'] = 'clearance_replay'
scene['wfrl_clearance_demo'] = 'normal'
scene.wfrl_clearance_normal_path = str(OUT / 'missing-result-package')
assert not scene.get('wfrl_farm_flex_path')
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 1
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100
snapshot = OUT / 'unavailable-replay.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(snapshot))
job = dict(job_id=uuid.uuid4().hex, addon_root=str(ROOT / 'blender_frontend/wfrl_blender'),
           snapshot=str(snapshot), scene_name=scene.name,
           frame_start=1, frame_end=2, frame_step=1, total_frames=2)
addon.unregister()
job_path = OUT / 'job.json'
job_path.write_text(json.dumps(job))
try:
    try:
        worker.run(job_path)
    except ValueError as error:
        assert '回放' in str(error), str(error)
    else:
        raise AssertionError('Animation completed without restoring its standalone replay')
    manifest = json.loads((OUT / 'manifest.json').read_text())
    assert manifest['state'] == 'FAILED'
    assert manifest['completed_frames'] == 0
    assert not (OUT / 'frames').exists()
    result = dict(status='PASS', source=str(ROOT / 'blender_frontend/wfrl_blender'),
                  blender=bpy.app.version_string, standalone_replay_required=True,
                  frame_count=0, scope='Native saved-scene replay recovery and render preflight.')
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
finally:
    restored_addon = sys.modules.get('_wfrl_animation_worker')
    if restored_addon is not None:
        restored_addon.unregister()
print('ANIMATION_REPLAY_RESTORE_PASS', flush=True)

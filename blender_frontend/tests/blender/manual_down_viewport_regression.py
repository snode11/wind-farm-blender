"""Visible GPU Down exports preserve the source and restore the viewport."""
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
assert not bpy.app.background
addon = importlib.import_module('wfrl_blender')
control = importlib.import_module('wfrl_blender.manual_control')
down = importlib.import_module('wfrl_blender.manual_down')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


def run():
    result = dict(status='RUNNING', modes=[], physical_input=False)
    try:
        addon.register()
        assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
        scene = bpy.context.scene
        source_camera = scene.camera
        render = scene.render
        saved = (render.resolution_x, render.resolution_y, render.resolution_percentage, render.engine)
        for mode in ('VIEWPORT_SOLID', 'VIEWPORT_MATERIAL'):
            pose = control.pose_snapshot(scene)
            target = OUT / (mode.lower() + '.mp4')
            assert down.export_down_video(scene, 'T1', str(target), frames=3, fps=25,
                                          size=(64, 36), engine=mode, blocking=True) == str(target)
            assert target.is_file() and target.with_suffix('.json').is_file()
            assert bpy.context.scene == scene and scene.camera == source_camera
            assert control.pose_snapshot(scene) == pose
            assert (render.resolution_x, render.resolution_y, render.resolution_percentage, render.engine) == saved
            assert not down.active(scene)
            result['modes'].append(mode)
        addon.unregister()
        result['status'] = 'PASS'
    except Exception:
        result.update(status='FAIL', traceback=traceback.format_exc())
        print(result['traceback'], flush=True)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MANUAL_DOWN_VIEWPORT ' + json.dumps(result), flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1.0)

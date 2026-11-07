"""Down view rejection preserves output settings; teardown needs no control RNA."""
from pathlib import Path
import importlib
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
assert bpy.app.background and not hasattr(bpy.types.Scene, 'wfrl_manual_turbines')
addon = importlib.import_module('wfrl_blender')
control = importlib.import_module('wfrl_blender.manual_control')
down = importlib.import_module('wfrl_blender.manual_down')
addon.register()
if not hasattr(bpy.types.Scene, 'wfrl_manual_blade_shape'):
    down.register()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
scene = bpy.context.scene
render = scene.render
render.resolution_x, render.resolution_y, render.resolution_percentage = 864, 486, 37
saved_render = (render.resolution_x, render.resolution_y, render.resolution_percentage)
entry = down._entry(scene, 'T2')
entry.rotor_object.rotation_euler.x += .2
try:
    result = bpy.ops.wfrl.manual_down_view(turbine='T2')
    assert result == {'CANCELLED'}
except RuntimeError:
    pass
assert (render.resolution_x, render.resolution_y, render.resolution_percentage) == saved_render
entry.rotor_object.rotation_euler.x -= .2
assert bpy.ops.wfrl.manual_down_view(turbine='T2') == {'FINISHED'}
assert (render.resolution_x, render.resolution_y, render.resolution_percentage) == (1920,1080,100)
copy = down._clone_scene(scene)
key = copy.as_pointer()
source_key = scene.as_pointer()
count = len(bpy.data.scenes)
plan = down._plan(scene, 25, 2, False, 'T2', control.pose_snapshot(scene))
temporary = tempfile.mkdtemp(prefix='wfrl-down-teardown-test-')
down._EXPORT[key] = dict(source=scene, source_key=source_key, scene=copy, plan=plan,
                         temp=temporary, labels=True, blocking=False, cancelled=False)
if down._frame_handler not in bpy.app.handlers.frame_change_pre:
    bpy.app.handlers.frame_change_pre.append(down._frame_handler)
proxy = SimpleNamespace(app=SimpleNamespace(is_job_running=lambda kind: True),
                        data=bpy.data, types=bpy.types, utils=bpy.utils)
with patch.object(down, 'bpy', proxy):
    down.unregister()
assert down._EXPORT[key]['cancelled']
assert len(bpy.data.scenes) == count, 'Live native render scene was removed during unregister'
control.unregister()
assert not hasattr(bpy.types.Scene, 'wfrl_manual_turbines')
with patch.object(down, 'apply_frame', side_effect=AssertionError('Accessed removed control RNA')):
    down._frame_handler(copy)
bpy.data.scenes.remove(scene)
assert down._finish(copy, completed=False) is None
assert len(bpy.data.scenes) == count-2
assert down._LAST[source_key]['state'] == 'CANCELLED'
assert down._frame_handler not in bpy.app.handlers.frame_change_pre
print('MANUAL_DOWN_LIFECYCLE_PASS', flush=True)

"""Navigation, replay recovery and compact UI using the delivered result packages."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import runpy
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import clearance_replay as playback, runtime
from wfrl_blender.panels import clearance
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
delivery = json.loads((ROOT / 'dist/lidar-delivery.json').read_text())
scene.wfrl_clearance_normal_path = str(ROOT / delivery['packages']['normal'])
scene.wfrl_clearance_near_tower_path = str(ROOT / delivery['packages']['close'])
assert not scene.wfrl_clearance_show_details and not scene.wfrl_clearance_show_camera
# Auxiliary World camera is not required to load a matching result or side view.
world = scene.objects['WFRL.Camera.World']
world.name = 'SavedWorldForTest'
assert bpy.ops.wfrl.clearance_clip(demo='normal') == {'FINISHED'}
assert scene.camera.name == 'WFRL.Camera.T1.Clearance'
world.name = 'WFRL.Camera.World'
assert bpy.ops.wfrl.clearance_clip(demo='normal') == {'FINISHED'}
assert scene.camera.name == 'WFRL.Camera.T1.Clearance'
scene.frame_set(201)
before = playback.sample(scene)
assert bpy.ops.wfrl.clearance_view(view='WORLD') == {'FINISHED'}
assert scene.camera.name == 'WFRL.Camera.World'
assert playback.sample(scene) == before and scene['wfrl_scene_kind'] == 'clearance_replay'
assert bpy.ops.wfrl.clearance_view(view='MEASUREMENT') == {'FINISHED'}
assert playback.sample(scene) == before
bpy.context.view_layer.update()
yaw = scene.objects['WFRL.Turbine.T1.YawRoot']
for point in ((-2, 0, 2), (-15, 0, -62)):
    uv = world_to_camera_view(scene, scene.camera, yaw.matrix_world @ Vector(point))
    assert .03 < uv.x < .97 and .03 < uv.y < .97, ('Measurement zone clipped', tuple(uv))
assert bpy.ops.wfrl.clearance_restart() == {'FINISHED'}
assert scene.frame_current == scene.frame_start and playback.sample(scene)['measurement'] is None
scene.frame_set(201)
assert playback.sample(scene) == before
assert bpy.ops.wfrl.clearance_clip(demo='near_tower') == {'FINISHED'}
assert scene['wfrl_clearance_demo'] == 'near_tower' and scene.frame_current == 1
scene.frame_set(scene.frame_end)
assert playback.sample(scene)['statistics'] == playback.reader_for(scene).package.statistics
# Empty path clears prior values and exposes the repair controls. Error reporting
# is called directly so Blender's ERROR-to-RuntimeError wrapper does not mask CANCELLED.
valid_path = scene.wfrl_clearance_normal_path
scene.wfrl_clearance_normal_path = ''
messages = []
op = SimpleNamespace(demo='normal', report=lambda level, msg: messages.append(msg))
assert clearance.WFRL_OT_ClearanceClip.execute(op, bpy.context) == {'CANCELLED'}
assert playback.sample(scene) is None and scene.wfrl_clearance_show_config and messages
assert not clearance.WFRL_OT_ClearanceRestart.poll(bpy.context)
scene.wfrl_clearance_normal_path = valid_path
assert bpy.ops.wfrl.clearance_clip(demo='normal') == {'FINISHED'}
assert not scene.wfrl_clearance_show_config
# UI hierarchy: statistics and configuration fields appear only when expanded.
class Layout:
    def __init__(self): self.labels, self.props = [], []
    def box(self): return self
    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def label(self, *, text='', **kwargs): self.labels.append(text)
    def prop(self, scene, name, **kwargs): self.props.append(name)
    def prop_enum(self, *args, **kwargs): pass
    def operator(self, *args, **kwargs): return SimpleNamespace()
layout = Layout()
clearance.draw(layout, scene)
assert not any('平均绝对误差' in x for x in layout.labels)
assert 'wfrl_clearance_normal_path' not in layout.props
scene.wfrl_clearance_show_details = scene.wfrl_clearance_show_config = True
clearance.draw(layout, scene)
assert any('平均绝对误差' in x for x in layout.labels)
assert 'wfrl_clearance_normal_path' in layout.props
# Fresh/held/expired UI follows the real reader contract, including an invalid
# B2 sample that retains the preceding triplet. This is a test-only fixture.
reader = runpy.run_path(str(ROOT / 'tests/lidar/test_replay.py'))['ReplayTests']().fixture()
scene.wfrl_clearance_show_details = scene.wfrl_clearance_show_config = False
def labels_at(t):
    result = Layout()
    with patch.object(playback, 'sample', return_value=reader.at(t)):
        clearance.draw(result, scene)
    return result.labels
fresh = labels_at(3.03)
assert '新测量 · 叶片 1' in fresh and '测量时刻：3.00 s' in fresh
assert '0.03 仿真秒前' in fresh
fps = scene.render.fps
scene.render.fps = 100
assert labels_at(3.03) == fresh, 'Playback FPS reclassified a fixed simulation frame'
scene.render.fps = fps
held = labels_at(4)
assert '上次有效测量 · 叶片 1' in held and '1.00 仿真秒前' in held
assert '测量时刻：3.00 s' in held and 'B2 估计：5.30 m' in held
expired = labels_at(5.1)
assert '等待下一次有效测量' in expired
assert '仿真真值：-- m' in expired and 'B2 估计：-- m' in expired and '偏差：-- m' in expired
assert not any('测量时刻' in label or '演示阈值' in label for label in expired)
assert len(fresh) == len(held) == len(expired), 'Readout rows moved the playback controls'
assert labels_at(3.03) == fresh and labels_at(4) == held, 'Seeking changed the displayed sample'
# Pose is inside the camera fold; measurement state stays on the main card.
from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal
ctx = SimpleNamespace(scene=scene, space_data=None, evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get)
closed = Layout()
WFRL_PT_Gimbal.draw(SimpleNamespace(layout=closed), ctx)
assert not any(label.startswith(('Pan ', 'Roll ', '当前 Camera')) for label in closed.labels)
scene.wfrl_clearance_show_camera = True
opened = Layout()
WFRL_PT_Gimbal.draw(SimpleNamespace(layout=opened), ctx)
assert any(label.startswith('Pan ') for label in opened.labels)
assert any(label.startswith('Roll ') for label in opened.labels)
scene.wfrl_clearance_show_camera = False
# Busy backend cannot be replaced through the result buttons.
old = runtime._state
runtime._state = SimpleNamespace(connection='CONNECTED', allows=lambda action: False)
assert not clearance.WFRL_OT_ClearanceClip.poll(bpy.context)
runtime._state = old
# Going back to the normal demo rebuilds only by the existing explicit action.
addon.load_demo_scene()
assert playback.reader_for(scene) is None and scene['wfrl_scene_kind'] == 'demo'
assert bpy.ops.wfrl.clearance_clip(demo='normal') == {'FINISHED'}
assert playback.sample(scene)['time_s'] == 18
addon.unregister()
print('CLEARANCE_UX_REGRESSION_PASS')

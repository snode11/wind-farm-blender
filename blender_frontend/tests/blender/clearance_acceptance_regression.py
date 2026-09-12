"""Offline frontend acceptance regression; writes only to explicit WFRL_TEST_OUTPUT.

Run in a separate Blender process with --factory-startup --python-exit-code 1.
No solver, plugin installation, original package mutation, or device access.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import math
import os
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[3]
DELIVERY = json.loads((ROOT / 'dist/lidar-delivery.json').read_text())
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import bpy
from mathutils import Euler, Matrix
import wfrl_blender as addon
from wfrl_blender import clearance_replay as playback
from wfrl_blender.panels.clearance import WFRL_OT_ClearanceClip
from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal

OUTPUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUTPUT.mkdir(parents=True, exist_ok=True)
results = {}
def check(name, fn):
    try:
        fn()
        results[name] = {'status': 'PASS'}
    except Exception:
        results[name] = {'status': 'FAIL', 'traceback': traceback.format_exc()}
    (OUTPUT / 'results.json').write_text(json.dumps(results, indent=2))

addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
original = ROOT / DELIVERY['packages']['normal']
scene.render.fps = 25
scene.render.fps_base = 1
scene.wfrl_clearance_normal_path = str(original)
scene.wfrl_clearance_near_tower_path = str(ROOT / DELIVERY['packages']['close'])

def error_recovery():
    mutations = {
        'wrong_model': lambda m: m['model'].update(id='wrong-model'),
        'wrong_clip': lambda m: m['segment'].update(id='close'),
        'missing_calibration': lambda m: m['calibration'].pop('origin_m'),
        'boolean_calibration': lambda m: m['calibration']['origin_m'].__setitem__(0, True),
        'zero_direction': lambda m: m['calibration']['beam_directions'].__setitem__(0, [0, 0, 0]),
        'corruption': None,
    }
    failures = []
    for name, mutation in mutations.items():
        candidate = OUTPUT / ('package-' + name)
        shutil.copytree(original, candidate)
        if mutation:
            manifest = json.loads((candidate / 'manifest.json').read_text())
            mutation(manifest)
            (candidate / 'manifest.json').write_text(json.dumps(manifest))
        else:
            (candidate / 'motion.json').write_text('[]')
        playback.load(scene, str(original), 'normal')
        reader = playback.reader_for(scene)
        record = next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
        scene.frame_set(1 + math.ceil((record['time_s'] - reader.start_s) * 25))
        assert playback.sample(scene)['measurement'] is not None
        scene.wfrl_clearance_normal_path = str(candidate)
        messages = []
        operator = SimpleNamespace(demo='normal', report=lambda level, message: messages.append(message))
        result = WFRL_OT_ClearanceClip.execute(operator, SimpleNamespace(scene=scene, screen=bpy.context.screen))
        if result != {'CANCELLED'} or playback.sample(scene) is not None or not messages:
            failures.append(name)
        scene.wfrl_clearance_normal_path = str(original)
        result = WFRL_OT_ClearanceClip.execute(operator, SimpleNamespace(scene=scene, screen=bpy.context.screen))
        assert result == {'FINISHED'} and scene.frame_current == 1
        assert playback.sample(scene)['time_s'] == 18
        assert playback.sample(scene)['measurement'] is None
    assert not failures, 'Rejected invalid package / cleared old card / reported cause failed: ' + ', '.join(failures)


def playback_speed():
    global scene
    scene.render.fps = 25
    scene.render.fps_base = 1
    playback.load(scene, str(original), 'normal')
    scene.frame_set(201)
    before = playback.sample(scene)
    last_frame = scene.frame_end
    for fps, base in [(50, 1), (25, 2), (30, 1.001)]:
        scene.render.fps = fps
        scene.render.fps_base = base
        assert playback.sample(scene) == before, 'Playback speed changed data at the current frame'
        scene.frame_set(last_frame)
        assert playback.sample(scene)['time_s'] == 36, 'Playback speed truncated clip'
        assert playback.sample(scene)['statistics'] == playback.reader_for(scene).package.statistics
        scene.frame_set(201)
    scene.render.fps = 50
    scene.render.fps_base = 1
    bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / 'speed-reload.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / 'speed-reload.blend'))
    restored = bpy.context.scene
    scene = restored
    assert playback.sample(restored) == before, 'Saved speed changed simulation mapping on reload'
    assert restored.frame_end == last_frame
    scene.render.fps = 25
    scene.render.fps_base = 1

class Layout:
    def __init__(self): self.labels = []
    def label(self, *, text='', **kwargs): self.labels.append(text)
    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def box(self, **kwargs): return self
    def operator(self, *args, **kwargs): return SimpleNamespace()
    def prop(self, *args, **kwargs): pass
    def prop_enum(self, *args, **kwargs): pass

def camera_pose():
    camera = scene.objects['WFRL.Camera.Side']
    # Construct a known heading/elevation with optical roll (about local camera Z).
    pan, tilt, roll = 135., -30., 25.
    camera.parent = None
    camera.rotation_mode = 'QUATERNION'
    camera.rotation_quaternion = (Euler((math.radians(90 + tilt), 0, math.radians(pan - 90)), 'XYZ').to_quaternion()
                                  @ Matrix.Rotation(math.radians(roll), 4, 'Z').to_quaternion())
    bpy.context.view_layer.update()
    layout = Layout()
    ctx = SimpleNamespace(scene=scene, space_data=SimpleNamespace(type='VIEW_3D', use_local_camera=True, camera=camera),
                          evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get)
    WFRL_PT_Gimbal.draw(SimpleNamespace(layout=layout), ctx)
    assert f'Pan {pan:.1f}°  Tilt {tilt:.1f}°' in layout.labels, layout.labels
    assert any(label.startswith(f'Roll {roll:.1f}°') for label in layout.labels), layout.labels
    # Parent yaw and an evaluated transform must be reflected in the displayed world bearing.
    parent = bpy.data.objects.new('AcceptanceCameraParent', None)
    scene.collection.objects.link(parent)
    parent.rotation_euler.z = math.radians(20)
    camera.parent = parent
    bpy.context.view_layer.update()
    layout = Layout()
    WFRL_PT_Gimbal.draw(SimpleNamespace(layout=layout), ctx)
    assert f'Pan {pan + 20:.1f}°  Tilt {tilt:.1f}°' in layout.labels, layout.labels
    camera.parent = None
    camera.rotation_quaternion = (1, 0, 0, 0)
    bpy.context.view_layer.update()
    layout = Layout()
    WFRL_PT_Gimbal.draw(SimpleNamespace(layout=layout), ctx)
    assert 'Pan 未定义（垂直视轴）  Tilt -90.0°' in layout.labels
    assert any(label.startswith('Roll 未定义（垂直视轴）') for label in layout.labels)

check('invalid_packages_clear_and_recover', error_recovery)
check('speed_preserves_time_mapping_and_end', playback_speed)
# File reload replaces Blender RNA objects; never retain the previous scene.
scene = bpy.context.scene
check('render_camera_world_pan_tilt_optical_roll', camera_pose)
addon.unregister()
print(json.dumps(results, indent=2), flush=True)
assert all(r['status'] == 'PASS' for r in results.values()), 'Frontend acceptance regression failed'
print('CLEARANCE_ACCEPTANCE_REGRESSION_PASS', flush=True)

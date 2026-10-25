"""Owned manual Down cameras, labels, selected motion and short verified media."""
from pathlib import Path
import hashlib
import importlib
import json
import math
import os
import sys
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
assert bpy.app.background and not hasattr(bpy.types.Scene, 'wfrl_manual_turbines')
addon = importlib.import_module('wfrl_blender')
control = importlib.import_module('wfrl_blender.manual_control')
down = importlib.import_module('wfrl_blender.manual_down')
manual_scene = importlib.import_module('wfrl_blender.manual_scene')
addon.register()
if not hasattr(bpy.types.Scene, 'wfrl_manual_blade_shape'):
    down.register()
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
scene = bpy.context.scene
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)


def signature(scene):
    rows = {}
    for obj in scene.objects:
        data = obj.data
        item = dict(location=list(obj.location), rotation=list(obj.rotation_euler),
                    parent=obj.parent.name if obj.parent else None,
                    data=data.name if data else None, hide=[obj.hide_viewport, obj.hide_render])
        if obj.type == 'MESH':
            coords = np.empty(len(data.vertices)*3, np.float32)
            data.vertices.foreach_get('co', coords)
            item['mesh'] = hashlib.sha256(coords.tobytes()).hexdigest()
        if obj.type == 'CAMERA':
            item['optics'] = [data.lens, data.sensor_width, data.shift_x, data.shift_y]
        if hasattr(data, 'materials'):
            item['materials'] = [(mat.name, tuple(mat.diffuse_color)) for mat in data.materials if mat]
        rows[obj.name] = item
    return dict(objects=rows, camera=scene.camera.name if scene.camera else None,
                frame=[scene.frame_current, scene.frame_subframe],
                render=[scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
                        scene.render.fps, scene.render.fps_base, scene.render.filepath])


assert scene.wfrl_manual_blade_shape == 'rootkink'
initial = control.pose_snapshot(scene)
cameras = [down.ensure_down_camera(scene, tid) for tid in ('T1', 'T2', 'T3')]
assert len({cam.data.as_pointer() for cam in cameras}) == 3
for cam in cameras:
    assert cam.data.lens == 8 and cam.data.users == 1
    assert cam.name == scene['wfrl_manual_owner']+'.Camera.'+cam['wfrl_turbine']+'.Down'
    labels = [obj for obj in cam.children if obj.get('wfrl_down_label')]
    assert {obj.data.body for obj in labels if obj.type == 'FONT'} == {'B1', 'B2', 'B3'}
    assert sum(not obj.hide_render for obj in labels if obj.type == 'FONT') == 1
    assert down.ensure_down_camera(scene, cam['wfrl_turbine']) == cam
    assert cam.get('wfrl_manual_owner') == cam.data.get('wfrl_manual_owner') == scene['wfrl_manual_owner']
assert control.pose_snapshot(scene) == initial
# Full scene copies and arbitrary camera/label names preserve role identities.
source_before_copy = signature(scene)
assert bpy.ops.scene.new(type='FULL_COPY') == {'FINISHED'}
copied_scene = bpy.context.scene
copied_camera = down.ensure_down_camera(copied_scene, 'T2')
copied_camera.name = 'Custom copied Down camera'
for index, child in enumerate(copied_camera.children):
    child.name = f'Custom copied overlay {index}'
counts = (len(bpy.data.objects), len(bpy.data.cameras), len(bpy.data.curves), len(bpy.data.materials))
for _ in range(3):
    assert down.ensure_down_camera(copied_scene, 'T2') == copied_camera
    down.update_labels(copied_scene)
assert (len(bpy.data.objects), len(bpy.data.cameras), len(bpy.data.curves), len(bpy.data.materials)) == counts
assert len(down._label_objects(copied_scene, copied_camera)) == 4
assert signature(scene) == source_before_copy
bpy.context.window.scene = scene
scene.wfrl_manual_blade_labels = False
assert all(obj.hide_render for cam in cameras for obj in cam.children if obj.get('wfrl_down_label'))
scene.wfrl_manual_blade_labels = True
saved_pose = control.pose_snapshot(scene)
scene.wfrl_manual_blade_shape = 'nrel5mw'
assert scene['wfrl_manual_blade_model'] == 'nrel5mw'
assert control.pose_snapshot(scene) == saved_pose
scene.wfrl_manual_blade_shape = 'rootkink'
entry = scene.wfrl_manual_turbines[1]
entry.rotor_object.rotation_euler.x += .2
try:
    down.ensure_down_camera(scene, 'T2')
except ValueError:
    pass
else:
    raise AssertionError('Native rotation was silently accepted')
entry.rotor_object.rotation_euler.x -= .2
cam = cameras[1]
constraint = cam.constraints.new('COPY_ROTATION')
try:
    down.ensure_down_camera(scene, 'T2', reset=True)
except ValueError:
    pass
else:
    raise AssertionError('Camera constraints were silently accepted')
cam.constraints.remove(constraint)

before = signature(scene)
source_objects = set(scene.objects)
copy = down._clone_scene(scene)
assert set(copy.objects).isdisjoint(source_objects)
for item in copy.wfrl_manual_turbines:
    original = down._entry(scene, item.turbine_id)
    assert item.blade1_object.data != original.blade1_object.data
    assert item.blade1_object.data.materials[0] != original.blade1_object.data.materials[0]
test_plan = down._plan(scene, 25, 3, True, 'T2', control.pose_snapshot(scene))
down.apply_frame(copy, test_plan, 0)
frozen = {tid: (tuple(down._entry(copy, tid).rotor_object.rotation_euler),
                control.effective_pitches(down._entry(copy, tid))) for tid in ('T1','T3')}
selected_start = down._entry(copy, 'T2').rotor_object.rotation_euler.x
down.apply_frame(copy, test_plan, 1)
assert abs(down._entry(copy, 'T2').rotor_object.rotation_euler.x - selected_start) > .1
assert {tid: (tuple(down._entry(copy, tid).rotor_object.rotation_euler),
              control.effective_pitches(down._entry(copy, tid))) for tid in ('T1','T3')} == frozen
down._drop_scene(copy)
assert signature(scene) == before

entry.rpm = 11
entry.pitch_deg = 10
control.pause_scene(scene)
poses = control.pose_snapshot(scene)
plan = down._plan(scene, 25, 3, True, 'T2', poses)
assert all(item['rpm'] == (11 if item['turbine'] == 'T2' else 0) for item in plan['turbines'])
pre_count = len(bpy.data.scenes)
before = signature(scene)
for mode in ('WORKBENCH', 'SCENE'):
    target = output/(mode.lower()+'.mp4')
    result = down.export_down_video(scene, 'T2', str(target), frames=3, fps=25,
                                   engine=mode, size=(64,36), samples=1, labels=True)
    assert result == str(target) and target.is_file()
    metadata = json.loads(target.with_suffix('.json').read_text())
    assert metadata['verification'] == dict(frame_count=3, fps=25, resolution=[64,36], full_decode=True)
    assert len(metadata['samples']) == 3
    for tid in ('T1', 'T3'):
        assert all(row[tid] == metadata['samples'][0][tid] for row in metadata['samples'])
    assert metadata['samples'][1]['T2']['azimuth_deg'] != metadata['samples'][0]['T2']['azimuth_deg']
    assert signature(scene) == before and len(bpy.data.scenes) == pre_count
    try:
        down.export_down_video(scene, 'T2', str(target), frames=1, engine=mode, size=(64,36))
    except FileExistsError:
        pass
    else:
        raise AssertionError('Existing destination was overwritten')

# A failure after encoding retains the source scene and recoverable encoded file.
with patch.object(down, '_verify_video', side_effect=ValueError('verification rejected')):
    try:
        down.export_down_video(scene, 'T2', str(output/'rejected.mp4'), frames=1,
                               engine='WORKBENCH', size=(64,36))
    except RuntimeError:
        pass
    else:
        raise AssertionError('Rejected media was published')
status = down._LAST[scene.as_pointer()]
assert status['state'] == 'FAILED' and list(Path(status['temporary']).glob('*.mp4'))
assert json.loads((Path(status['temporary'])/'down.json').read_text())['frames'] == 1
assert not (output/'rejected.mp4').exists()
assert signature(scene) == before and len(bpy.data.scenes) == pre_count
assert not down.active(scene)
# Cancellation releases the isolated scene, retaining explicitly shared/fake-user data.
copy = down._clone_scene(scene)
kept = down._entry(copy, 'T1').blade1_object.data
kept.use_fake_user = True
shared = next(obj.data for obj in copy.objects if obj.type == 'MESH' and obj.data != kept)
other = bpy.data.objects.new('Shared export mesh consumer', shared)
scene.collection.objects.link(other)
temporary = Path(down.tempfile.mkdtemp(prefix='wfrl-down-cancel-test-'))
(temporary/'partial.mp4').write_bytes(b'partial')
down._EXPORT[copy.as_pointer()] = dict(source=scene, scene=copy, temp=str(temporary))
assert down._finish(copy, completed=False) is None
assert down._LAST[scene.as_pointer()]['state'] == 'CANCELLED'
assert other.data == shared and kept.use_fake_user
assert (temporary/'partial.mp4').read_bytes() == b'partial'
bpy.data.objects.remove(other, do_unlink=True)
kept.use_fake_user = False
bpy.data.meshes.remove(kept)
bpy.data.meshes.remove(shared)
assert signature(scene) == before and len(bpy.data.scenes) == pre_count
# Frozen trial identity and shape selection remain consistent after rejection.
control.confirm_trial(scene)
scene.wfrl_manual_blade_shape = 'nrel5mw'
assert scene.wfrl_manual_blade_shape == scene['wfrl_manual_blade_model'] == 'rootkink'
try:
    down.export_down_video(scene, 'T2', str(output/'trial.mp4'), frames=1, engine='WORKBENCH', size=(64,36))
except ValueError:
    pass
else:
    raise AssertionError('Convenience export accepted an active frozen trial')
control.trial_action(scene, 'END')
assert not (output/'trial.mp4').exists()
print('MANUAL_DOWN_REGRESSION_PASS', flush=True)

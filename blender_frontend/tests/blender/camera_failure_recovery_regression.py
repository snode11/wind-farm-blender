"""Camera draft cancellation and nacelle construction with real Blender IDs."""
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import cameras, custom_cameras as core, custom_camera_capture
from wfrl_blender.panels.custom_cameras import EditSession

assert bpy.app.background
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.register()
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
root = bpy.data.objects.new(core.ROOT_NAME, None)
scene.collection.objects.link(root)
bpy.ops.mesh.primitive_cube_add(size=2)
shell = bpy.context.object
shell.name, shell.parent = core.SURFACE_NAMES[0], root
bpy.context.view_layer.update()
for slot, location in ((1, (0, 0, 1.1)), (2, (0, -1.1, 0)), (3, (1.1, 0, 0))):
    draft = core.begin_draft(scene, slot)
    core.apply_parameters(draft, core.CameraParameters(location), core.validate_position(scene, location)[0])
    core.commit_draft(scene, slot, draft)
installed = [core.get_camera(scene, slot).as_pointer() for slot in core.SLOTS]
checks = {}
for failure in ('object', 'data', 'externally_removed_object', 'externally_removed_data', 'externally_removed_both'):
    session = EditSession(scene, 1)
    draft = session.begin()
    data = draft.data
    if failure.startswith('externally_removed'):
        if failure in ('externally_removed_object', 'externally_removed_both'):
            bpy.data.objects.remove(draft, do_unlink=True)
        if failure in ('externally_removed_data', 'externally_removed_both'):
            bpy.data.cameras.remove(data, do_unlink=True)
    else:
        def fail_remove(camera):
            if failure == 'data':
                bpy.data.objects.remove(camera, do_unlink=True)
            raise RuntimeError('Injected draft ' + failure + ' release failure')
        with patch.object(core, '_remove', side_effect=fail_remove):
            try:
                session.cancel()
            except RuntimeError as exc:
                assert '请重试取消' in str(exc)
            else:
                raise AssertionError('Cancellation silently lost its failed resource')
        assert not session.closed and session.draft is not None and session.error
        assert session.draft_data is data
    session.cancel()
    assert session.closed and session.draft is None and session.draft_data is None and not session.error
    assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)
    assert [core.get_camera(scene, slot).as_pointer() for slot in core.SLOTS] == installed
    capture = custom_camera_capture.preflight(bpy.context)
    assert not capture['errors'], capture['errors']
    retry = EditSession(scene, 1)
    retry.begin()
    retry.cancel()
    checks[failure] = True

counts = lambda: (len(bpy.data.objects), len(bpy.data.cameras))
before = counts()
for _ in range(2):
    try:
        cameras.ensure_nacelle_gimbal(scene, 'T1')
    except ValueError as exc:
        assert 'Rotor datum' in str(exc)
    else:
        raise AssertionError('Missing installation datum accepted')
    assert counts() == before
rotor = bpy.data.objects.new('WFRL.Turbine.T1.Rotor', None)
scene.collection.objects.link(rotor)
rotor.parent = root
before = counts()
with patch.object(cameras, 'reset_nacelle_gimbal', side_effect=RuntimeError('Injected mount failure')):
    try:
        cameras.ensure_nacelle_gimbal(scene, 'T1')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Initialization failure was swallowed')
assert counts() == before
name = 'WFRL.Camera.T1.NacelleGimbal'
partial = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
scene.collection.objects.link(partial)
partial.parent = root
partial['wfrl_nacelle_camera'] = True
camera = cameras.ensure_nacelle_gimbal(scene, 'T1')
assert camera == partial and camera['wfrl_nacelle_initialized']
assert camera['wfrl_nacelle_intrinsics']
cameras.aim_gimbal(camera, 41, -23, 61)
pose = tuple(camera.rotation_euler), camera.data.lens, camera['gimbal_yaw'], camera['gimbal_pitch']
assert cameras.ensure_nacelle_gimbal(scene, 'T1') == camera
assert pose == (tuple(camera.rotation_euler), camera.data.lens, camera['gimbal_yaw'], camera['gimbal_pitch'])
checks.update(nacelle_preflight=True, nacelle_allocation_rollback=True,
              incomplete_camera_repaired=True, saved_ptz_preserved=True)
other = bpy.data.scenes.new('Nacelle mount scene B')
collection_a = bpy.data.collections.new('Nacelle mount A')
collection_b = bpy.data.collections.new('Nacelle mount B')
scene.collection.children.link(collection_a)
other.collection.children.link(collection_b)
def shared_mount(turbine, collections):
    root = bpy.data.objects.new(f'WFRL.Turbine.{turbine}.YawRoot', None)
    shell = bpy.data.objects.new(f'WFRL.Turbine.{turbine}.Nacelle', None)
    rotor = bpy.data.objects.new(f'WFRL.Turbine.{turbine}.Rotor', None)
    shell.parent = rotor.parent = root
    for collection in collections:
        for obj in (root, shell, rotor):
            collection.objects.link(obj)
    return root
shared_mount('T2', (collection_a, collection_b))
camera_b = cameras.ensure_nacelle_gimbal(other, 'T2')
assert other.objects.get(camera_b.name) == camera_b and scene.objects.get(camera_b.name) is None
cameras.aim_gimbal(camera_b, 37, -18, 62)
pose_b = tuple(camera_b.rotation_euler), camera_b.data.lens
before = counts()
for _ in range(2):
    assert cameras.ensure_nacelle_gimbal(other, 'T2') == camera_b
    assert pose_b == (tuple(camera_b.rotation_euler), camera_b.data.lens)
    try:
        cameras.ensure_nacelle_gimbal(scene, 'T2')
    except ValueError as exc:
        assert 'outside this scene' in str(exc)
    else:
        raise AssertionError('Foreign named camera allowed duplicate allocation')
    assert counts() == before
shared_collection = bpy.data.collections.new('Shared nacelle collection')
scene.collection.children.link(shared_collection)
other.collection.children.link(shared_collection)
shared_mount('T3', (shared_collection,))
before = counts()
for _ in range(2):
    try:
        cameras.ensure_nacelle_gimbal(other, 'T3')
    except ValueError as exc:
        assert 'collection owned by this scene' in str(exc)
    else:
        raise AssertionError('Shared mount collection accepted for camera allocation')
    assert counts() == before
checks.update(requested_scene_collection=True, foreign_name_preflight=True,
              shared_collection_rejected=True, repeated_selection_no_duplicates=True)
addon.unregister()
result = dict(status='PASS', final=True, checks=checks, blender=bpy.app.version_string,
              addon_file=addon.__file__, scope='Native background IDs and injected release failures')
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('CAMERA_FAILURE_RECOVERY_PASS', json.dumps(result), flush=True)

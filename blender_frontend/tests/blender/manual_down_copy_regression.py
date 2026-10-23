"""Independent manual Down cameras in full scene copies and saved files."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
assert bpy.app.background, 'Use an independent --background --factory-startup process'
assert len(bpy.data.scenes) == 1 and bpy.context.scene.objects.get('Cube') is not None
assert not hasattr(bpy.types.Scene, 'wfrl_manual_turbines'), 'Requires an unregistered factory startup'
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
control = importlib.import_module('wfrl_blender.manual_control')
cameras = importlib.import_module('wfrl_blender.cameras')
ADDON_ROOT = Path(addon.__file__).resolve().parent
assert ADDON_ROOT == (ROOT / 'blender_frontend/wfrl_blender').resolve()
assert ADDON_ROOT == Path(os.environ.get('WFRL_EXPECTED_ADDON_ROOT', ADDON_ROOT)).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
RESULT = dict(status='RUNNING', addon_module=addon.__name__, addon_file=addon.__file__,
              blender=bpy.app.version_string, checks={},
              source_sha256={name: hashlib.sha256((ADDON_ROOT / name).read_bytes()).hexdigest()
                             for name in ('manual_scene.py', 'cameras.py', 'scene_builder.py')})


def write_result():
    (OUT / 'checks.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')


def source_snapshot(scene):
    objects = {}
    for obj in scene.objects:
        item = dict(type=obj.type, location=tuple(obj.location), rotation=tuple(obj.rotation_euler),
                    scale=tuple(obj.scale), parent=obj.parent.name if obj.parent else None,
                    data=obj.data.name if obj.data else None,
                    hidden=(obj.hide_viewport, obj.hide_render))
        if obj.type == 'CAMERA':
            item['optics'] = {name: getattr(obj.data, name) for name in (
                'lens', 'sensor_width', 'sensor_height', 'sensor_fit', 'shift_x', 'shift_y',
                'clip_start', 'clip_end', 'show_passepartout', 'passepartout_alpha')}
            item['owner'] = obj.get('wfrl_manual_owner')
            item['data_owner'] = obj.data.get('wfrl_manual_owner')
            item['down'] = obj.get('wfrl_down_camera')
        objects[obj.name] = item
    return dict(objects=objects, camera=scene.camera.name if scene.camera else None)


def t1(scene):
    return next(entry for entry in scene.wfrl_manual_turbines if entry.turbine_id == 'T1')


def down_cameras(scene):
    return [obj for obj in scene.objects if cameras.is_down_camera(obj)]


def assert_owned(scene, camera, source):
    assert control.validate_scene(scene)[0], control.validate_scene(scene)
    assert camera.type == 'CAMERA' and cameras.is_down_camera(camera)
    assert camera.parent == t1(scene).yaw_object and camera.parent != t1(source).yaw_object
    assert camera.get('wfrl_manual_owner') == scene['wfrl_manual_owner']
    assert camera.data.get('wfrl_manual_owner') == scene['wfrl_manual_owner']
    assert set(camera.users_scene) == {scene} and camera.data.users == 1
    assert camera not in set(source.objects)
    assert all(obj.data != camera.data for obj in source.objects if obj.type == 'CAMERA')
    assert down_cameras(scene) == [camera], 'Down created duplicate camera objects'


def assert_reused(scene, camera, source):
    object_count, lens_count = len(bpy.data.objects), len(bpy.data.cameras)
    for _ in range(3):
        assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
        assert scene.camera == camera, 'Repeated Down replaced the copied camera'
        assert_owned(scene, camera, source)
    assert (len(bpy.data.objects), len(bpy.data.cameras)) == (object_count, lens_count)


def copy_case(source, *, existing_down):
    bpy.context.window.scene = source
    assert control.validate_scene(source)[0], control.validate_scene(source)
    if existing_down:
        assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
    assert bool(down_cameras(source)) == existing_down
    source_name = source.name
    source_before = source_snapshot(source)
    source_camera = source.camera
    source_lens = source_camera.data if source_camera else None
    source_objects = set(source.objects)
    assert bpy.ops.scene.new(type='FULL_COPY') == {'FINISHED'}
    scene = bpy.context.scene
    assert scene != source
    assert control.validate_scene(scene)[0], control.validate_scene(scene)
    assert set(scene.objects).isdisjoint(source_objects)
    assert t1(scene).yaw_object != t1(source).yaw_object
    shell = next(obj for obj in t1(scene).yaw_object.children
                 if re.search(r'\.Nacelle(?:\.\d+)*$', obj.name))
    assert re.search(r'\.Nacelle\.\d+$', shell.name), shell.name
    assert shell.get('wfrl_nacelle_shell'), 'New turbine shell is not identified'
    case = 'full_copy_with_down' if existing_down else 'full_copy_without_down'
    report = RESULT['checks'][case] = dict(scene=scene.name, source=source_name)
    if not existing_down:
        del shell['wfrl_nacelle_shell']
        report['legacy_suffixed_shell'] = shell.name
    prior_cameras = down_cameras(scene)
    assert len(prior_cameras) == int(existing_down)
    lens_count = len(bpy.data.cameras)
    assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
    camera = scene.camera
    assert_owned(scene, camera, source)
    assert len(bpy.data.cameras) == lens_count + int(not existing_down)
    if existing_down:
        assert camera == prior_cameras[0], 'Down did not reuse the FULL_COPY camera'
    assert_reused(scene, camera, source)
    assert source_snapshot(source) == source_before, 'Copied Down modified source objects or optics'
    if source_camera:
        assert source.camera == source_camera and source.camera.data == source_lens
    shell['wfrl_nacelle_shell'] = True
    shell.name = case + ' custom nacelle'
    camera.name = case + ' custom Down'
    assert_reused(scene, camera, source)
    report['renamed_marked_objects_reused'] = True
    scene_name, camera_name, data_name = scene.name, camera.name, camera.data.name
    path = OUT / (case + '.blend')
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene, source = bpy.data.scenes[scene_name], bpy.data.scenes[source_name]
    assert bpy.context.scene == scene
    camera = scene.objects[camera_name]
    assert camera.data.name == data_name
    assert_owned(scene, camera, source)
    assert_reused(scene, camera, source)
    assert source_snapshot(source) == source_before, 'Saving copied Down changed the source'
    report.update(status='PASS', camera=camera.name, camera_data=camera.data.name,
                  independent_camera_and_lens=True, repeated_calls_reuse_camera=True,
                  source_objects_and_optics_preserved=True, saved_and_reopened=True,
                  blend_path=str(path))
    write_result()
    return source


def run():
    addon.register()
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    source = bpy.context.scene
    assert not down_cameras(source)
    source = copy_case(source, existing_down=False)
    copy_case(source, existing_down=True)
    addon.unregister()
    RESULT['status'] = 'PASS'
    write_result()
    print('MANUAL_DOWN_COPY_PASS ' + json.dumps(RESULT), flush=True)


try:
    run()
except Exception as exc:
    RESULT['status'] = 'FAIL'
    RESULT['error'] = str(exc)
    RESULT['traceback'] = traceback.format_exc()
    write_result()
    raise

"""Verify camera-publication release retries using real Blender datablocks."""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy


assert bpy.app.background, 'Run this check in an independent background process.'
ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path.insert(0, str(ROOT / 'blender_frontend'))
    expected = ROOT / 'blender_frontend' / 'wfrl_blender'
else:
    expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT'])
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE + '.custom_cameras')
assert Path(addon.__file__).resolve().parent == expected.resolve()
assert Path(core.__file__).resolve().parent == expected.resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
assert not (OUT / 'retirement-validation.json').exists(), 'Choose a fresh output directory.'
RESULT = {'status': 'RUNNING', 'module': MODULE, 'addon_file': addon.__file__,
          'custom_cameras_file': core.__file__,
          'custom_cameras_sha256': hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
          'blender': bpy.app.version_string,
          'method': 'Background Blender datablocks with explicit release-failure injection.',
          'natural_release_failure_observed': False, 'checks': {}}
original_remove = core._remove


def present(resources, pointer):
    return any(resource.as_pointer() == pointer for resource in resources)


def assert_published(expected_camera):
    assert scene.camera == expected_camera
    assert all(core.get_camera(scene, slot) is not None for slot in core.SLOTS)
    assert len({core.get_camera(scene, slot).as_pointer() for slot in core.SLOTS}) == 3


def replacement():
    layout = deepcopy(core.layout_dict(scene))
    layout['cameras'][0]['parameters']['fov'] += 1
    return layout


def publish_with_failure(after_object_removal):
    old = core.get_camera(scene, 1)
    data = old.data
    object_pointer, data_pointer = old.as_pointer(), data.as_pointer()
    reached = []

    def fail_once(camera):
        if camera.as_pointer() == object_pointer and not reached:
            reached.append(True)
            if after_object_removal:
                bpy.data.objects.remove(camera, do_unlink=True)
            raise RuntimeError('Injected camera retirement release failure')
        return original_remove(camera)

    core._remove = fail_once
    try:
        core.import_layout(scene, replacement(), overwrite=True)
    finally:
        core._remove = original_remove
    assert reached and len(core._RETIRED) == 1 and len(core._RETIRED_DATA) == 1
    current = core.get_camera(scene, 1)
    assert current.as_pointer() != object_pointer
    assert_published(current)
    return old, data, object_pointer, data_pointer, current


def assert_released(object_pointer, data_pointer, current):
    assert not present(bpy.data.objects, object_pointer)
    assert not present(bpy.data.cameras, data_pointer)
    assert not core._RETIRED and not core._RETIRED_DATA
    core.cleanup_retired()
    assert not core._RETIRED and not core._RETIRED_DATA
    assert_published(current)


try:
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
    for slot, location, research in ((1, (0, 0, 1.1), False),
                                      (2, (0, -1.1, 0), False),
                                      (3, (7, 0, 0), True)):
        camera = core.begin_draft(scene, slot)
        params = core.CameraParameters(location, 123, -45, 37, 90, 60, 72, 800, .006, 2000)
        if research:
            core.apply_research(scene, camera, params, True)
        else:
            core.apply_parameters(camera, params, core.validate_position(scene, location)[0])
        core.commit_draft(scene, slot, camera)
    scene.camera = core.get_camera(scene, 1)
    marker = scene.timeline_markers.new('C1 observation', frame=10)
    marker.camera = scene.camera
    for checkpoint in ('references', 'detach', 'commit'):
        old = scene.camera
        def fail(stage, slot=None):
            if stage == checkpoint:
                raise RuntimeError('Injected publication rollback')
        original_checkpoint = core._publication_checkpoint
        core._publication_checkpoint = fail
        try:
            core.import_layout(scene, replacement(), overwrite=True)
        except RuntimeError as exc:
            assert 'Injected publication rollback' in str(exc)
        else:
            raise AssertionError('Publication did not reach the injected checkpoint')
        finally:
            core._publication_checkpoint = original_checkpoint
        assert scene.camera == old and marker.camera == old
    RESULT['checks']['marker_rollback'] = True

    old, data, object_pointer, data_pointer, current = publish_with_failure(True)
    assert marker.camera == current
    RESULT['checks']['marker_replacement'] = True
    assert not present(bpy.data.objects, object_pointer) and data.users == 0
    core.cleanup_retired()
    assert_released(object_pointer, data_pointer, current)
    RESULT['checks']['partial_release_retries_camera_datablock'] = True

    old, data, object_pointer, data_pointer, current = publish_with_failure(True)
    shared = bpy.data.objects.new('SharedCameraDataUser', data)
    scene.collection.objects.link(shared)
    core.cleanup_retired()
    core.cleanup_retired()
    assert shared.data == data and data.users == 1 and len(core._RETIRED_DATA) == 1
    bpy.data.objects.remove(shared, do_unlink=True)
    core.cleanup_retired()
    assert_released(object_pointer, data_pointer, current)
    RESULT['checks']['pending_shared_data_waits_for_last_user'] = True

    for missing in ('object', 'data', 'both'):
        old, data, object_pointer, data_pointer, current = publish_with_failure(False)
        if missing in {'object', 'both'}:
            bpy.data.objects.remove(old, do_unlink=True)
        if missing in {'data', 'both'}:
            bpy.data.cameras.remove(data, do_unlink=True)
        core.cleanup_retired()
        assert_released(object_pointer, data_pointer, current)
        RESULT['checks']['externally_removed_' + missing] = True

    data = core.get_camera(scene, 1).data
    data_pointer = data.as_pointer()
    shared = bpy.data.objects.new('PreservedSharedCameraData', data)
    scene.collection.objects.link(shared)
    core.import_layout(scene, replacement(), overwrite=True)
    core.cleanup_retired()
    assert data.users == 1 and shared.data == data
    assert not core._RETIRED and not core._RETIRED_DATA
    bpy.data.objects.remove(shared, do_unlink=True)
    core.cleanup_retired()
    assert present(bpy.data.cameras, data_pointer) and data.users == 0
    RESULT['checks']['normal_removal_preserves_shared_data_ownership'] = True
    core._publish(scene, {1: None})
    assert marker.camera is None and scene.camera is None
    RESULT['checks']['marker_clear'] = True
    RESULT['status'] = 'PASS'
except Exception:
    RESULT['status'] = 'ERROR'
    RESULT['error'] = traceback.format_exc()
    raise
finally:
    core._remove = original_remove
    (OUT / 'retirement-validation.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n')
    print('CUSTOM_CAMERA_RETIREMENT ' + json.dumps(RESULT, ensure_ascii=False), flush=True)

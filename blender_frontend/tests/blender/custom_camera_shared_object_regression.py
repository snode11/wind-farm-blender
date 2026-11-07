"""Camera publication refuses objects used by another scene without side effects.

Uses real camera objects, camera data, collections, scene and local-view bindings.
Edit, layout restore and clear use their public entry points without GPU drawing.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy


assert bpy.app.background, 'Run in an independent background factory-startup process.'
ROOT = Path(__file__).resolve().parents[3]
assert os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender') == 'wfrl_blender'
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
core = importlib.import_module('wfrl_blender.custom_cameras')
panel = importlib.import_module('wfrl_blender.panels.custom_cameras')
history = importlib.import_module('wfrl_blender.custom_camera_history')
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory.'
OUT.mkdir(parents=True)
REPORT = {'status': 'RUNNING', 'blender': bpy.app.version_string,
          'addon_file': addon.__file__, 'background': bpy.app.background,
          'method': 'Real Blender scene/collection and local-viewport references, including shared Camera data.',
          'source_sha256': hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
          'checks': {}, 'failures': {}}


def reference(obj):
    if obj is None:
        return None
    try:
        return obj.as_pointer()
    except ReferenceError:
        return 'REMOVED'


def snapshot(scene, other, space):
    return {
        'scene_camera': reference(scene.camera), 'other_camera': reference(other.camera),
        'viewport_camera': reference(space.camera), 'local_camera': space.use_local_camera,
        'perspective': space.region_3d.view_perspective, 'lens': space.lens,
        'layout': core.layout_dict(scene),
        'objects': {obj.as_pointer(): (obj.name, obj.data.name if obj.data else None,
                    tuple(sorted(col.as_pointer() for col in obj.users_collection)),
                    repr(sorted(obj.items()))) for obj in bpy.data.objects},
        'camera_data': {data.as_pointer(): (data.name, data.users, data.lens)
                        for data in bpy.data.cameras},
        'scene_objects': tuple(sorted(obj.as_pointer() for obj in scene.objects)),
        'other_objects': tuple(sorted(obj.as_pointer() for obj in other.objects)),
        'timeline_markers': tuple((owner.as_pointer(), marker.name, marker.frame, reference(marker.camera))
                                  for owner in bpy.data.scenes for marker in owner.timeline_markers),
        'local_camera_views': tuple((screen.as_pointer(), area.as_pointer(),
                                    area.spaces.active.use_local_camera,
                                    reference(area.spaces.active.camera))
                                   for screen in bpy.data.screens for area in screen.areas
                                   if area.type == 'VIEW_3D'),
        'history': (len(history.HISTORY.entries), history.HISTORY.identity, history.HISTORY.notice),
    }


def fixture():
    scene = bpy.context.scene
    for other in list(bpy.data.scenes):
        if other != scene:
            bpy.data.scenes.remove(other)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    core.cleanup_retired()
    history.HISTORY.clear()
    root = bpy.data.objects.new(core.ROOT_NAME, None)
    scene.collection.objects.link(root)
    bpy.ops.mesh.primitive_cube_add(size=2)
    shell = bpy.context.object
    shell.name, shell.parent = core.SURFACE_NAMES[0], root
    bpy.context.view_layer.update()
    for slot, location in ((1, (0, 0, 1.1)), (2, (0, -1.1, 0))):
        camera = core.begin_draft(scene, slot)
        core.apply_parameters(camera, core.CameraParameters(location), core.validate_position(scene, location)[0])
        core.commit_draft(scene, slot, camera)
    return scene


def run_case(operation, topology):
    scene = fixture()
    old = core.get_camera(scene, 2)
    shared_data = old.data
    other = bpy.data.scenes.new('ExternalCameraOwner')
    if topology == 'separate_collections':
        other.collection.objects.link(old)
    elif topology == 'shared_collection':
        collection = bpy.data.collections.new('SharedCameraCollection')
        scene.collection.children.link(collection)
        other.collection.children.link(collection)
        collection.objects.link(old)
        for previous in tuple(old.users_collection):
            if previous != collection:
                previous.objects.unlink(old)
    elif topology == 'inactive_viewport':
        window = bpy.context.window_manager.windows[0]
        screen, area = next((screen, area) for screen in bpy.data.screens
                            if screen != window.screen for area in screen.areas
                            if area.type == 'VIEW_3D')
        area.spaces.active.use_local_camera, area.spaces.active.camera = True, old
    elif topology == 'marker_reference':
        marker = other.timeline_markers.new('ExternalCameraMarker', frame=8)
        marker.camera = old
        other.camera = None
        assert marker.camera == old and other.user_of_id(old) > 0
        assert other.camera is None and other.objects.get(old.name) is None
    elif topology != 'camera_reference':
        raise AssertionError(topology)
    if topology not in {'inactive_viewport', 'marker_reference'}:
        other.camera = old
    data_user = bpy.data.objects.new('ExternalCameraDataUser', shared_data)
    other.collection.objects.link(data_user)
    scene.camera = old
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    space = area.spaces.active
    space.use_local_camera, space.camera = True, old
    space.region_3d.view_perspective, space.lens = 'CAMERA', 43.
    payload = deepcopy(core.layout_dict(scene))
    payload['cameras'][0]['parameters']['fov'] += 2
    draft = None
    if operation == 'edit':
        draft = core.begin_draft(scene, 2)
        core.apply_parameters(draft, replace(core.parameters(draft), fov=80), core.anchor(draft))
        # commit_draft validates and annotates only this private draft.
        draft['custom_model_signature'] = core.model_signature(scene)
    before = snapshot(scene, other, space)
    if draft is not None:
        before['objects'].pop(draft.as_pointer())
    reached = []
    original_checkpoint = core._publication_checkpoint
    core._publication_checkpoint = lambda *args: reached.append(args)
    error = None
    try:
        if operation == 'edit':
            core.commit_draft(scene, 2, draft)
        elif operation == 'restore':
            core.restore_layout(scene, payload)
        else:
            core.clear_slot(scene, 2)
    except ValueError as exc:
        error = str(exc)
    finally:
        core._publication_checkpoint = original_checkpoint
    name = operation + '_' + topology
    after = snapshot(scene, other, space)
    if draft is not None:
        after['objects'].pop(reference(draft), None)
    # Restore may stage objects before publication; no publication checkpoint
    # may retire an original, rebind a view or detach a collection.
    mutated_publication = [args for args in reached if args[0] != 'stage']
    owner_name = screen.name if topology == 'inactive_viewport' else 'ExternalCameraOwner'
    valid = (error is not None and owner_name in error and
             before == after and not mutated_publication and data_user.data == shared_data)
    REPORT['checks'][name] = valid
    if not valid:
        REPORT['failures'][name] = {
            'error': error, 'state_equal': before == after,
            'changed_fields': [key for key in before if before[key] != after[key]],
            'publication_checkpoints': mutated_publication,
            'other_camera': reference(other.camera), 'original': reference(old),
            'shared_data_preserved': data_user.data == shared_data,
        }
    if draft is not None:
        core.cancel_draft(draft)


def verify_shared_data(operation):
    scene = fixture()
    old = core.get_camera(scene, 2)
    data = old.data
    other = bpy.data.scenes.new('IndependentCameraDataOwner')
    external = bpy.data.objects.new('IndependentExternalCamera', data)
    other.collection.objects.link(external)
    other.camera = external
    original_pose = tuple(value for row in external.matrix_world for value in row)
    original_data_name = data.name
    original_optics = (data.lens, data.clip_start, data.clip_end, data.sensor_width)
    payload = deepcopy(core.layout_dict(scene))
    payload['cameras'][0]['parameters']['fov'] += 2
    if operation == 'edit':
        draft = core.begin_draft(scene, 2)
        core.apply_parameters(draft, replace(core.parameters(draft), fov=80), core.anchor(draft))
        core.commit_draft(scene, 2, draft)
    elif operation == 'restore':
        core.restore_layout(scene, payload)
    else:
        core.clear_slot(scene, 2)
    core.cleanup_retired()
    check = (other.camera == external and external.data == data and data.users == 1
             and data.name == original_data_name
             and other.objects.get(external.name) == external
             and tuple(value for row in external.matrix_world for value in row) == original_pose
             and (data.lens, data.clip_start, data.clip_end, data.sensor_width) == original_optics)
    REPORT['checks'][operation + '_independent_object_shared_data_supported'] = check
    if not check:
        REPORT['failures'][operation + '_independent_object_shared_data_supported'] = {
            'external_camera': reference(other.camera), 'external_object': reference(external),
            'camera_data_users': data.users, 'data_name_before': original_data_name,
            'data_name_after': data.name,
        }


def verify_foreign_window_viewport():
    scene = fixture()
    old = core.get_camera(scene, 2)
    other = bpy.data.scenes.new('ForeignViewportScene')
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    space = area.spaces.active
    scene.camera = old
    window.scene = other
    space.use_local_camera, space.camera = True, old
    before = snapshot(scene, other, space)
    reached = []
    original_checkpoint = core._publication_checkpoint
    core._publication_checkpoint = lambda *args: reached.append(args)
    error = None
    try:
        core._publish(scene, {2: None})
    except ValueError as exc:
        error = str(exc)
    finally:
        core._publication_checkpoint = original_checkpoint
        after = snapshot(scene, other, space)
        window.scene = scene
    valid = (error is not None and window.screen.name in error and not reached
             and before == after and other.camera is None
             and other.objects.get(old.name) is None)
    REPORT['checks']['foreign_window_local_camera_only'] = valid
    if not valid:
        REPORT['failures']['foreign_window_local_camera_only'] = {
            'error': error, 'state_equal': before == after, 'publication_checkpoints': reached,
        }


try:
    addon.register()
    for topology in ('separate_collections', 'shared_collection', 'camera_reference',
                     'inactive_viewport', 'marker_reference'):
        for operation in ('edit', 'restore', 'clear'):
            run_case(operation, topology)
    for operation in ('edit', 'restore', 'clear'):
        verify_shared_data(operation)
    verify_foreign_window_viewport()
    REPORT['status'] = 'FAIL' if REPORT['failures'] else 'PASS'
except Exception:
    REPORT['status'] = 'ERROR'
    REPORT['error'] = traceback.format_exc()
    raise
finally:
    addon.unregister()
    (OUT / 'checks.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + '\n')
    print('CUSTOM_CAMERA_SHARED_OBJECT ' + json.dumps(REPORT, ensure_ascii=False), flush=True)
assert REPORT['status'] == 'PASS', REPORT.get('failures', REPORT.get('error'))
print('CUSTOM_CAMERA_SHARED_OBJECT_REGRESSION_PASS', flush=True)

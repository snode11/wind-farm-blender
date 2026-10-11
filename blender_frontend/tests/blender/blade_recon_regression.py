"""Native Blade Recon replay regression; no rendering or frontend FPS claim.

Run with Blender --background --factory-startup --python-exit-code 1 --python
this file. WFRL_ADDON_MODULE selects an installed extension namespace; only the
bare-source default adds blender_frontend to sys.path. WFRL_TEST_OUTPUT is a
directory for JSON evidence, a portable .blend and malformed input fixtures.
"""
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix
import numpy as np


ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path.insert(0, str(ROOT / 'blender_frontend'))
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-blade-recon-regression')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
addon = importlib.import_module(MODULE)
review = importlib.import_module(MODULE + '.blade_recon_review')
reader = importlib.import_module(MODULE + '.blade_recon_data')
panel = importlib.import_module(MODULE + '.panels.blade_recon')

# Independently execute the delivered forward model, rather than making the
# adapter its own geometry oracle. This does not add an algorithm directory to
# sys.path and therefore cannot shadow an installed extension's dependencies.
original_path = Path(os.environ.get('WFRL_BLADE_RECON_MODEL', str(ROOT.parent / 'blade_recon' / 'model.py')))
spec = importlib.util.spec_from_file_location('wfrl_blade_recon_original_test_model', original_path)
original_model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = original_model
spec.loader.exec_module(original_model)

evidence = {
    'status': 'RUNNING', 'blender': bpy.app.version_string,
    'runtime': MODULE, 'addon_file': str(Path(addon.__file__).resolve()),
    'review_file': str(Path(review.__file__).resolve()),
    'original_model': str(original_path.resolve()),
    'scope': 'Blender native background objects, replay and file lifecycle; no rendering or visible FPS measurement',
    'checks': {}, 'geometry_frames': [],
}
checks = evidence['checks']
sentinel_calls = []


@persistent
def unrelated_frame_handler(scene, *_):
    sentinel_calls.append((scene.name, scene.frame_current))


def vertices(obj):
    result = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', result)
    return result.reshape(-1, 3)


def colors(obj):
    attribute = obj.data.color_attributes['evidence']
    assert attribute.domain == 'POINT' and attribute.data_type == 'FLOAT_COLOR'
    result = np.empty(len(attribute.data) * 4, dtype=np.float32)
    attribute.data.foreach_get('color', result)
    return result.reshape(-1, 4)


def check_geometry(scene, scene_frame, *, record=True):
    scene.frame_set(scene_frame)
    # Background scripts do not service Blender's UI timer loop. Run exactly
    # the callback the registered timer would commit on the main thread.
    review._tick()
    bpy.context.view_layer.update()
    value = review.session(scene)
    assert value is not None and not value['pending']
    seq, index = value['sequence'], scene_frame - 1
    assert value['index'] == index
    rotor = original_model.Rotor(original_model.TurbineConfig.from_dict(seq.data['turbine']))
    expected, _ = rotor.forward(seq.frames[index]['state'])
    row = {'scene_frame': scene_frame, 'source_frame': int(seq.source_frames[index]), 'blades': []}
    for b, obj in enumerate(value['blades']):
        assert len(obj.data.vertices) == seq.rotor.cfg.n_sections * seq.rotor.cfg.n_ring
        assert len(obj.data.polygons) == len(rotor.tpl.faces())
        error = float(np.max(np.abs(vertices(obj) - expected[b].reshape(-1, 3))))
        assert error < 1e-4, (scene_frame, b, error)
        expected_color = np.where(np.repeat(seq.observed[index, b], seq.rotor.cfg.n_ring)[:, None], review.GREEN, review.ORANGE)
        color_error = float(np.max(np.abs(colors(obj) - expected_color)))
        assert color_error < 1e-6, (scene_frame, b, color_error)
        assert obj['blade_recon_applied_frame'] == scene_frame
        row['blades'].append({'blade': b + 1, 'vertices': len(obj.data.vertices),
                              'faces': len(obj.data.polygons), 'max_vertex_error_m': error,
                              'max_color_error': color_error,
                              'green_vertices': int(seq.observed[index, b].sum()) * seq.rotor.cfg.n_ring})
    if record:
        evidence['geometry_frames'].append(row)
    return value


def check_preserved(scene_name, sentinel_name, original_snapshot):
    scene = bpy.data.scenes[scene_name]
    obj = bpy.data.objects[sentinel_name]
    assert obj.name in scene.objects
    assert list(obj.location) == original_snapshot['sentinel_location']
    assert obj['regression_marker'] == 'preserve original scene'
    assert sorted(o.name for o in scene.objects) == original_snapshot['objects']
    assert [scene.frame_start, scene.frame_end, scene.frame_current] == original_snapshot['timeline']
    assert [scene.render.fps, scene.render.fps_base] == original_snapshot['fps']
    assert unrelated_frame_handler in bpy.app.handlers.frame_change_post


def check_truth(scene, expected_by_source):
    scene.wfrl_recon_show_truth = True
    review._tick()
    value = review.session(scene)
    seq, index = value['sequence'], value['index']
    source = int(seq.source_frames[index])
    state = expected_by_source.get(source)
    rotor = original_model.Rotor(original_model.TurbineConfig.from_dict(seq.data['turbine']))
    expected = rotor.forward(state)[0] if state is not None else None
    assert len(value['ghosts']) == 3
    for b, obj in enumerate(value['ghosts']):
        assert obj.display_type == 'WIRE' and obj.hide_render
        assert obj.hide_get(view_layer=scene.view_layers[0]) == (expected is None)
        if expected is not None:
            assert np.max(np.abs(vertices(obj) - expected[b].reshape(-1, 3))) < 1e-4
    scene.wfrl_recon_show_truth = False
    review._tick()
    assert all(obj.hide_get(view_layer=scene.view_layers[0]) for obj in value['ghosts'])


try:
    delivered_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
    assert reader.MODEL_SHA256 == delivered_hash
    evidence['model_sha256'] = delivered_hash
    checks['bundled_model_matches_delivered_bytes'] = True

    original_scene = bpy.context.scene
    original_scene.name = 'BladeReconRegression.Original'
    original_scene.frame_start, original_scene.frame_end = 12, 98
    original_scene.frame_set(37)
    original_scene.render.fps, original_scene.render.fps_base = 24, 1.001
    sentinel = bpy.data.objects.new('BladeReconRegression.Sentinel', None)
    original_scene.collection.objects.link(sentinel)
    sentinel.location = (2.5, -4.0, 7.25)
    sentinel['regression_marker'] = 'preserve original scene'
    original_name, sentinel_name = original_scene.name, sentinel.name
    original_snapshot = {'objects': sorted(o.name for o in original_scene.objects),
                         'sentinel_location': list(sentinel.location),
                         'timeline': [12, 98, 37],
                         'fps': [original_scene.render.fps, original_scene.render.fps_base]}
    bpy.app.handlers.frame_change_post.append(unrelated_frame_handler)
    addon.register()
    assert panel.WFRL_PT_BladeRecon.is_registered
    assert panel.WFRL_PT_BladeRecon.bl_category == '叶片三维重建'
    assert panel.WFRL_PT_BladeRecon.bl_space_type == 'VIEW_3D'
    assert panel.WFRL_PT_BladeRecon.bl_region_type == 'UI'
    checks['registered_independent_chinese_sidebar_category'] = True

    assert bpy.ops.wfrl.blade_recon_load(sample=True) == {'FINISHED'}
    scene = bpy.context.scene
    sample_scene_name = scene.name
    assert scene.name != original_name and review.active(scene)
    assert scene['blade_recon_return_scene'] == original_name
    value = review.session(scene)
    seq = value['sequence']
    assert len(seq.frames) == 200 and seq.fps == 10
    assert scene.frame_start == 1 and scene.frame_end == 200
    assert abs(scene.render.fps / scene.render.fps_base - seq.fps) < 1e-9
    assert 'SYNTHETIC' in seq.source_label.upper()
    assert np.array_equal(seq.states, np.array([f['state'] for f in seq.data['frames']]))
    sample_data = copy.deepcopy(seq.data)
    truth_path = reader.default_recon_path().with_name('truth.json')
    truth_data = json.loads(truth_path.read_text(encoding='utf-8'))
    truth_by_source = {f['frame']: f['state'] for f in truth_data['frames']}
    for frame in (1, 51, 101, 200, 51):
        check_geometry(scene, frame)
        check_truth(scene, truth_by_source)
    checks['sample_200_frames_direct_model_geometry_colors_and_backward_seek'] = True
    checks['truth_toggle_and_source_frame_geometry'] = True

    assert bpy.ops.wfrl.blade_recon_step(delta=1) == {'FINISHED'}
    assert scene.frame_current == 52
    check_geometry(scene, 52)
    assert bpy.ops.wfrl.blade_recon_step(delta=-1) == {'FINISHED'}
    assert scene.frame_current == 51
    assert bpy.ops.wfrl.blade_recon_step(first=True) == {'FINISHED'}
    assert scene.frame_current == 1
    assert bpy.ops.wfrl.blade_recon_step(delta=-1) == {'FINISHED'} and scene.frame_current == 1
    scene.frame_set(200)
    review._tick()
    assert bpy.ops.wfrl.blade_recon_step(delta=1) == {'FINISHED'} and scene.frame_current == 200
    for view in ('FRONT', 'SIDE', 'OVERVIEW'):
        assert bpy.ops.wfrl.blade_recon_view(view=view) == {'FINISHED'}
        assert scene.camera['blade_recon_view'] == view and scene.frame_current == 200
        viewports = [area.spaces.active for area in bpy.context.screen.areas if area.type == 'VIEW_3D']
        assert viewports, 'No VIEW_3D space available to verify the review view'
        assert all(space.region_3d.view_perspective == 'PERSP' for space in viewports), view
    checks['native_step_boundary_and_view_operators'] = True
    checks['borderless_review_views'] = True

    camera_errors = []
    for obj, camera in zip(review.session(scene)['cameras'], seq.cameras):
        assert isinstance(obj.matrix_world, Matrix)
        T = np.eye(4)
        T[:3] = camera['T_cv_from_world']
        expected = np.linalg.inv(T) @ np.diag([1, -1, -1, 1])
        error = float(np.max(np.abs(np.array(obj.matrix_world) - expected)))
        assert error < 1e-4, error
        camera_errors.append({'name': camera['name'], 'max_world_matrix_error': error})
    assert len(camera_errors) == 3
    evidence['cameras'] = camera_errors
    checks['camera_matrix_preserves_extrinsic_translation_and_rotation'] = True
    check_preserved(original_name, sentinel_name, original_snapshot)
    checks['original_scene_sentinel_and_unrelated_handler_preserved'] = True

    assert bpy.ops.wfrl.blade_recon_return() == {'FINISHED'}
    assert bpy.context.scene.name == original_name
    scene_names, object_names = set(bpy.data.scenes.keys()), set(bpy.data.objects.keys())
    malformed_dir = OUT / 'malformed'
    malformed_dir.mkdir(exist_ok=True)
    malformed_errors = []
    bad_cases = [('empty_frames', {'frames': []}), ('short_state', None), ('bad_observations', None)]
    for name, payload in bad_cases:
        if payload is None:
            payload = copy.deepcopy(sample_data)
            if name == 'short_state':
                payload['frames'][0]['state'] = [0]
            else:
                payload['frames'][0]['observed_sections'][0][0] = '0'
        bad_path = malformed_dir / (name + '.json')
        bad_path.write_text(json.dumps(payload), encoding='utf-8')
        try:
            review.load(bpy.context, bad_path)
        except ValueError as exc:
            malformed_errors.append({'case': name, 'error': str(exc)})
        else:
            raise AssertionError('Malformed input accepted: ' + name)
        assert bpy.context.scene.name == original_name
        assert set(bpy.data.scenes.keys()) == scene_names and set(bpy.data.objects.keys()) == object_names
        check_preserved(original_name, sentinel_name, original_snapshot)
    evidence['malformed_import_errors'] = malformed_errors
    checks['malformed_import_transaction_preserves_original_scene_objects'] = True

    # A short sparse reconstruction supplies truth for only three source IDs;
    # this detects accidental list-index matching and fabricated truth rows.
    sparse_dir = OUT / 'sparse'
    sparse_dir.mkdir(exist_ok=True)
    sparse = copy.deepcopy(sample_data)
    sparse['frames'] = [sparse['frames'][i] for i in (0, 3, 7, 13, 19)]
    sparse_truth = {'frames': [f for f in truth_data['frames'] if f['frame'] in (0, 7, 19)]}
    (sparse_dir / 'recon.json').write_text(json.dumps(sparse), encoding='utf-8')
    (sparse_dir / 'truth.json').write_text(json.dumps(sparse_truth), encoding='utf-8')
    sparse_scene = review.load(bpy.context, sparse_dir / 'recon.json')
    for frame in (1, 2, 3, 4, 5):
        check_geometry(sparse_scene, frame)
        check_truth(sparse_scene, {f['frame']: f['state'] for f in sparse_truth['frames']})
    bpy.context.window.scene = bpy.data.scenes[original_name]
    review.remove(sparse_scene)
    checks['sparse_truth_matches_frame_ids_and_missing_truth_stays_hidden'] = True

    bpy.context.window.scene = bpy.data.scenes[sample_scene_name]
    scene = bpy.context.scene
    check_geometry(scene, 101)
    scene.wfrl_recon_show_truth = True
    review._tick()
    # Intentionally make external paths unavailable; recovery must use the
    # integrity-checked embedded JSON and bundled model, not source path I/O.
    scene['blade_recon_source_path'] = '/unavailable/blade_recon/recon.json'
    scene.wfrl_recon_truth_path = '/unavailable/blade_recon/truth.json'
    scene.wfrl_recon_cameras_path = '/unavailable/blade_recon/cameras.json'
    blend_path = OUT / 'blade_recon_portable.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    scene = bpy.data.scenes[sample_scene_name]
    assert review.session(scene) is None
    review._tick()
    assert review.session(scene) is not None and scene.get('blade_recon_error') == ''
    assert review.session(scene)['sequence'].source_label == seq.source_label
    for frame in (101, 200, 1):
        check_geometry(scene, frame)
        check_truth(scene, truth_by_source)
    check_preserved(original_name, sentinel_name, original_snapshot)
    checks['save_reopen_embedded_json_recovers_playback_without_external_paths'] = True

    for _ in range(2):
        addon.register()
        assert bpy.app.handlers.frame_change_post.count(review._frame) == 1
        assert bpy.app.handlers.load_post.count(review._loaded) == 1
        assert bpy.app.handlers.frame_change_post.count(unrelated_frame_handler) == 1
        assert bpy.app.timers.is_registered(review._tick)
        review._tick()
        check_geometry(scene, 51)
    checks['reregistration_deduplicates_own_handlers_and_restores_sessions'] = True
    addon.unregister()
    assert review._frame not in bpy.app.handlers.frame_change_post
    assert review._loaded not in bpy.app.handlers.load_post
    assert not bpy.app.timers.is_registered(review._tick)
    assert unrelated_frame_handler in bpy.app.handlers.frame_change_post
    assert not panel.WFRL_PT_BladeRecon.is_registered
    assert not hasattr(bpy.types.Scene, 'wfrl_recon_show_truth')
    check_preserved(original_name, sentinel_name, original_snapshot)
    assert sentinel_calls
    checks['unregister_cleans_own_handlers_timer_ui_preserves_unrelated_handler'] = True
    evidence['unrelated_handler_calls'] = len(sentinel_calls)
    evidence['saved_blend'] = str(blend_path)
    evidence['status'] = 'PASS'
except Exception:
    evidence['status'] = 'FAIL'
    evidence['error'] = traceback.format_exc()
    raise
finally:
    (OUT / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')

print('BLADE_RECON_REGRESSION_PASS', flush=True)

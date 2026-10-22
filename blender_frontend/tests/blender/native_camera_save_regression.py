"""Visible observation routing and save isolation using disposable scene copies.

WFRL_ADDON_MODULE selects source or an installed bl_ext module. Installed runs
require WFRL_EXPECTED_ADDON_ROOT. WFRL_TEST_OUTPUT must be a new directory.
"""
from pathlib import Path
import hashlib
import importlib
import json
import os
import sys
import tempfile
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
TEMP = tempfile.TemporaryDirectory(prefix='wfrl-camera-save-')
bpy.context.preferences.filepaths.temporary_directory = TEMP.name
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    expected = Path(os.environ.get('WFRL_EXPECTED_ADDON_ROOT', ROOT / 'blender_frontend/wfrl_blender'))
else:
    assert MODULE.startswith('bl_ext.')
    expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT'])
addon = importlib.import_module(MODULE)
native = importlib.import_module(MODULE + '.native_camera_views')
cameras = importlib.import_module(MODULE + '.cameras')
core = importlib.import_module(MODULE + '.custom_cameras')
assert Path(addon.__file__).resolve().parent == expected.resolve(), addon.__file__
RESULT = {'status': 'RUNNING', 'module': MODULE, 'addon_file': addon.__file__,
          'blender': bpy.app.version_string,
          'method': 'Independent visible process, scripted native operators and RNA assertions; no mouse input or FPS acceptance.',
          'source_sha256': {Path(m.__file__).name: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                            for m in (native, cameras)}, 'checks': {}}
STATE = {'step': 'setup', 'deadline': time.monotonic() + 180}


def check(name, condition):
    RESULT['checks'][name] = bool(condition)
    assert condition, name


def quality(scene):
    return (scene.render.preview_pixel_size, scene.eevee.taa_samples,
            scene.eevee.use_shadows, scene.eevee.use_fast_gi)


def proxies():
    return [o for o in bpy.data.objects if o.get(native._PROXY_OWNER)]


def camera_bindings():
    return all(e['area'].spaces.active.use_local_camera
               and e['area'].spaces.active.camera == e['proxy'] for e in native._ACTIVE.entries)


def finish(error=None):
    RESULT['status'] = 'FAIL' if error else 'PASS'
    RESULT['error'] = error
    (OUT / 'checks.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n')
    print('NATIVE_CAMERA_SAVE_' + RESULT['status'], flush=True)
    if error:
        print(error, flush=True)
    native.shutdown(preserve_playback=False)
    TEMP.cleanup()
    os._exit(1 if error else 0)


def tick():
    try:
        assert time.monotonic() < STATE['deadline'], 'timed out'
        step = STATE['step']
        if step == 'setup':
            if not native.WFRL_OT_NativeCamera.is_registered:
                addon.register()
            addon.load_demo_scene()
            scene, window = bpy.context.scene, bpy.context.window
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            area.spaces.active.shading.type = 'MATERIAL'
            other = bpy.data.scenes.new('CameraSave.Unrelated')
            ordinary = bpy.data.objects.new('T1.Native.UserCamera', bpy.data.cameras.new('CameraSave.UserData'))
            other.collection.objects.link(ordinary)
            STATE.update(scene=scene, window=window, area=area, original_quality=quality(scene),
                         other_name=other.name, other_quality=quality(other), ordinary_name=ordinary.name,
                         frame=scene.frame_current, filepath=str(Path(TEMP.name) / 'saved.blend'))
            with bpy.context.temp_override(window=window, area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE', layout='STRIP') == {'FINISHED'}
            STATE['step'] = 'routing'
            return .5
        if step == 'routing':
            session = native._ACTIVE
            if not session or session.preparing:
                return .25
            scene = STATE['scene']
            check('main_view_excludes_observation_areas',
                  not any(e['area'] in list(cameras.demo_view_areas(scene)) for e in session.entries))
            for turbine, angle in [('T1', 'DOWN'), ('all', 'DOWN'), ('T2', 'DOWN')]:
                with bpy.context.temp_override(window=STATE['window'], area=STATE['area']):
                    assert bpy.ops.wfrl.farm_flex_view(turbine=turbine, angle=angle) == {'FINISHED'}
                check('routing_' + turbine, camera_bindings())
            free = session.entries[0]['area'].spaces.active
            free.region_3d.view_perspective = 'PERSP'
            free.region_3d.view_location = (4, 5, 6)
            free.region_3d.view_distance = 77
            before = native._view_state(free)
            free.use_local_camera = False
            free.camera = scene.camera
            session.sync()
            check('sync_repairs_binding_without_resetting_free_view', camera_bindings() and native._view_state(free) == before)
            STATE['free_view'] = before
            STATE['step'] = 'save'
            return .5
        if step == 'save':
            session, scene = native._ACTIVE, STATE['scene']
            preview_quality = quality(scene)
            source_shading = STATE['area'].spaces.active.shading.type
            probe_calls = []
            def probe(_):
                probe_calls.append(True)
                check('save_serializes_original_quality', quality(scene) == STATE['original_quality'])
                check('save_serializes_original_main_shading', STATE['area'].spaces.active.shading.type == 'MATERIAL')
                check('save_excludes_proxy_objects_and_data', not proxies()
                      and not any(c.get(native._PROXY_OWNER) for c in bpy.data.cameras))
                check('save_binds_real_slot_cameras', all(e['area'].spaces.active.camera == core.get_camera(scene, e['slot'])
                                                        for e in session.entries))
            bpy.app.handlers.save_pre.append(probe)
            try:
                with bpy.context.temp_override(window=STATE['window'], area=STATE['area']):
                    assert bpy.ops.wm.save_as_mainfile(filepath=STATE['filepath']) == {'FINISHED'}
            finally:
                bpy.app.handlers.save_pre.remove(probe)
            check('save_pre_probe_executed', len(probe_calls) == 1)
            check('save_restores_live_session', native._ACTIVE is session and session.save_state is None
                  and quality(scene) == preview_quality and camera_bindings())
            check('save_restores_free_view', native._view_state(session.entries[0]['area'].spaces.active) == STATE['free_view'])
            check('save_restores_temporary_main_shading', STATE['area'].spaces.active.shading.type == source_shading)
            check('save_preserves_unrelated_scene', quality(bpy.data.scenes[STATE['other_name']]) == STATE['other_quality'])
            fail_path = Path(TEMP.name) / 'fail.blend'
            fail_calls = []
            def fail_save(_):
                fail_path.mkdir()
            def saw_failure(_):
                fail_calls.append(True)
            bpy.app.handlers.save_pre.append(fail_save)
            bpy.app.handlers.save_post_fail.append(saw_failure)
            try:
                try:
                    with bpy.context.temp_override(window=STATE['window'], area=STATE['area']):
                        result = bpy.ops.wm.save_as_mainfile(filepath=str(fail_path))
                        assert result == {'CANCELLED'}, result
                except RuntimeError:
                    pass
            finally:
                bpy.app.handlers.save_pre.remove(fail_save)
                bpy.app.handlers.save_post_fail.remove(saw_failure)
            check('native_save_failure_callback_executed', len(fail_calls) == 1)
            check('failed_save_restores_live_session', native._ACTIVE is session and session.save_state is None
                  and quality(scene) == preview_quality and camera_bindings())
            check('failed_save_restores_free_view', native._view_state(session.entries[0]['area'].spaces.active) == STATE['free_view'])
            check('save_does_not_seek_time', scene.frame_current == STATE['frame'])
            scene_uid = scene.session_uid
            with bpy.context.temp_override(window=STATE['window'], area=STATE['area']):
                bpy.ops.ed.undo_push(message='Observation camera view')
                scene['camera_save_undo_probe'] = 1
                bpy.ops.ed.undo_push(message='Scene property')
                assert bpy.ops.ed.undo() == {'FINISHED'}
            scene = next(s for s in bpy.data.scenes if s.session_uid == scene_uid)
            check('undo_closes_observation_without_proxy_residue', native._ACTIVE is None and not proxies())
            check('undo_restores_owning_scene_quality', quality(scene) == STATE['original_quality'])
            check('undo_preserves_unrelated_scene_quality', quality(bpy.data.scenes[STATE['other_name']]) == STATE['other_quality'])
            STATE['step'] = 'reopen'
            return .5
        if step == 'reopen':
            bpy.ops.wm.open_mainfile(filepath=STATE['filepath'], load_ui=False)
            # Loading clears nonpersistent timers; complete this verification now.
            scene = bpy.context.scene
            check('reopen_has_original_quality', quality(scene) == STATE['original_quality'])
            check('reopen_has_no_proxy_resources', not proxies()
                  and not any(c.get(native._PROXY_OWNER) for c in bpy.data.cameras))
            check('reopen_has_no_observation_session', native._ACTIVE is None)
            check('ordinary_similar_named_camera_preserved', bpy.data.objects.get(STATE['ordinary_name']) is not None)
            check('reopen_preserves_unrelated_quality', quality(bpy.data.scenes[STATE['other_name']]) == STATE['other_quality'])
            finish()
            return None
    except Exception:
        finish(traceback.format_exc())
        return None

bpy.app.timers.register(tick, first_interval=.5)

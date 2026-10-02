"""Visible-window regression for participation and lightweight single-camera views.

Run in a disposable Blender window. Installed-module mode imports only the
installed extension; it never prepends the source checkout to sys.path.
"""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import math
import os
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
INSTALLED = MODULE.startswith('bl_ext.')
if not INSTALLED:
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-native-enabled'))
OUT.mkdir(parents=True, exist_ok=True)
state = {'phase': 'start', 'waits': 0, 'started': time.monotonic()}
checks = {}
addon = native = core = preview = None


def wait_until(ready, message):
    if ready:
        state['waits'] = 0
        return False
    state['waits'] += 1
    assert state['waits'] <= 60, message
    return True


def wait_for_entries(count):
    return wait_until(native._ACTIVE is not None
                      and not native._ACTIVE.preparing
                      and len(native._ACTIVE.entries) == count,
                      'native window did not finish preparing')


def clock():
    scene = state['scene']
    return {'frame': scene.frame_current, 'subframe': scene.frame_subframe,
            'time_s': scene.get('wfrl_clearance_time_s')}


def assert_clock(expected):
    assert clock() == expected, 'view/quality transition changed the common time'
    assert native._ACTIVE.scene == state['scene']
    assert native._ACTIVE.window.scene == state['window'].scene == state['scene']


def assert_layout_and_fov():
    scene = state['scene']
    assert core.layout_dict(scene) == state['baseline'], 'viewing changed camera parameters'
    aspect = (scene.render.resolution_x * scene.render.pixel_aspect_x
              / (scene.render.resolution_y * scene.render.pixel_aspect_y))
    for entry in native._ACTIVE.entries:
        proxy = entry['proxy']
        if proxy is None:
            continue
        camera = core.get_camera(scene, entry['slot'])
        params = core.parameters(camera)
        outer_half_width = proxy.data.sensor_width / (2 * proxy.data.lens)
        hfov = math.degrees(2 * math.atan(outer_half_width * entry['fx']))
        vfov = math.degrees(2 * math.atan(outer_half_width / aspect * entry['fy']))
        assert math.isclose(hfov, params.fov, abs_tol=1e-4), (hfov, params.fov)
        assert math.isclose(vfov, params.vfov, abs_tol=1e-4), (vfov, params.vfov)
        space = entry['area'].spaces.active
        assert space.use_local_camera and space.camera == proxy
        assert space.region_3d.view_perspective == 'CAMERA'
        assert not space.lock_camera


def assert_single_solid():
    session = native._ACTIVE
    assert len(session.entries) == 1
    entry = session.entries[0]
    assert entry['slot'] == 2 and entry['proxy'] is not None
    assert not core.get_camera(state['scene'], 2)['custom_enabled'], 'WATCH enabled the camera'
    shader = entry['area'].spaces.active.shading
    assert shader.type == 'SOLID'
    assert shader.color_type == 'MATERIAL'
    assert not shader.show_shadows
    assert not shader.show_cavity
    assert not shader.show_specular_highlight
    with bpy.context.temp_override(window=session.window, area=entry['area']):
        assert not bpy.ops.wfrl.native_camera_quality.poll(), 'single view exposed a quality toggle'
        # Call the guard directly: Blender normally blocks this via poll before
        # execute, but a stale/direct caller must not change the TRIPLE setting.
        before = session.fast
        messages = []
        operator = SimpleNamespace(poll=native.WFRL_OT_NativeQuality.poll,
                                   report=lambda level, text: messages.append((list(level), text)))
        assert native.WFRL_OT_NativeQuality.execute(operator, bpy.context) == {'CANCELLED'}
        assert session.fast == before
    assert_layout_and_fov()


def assert_material():
    assert all(entry['area'].spaces.active.shading.type == 'MATERIAL'
               for entry in native._ACTIVE.entries)


def screenshot(name):
    with bpy.context.temp_override(window=native._ACTIVE.window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        assert bpy.ops.screen.screenshot(filepath=str(OUT / name)) == {'FINISHED'}


def assert_capture_contract():
    """Exercise sampling settings only; do not start an image-capture job."""
    scene, session = state['scene'], native._ACTIVE
    current = session.quality_settings()
    original = session.preview_quality
    probe = ('2' if original[0] != '2' else '1',
             8 if original[1] != 8 else 16, not original[2], not original[3])
    try:
        session.set_quality(probe)
        with native.capture_quality(scene):
            assert session.quality_settings() == original
            assert preview.render_profile(scene)['shading'] == 'MATERIAL'
        assert session.quality_settings() == probe
        try:
            with native.capture_quality(scene):
                assert session.quality_settings() == original
                raise ValueError('intentional capture-context restoration probe')
        except ValueError:
            pass
        assert session.quality_settings() == probe
    finally:
        session.set_quality(current)
    checks['capture_quality_restores_on_success_and_error'] = True
    checks['default_capture_profile_remains_material'] = True
    checks['image_capture_started'] = False


def record_draw():
    if (state['phase'] == 'single_playing' and native._ACTIVE
            and bpy.context.window == native._ACTIVE.window
            and any(entry['area'] == bpy.context.area for entry in native._ACTIVE.entries)):
        state['draw_frames'].add(bpy.context.scene.frame_current)


def remove_draw_handler():
    handle = state.pop('draw_handler', None)
    if handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')


def assert_restored():
    scene = state['scene']
    assert native._ACTIVE is None
    assert not any(obj.name.startswith('T1.Native.') for obj in scene.objects)
    assert core.layout_dict(scene) == state['baseline']
    assert (scene.render.preview_pixel_size, scene.eevee.taa_samples,
            scene.eevee.use_shadows, scene.eevee.use_fast_gi) == state['quality']
    assert scene.camera == state['scene_camera']
    assert (scene.render.resolution_x, scene.render.resolution_y,
            scene.render.pixel_aspect_x, scene.render.pixel_aspect_y) == state['render_gate']
    for area in state['window'].screen.areas:
        if area.as_pointer() in state['source_shading']:
            assert area.spaces.active.shading.type == state['source_shading'][area.as_pointer()]


def finish(status, error=None):
    remove_draw_handler()
    result = {'status': status, 'module': MODULE, 'installed_module': INSTALLED,
              'addon_path': str(Path(addon.__file__).resolve().parent) if addon else None,
              'blender': bpy.app.version_string, 'phase': state['phase'],
              'elapsed_s': time.monotonic() - state['started'],
              'method': 'scripted visible-window operators and POST_PIXEL draws; not physical mouse input or monitor scanout',
              'checks': checks, 'error': error}
    (OUT / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if error:
        (OUT / 'error.txt').write_text(error)
        print(error, flush=True)
    print('NATIVE_CAMERA_ENABLED_' + status, flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    global addon, native, core, preview
    try:
        phase = state['phase']
        if phase == 'start':
            addon = importlib.import_module(MODULE)
            native = importlib.import_module(MODULE + '.native_camera_views')
            core = importlib.import_module(MODULE + '.custom_cameras')
            preview = importlib.import_module(MODULE + '.custom_camera_preview')
            registered_here = not native.WFRL_OT_NativeCamera.is_registered
            if registered_here:
                addon.register()
            checks['registered_here'] = registered_here
            addon.load_demo_scene()
            scene = bpy.context.scene
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            core.get_camera(scene, 2)['custom_enabled'] = False
            state.update(scene=scene, area=area, window=bpy.context.window,
                         baseline=core.layout_dict(scene), scene_camera=scene.camera,
                         quality=(scene.render.preview_pixel_size, scene.eevee.taa_samples,
                                  scene.eevee.use_shadows, scene.eevee.use_fast_gi),
                         render_gate=(scene.render.resolution_x, scene.render.resolution_y,
                                      scene.render.pixel_aspect_x, scene.render.pixel_aspect_y),
                         source_shading={a.as_pointer(): a.spaces.active.shading.type
                                         for a in bpy.context.screen.areas if a.type == 'VIEW_3D'})
            state['initial_clock'] = clock()
            with bpy.context.temp_override(area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE') == {'FINISHED'}
            state['phase'] = 'triple_disabled'
            return .5

        scene, area, window = state['scene'], state['area'], state['window']
        if phase == 'triple_disabled':
            if wait_for_entries(3):
                return .25
            session = native._ACTIVE
            entries = {e['slot']: e for e in session.entries}
            assert entries[1]['proxy'] is not None
            assert entries[2]['proxy'] is None, 'disabled C2 appeared in TRIPLE'
            assert entries[3]['proxy'] is not None
            assert session.fast is True
            assert_material()
            assert_clock(state['initial_clock'])
            assert_layout_and_fov()
            screenshot('triple-c2-disabled.png')
            checks['triple_material_fast_default'] = True
            checks['disabled_triple_slot_blank'] = True
            core.get_camera(scene, 2)['custom_enabled'] = True
            core.get_camera(scene, 3)['custom_enabled'] = False
            state['phase'] = 'triple_dynamic'
            return .5

        if phase == 'triple_dynamic':
            session = native._ACTIVE
            entries = {e['slot']: e for e in session.entries}
            if wait_until(entries[2]['proxy'] is not None and entries[3]['proxy'] is None,
                          'live participation changes did not update the views'):
                return .25
            checks['live_participation_changes'] = True
            core.get_camera(scene, 2)['custom_enabled'] = False
            core.get_camera(scene, 3)['custom_enabled'] = True
            with bpy.context.temp_override(window=session.window, area=entries[1]['area']):
                assert bpy.ops.wfrl.native_camera_quality.poll()
                assert bpy.ops.wfrl.native_camera_quality() == {'FINISHED'}
                assert session.fast is False
                assert session.quality_settings() == state['quality']
                assert_material()
                state['before_focus'] = clock()
                state['native_window'] = session.window.as_pointer()
                assert bpy.ops.wfrl.native_camera_focus(slot=2) == {'FINISHED'}
            state['phase'] = 'focused'
            return .5

        if phase == 'focused':
            if wait_for_entries(1):
                return .25
            session = native._ACTIVE
            assert session.window.as_pointer() == state['native_window']
            assert session.fast is False, 'focus overwrote the TRIPLE quality preference'
            assert_single_solid()
            assert_clock(state['before_focus'])
            assert_capture_contract()
            screenshot('focused-single-solid.png')
            checks['triple_high_quality_to_focus_solid'] = True
            checks['single_quality_toggle_unavailable'] = True
            checks['disabled_focus_slot_remains_inspectable'] = True
            state.update(before_play=clock(), play_started=time.monotonic(), draw_frames=set())
            state['draw_handler'] = bpy.types.SpaceView3D.draw_handler_add(record_draw, (), 'WINDOW', 'POST_PIXEL')
            with bpy.context.temp_override(window=session.window, area=session.entries[0]['area']):
                assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
            state['phase'] = 'single_playing'
            return 2.

        if phase == 'single_playing':
            session = native._ACTIVE
            assert session.window.screen.is_animation_playing
            assert clock()['frame'] > state['before_play']['frame'], 'single-view playback did not advance'
            if state['before_play']['time_s'] is not None:
                assert clock()['time_s'] > state['before_play']['time_s']
            assert len(state['draw_frames']) >= 2, 'single view did not draw multiple playback frames'
            assert_single_solid()
            assert_clock(clock())
            with bpy.context.temp_override(window=session.window, area=session.entries[0]['area']):
                assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
            assert not session.window.screen.is_animation_playing
            state['paused_clock'] = clock()
            checks['single_playback'] = {'start': state['before_play'], 'paused': state['paused_clock'],
                                        'wall_s': time.monotonic() - state['play_started'],
                                        'unique_drawn_frames': len(state['draw_frames'])}
            remove_draw_handler()
            state['phase'] = 'single_paused'
            return 1.

        if phase == 'single_paused':
            assert_clock(state['paused_clock'])
            assert_single_solid()
            session = native._ACTIVE
            with bpy.context.temp_override(window=session.window, area=session.entries[0]['area']):
                assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE', layout=session.layout) == {'FINISHED'}
            checks['pause_preserves_common_time'] = True
            state['phase'] = 'triple_restored'
            return .5

        if phase == 'triple_restored':
            if wait_for_entries(3):
                return .25
            session = native._ACTIVE
            assert session.window.as_pointer() == state['native_window']
            assert session.fast is False, 'returning to TRIPLE reset its high-quality preference'
            assert session.quality_settings() == state['quality']
            assert_material()
            assert_clock(state['paused_clock'])
            assert_layout_and_fov()
            entries = {e['slot']: e for e in session.entries}
            assert entries[2]['proxy'] is None and entries[3]['proxy'] is not None
            screenshot('triple-high-quality-restored.png')
            checks['return_to_triple_preserves_high_quality_and_time'] = True
            native.shutdown()
            assert_restored()
            bpy.context.window_manager.wfrl_custom_slot = 2
            with bpy.context.temp_override(window=window, area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
            state['phase'] = 'direct_watch'
            return .5

        if phase == 'direct_watch':
            if wait_for_entries(1):
                return .25
            assert_single_solid()
            assert_clock(state['paused_clock'])
            screenshot('single-solid.png')
            checks['direct_disabled_watch_defaults_to_solid'] = True
            checks['calibrated_fov_and_camera_parameters_preserved'] = True
            native.shutdown()
            state['phase'] = 'closed'
            return .5

        if phase == 'closed':
            assert_restored()
            assert clock() == state['paused_clock']
            assert not window.screen.is_animation_playing
            checks['close_restores_quality_source_shading_and_camera'] = True
            checks['temporary_cameras_cleaned_up'] = True
            finish('PASS')
            return None
        raise AssertionError('unknown regression phase: ' + phase)
    except Exception:
        finish('ERROR', traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.)

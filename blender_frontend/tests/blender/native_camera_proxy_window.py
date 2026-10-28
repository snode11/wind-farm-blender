"""Exercise native camera proxy updates in a disposable visible Blender window.

WFRL_ADDON_MODULE and WFRL_EXPECTED_ADDON_ROOT select the extension. An optional
WFRL_EXPECTED_INVENTORY verifies the imported module files against the package
inventory. WFRL_TEST_OUTPUT must name a fresh output directory.
"""

from dataclasses import replace
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=True)
assert not (OUT / 'checks.json').exists(), 'WFRL_TEST_OUTPUT already contains results'
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
EXPECTED_ROOT = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).expanduser().resolve()
RESULT = {
    'status': 'RUNNING', 'module': MODULE, 'blender': bpy.app.version_string,
    'method': 'Scripted native operators and Blender API in a visible, disposable window; no physical mouse input, GPU image capture, full-clip FPS or display scanout.',
    'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'checks': {}, 'observations': [],
}
STATE = {'phase': 'start', 'started': time.monotonic(), 'deadline': time.monotonic() + 180}
addon = native = core = playback = None


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(name, condition, **details):
    RESULT['checks'][name] = {'passed': bool(condition), **details}
    assert condition, (name, details)


def clock(scene):
    return {'frame': scene.frame_current, 'subframe': scene.frame_subframe,
            'time_s': scene.get('wfrl_clearance_time_s')}


def scene_clocks():
    scene = STATE['scene']
    return {str(other.session_uid): clock(other) for other in bpy.data.scenes if other != scene}


def entry():
    session = native._ACTIVE
    assert session is not None and not session.preparing and len(session.entries) == 1
    return session, session.entries[0]


def open_watch():
    window, area = STATE['window'], STATE['area']
    with bpy.context.temp_override(window=window, area=area):
        assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
    deadline = time.monotonic() + 20
    while not native._ACTIVE or native._ACTIVE.preparing or len(native._ACTIVE.entries) != 1:
        assert time.monotonic() < deadline, 'native observation window did not become ready'
        yield .2
    yield .4


def proxy_identity(view):
    proxy = view['proxy']
    return (proxy.session_uid, proxy.data.session_uid, proxy.as_pointer(), proxy.data.as_pointer())


def assert_tracking(session, view, source):
    proxy = view['proxy']
    assert proxy is not None and proxy.type == 'CAMERA'
    assert len(proxy.constraints) == 1
    constraint = proxy.constraints[0]
    assert constraint.type == 'COPY_TRANSFORMS' and constraint.target == source
    with bpy.context.temp_override(window=session.window, area=view['area']):
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        matrix_a = proxy.evaluated_get(depsgraph).matrix_world
        matrix_b = source.evaluated_get(depsgraph).matrix_world
        error = max(abs(matrix_a[i][j] - matrix_b[i][j]) for i in range(4) for j in range(4))
    assert error < 2e-5, ('evaluated world transform mismatch', error)
    assert not proxy.data.show_passepartout and proxy.data.passepartout_alpha == 0
    assert not view['area'].spaces.active.overlay.show_overlays
    return error


def assert_calibrated(session, view, source):
    scene = session.scene
    params = core.parameters(source)
    proxy = view['proxy']
    aspect = scene.render.resolution_x * scene.render.pixel_aspect_x / (scene.render.resolution_y * scene.render.pixel_aspect_y)
    outer = proxy.data.sensor_width / (2 * proxy.data.lens)
    hfov = math.degrees(2 * math.atan(outer * view['fx']))
    vfov = math.degrees(2 * math.atan(outer / aspect * view['fy']))
    assert math.isclose(hfov, params.fov, abs_tol=1e-4), (hfov, params.fov)
    assert math.isclose(vfov, params.vfov, abs_tol=1e-4), (vfov, params.vfov)
    space = view['area'].spaces.active
    assert space.use_local_camera and space.camera == proxy
    assert space.region_3d.view_perspective == 'CAMERA'
    assert not space.lock_camera
    region = next(r for r in view['area'].regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=session.window, area=view['area'], region=region):
        bpy.context.view_layer.update()
        space.region_3d.update()
        bounds = native.gate_bounds(scene, view, region, space.region_3d)
    assert bounds and all(math.isfinite(value) for value in bounds)
    assert bounds[2] > 0 and bounds[3] > 0, bounds
    assert bounds[0] >= -2 and bounds[1] >= -2, (bounds, region.width, region.height)
    assert bounds[0] + bounds[2] <= region.width + 2, (bounds, region.width, region.height)
    assert bounds[1] + bounds[3] <= region.height + 2, (bounds, region.width, region.height)
    return {'horizontal_fov': hfov, 'vertical_fov': vfov, 'gate_bounds': list(bounds),
            'region': [region.width, region.height]}


def screenshot(name):
    session, _ = entry()
    with bpy.context.temp_override(window=session.window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        assert bpy.ops.screen.screenshot(filepath=str(OUT / name)) == {'FINISHED'}
    assert (OUT / name).stat().st_size > 0


def run():
    global addon, native, core, playback
    assert not bpy.app.background and not bpy.data.filepath
    assert len(bpy.context.window_manager.windows) == 1
    addon = importlib.import_module(MODULE)
    native = importlib.import_module(MODULE + '.native_camera_views')
    core = importlib.import_module(MODULE + '.custom_cameras')
    playback = importlib.import_module(MODULE + '.playback')
    modules = (addon, native, core, playback)
    files = {Path(module.__file__).name: sha256(module.__file__) for module in modules}
    actual_root = Path(addon.__file__).resolve().parent
    assert actual_root == EXPECTED_ROOT
    assert all(Path(module.__file__).resolve().parent == EXPECTED_ROOT for module in modules)
    inventory_file = os.environ.get('WFRL_EXPECTED_INVENTORY')
    if inventory_file:
        inventory = json.loads(Path(inventory_file).read_text())
        expected = {row['path']: row['sha256'] for row in inventory['files']}
        assert all(expected[name] == digest for name, digest in files.items()), (expected, files)
    RESULT.update(addon_root=str(actual_root), module_sha256=files,
                  build_info=json.loads((actual_root / 'build-info.json').read_text()) if (actual_root / 'build-info.json').exists() else None,
                  inventory=str(Path(inventory_file).resolve()) if inventory_file else None)
    record('visible_installed_identity', True, inventory_verified=bool(inventory_file))
    if not native.WFRL_OT_NativeCamera.is_registered:
        addon.register()
    assert not bpy.app.timers.is_registered(native.watch)
    addon.load_demo_scene()
    scene, window = bpy.context.scene, bpy.context.window
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    source = core.get_camera(scene, 1)
    assert source is not None
    foreign = bpy.data.scenes.new('NativeProxy.ForeignClock')
    STATE.update(scene=scene, window=window, area=area, foreign=foreign, source=source,
                 base_clock=clock(scene))
    STATE['foreign_clocks'] = scene_clocks()
    assets = [actual_root / 'assets/mappo/manifest.json', actual_root / 'assets/mappo/geometry.npz',
              actual_root / 'assets/mappo/data.json']
    asset_hashes = {str(p.relative_to(actual_root)): sha256(p) for p in assets}
    bpy.context.window_manager.wfrl_custom_slot = 1
    STATE['phase'] = 'open'
    yield from open_watch()
    session, view = entry()
    assert session.scene == scene and session.window.scene == scene
    assert view['slot'] == 1 and view['area'].spaces.active.shading.type == 'SOLID'
    assert bpy.app.timers.is_registered(native.watch)
    assert clock(scene) == STATE['base_clock'] and scene_clocks() == STATE['foreign_clocks']
    record('single_visible_window_ready', True, region_size=[view['area'].width, view['area'].height])
    screenshot('camera-initial.png')

    sync_count = [0]
    original_sync = session.sync
    def counted_sync():
        sync_count[0] += 1
        return original_sync()
    session.sync = counted_sync
    params = core.parameters(source)
    identity = proxy_identity(view)
    counts = (len(bpy.data.objects), len(bpy.data.cameras))
    space = view['area'].spaces.active
    rv = space.region_3d
    rv.view_perspective = 'CAMERA'
    rv.view_camera_zoom = 18.0
    rv.view_camera_offset = (.08, -.06)
    expected_zoom, expected_offset = rv.view_camera_zoom, tuple(rv.view_camera_offset)
    max_error = 0.
    STATE['phase'] = 'camera_pose_and_metadata'
    for index in range(1, 17):
        before_sync = sync_count[0]
        changing = replace(params,
                           location=tuple(value + index * delta for value, delta in zip(params.location, (.002, -.001, .0015))),
                           yaw=(params.yaw + index * .6) % 360,
                           pitch=max(-89., min(89., params.pitch + index * .15)),
                           roll=params.roll + index * .35,
                           output_long_edge_px=2048 if index % 2 else 3072,
                           focal_length_mm_record=35. if index % 2 else 50.)
        core.apply_parameters(source, changing)
        deadline = time.monotonic() + 8
        while sync_count[0] == before_sync:
            assert time.monotonic() < deadline, 'watch timer stopped during pose edits'
            yield .15
        session, view = entry()
        max_error = max(max_error, assert_tracking(session, view, source))
        assert proxy_identity(view) == identity
        assert (len(bpy.data.objects), len(bpy.data.cameras)) == counts
        assert rv.view_perspective == 'CAMERA' and abs(rv.view_camera_zoom - expected_zoom) < 1e-5
        assert max(abs(rv.view_camera_offset[i] - expected_offset[i]) for i in range(2)) < 1e-6
        assert clock(scene) == STATE['base_clock'] and scene_clocks() == STATE['foreign_clocks']
    record('pose_metadata_preserve_proxy_and_manual_camera_framing', True,
           changes=16, observed_timer_syncs=sync_count[0], max_world_matrix_error=max_error,
           zoom=expected_zoom, offset=expected_offset, object_camera_counts=counts)
    screenshot('camera-after-pose.png')

    rv.view_perspective = 'PERSP'
    rv.view_location = (3., 4., 5.)
    rv.view_distance = 23.
    rv.view_rotation = (.9659258, 0., .258819, 0.)
    free_state = (rv.view_perspective, tuple(rv.view_location), rv.view_distance, tuple(rv.view_rotation))
    before_sync = sync_count[0]
    core.apply_parameters(source, replace(core.parameters(source), yaw=(changing.yaw + 4) % 360,
                                          pitch=changing.pitch + 1, roll=changing.roll + 2,
                                          output_long_edge_px=2304, focal_length_mm_record=42.))
    while sync_count[0] == before_sync:
        assert time.monotonic() < STATE['deadline']
        yield .15
    assert proxy_identity(view) == identity
    assert (rv.view_perspective, tuple(rv.view_location), rv.view_distance, tuple(rv.view_rotation)) == free_state
    max_error = max(max_error, assert_tracking(session, view, source))
    record('free_view_survives_pose_and_metadata', True, view=list(free_state[:3]))

    STATE['phase'] = 'optical_calibration'
    rv.view_perspective = 'CAMERA'
    assert rv.view_perspective == 'CAMERA', 'failed to enter camera view before optical changes'
    for field, value in (('fov', 90.), ('vfov', 40.)):
        before_uid, before_sync = view['proxy'].session_uid, sync_count[0]
        perspective_before = rv.view_perspective
        core.apply_parameters(source, replace(core.parameters(source), **{field: value}))
        perspective_after_edit = rv.view_perspective
        deadline = time.monotonic() + 8
        while view['proxy'].session_uid == before_uid:
            assert time.monotonic() < deadline, field + ' change did not rebuild the optical proxy'
            yield .15
        assert sync_count[0] > before_sync
        assert (len(bpy.data.objects), len(bpy.data.cameras)) == counts
        assert_tracking(session, view, source)
        RESULT['observations'].append({'field': field, 'perspective_before_edit': perspective_before,
                                       'perspective_after_edit': perspective_after_edit,
                                       'perspective_after_sync': rv.view_perspective})
        RESULT['observations'].append({'field': field, 'value': value,
                                       'calibration': assert_calibrated(session, view, source)})
    record('fov_and_vfov_rebuild_calibrated_unmasked_proxy', True)
    screenshot('camera-calibrated.png')
    assert clock(scene) == STATE['base_clock'] and scene_clocks() == STATE['foreign_clocks']
    for perspective, field, value in (('PERSP', 'fov', 83.), ('ORTHO', 'vfov', 47.)):
        rv.view_perspective = perspective
        rv.view_location = (3., 4., 5.)
        rv.view_distance = 23.
        rv.view_rotation = (.9659258, 0., .258819, 0.)
        before_view = (rv.view_perspective, tuple(rv.view_location), rv.view_distance, tuple(rv.view_rotation))
        before_uid = view['proxy'].session_uid
        core.apply_parameters(source, replace(core.parameters(source), **{field: value}))
        deadline = time.monotonic() + 8
        while view['proxy'].session_uid == before_uid:
            assert time.monotonic() < deadline, field + ' change did not rebuild in free view'
            yield .15
        after_view = (rv.view_perspective, tuple(rv.view_location), rv.view_distance, tuple(rv.view_rotation))
        assert after_view == before_view, (perspective, before_view, after_view)
        assert_tracking(session, view, source)
        assert (len(bpy.data.objects), len(bpy.data.cameras)) == counts
    record('optical_rebuild_preserves_free_perspective_and_ortho_views', True)
    rv.view_perspective = 'CAMERA'
    before_uid = view['proxy'].session_uid
    core.apply_parameters(source, replace(core.parameters(source), fov=90.))
    deadline = time.monotonic() + 8
    while view['proxy'].session_uid == before_uid:
        assert time.monotonic() < deadline, 'camera view did not recalibrate after free views'
        yield .15
    assert_calibrated(session, view, source)
    assert clock(scene) == STATE['base_clock'] and scene_clocks() == STATE['foreign_clocks']

    STATE['phase'] = 'playback'
    play_identity = proxy_identity(view)
    before_play = clock(scene)
    with bpy.context.temp_override(window=session.window, area=view['area']):
        assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
    yield 1.0
    assert playback.is_playing(scene) and scene.frame_current > before_play['frame']
    assert proxy_identity(view) == play_identity
    assert_tracking(session, view, source)
    with bpy.context.temp_override(window=session.window, area=view['area']):
        assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
    assert not playback.is_playing(scene)
    paused = clock(scene)
    yield .6
    assert clock(scene) == paused and scene_clocks() == STATE['foreign_clocks']
    record('short_play_and_pause', True, before=before_play, paused=paused)

    STATE['phase'] = 'retry_and_reopen'
    original_release = session.release_proxy
    release_calls = [0]
    def release_once_late(item):
        release_calls[0] += 1
        if release_calls[0] == 1:
            return ['intentional one-time proxy release failure']
        return original_release(item)
    session.release_proxy = release_once_late
    native.shutdown()
    assert native._ACTIVE is session and session.closing
    assert bpy.app.timers.is_registered(native.watch) and release_calls[0] == 1
    deadline = time.monotonic() + 8
    while native._ACTIVE is not None or bpy.app.timers.is_registered(native.watch):
        assert time.monotonic() < deadline, 'watch did not complete pending cleanup and stop'
        yield .15
    assert release_calls[0] >= 2 and native.watch() is None
    assert clock(scene) == paused and scene_clocks() == STATE['foreign_clocks']
    assert len(bpy.context.window_manager.windows) == 1
    record('watch_retries_owned_cleanup_then_stops', True, release_calls=release_calls[0])
    yield from open_watch()
    reopened, new_view = entry()
    assert bpy.app.timers.is_registered(native.watch)
    assert_tracking(reopened, new_view, source)
    assert_calibrated(reopened, new_view, source)
    assert clock(scene) == paused and scene_clocks() == STATE['foreign_clocks']
    screenshot('camera-reopened.png')
    native.shutdown()
    deadline = time.monotonic() + 8
    while native._ACTIVE is not None or bpy.app.timers.is_registered(native.watch):
        assert time.monotonic() < deadline, 'watch timer remained after final close'
        yield .15
    assert len(bpy.context.window_manager.windows) == 1
    assert clock(scene) == paused and scene_clocks() == STATE['foreign_clocks']
    assert all(not obj.get(native._PROXY_OWNER) for obj in bpy.data.objects)
    assert all(not data.get(native._PROXY_OWNER) for data in bpy.data.cameras)
    record('reopen_registers_watch_and_final_close_stops_it', True)
    record('other_scene_clock_and_frozen_assets_preserved',
           asset_hashes == {str(p.relative_to(actual_root)): sha256(p) for p in assets},
           other_scene_clocks=scene_clocks(), asset_sha256=asset_hashes)
    STATE['phase'] = 'complete'


def finish(status, error=None):
    RESULT.update(status=status, phase=STATE['phase'], elapsed_s=time.monotonic() - STATE['started'], error=error)
    (OUT / 'checks.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n')
    if error:
        print(error, flush=True)
    print('NATIVE_CAMERA_PROXY_WINDOW_' + status, flush=True)
    bpy.ops.wm.quit_blender()


def drive():
    try:
        assert time.monotonic() < STATE['deadline'], 'window regression timed out'
        return next(STATE['task'])
    except StopIteration:
        finish('PASS')
    except Exception:
        finish('ERROR', traceback.format_exc())
    return None


STATE['task'] = run()
bpy.app.timers.register(drive, first_interval=1.)

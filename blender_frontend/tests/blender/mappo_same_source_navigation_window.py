"""Installed, visible-window regression for the same-source navigation bug.

Open a byte-identical COPY of the installed bundled geometry .blend before
--python this_file. Set WFRL_ADDON_MODULE to the actual bl_ext module,
WFRL_EXPECTED_ADDON_ROOT to its installed directory, and WFRL_TEST_OUTPUT to an
isolated output directory. WFRL_SPLIT_SAVE optionally names a new .blend inside
that directory. The script uses native camera-exit and numpad-equivalent orbit
operations from the untouched default view, the user's play/pause and sample-step operators,
native animation, native save/reopen, JSON and screen.screenshot; it does not
render or save over the canonical example. WFRL_QUIT_AFTER_TEST=1 closes only
this dedicated Blender process after the result is written.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import time
import traceback

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Quaternion, Vector

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'same_source_navigation_helpers', HERE / 'mappo_same_source_texture_regression.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=True)
assert not (OUT / 'navigation-window-validation.json').exists(), 'Use a new output directory'
state = dict(stage=0, checks=[], deadline=time.monotonic() + 300.)
validation = None


def views():
    return sorted((area for area in bpy.context.window.screen.areas if area.type == 'VIEW_3D'),
                  key=lambda area: area.x)


def operator(name, **kwargs):
    area = views()[0]
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        result = getattr(bpy.ops.wfrl, name)(**kwargs)
    assert result == {'FINISHED'}, (name, result)


def pair():
    scene = validation.scene
    areas = views()
    assert len(areas) == 2, len(areas)
    left, right = [area.spaces.active for area in areas]
    assert validation.split.enabled(scene) and scene.get('split_review_status') == 'READY'
    assert validation.same.active(scene) and not validation.synthetic.active(scene)
    assert left.local_view is None and right.local_view is not None
    assert right.use_local_camera and right.camera == validation.same.camera(scene)
    assert right.shading.type == left.shading.type == 'MATERIAL'
    for obj in validation.same.objects(scene):
        assert obj.local_view_get(right), obj.name
    for bid in (1, 2, 3):
        assert not scene.objects[f'WFRL.Turbine.T1.Blade{bid}'].local_view_get(right)
        assert not scene.objects[f'SplitRecon.B{bid}'].local_view_get(right)
    return areas


def right_view():
    space = pair()[1].spaces.active
    rv = space.region_3d
    return dict(location=list(rv.view_location), rotation=list(rv.view_rotation),
                distance=rv.view_distance, perspective=rv.view_perspective,
                camera_zoom=rv.view_camera_zoom, camera_offset=list(rv.view_camera_offset),
                lens=space.lens, camera=space.camera.name,
                lock_object=space.lock_object.name if space.lock_object else None,
                lock_cursor=space.lock_cursor, lock_camera=space.lock_camera)


def unchanged(expected):
    actual = right_view()
    for key in ('perspective', 'camera', 'lock_object', 'lock_cursor', 'lock_camera'):
        assert actual[key] == expected[key], (key, expected, actual)
    for key in ('location', 'rotation', 'distance', 'camera_zoom', 'camera_offset', 'lens'):
        assert helpers.np.allclose(actual[key], expected[key], atol=1e-5, rtol=0), (
            key, expected, actual)
    return actual


def rotor_center():
    return Vector(validation.same.OFFSET) + Vector(validation.value['sequence'].rotor.hub)


def native_orbit():
    """Orbit the right free view using Blender's numpad 6 operation."""
    area = pair()[1]
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        result = bpy.ops.view3d.view_orbit(type='ORBITRIGHT', angle=math.radians(15.))
    assert result == {'FINISHED'}, result
    # Let Blender finish its own navigation smoothing and redraw before checks.
    return max(.3, getattr(bpy.context.preferences.view, 'smooth_view', 0) / 1000. + .2)


def native_camera_exit():
    """Leave CAMERA through Blender's own toggle without modifying navigation."""
    area = pair()[1]
    assert area.spaces.active.region_3d.view_perspective == 'CAMERA', right_view()
    region = next(region for region in area.regions if region.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        result = bpy.ops.view3d.view_camera()
    assert result == {'FINISHED'}, result
    return max(.3, getattr(bpy.context.preferences.view, 'smooth_view', 0) / 1000. + .2)


def surface_target_pixels(target):
    area = pair()[1]
    region = next(region for region in area.regions if region.type == 'WINDOW')
    rv = area.spaces.active.region_3d
    rv.update()
    point = location_3d_to_region_2d(region, rv, Vector(target))
    assert point is not None and 8 < point.x < region.width - 8 and 8 < point.y < region.height - 8, (
        target, tuple(point) if point is not None else None, right_view())
    return dict(target_m=list(target), pixels=list(point), region_size=[region.width, region.height])


def full_rotor_pixels():
    area = pair()[1]
    space = area.spaces.active
    region = next(region for region in area.regions if region.type == 'WINDOW')
    space.region_3d.update()
    preview = helpers.importlib.import_module(validation.module + '.custom_camera_preview')
    x, y, width, height = preview.available_rectangle(area, region)
    graph = bpy.context.evaluated_depsgraph_get()
    blades = []
    for source in validation.same.objects(validation.scene):
        obj = source.evaluated_get(graph)
        points = [location_3d_to_region_2d(region, space.region_3d, obj.matrix_world @ vertex.co)
                  for vertex in obj.data.vertices]
        assert points and all(point is not None for point in points), source.name
        box = [min(p.x for p in points), max(p.x for p in points),
               min(p.y for p in points), max(p.y for p in points)]
        assert x + 8 <= box[0] <= box[1] <= x + width - 8, (source.name, box)
        assert y + 8 <= box[2] <= box[3] <= y + height - 8, (source.name, box)
        blades.append(dict(object=source.name, pixels=box))
    return dict(region_size=[region.width, region.height], blades=blades)


def capture(name, *, full=False):
    path = OUT / (name + '.png')
    assert not path.exists(), path
    data = dict(frame=validation.scene.frame_current, index=validation.scene[validation.same.INDEX],
                detail=bool(validation.scene.get(validation.same.DETAIL)), right_view=right_view(),
                screenshot=str(path))
    if full:
        data['full_rotor_pixels'] = full_rotor_pixels()
    assert bpy.ops.screen.screenshot(filepath=str(path)) == {'FINISHED'}
    state[name] = data
    (OUT / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def play_start(label):
    state[label] = dict(start=validation.motion_signature(), observed=[],
                        deadline=time.monotonic() + 12., operator='wfrl.clearance_playback')
    operator('clearance_playback')
    assert bpy.context.screen.is_animation_playing


def observe_play(label, expected_view, *, manual=False):
    assert bpy.context.screen.is_animation_playing
    current = validation.motion_signature()
    record = state[label]
    if current['index'] <= record['start']['index']:
        assert time.monotonic() < record['deadline'], (label, record, current)
        return False
    assert current['frame'] > record['start']['frame']
    assert current['left'] != record['start']['left'], ('Left source stopped', record, current)
    assert current['right'] != record['start']['right'], ('Right reconstruction stopped', record, current)
    if manual:
        assert not validation.scene.get(validation.same.DETAIL), 'Manual navigation must release detail tracking'
    record['observed'].append(dict(motion=current, clock=validation.frame(current['frame'], seek=False),
                                   right_view=unchanged(expected_view)))
    if len(record['observed']) < 2:
        return False
    operator('clearance_playback')
    assert not bpy.context.screen.is_animation_playing
    record['pause'] = validation.motion_signature()
    record.pop('deadline')
    return True


def manual_full_rotor_view():
    """Modify native navigation exactly as user orbit/zoom would, without fit()."""
    area = pair()[1]
    rv = area.spaces.active.region_3d
    rv.view_location = rotor_center() + Vector((0., 1.75, -1.25))
    rv.view_rotation = Quaternion(Vector((0., 0., 1.)), math.radians(12.)) @ (
        validation.same.camera(validation.scene).matrix_world.to_quaternion())
    rv.view_distance = 380.
    rv.view_perspective = 'PERSP'
    rv.update()
    area.tag_redraw()
    # Deliberately leave DETAIL true. Only the implementation may release it
    # when playback observes navigation that differs from its own last view.
    assert validation.scene.get(validation.same.DETAIL)
    return right_view()


def report(status, error=None):
    result = {key: value for key, value in state.items() if key != 'deadline'}
    result.update(status=status, error=error, blender=bpy.app.version_string,
                  addon_module=validation.module if validation else None,
                  addon_file=validation.addon.__file__ if validation else None,
                  actual_module_root=str(validation.root) if validation else None,
                  blend=bpy.data.filepath,
                  verification='INSTALLED_VISIBLE_WINDOW_NATIVE_ORBIT_PLAY_NAVIGATION_SEEK_STEP_CURRENT_SAVE_REOPEN',
                  software_sync_and_navigation_only=True, reconstruction_accuracy_accepted=False,
                  visible_window_left_open=os.environ.get('WFRL_QUIT_AFTER_TEST') != '1')
    (OUT / 'navigation-window-validation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def tick():
    global validation
    try:
        assert not bpy.app.background and bpy.context.window is not None
        assert time.monotonic() < state['deadline'], ('timeout', state['stage'])
        if validation is not None:
            scene = bpy.context.scene
            assert not str(scene.get('split_review_status', '')).startswith('ERROR'), scene.get('split_review_status')
            cameras = helpers.importlib.import_module(validation.module + '.cameras')
            if cameras._LAYOUT_JOB is not None or validation.split._JOBS or scene.get('split_review_status') == 'PREPARING':
                return .2
        stage = state['stage']
        if stage == 0:
            assert os.environ.get('WFRL_ADDON_MODULE', '').startswith('bl_ext.'), 'Use the installed extension module'
            validation = helpers.Validation()
            canonical = validation.root / 'assets/examples/mappo_reconstruction_split.blend'
            initial = Path(bpy.data.filepath).resolve()
            assert initial != canonical.resolve(), 'Open a copy of the canonical geometry scene'
            expected = os.environ.get('WFRL_EXPECTED_GEOMETRY_SHA256', helpers.GEOMETRY_SHA256)
            assert helpers.sha256(canonical) == helpers.sha256(initial) == expected
            assert not validation.same.active(validation.scene) and not validation.synthetic.active(validation.scene)
            assert not bpy.context.screen.is_animation_playing
            state['input'] = dict(copy=str(initial), canonical=str(canonical), sha256=expected)
            validation.scene.frame_set(52)
            operator('split_recon_same_source')
        elif stage == 1:
            pair()
            validation.activate()
            assert not validation.scene.get(validation.same.DETAIL)
            state['entry_clock'] = validation.frame(52, seek=False)
            capture('entry-full-rotor', full=True)
            state['entry_view'] = right_view()
            state['checks'].append('Explicit same-source entry shows all three blades with DETAIL false')
            state['default_native_orbit'] = dict(operator='view3d.view_orbit', type='ORBITRIGHT',
                angle_degrees=15., target_center_m=list(rotor_center()), steps=[])
            state['stage'] = 14
            return native_camera_exit()
        elif stage == 14:
            actual = right_view()
            center_error = (Vector(actual['location']) - rotor_center()).length
            state['default_camera_exit'] = dict(operator='view3d.view_camera',
                before=state['entry_view'], after=actual, center_error_m=center_error)
            # Capture even an incorrect inherited pivot so a failed baseline
            # retains the actual native window after the camera toggle.
            capture('native-camera-exit-free-view')
            assert center_error <= 5., ('Native camera exit inherited the wrong rotor pivot', center_error, actual)
            assert actual['perspective'] in {'PERSP', 'ORTHO'}, actual
            state['default_camera_exit']['full_rotor_pixels'] = full_rotor_pixels()
            state['checks'].append('Native camera exit retains the right rotor hub pivot and all three visible blades')
            state['stage'] = 2
            return native_orbit()
        elif stage == 2:
            actual = right_view()
            center_error = (Vector(actual['location']) - rotor_center()).length
            assert center_error <= 5., ('Default native orbit pivot is not at the right rotor', center_error, actual)
            assert actual['perspective'] in {'PERSP', 'ORTHO'}, actual
            assert not validation.scene.get(validation.same.DETAIL)
            orbit = state['default_native_orbit']
            orbit['steps'].append(dict(angle_degrees=15 * (len(orbit['steps']) + 1), right_view=actual,
                center_error_m=center_error, full_rotor_pixels=full_rotor_pixels()))
            if len(orbit['steps']) < 12:
                return native_orbit()
            # Use the actual camera direction as the starting direction; the
            # inherited region rotation is the field that caused this bug.
            front = validation.same.camera(validation.scene).matrix_world.to_quaternion() @ Vector((0., 0., -1.))
            back = Quaternion(actual['rotation']) @ Vector((0., 0., -1.))
            orbit['front_back_direction_dot'] = front.normalized().dot(back.normalized())
            assert orbit['front_back_direction_dot'] <= -.98, orbit
            state['orbit_view'] = actual
            capture('native-orbit-back-full-rotor', full=True)
            state['checks'].append('Twelve native 15 degree orbit steps reach the back with all three blades visible and the pivot near the right rotor hub')
            play_start('default_play')
        elif stage == 3:
            if not observe_play('default_play', state['orbit_view']):
                return .2
            capture('default-paused-back-full-rotor', full=True)
            state['checks'].append('Native user playback changes both geometries on the shared clock and retains the native-orbited back view')
        elif stage == 4:
            assert not bpy.context.screen.is_animation_playing
            assert validation.motion_signature() == state['default_play']['pause']
            validation.frame(52)
            operator('split_recon_inspect', point='CURRENT')
        elif stage == 5:
            assert validation.scene.get(validation.same.DETAIL)
            capture('explicit-detail-before-navigation')
            target, _normal, _up = validation.same._tracked_surface(validation.scene, validation.value)
            state['detail_native_orbit'] = dict(before=right_view(), target_m=list(target),
                target_before=surface_target_pixels(target), operator='view3d.view_orbit',
                type='ORBITRIGHT', angle_degrees=15.)
            state['stage'] = 6
            return native_orbit()
        elif stage == 6:
            record = state['detail_native_orbit']
            actual = right_view()
            assert helpers.np.allclose(actual['location'], record['before']['location'], atol=1e-5, rtol=0), record
            assert abs(actual['distance'] - record['before']['distance']) <= 1e-5, record
            assert not helpers.np.allclose(actual['rotation'], record['before']['rotation'], atol=1e-5, rtol=0), record
            record['target_after'] = surface_target_pixels(record['target_m'])
            record['after'] = actual
            # Re-seek the same native frame to observe the navigation ownership
            # change without moving the selected surface out of the closeup.
            record['clock'] = validation.frame(validation.scene.frame_current)
            assert not validation.scene.get(validation.same.DETAIL), 'Native detail orbit must release tracking'
            unchanged(actual)
            record['target_after_release'] = surface_target_pixels(record['target_m'])
            capture('detail-native-orbit-released')
            state['checks'].append('One native orbit from explicit detail keeps its surface target visible and releases automatic tracking')
            operator('split_recon_inspect', point='CURRENT')
        elif stage == 7:
            assert validation.scene.get(validation.same.DETAIL)
            state['manual_view'] = manual_full_rotor_view()
            play_start('manual_resume')
        elif stage == 8:
            if not observe_play('manual_resume', state['manual_view'], manual=True):
                return .2
            capture('manual-full-rotor-after-resume', full=True)
            state['checks'].append('Pause, detail focus, manual orbit/zoom, and user resume release tracking and preserve all right navigation')
        elif stage == 9:
            assert validation.motion_signature() == state['manual_resume']['pause']
            for frame in (901, 52):
                state.setdefault('native_seeks', []).append(validation.frame(frame))
                unchanged(state['manual_view'])
                assert not validation.scene.get(validation.same.DETAIL)
            operator('split_recon_step', direction=1)
            assert validation.scene.frame_current == 55
            state['sample_step'] = validation.frame(55, seek=False)
            unchanged(state['manual_view'])
            assert not validation.scene.get(validation.same.DETAIL)
            state['checks'].append('Native seek and actual-sample step retain the manual right view and shared clock')
            operator('split_recon_enter', mode='CURRENT')
        elif stage == 10:
            pair()
            unchanged(state['manual_view'])
            assert not validation.scene.get(validation.same.DETAIL)
            validation.frame(55, seek=False)
            capture('current-restored-free-view', full=True)
            state['checks'].append('CURRENT layout restore preserves the manual free view')
            state['saved'] = str(helpers.save_path(OUT))
            assert bpy.ops.wm.save_as_mainfile(filepath=state['saved']) == {'FINISHED'}
        elif stage == 11:
            validation = None
            state['stage'] = 12
            bpy.ops.wm.open_mainfile(filepath=state['saved'], load_ui=True, use_scripts=False)
            return 3.
        elif stage == 12:
            validation = helpers.Validation()
            assert validation.same.information(validation.scene) is not None, 'Native load did not restore same-source data'
            validation.activate()
            state['stage'] = 13
            return .2
        elif stage == 13:
            pair()
            assert not validation.scene.get(validation.same.DETAIL)
            unchanged(state['manual_view'])
            state['reopen_clock'] = validation.frame(55, seek=False)
            capture('native-reopen-free-view', full=True)
            state['checks'].append('Additive native save/reopen preserves all manual navigation and both replay sources')
            report('PASS')
            print('MAPPO_SAME_SOURCE_NAVIGATION_WINDOW_PASS', flush=True)
            if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
                bpy.ops.wm.quit_blender()
            return None
        state['stage'] += 1
        return .5
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        report('FAIL', error)
        if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
            bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=3., persistent=True)

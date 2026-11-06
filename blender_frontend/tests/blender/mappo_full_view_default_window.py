"""Verify the default full-rotor same-source view in a dedicated visible window.

Open an unchanged COPY of the installed formal geometry example before
--python this_file. Use the daily enabled bl_ext installation, a fresh output
directory in WFRL_TEST_OUTPUT, and no --background. WFRL_ADDON_MODULE and
WFRL_EXPECTED_ADDON_ROOT identify an alternative enabled installation.

Real native playback lasts three seconds. Screen screenshots and JSON record
the default framing, fixed view, moving saved geometry, hidden right sidebar,
explicit detail selection, CURRENT restoration, and exit/return behavior.
WFRL_QUIT_AFTER_TEST defaults to 1 and quits only this dedicated process.
No blend file is saved, source data rewritten, render or solver started.
This verifies software presentation, not reconstruction accuracy or draw rate.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import time
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[3]
INTERVAL = .1


def rotation_angle(baseline, current):
    """Shortest angle in radians between two quaternion orientations."""
    baseline_norm, current_norm = math.hypot(*baseline), math.hypot(*current)
    if not all(math.isfinite(norm) and norm > 0 for norm in (baseline_norm, current_norm)):
        return None
    baseline = tuple(value / baseline_norm for value in baseline)
    current = tuple(value / current_norm for value in current)
    chord = min(math.dist(baseline, current), math.dist(baseline, (-value for value in current)))
    return 4. * math.asin(min(1., chord / 2.))


def navigation_context(core, areas, index):
    window = bpy.context.window
    key = core._key(window)
    job = core._JOBS.get(key)
    session = core._SESSIONS.get(key, {})
    area = areas[index]
    viewport = core._region(area)
    return dict(side=('left', 'right')[index], frame=window.scene.frame_current,
        playing=window.screen.is_animation_playing, area_size=[area.width, area.height],
        region_size=[viewport.width, viewport.height] if viewport else None,
        layout_signature=core._signature(areas),
        fit_job=dict(status=window.scene.get('split_review_status'),
            active={field: job[field] for field in
                    ('stage', 'explicit', 'settle', 'deadline', 'signature', 'deferred_signature')
                    if field in job} if job else None,
            pending=session.get('changed') is not None, deferred=bool(session.get('fit_deferred')),
            signature=session.get('signature'), changed_at=session.get('changed')))


def assert_navigation(condition, report_state, core, areas, index, field, baseline, current, **details):
    if not condition:
        report_state['navigation_failure'] = dict(navigation_context(core, areas, index),
            field=field, baseline=baseline, current=current, **details)
    assert condition, report_state['navigation_failure']


def pair():
    areas = split._pair(bpy.context.scene, bpy.context.window.screen)
    assert areas, 'Expected the native comparison views'
    return areas


def region(area):
    return next(item for item in area.regions if item.type == 'WINDOW')


def operator(name, **kwargs):
    views = sorted((area for area in bpy.context.window.screen.areas
                    if area.type == 'VIEW_3D'), key=lambda area: area.x)
    assert views, 'No visible VIEW_3D'
    with bpy.context.temp_override(window=bpy.context.window, area=views[0], region=region(views[0])):
        result = getattr(bpy.ops.wfrl, name)(**kwargs)
    assert result == {'FINISHED'}, (name, result)


def animation(play):
    area = pair()[1]
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region(area)):
        if play:
            assert not bpy.context.screen.is_animation_playing
            result = bpy.ops.screen.animation_play()
        elif bpy.context.screen.is_animation_playing:
            result = bpy.ops.screen.animation_cancel(restore_frame=False)
        else:
            return
    # Some Blender builds return PASS_THROUGH after cancellation has succeeded.
    assert result in ({'FINISHED'}, {'PASS_THROUGH'}), result
    assert bpy.context.window.screen.is_animation_playing is play, (play, result)


def rounded(value):
    return [round(float(axis), 6) for axis in value]


def pose(obj, graph):
    obj = obj.evaluated_get(graph)
    vertices = obj.data.vertices
    assert vertices
    return [rounded(obj.matrix_world @ vertices[index].co)
            for index in (0, len(vertices) // 2, len(vertices) - 1)]


def sample():
    scene = bpy.context.scene
    left, right = pair()
    space, rv = right.spaces.active, right.spaces.active.region_3d
    graph = bpy.context.evaluated_depsgraph_get()
    bounds = split._project(right, split._points(scene, right, reconstruction=True))
    viewport = region(right)
    row = dict(stage=state['stage'], elapsed_s=time.monotonic() - state['started'],
               frame=int(scene.frame_current), sample=scene.get(same.INDEX),
               playing=bool(bpy.context.window.screen.is_animation_playing),
               status=scene.get('split_review_status'), detail=bool(scene.get(same.DETAIL)),
               perspective=rv.view_perspective, sidebar=bool(space.show_region_ui),
               sidebar_width=max((item.width for item in right.regions if item.type == 'UI'), default=0),
               viewport=[viewport.width, viewport.height], full_rotor_bounds=list(bounds) if bounds else None,
               view=dict(rotation=rounded(rv.view_rotation), location=rounded(rv.view_location),
                         distance=round(float(rv.view_distance), 6),
                         camera_zoom=round(float(rv.view_camera_zoom), 6),
                         camera_offset=rounded(rv.view_camera_offset),
                         camera_matrix=[rounded(row) for row in space.camera.matrix_world]),
               navigation_context=navigation_context(split, (left, right), 1),
               left_pose=[pose(scene.objects[f'WFRL.Turbine.T1.Blade{bid}'], graph) for bid in (1, 2, 3)],
               right_pose=[pose(obj, graph) for obj in same.objects(scene)],
               texture_information=same.information(scene))
    state['samples'].append(row)
    return row


def capture(name):
    row = sample()
    path = OUT / (name + '.png')
    bpy.ops.screen.screenshot(filepath=str(path))
    state['captures'].append(dict(name=name, screenshot=str(path), observation=row))
    print('MAPPO_FULL_VIEW_WINDOW_READY', name, flush=True)
    return row


def check(condition, message):
    state['checks'].append(dict(passed=bool(condition), message=message))
    assert condition, message


def check_samples(rows, baseline, message, *, view=False):
    for row in rows:
        values = row['view'] if view else row
        for field, expected in baseline.items():
            actual = values[field]
            details = {}
            if field == 'rotation':
                angle = rotation_angle(expected, actual)
                matches = angle == 0.
                details.update(angle_rad=angle, tolerance_rad=0.)
            elif field == 'sidebar_width':
                matches = actual <= expected
            else:
                matches = actual == expected
            if not matches:
                state['navigation_failure'] = dict(row['navigation_context'], field=field,
                    baseline=expected, current=actual, **details)
                check(False, message)
    check(True, message)


def assert_view(row, closeup):
    # Surface inspection positions one stationary close-up; it never enables tracking.
    check(row['status'] == 'READY' and row['detail'] is False,
          state['stage'] + ': READY without surface tracking')
    check_samples((row,), dict(perspective='ORTHO' if closeup else 'CAMERA'),
                  state['stage'] + ': requested native view perspective')
    check_samples((row,), dict(sidebar=False, sidebar_width=8),
                  state['stage'] + ': right sidebar hidden')
    if not closeup:
        bounds, (width, height) = row['full_rotor_bounds'], row['viewport']
        check(bounds is not None and 0 <= bounds[0] < bounds[2] <= width
              and 0 <= bounds[1] < bounds[3] <= height,
              state['stage'] + ': saved full-rotor envelope lies inside the visible viewport')


def wait_for(stage):
    state['stage'] = stage
    state.pop('settle_until', None)


def ready():
    scene = bpy.context.scene
    return scene.get('split_review_status') == 'READY' and not split._JOBS and cameras._LAYOUT_JOB is None


def finish(error=None):
    if split is not None and bpy.context.window.screen.is_animation_playing:
        animation(False)
    data = {key: value for key, value in state.items()
            if key not in {'started', 'deadline', 'settle_until', 'until'}}
    data.update(status='FAIL' if error else 'PASS', error=error, addon_module=MODULE,
                addon_file=getattr(addon, '__file__', None), blender=bpy.app.version_string,
                blend=bpy.data.filepath, actual_screen_screenshots=True,
                verification='VISIBLE_WINDOW_NATIVE_PLAYBACK_FULL_VIEW_DEFAULT',
                software_presentation_only=True, reconstruction_accuracy_accepted=False)
    REPORT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('MAPPO_FULL_VIEW_WINDOW_' + data['status'], str(REPORT), flush=True)
    if os.environ.get('WFRL_QUIT_AFTER_TEST', '1') == '1':
        bpy.ops.wm.quit_blender()
    return None


def tick():
    global addon, split, same, cameras, farm
    try:
        assert not bpy.app.background and bpy.context.window, 'Use a dedicated visible Blender process'
        assert time.monotonic() < state['deadline'], ('timeout', state['stage'])
        scene = bpy.context.scene
        stage = state['stage']
        if stage == 'initialize':
            addon = importlib.import_module(MODULE)
            root = Path(addon.__file__).resolve().parent
            expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
            if expected:
                assert root == Path(expected).expanduser().resolve(), (root, expected)
            split = importlib.import_module(MODULE + '.split_reconstruction')
            same = importlib.import_module(MODULE + '.split_mappo_texture')
            cameras = importlib.import_module(MODULE + '.cameras')
            farm = importlib.import_module(MODULE + '.farm_flex')
            assert hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'), 'Enable the requested installed extension'
            canonical = root / 'assets/examples/mappo_reconstruction_split.blend'
            source_canonical = ROOT / 'blender_frontend/wfrl_blender/assets/examples/mappo_reconstruction_split.blend'
            current = Path(bpy.data.filepath).resolve()
            assert current.is_file() and current not in (canonical.resolve(), source_canonical.resolve())
            input_hash = hashlib.sha256(current.read_bytes()).hexdigest()
            assert input_hash == hashlib.sha256(canonical.read_bytes()).hexdigest(), 'Use an unchanged formal geometry copy'
            assert farm.is_active(scene) and not same.active(scene)
            assert scene.get('split_reconstruction_samples') == 601
            state.update(actual_addon_root=str(root), input_geometry_sha256=input_hash)
            scene.frame_set(52)
            operator('split_recon_same_source')
            wait_for('wait-default-full')
            return INTERVAL
        assert not str(scene.get('split_review_status', '')).startswith('ERROR'), scene.get('split_review_status')
        if stage == 'wait-exit-complete':
            views = [area for area in bpy.context.window.screen.areas if area.type == 'VIEW_3D']
            if (scene.get('split_review_status') != 'OFF' or split._JOBS
                    or cameras._LAYOUT_JOB is not None or len(views) != 1):
                return INTERVAL
            check(not split.enabled(scene), 'Completed native exit leaves the comparison mode')
            check(not scene.get(same.DETAIL), 'Completed exit preserves the full-view selection')
            state['exit_completed'] = dict(status=scene.get('split_review_status'),
                                           visible_viewports=len(views), detail=bool(scene.get(same.DETAIL)))
            operator('split_recon_enter', mode='CURRENT')
            wait_for('wait-return-full')
            return INTERVAL
        if stage.startswith('wait-'):
            if not ready():
                state.pop('settle_until', None)
                return INTERVAL
            now = time.monotonic()
            if 'settle_until' not in state:
                state['settle_until'] = now + .6
            if now < state['settle_until']:
                return INTERVAL
            closeup = stage in {'wait-explicit-detail', 'wait-current-detail'}
            row = capture(stage)
            assert_view(row, closeup)
            if stage == 'wait-default-full':
                state['play_start'] = row
                state['play_sample_start'] = len(state['samples'])
                state['stage'] = 'playing-full'
                state['until'] = time.monotonic() + 3.
                animation(True)
            elif stage == 'wait-explicit-detail':
                operator('split_recon_enter', mode='CURRENT')
                wait_for('wait-current-detail')
            elif stage == 'wait-current-detail':
                operator('split_recon_same_source')
                wait_for('wait-explicit-source-full')
            elif stage == 'wait-explicit-source-full':
                operator('split_recon_enter', mode='CURRENT')
                wait_for('wait-current-full')
            elif stage == 'wait-current-full':
                operator('split_recon_exit')
                wait_for('wait-exit-complete')
            else:
                check(stage == 'wait-return-full', 'Expected final return stage')
                return finish()
            return INTERVAL
        row = sample()
        if stage == 'playing-full':
            assert row['playing'], 'Native playback stopped early'
            if time.monotonic() < state['until']:
                return INTERVAL
            rows = state['samples'][state['play_sample_start']:]
            start = state['play_start']
            state['play_end'] = row
            check_samples(rows, start['view'], 'Full-view camera and navigation stay fixed during playback', view=True)
            check_samples(rows, dict(detail=False, perspective='CAMERA', sidebar=False, sidebar_width=8),
                          'All sampled playback states retain full view and a hidden right sidebar')
            check(row['frame'] > start['frame'] and row['sample'] > start['sample']
                  and row['left_pose'] != start['left_pose'] and row['right_pose'] != start['right_pose'],
                  'Native playback advances both MAPPO blades and held same-source texture geometry')
            capture('full-during-native-play')
            animation(False)
            state['pause_start'] = sample()
            state['stage'] = 'paused-full'
            state['until'] = time.monotonic() + .6
        elif stage == 'paused-full':
            if time.monotonic() < state['until']:
                return INTERVAL
            start = state['pause_start']
            check_samples((row,), start['view'], 'Native pause holds the view', view=True)
            check(not row['playing'] and all(row[key] == start[key]
                  for key in ('frame', 'sample', 'left_pose', 'right_pose')),
                  'Native pause holds both sides')
            operator('split_recon_inspect', point='CURRENT')
            wait_for('wait-explicit-detail')
        else:
            raise AssertionError(('unexpected stage', stage))
        return INTERVAL
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        return finish(error)


if __name__ == '__main__':
    OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT = OUT / 'full-view-window-validation.json'
    assert not REPORT.exists(), 'Use a new WFRL_TEST_OUTPUT directory'
    MODULE = os.environ.get('WFRL_ADDON_MODULE', 'bl_ext.user_default.wfrl_blender')
    state = dict(stage='initialize', started=time.monotonic(),
                 deadline=time.monotonic() + 180, samples=[], captures=[], checks=[])
    addon = split = same = cameras = farm = None
    bpy.app.timers.register(tick, first_interval=3.)

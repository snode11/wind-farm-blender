"""Sample the native sidebar during real same-source texture playback.

Open a COPY of assets/examples/mappo_reconstruction_split.blend using Blender's
CLI before --python this_file; do not use --background. Use a dedicated process,
an isolated config, and a new WFRL_TEST_OUTPUT directory. The default module is
the daily bl_ext.user_default.wfrl_blender installation. WFRL_ADDON_MODULE and
WFRL_EXPECTED_ADDON_ROOT can identify another installation or explicit source.

The test samples at 0.03 s requested intervals (actual gaps are recorded), plays
each case for 5 s, and saves screen.screenshot PNGs, JSONL observations, and a
complete JSON report. It also replaces the existing same-source frame callback
in place with a right-area context wrapper, preserving its call frequency. This
reproduces playing with the mouse over the right view, where Blender's animated
RNA sidebar setter is otherwise easy to miss from a context-free CLI timer.
Both ordinary and forced right-area callback contexts are tested and reported.
The wrapper is removed at completion; no product function is monkeypatched.

WFRL_SIDEBAR_EXPECT_OLD=1 runs only the first forced-context playback, reports
OBSERVED with measured failures, and does not require the unfixed build to pass.
WFRL_SIDEBAR_SAMPLE_SECONDS may be 0.025-0.05; WFRL_SIDEBAR_PLAY_SECONDS may be
4-6. WFRL_QUIT_AFTER_TEST=1 quits only this dedicated process. By default it is
left visible and paused. No blend is saved, render/solver is started, or original
geometry and texture data are rewritten. This is a UI regression, not physical
reconstruction acceptance or a display-rate guarantee.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / 'sidebar-window-validation.json'
assert not REPORT.exists(), 'Use a new WFRL_TEST_OUTPUT directory'
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'bl_ext.user_default.wfrl_blender')
BASELINE = os.environ.get('WFRL_SIDEBAR_EXPECT_OLD') == '1'
INTERVAL = float(os.environ.get('WFRL_SIDEBAR_SAMPLE_SECONDS', '.03'))
PLAY_SECONDS = float(os.environ.get('WFRL_SIDEBAR_PLAY_SECONDS', '5'))
assert .025 <= INTERVAL <= .05, INTERVAL
assert 4 <= PLAY_SECONDS <= 6, PLAY_SECONDS
SMALL_UI_WIDTH = 8
OPEN_UI_WIDTH = 40
state = dict(stage='initialize', started=time.monotonic(), deadline=time.monotonic() + 300,
             samples=[], frame_callbacks=[], phases=[], captures=[], errors=[], checks=[],
             baseline_observation_only=BASELINE, forced_right_callback_context=False)
addon = split = same = cameras = farm = None
callback_original = callback_wrapper = None
callback_position = None
sample_log = (OUT / 'sidebar-samples.jsonl').open('x', encoding='utf-8')
callback_log = (OUT / 'sidebar-frame-callbacks.jsonl').open('x', encoding='utf-8')


def pair():
    areas = split._pair(bpy.context.scene, bpy.context.window.screen)
    assert areas is not None, 'The current window must contain the two comparison views'
    return areas


def region(area):
    return next(item for item in area.regions if item.type == 'WINDOW')


def ui_width(area):
    return max((item.width for item in area.regions if item.type == 'UI'), default=0)


def ready():
    scene = bpy.context.scene
    return (scene.get('split_review_status') == 'READY' and not split._JOBS
            and cameras._LAYOUT_JOB is None and split._pair(scene, bpy.context.window.screen))


def operator(name, **kwargs):
    areas = sorted((area for area in bpy.context.window.screen.areas
                    if area.type == 'VIEW_3D'), key=lambda area: area.x)
    assert areas, 'No visible VIEW_3D in the copied input scene'
    with bpy.context.temp_override(window=bpy.context.window, area=areas[0], region=region(areas[0])):
        result = getattr(bpy.ops.wfrl, name)(**kwargs)
    assert result == {'FINISHED'}, (name, result)


def animation(play):
    area = pair()[1]  # Real animation operator uses the right-view context.
    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region(area)):
        if play:
            assert not bpy.context.screen.is_animation_playing
            result = bpy.ops.screen.animation_play()
        elif bpy.context.screen.is_animation_playing:
            result = bpy.ops.screen.animation_cancel(restore_frame=False)
        else:
            return
    # Blender's cancel operator may return PASS_THROUGH after stopping playback.
    assert result in ({'FINISHED'}, {'PASS_THROUGH'}), ('animation', play, result)
    assert bpy.context.window.screen.is_animation_playing is play, ('animation state', play, result)


def points(obj, graph=None):
    """Three native vertices provide a cheap pose signature, not a model audit."""
    value = obj.evaluated_get(graph) if graph is not None else obj
    vertices = value.data.vertices
    assert vertices
    indices = (0, len(vertices) // 2, len(vertices) - 1)
    return [[round(float(axis), 6) for axis in value.matrix_world @ vertices[index].co]
            for index in indices]


def sample():
    scene = bpy.context.scene
    left, right = pair()
    space = right.spaces.active
    info = same.information(scene)
    graph = bpy.context.evaluated_depsgraph_get()
    texture = same.blades(scene)
    row = dict(elapsed_s=time.monotonic() - state['started'], stage=state['stage'],
               playing=bpy.context.window.screen.is_animation_playing,
               forced_right_callback_context=callback_wrapper is not None,
               show_region_ui=bool(space.show_region_ui), ui_width=ui_width(right),
               left_ui_width=ui_width(left), left_show_region_ui=bool(left.spaces.active.show_region_ui),
               right_area_size=[right.width, right.height],
               perspective=space.region_3d.view_perspective, detail=bool(scene.get(same.DETAIL)),
               split_status=scene.get('split_review_status'), frame=int(scene.frame_current),
               left_frame=int(scene.frame_current), right_timeline_frame=int(scene.frame_current),
               right_sample=scene.get(same.INDEX), texture_information=info,
               left_simulation_time_s=scene.get('wfrl_clearance_time_s'),
               right_shape_eval_times=[float(obj.data.shape_keys.eval_time) for obj in texture],
               left_pose=[points(scene.objects[f'WFRL.Turbine.T1.Blade{bid}']) for bid in (1, 2, 3)],
               right_pose=[points(obj, graph) for obj in texture])
    state['samples'].append(row)
    sample_log.write(json.dumps(row, ensure_ascii=False) + '\n')
    sample_log.flush()
    return row


def capture(name):
    row = sample()
    path = OUT / (name + '.png')
    bpy.ops.screen.screenshot(filepath=str(path))
    state['captures'].append(dict(name=name, screenshot=str(path), sample=row))
    print('SPLIT_SIDEBAR_WINDOW_READY', name, flush=True)


def check(condition, message, details=None):
    entry = dict(passed=bool(condition), message=message, details=details)
    state['checks'].append(entry)
    if not condition:
        state['errors'].append(entry)


def install_right_callback():
    global callback_original, callback_wrapper, callback_position
    handlers = bpy.app.handlers.frame_change_post
    matches = [index for index, fn in enumerate(handlers) if fn is same.frame_changed]
    assert len(matches) == 1, ('Expected exactly one installed texture callback', matches)
    callback_position = matches[0]
    callback_original = handlers[callback_position]

    def right_context(scene, depsgraph=None):
        window = next((window for window in bpy.context.window_manager.windows
                       if window.scene == scene and split._pair(scene, window.screen)), None)
        areas = split._pair(scene, window.screen) if window else None
        if areas is None:
            return callback_original(scene, depsgraph)
        area = areas[1]
        before = dict(show_region_ui=bool(area.spaces.active.show_region_ui), ui_width=ui_width(area))
        with bpy.context.temp_override(window=window, area=area, region=region(area)):
            callback_original(scene, depsgraph)
        event = dict(elapsed_s=time.monotonic() - state['started'], frame=int(scene.frame_current),
                     stage=state['stage'], before=before,
                     after=dict(show_region_ui=bool(area.spaces.active.show_region_ui), ui_width=ui_width(area)),
                     context_area='RIGHT_VIEW_3D')
        state['frame_callbacks'].append(event)
        callback_log.write(json.dumps(event, ensure_ascii=False) + '\n')
        callback_log.flush()

    callback_wrapper = right_context
    handlers[callback_position] = callback_wrapper
    state['forced_right_callback_context'] = True


def restore_callback():
    global callback_wrapper
    if callback_wrapper is not None:
        handlers = bpy.app.handlers.frame_change_post
        if callback_wrapper in handlers:
            handlers[handlers.index(callback_wrapper)] = callback_original
        callback_wrapper = None


def begin_play(name, expected_sidebar):
    state['stage'] = name
    state['phase_start'] = len(state['samples'])
    state['phase_expected_sidebar'] = expected_sidebar
    state['phase_until'] = time.monotonic() + PLAY_SECONDS
    state['phase_mid_capture'] = False
    capture(name + '-start')
    animation(True)


def end_play():
    rows = state['samples'][state['phase_start']:]
    first, last = rows[0], rows[-1]
    transitions = [dict(elapsed_s=b['elapsed_s'], before=a['show_region_ui'], after=b['show_region_ui'])
                   for a, b in zip(rows, rows[1:]) if a['show_region_ui'] != b['show_region_ui']]
    width_transitions = [dict(elapsed_s=b['elapsed_s'], before=a['ui_width'], after=b['ui_width'])
                        for a, b in zip(rows, rows[1:])
                        if (a['ui_width'] > SMALL_UI_WIDTH) != (b['ui_width'] > SMALL_UI_WIDTH)]
    expected = state['phase_expected_sidebar']
    wrong_flag = [index for index, row in enumerate(rows) if row['show_region_ui'] != expected]
    wrong_width = [index for index, row in enumerate(rows)
                   if (row['ui_width'] <= OPEN_UI_WIDTH if expected else row['ui_width'] > SMALL_UI_WIDTH)]
    phase = dict(name=state['stage'], expected_show_region_ui=expected, observations=len(rows),
                 elapsed_s=last['elapsed_s'] - first['elapsed_s'],
                 requested_interval_s=INTERVAL,
                 largest_observed_gap_s=max((b['elapsed_s'] - a['elapsed_s'] for a, b in zip(rows, rows[1:])), default=0),
                 show_region_ui_transitions=transitions, ui_visible_width_transitions=width_transitions,
                 wrong_sidebar_flag_sample_indices=wrong_flag, wrong_sidebar_width_sample_indices=wrong_width,
                 frame_range=[first['frame'], last['frame']],
                 sample_range=[first['right_sample'], last['right_sample']],
                 perspectives=sorted({row['perspective'] for row in rows}),
                 split_statuses=sorted({str(row['split_status']) for row in rows}),
                 left_pose_moved=first['left_pose'] != last['left_pose'],
                 right_pose_moved=first['right_pose'] != last['right_pose'])
    state['phases'].append(phase)
    check(not wrong_flag and not wrong_width and not transitions and not width_transitions,
          phase['name'] + ': sidebar remains in the requested visible state', phase)
    check(last['frame'] > first['frame'] and last['right_sample'] > first['right_sample']
          and phase['left_pose_moved'] and phase['right_pose_moved'],
          phase['name'] + ': both native sides and the held sample advance', phase)
    check(all(row['detail'] and row['perspective'] == 'ORTHO' for row in rows),
          phase['name'] + ': surface detail tracking stays active')
    animation(False)
    capture(state['stage'] + '-paused')
    state['pause_row'] = state['samples'][-1]
    state['phase_until'] = time.monotonic() + .8


def paused_stable(row):
    start = state['pause_row']
    check(not row['playing'] and all(row[key] == start[key]
          for key in ('frame', 'right_sample', 'left_pose', 'right_pose')),
          state['stage'] + ': native pause holds both sides')


def write_report(status, error=None):
    data = {key: value for key, value in state.items()
            if key not in {'deadline', 'phase_until', 'started', 'phase_start', 'phase_mid_capture', 'pause_row'}}
    data.update(status=status, error=error, addon_module=MODULE,
                addon_file=getattr(addon, '__file__', None), blender=bpy.app.version_string,
                blend=bpy.data.filepath, requested_sample_interval_s=INTERVAL,
                play_duration_s=PLAY_SECONDS, actual_screen_screenshots=True,
                callback_wrapper_restored=callback_wrapper is None,
                original_callback_frequency_preserved=True,
                source_regenerated=False, reconstruction_accuracy_accepted=False,
                visible_window_left_open=os.environ.get('WFRL_QUIT_AFTER_TEST') != '1')
    REPORT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def finish(error=None):
    try:
        if split is not None and split._pair(bpy.context.scene, bpy.context.window.screen):
            animation(False)
            capture('final-visible-window')
    except Exception:
        state['errors'].append(dict(message='Could not capture final window', details=traceback.format_exc()))
    restore_callback()
    status = 'FAIL' if error else ('OBSERVED' if BASELINE else ('FAIL' if state['errors'] else 'PASS'))
    write_report(status, error)
    sample_log.close()
    callback_log.close()
    print('SPLIT_TEXTURE_SIDEBAR_WINDOW_' + status, str(REPORT), flush=True)
    if os.environ.get('WFRL_QUIT_AFTER_TEST') == '1':
        bpy.ops.wm.quit_blender()
    return None


def tick():
    global addon, split, same, cameras, farm
    try:
        assert not bpy.app.background and bpy.context.window is not None, 'Use a dedicated visible Blender process'
        assert time.monotonic() < state['deadline'], ('timeout', state['stage'])
        stage = state['stage']
        if stage == 'initialize':
            if MODULE == 'wfrl_blender':
                sys.path.insert(0, str(ROOT / 'blender_frontend'))
            addon = importlib.import_module(MODULE)
            root = Path(addon.__file__).resolve().parent
            expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
            if expected:
                assert root == Path(expected).expanduser().resolve(), (root, expected)
            split = importlib.import_module(MODULE + '.split_reconstruction')
            same = importlib.import_module(MODULE + '.split_mappo_texture')
            cameras = importlib.import_module(MODULE + '.cameras')
            farm = importlib.import_module(MODULE + '.farm_flex')
            assert hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'), 'Enable the requested extension before this script'
            canonical = root / 'assets/examples/mappo_reconstruction_split.blend'
            current = Path(bpy.data.filepath).resolve()
            assert current.is_file() and current != canonical, 'Open a copied formal geometry scene through the CLI'
            assert current != (ROOT / 'blender_frontend/wfrl_blender/assets/examples/mappo_reconstruction_split.blend').resolve()
            input_hash = hashlib.sha256(current.read_bytes()).hexdigest()
            canonical_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()
            assert input_hash == canonical_hash, 'The input must be an unchanged copy of the formal geometry blend'
            state['input_geometry_sha256'] = input_hash
            state['canonical_geometry_sha256'] = canonical_hash
            state['actual_addon_root'] = str(root)
            scene = bpy.context.scene
            assert farm.is_active(scene), scene.get('wfrl_clearance_status')
            assert scene.get('split_reconstruction_review') and scene.get('split_reconstruction_samples') == 601
            for bid in (1, 2, 3):
                assert len(scene.objects[f'SplitRecon.B{bid}'].data.shape_keys.key_blocks) == 601
            assert not same.active(scene), 'Begin with the formal original geometry scene'
            if bpy.context.window.screen.is_animation_playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            scene.frame_set(52)
            operator('split_recon_same_source')
            state['stage'] = 'wait-full-entry'
            write_report('RUNNING')
            return INTERVAL
        scene = bpy.context.scene
        assert not str(scene.get('split_review_status', '')).startswith('ERROR'), scene.get('split_review_status')
        if stage.startswith('wait-'):
            if not ready():
                return INTERVAL
            state.setdefault('settle_until', time.monotonic() + .8)
            sample()
            if time.monotonic() < state['settle_until']:
                return INTERVAL
            del state['settle_until']
            if stage == 'wait-full-entry':
                if not BASELINE:
                    check(not scene.get(same.DETAIL) and pair()[1].spaces.active.region_3d.view_perspective == 'CAMERA',
                          'The same-source entry starts in full rotor view')
                operator('split_recon_inspect', point='CURRENT')
                state['stage'] = 'wait-detail-entry'
            elif stage == 'wait-detail-entry':
                capture('same-source-detail-entry')
                if BASELINE:
                    install_right_callback()
                    begin_play('play-hidden-right-context', False)
                else:
                    begin_play('play-hidden-normal-context', False)
            elif stage == 'wait-manual-open':
                capture('manual-sidebar-open-before-play')
                begin_play('play-manually-open-right-context', True)
            elif stage in {'wait-fit-open', 'wait-current-hidden', 'wait-focus-hidden', 'wait-fit-hidden'}:
                row = state['samples'][-1]
                expected_open = stage == 'wait-fit-open'
                check(row['show_region_ui'] == expected_open and
                      (row['ui_width'] > OPEN_UI_WIDTH if expected_open else row['ui_width'] <= SMALL_UI_WIDTH),
                      stage + ': explicit layout action preserves or hides the right sidebar as intended', row)
                capture(stage)
                if stage == 'wait-fit-open':
                    operator('split_recon_enter', mode='CURRENT')
                    state['stage'] = 'wait-current-hidden'
                elif stage == 'wait-current-hidden':
                    pair()[1].spaces.active.show_region_ui = True
                    pair()[1].tag_redraw()
                    operator('split_recon_inspect', point='CURRENT')
                    state['stage'] = 'wait-focus-hidden'
                elif stage == 'wait-focus-hidden':
                    operator('split_recon_fit')
                    state['stage'] = 'wait-fit-hidden'
                else:
                    return finish()
            return INTERVAL
        row = sample()
        if stage.startswith('play-'):
            check(row['playing'], stage + ': native animation is running') if not row['playing'] else None
            if not state['phase_mid_capture'] and time.monotonic() >= state['phase_until'] - PLAY_SECONDS / 2:
                capture(stage + '-midplay')
                state['phase_mid_capture'] = True
            if time.monotonic() < state['phase_until']:
                return INTERVAL
            end_play()
            state['stage'] = stage.replace('play-', 'pause-', 1)
            write_report('RUNNING')
            return INTERVAL
        if stage.startswith('pause-'):
            if time.monotonic() < state['phase_until']:
                return INTERVAL
            paused_stable(row)
            if BASELINE:
                return finish()
            if stage == 'pause-hidden-normal-context':
                install_right_callback()
                begin_play('play-hidden-right-context', False)
            elif stage == 'pause-hidden-right-context':
                begin_play('play-resumed-hidden-right-context', False)
            elif stage == 'pause-resumed-hidden-right-context':
                pair()[1].spaces.active.show_region_ui = True
                pair()[1].tag_redraw()
                state['stage'] = 'wait-manual-open'
            elif stage == 'pause-manually-open-right-context':
                operator('split_recon_fit')
                state['stage'] = 'wait-fit-open'
            return INTERVAL
        raise AssertionError(('unexpected stage', stage))
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        return finish(error)


bpy.app.timers.register(tick, first_interval=3.)

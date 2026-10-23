"""Native camera playback ownership in independent Blender window groups.

Run source with --factory-startup, or an installed module in its isolated profile,
without --background, and a new
WFRL_TEST_OUTPUT directory. WFRL_ADDON_MODULE defaults to workspace source
wfrl_blender. Installed bl_ext modules require WFRL_EXPECTED_ADDON_ROOT.
WFRL_NATIVE_PLAYBACK_MANUAL_SPACE=1 waits for external keyboard inspection
between manual-space-ready.json and the caller's manual-space-done marker.
Uses saved demo results and scripted operators. Optional external Space events
are recorded separately; no rendering-performance or scientific acceptance.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=False)
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    expected = Path(os.environ.get('WFRL_EXPECTED_ADDON_ROOT', ROOT / 'blender_frontend' / 'wfrl_blender'))
else:
    assert MODULE.startswith('bl_ext.'), MODULE
    expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT'])
addon = importlib.import_module(MODULE)
native = importlib.import_module(MODULE + '.native_camera_views')
playback = importlib.import_module(MODULE + '.playback')
assert Path(addon.__file__).resolve().parent == expected.expanduser().resolve(), addon.__file__
assert all(Path(module.__file__).resolve().parent == Path(addon.__file__).resolve().parent
           for module in (native, playback)), 'Mixed module roots'

RESULT = {
    'status': 'RUNNING', 'module': MODULE, 'addon_file': addon.__file__,
    'blender': bpy.app.version_string, 'build_hash': bpy.app.build_hash.decode(errors='replace'),
    'method': 'Scripted visible-window operators and a stale-execute guard check; optional external Space events recorded separately.',
    'source_sha256': {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                      for module in (native, playback)},
    'checks': {}, 'observations': {},
}
STATE = {'step': 'setup', 'deadline': time.monotonic() + 180.0}


def save():
    (OUT / 'result.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(name, condition, **details):
    RESULT['checks'][name] = {'status': 'PASS' if condition else 'FAIL', **details}
    save()
    assert condition, (name, details)


def pointer(value):
    try:
        return value.as_pointer() if value is not None else None
    except ReferenceError:
        return None


def observe(name):
    STATE['step'] = name
    RESULT['observations'][name] = {
        'owner_scene': playback.owner_scene().name if playback.owner_scene() else None,
        'owner_window': pointer(playback.owner_window()), 'any_playing': playback.any_playing(),
        'windows': [{'pointer': pointer(w), 'parent': pointer(w.parent), 'scene': w.scene.name,
                     'frame': w.scene.frame_current, 'global_animation_flag': bool(w.screen.is_animation_playing)}
                    for w in bpy.context.window_manager.windows],
    }
    save()


def override(window, area=None):
    area = area or next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return {'window': window, 'area': area, 'region': next(r for r in area.regions if r.type == 'WINDOW')}


def screenshot(name, window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        assert bpy.ops.screen.screenshot(filepath=str(OUT / name)) == {'FINISHED'}


def persist_space_observer():
    state = STATE['space_observer_state']
    (OUT / 'manual-space-events.json').write_text(json.dumps({
        'A_window': state['window_id'], 'closed': state['closed'],
        'events': RESULT['manual_space_events'],
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def release_space_observer_timer():
    state = STATE['space_observer_state']
    if state['timer'] is not None:
        state['wm'].event_timer_remove(state['timer'])
        state['timer'] = None


class WFRL_TEST_OT_SpaceObserver(bpy.types.Operator):
    """Observe window delivery without consuming the inspected key event."""
    bl_idname = 'wm.wfrl_test_space_observer'
    bl_label = 'Test Space Event Observer'
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        wm = context.window_manager
        STATE['space_observer_state'] = {
            'window_id': pointer(context.window), 'wm': wm,
            'closed': False, 'stop_requested': False,
            'timer': wm.event_timer_add(.1, window=context.window),
        }
        RESULT['manual_space_events'] = []
        persist_space_observer()
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def finish(self):
        release_space_observer_timer()
        STATE['space_observer_state']['closed'] = True
        persist_space_observer()

    def modal(self, context, event):
        state = STATE['space_observer_state']
        if state['stop_requested']:
            self.finish()
            return {'FINISHED', 'PASS_THROUGH'}
        if (pointer(context.window) == state['window_id'] and event.type == 'SPACE'
                and event.value in {'PRESS', 'RELEASE'}):
            RESULT['manual_space_events'].append({
                'type': event.type, 'value': event.value, 'window': pointer(context.window),
                'scene': context.window.scene.name, 'wall_s': time.monotonic(),
                'A_frame': STATE['scene'].frame_current, 'B_frame': STATE['foreign'].frame_current,
            })
            persist_space_observer()
            save()
        return {'RUNNING_MODAL', 'PASS_THROUGH'}

    def cancel(self, context):
        self.finish()


def assert_separate(require_session=True):
    main, foreign_window = STATE['main'], STATE['foreign_window']
    assert main.parent is None and foreign_window.parent is None, 'Independent main windows are required'
    assert main.scene == STATE['scene'] and foreign_window.scene == STATE['foreign']
    assert pointer(main) != pointer(foreign_window)
    if require_session:
        session = STATE['session']
        assert native._ACTIVE is session and session.alive_window() is not None
        assert session.window.scene == session.scene == STATE['scene']
        assert pointer(session.window.parent) == pointer(main)


def raw_play(window):
    with bpy.context.temp_override(**override(window)):
        assert bpy.ops.screen.animation_play() == {'FINISHED'}


def raw_stop(window):
    with bpy.context.temp_override(**override(window)):
        response = bpy.ops.screen.animation_cancel(restore_frame=False)
    observe('raw_stop_' + window.scene.name)
    RESULT['observations']['raw_stop_' + window.scene.name]['operator_result'] = sorted(response)
    save()
    assert not playback.any_playing() and playback.owner_scene() is None, response


def open_camera():
    with bpy.context.temp_override(**override(STATE['main'])):
        assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}


def wait_camera():
    deadline = time.monotonic() + 30.0
    while native._ACTIVE and native._ACTIVE.preparing:
        assert time.monotonic() < deadline, 'Native camera layout timeout'
        yield .2
    assert native._ACTIVE is not None and len(native._ACTIVE.entries) == 1
    STATE['session'] = native._ACTIVE


def native_toggle():
    session = STATE['session']
    with bpy.context.temp_override(**override(session.window, session.entries[0]['area'])):
        assert bpy.ops.wfrl.native_camera_play.poll()
        return bpy.ops.wfrl.native_camera_play()


def run():
    require('isolated_visible_factory_startup', not bpy.app.background and not bpy.data.filepath
            and (MODULE.startswith('bl_ext.') or '--factory-startup' in sys.argv)
            and len(bpy.context.window_manager.windows) == 1)
    if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
        bpy.context.preferences.filepaths.use_save_on_exit = False
    addon.register()
    addon.load_demo_scene()
    STATE.update(main=bpy.context.window, scene=bpy.context.scene)
    scene = STATE['scene']
    scene.frame_set(121)
    bpy.context.window_manager.wfrl_custom_slot = 1
    require('demo_starts_paused', not playback.any_playing())
    RESULT['saved_result_source'] = scene.get('wfrl_farm_flex_path')
    open_camera()
    yield from wait_camera()
    before = {pointer(w) for w in bpy.context.window_manager.windows}
    with bpy.context.temp_override(**override(STATE['main'])):
        assert bpy.ops.wm.window_new_main() == {'FINISHED'}
    added = [w for w in bpy.context.window_manager.windows if pointer(w) not in before]
    assert len(added) == 1 and added[0].parent is None
    foreign = bpy.data.scenes.new('NativeCamera.IndependentPlayerB')
    foreign.frame_end = 10000
    added[0].scene = foreign
    STATE.update(foreign_window=added[0], foreign=foreign)
    yield .5
    observe('independent_window_groups')
    assert_separate()
    require('independent_scene_fixture', scene != foreign and not playback.any_playing())

    start = scene.frame_current
    require('A_button_starts_A', native_toggle() == {'FINISHED'} and playback.is_playing(scene)
            and not playback.is_playing(foreign))
    yield .6
    observe('A_playing')
    assert_separate()
    require('A_advances', playback.is_playing(scene) and scene.frame_current > start,
            before=start, after=scene.frame_current)
    require('A_button_pauses_A', native_toggle() == {'FINISHED'} and not playback.any_playing())
    held_a = scene.frame_current
    yield .4
    require('A_pause_holds_frame', scene.frame_current == held_a and not playback.any_playing(),
            expected=held_a, actual=scene.frame_current)

    raw_play(STATE['foreign_window'])
    start_b = foreign.frame_current
    yield .6
    observe('B_playing_before_A_button')
    assert_separate()
    require('B_advances_independently', playback.is_playing(foreign) and not playback.is_playing(scene)
            and foreign.frame_current > start_b and scene.frame_current == held_a,
            before=start_b, after=foreign.frame_current)
    session = STATE['session']
    if os.environ.get('WFRL_NATIVE_PLAYBACK_MANUAL_SPACE') == '1':
        STATE['step'] = 'await_external_space_input'
        manual_start_b = foreign.frame_current
        bpy.utils.register_class(WFRL_TEST_OT_SpaceObserver)
        STATE['space_observer_registered'] = True
        with bpy.context.temp_override(**override(session.window, session.entries[0]['area'])):
            assert bpy.ops.wm.wfrl_test_space_observer('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
        observer = STATE['space_observer_state']
        screenshot('manual-space-before.png', session.window)
        ready = {'A_window': pointer(session.window), 'A_scene': scene.name,
                 'A_workspace': session.window.workspace.name, 'A_frame': held_a,
                 'B_window': pointer(STATE['foreign_window']), 'B_scene': foreign.name,
                 'B_frame': manual_start_b, 'input': 'External Space key in A native camera window',
                 'screenshot': str(OUT / 'manual-space-before.png'),
                 'event_evidence': str(OUT / 'manual-space-events.json'),
                 'completion_marker': str(OUT / 'manual-space-done')}
        (OUT / 'manual-space-ready.json').write_text(json.dumps(ready, ensure_ascii=False, indent=2) + '\n')
        print('NATIVE_CAMERA_MANUAL_SPACE_READY', flush=True)
        while not (OUT / 'manual-space-done').exists():
            yield .2
        screenshot('manual-space-after.png', session.window)
        observer['stop_requested'] = True
        deadline = time.monotonic() + 3.0
        while not observer['closed']:
            assert time.monotonic() < deadline, 'Space observer did not finish'
            yield .1
        bpy.utils.unregister_class(WFRL_TEST_OT_SpaceObserver)
        STATE['space_observer_registered'] = False
        require('manual_space_observer_released', observer['closed'] and observer['timer'] is None)
        require('external_space_press_delivered_to_A', any(
            event['type'] == 'SPACE' and event['value'] == 'PRESS'
            and event['window'] == pointer(session.window) for event in RESULT['manual_space_events']),
            events=RESULT['manual_space_events'])
        observe('after_acknowledged_external_space_input')
        assert_separate()
        require('B_preserved_after_external_space_acknowledgement', playback.is_playing(foreign)
                and not playback.is_playing(scene) and foreign.frame_current > manual_start_b
                and scene.frame_current == held_a,
                before=manual_start_b, after=foreign.frame_current,
                input_evidence='manual-space-events.json records a SPACE PRESS delivered to A; before/after PNGs show A')
    messages = []
    with bpy.context.temp_override(**override(session.window, session.entries[0]['area'])):
        require('A_operator_handles_shortcut_for_B_owner', bpy.ops.wfrl.native_camera_play.poll())
        response = bpy.ops.wfrl.native_camera_play()
        require('A_operator_rejects_B_owner', response == {'CANCELLED'}, response=sorted(response))
        operator = SimpleNamespace(report=lambda level, text: messages.append({'level': sorted(level), 'text': text}))
        response = native.WFRL_OT_NativePlay.execute(operator, bpy.context)
    require('A_stale_execute_rejects_B_owner', response == {'CANCELLED'}
            and any('WARNING' in message['level'] for message in messages),
            response=sorted(response), reports=messages)
    require('A_button_keeps_B_owner', playback.is_playing(foreign) and not playback.is_playing(scene)
            and playback.owner_window() == STATE['foreign_window'])
    after_button_b = foreign.frame_current
    yield .6
    observe('B_after_A_button')
    assert_separate()
    require('B_advances_after_A_button', playback.is_playing(foreign)
            and foreign.frame_current > after_button_b and scene.frame_current == held_a,
            before=after_button_b, after=foreign.frame_current)

    before_close_b = foreign.frame_current
    native.shutdown()
    require('closing_A_keeps_B_owner', native._ACTIVE is None and playback.is_playing(foreign))
    yield .6
    observe('B_after_A_close')
    assert_separate(require_session=False)
    require('B_advances_after_A_close', playback.is_playing(foreign)
            and foreign.frame_current > before_close_b and scene.frame_current == held_a,
            before=before_close_b, after=foreign.frame_current)

    open_camera()
    yield from wait_camera()
    observe('A_reopened_while_B_plays')
    assert_separate()
    require('A_reopen_keeps_B_owner', playback.is_playing(foreign))
    raw_stop(STATE['foreign_window'])
    session = STATE['session']
    raw_play(session.window)
    require('A_child_window_owns_player', playback.is_playing(scene) and playback.owner_window() == session.window)
    race_start_a = scene.frame_current
    yield .5
    require('A_advances_before_resume_race', playback.is_playing(scene) and scene.frame_current > race_start_a)
    native.shutdown()
    require('A_close_schedules_restore', native._ACTIVE is None and session.resume_scheduled and not playback.any_playing())
    # Start B before Blender can deliver A's deferred restore callback.
    raw_play(STATE['foreign_window'])
    race_start_b, held_a = foreign.frame_current, scene.frame_current
    yield .7
    observe('B_after_deferred_A_resume')
    assert_separate(require_session=False)
    RESULT['closed_session_lifecycle'] = list(session.lifecycle)
    require('deferred_A_resume_keeps_B_owner', playback.is_playing(foreign) and not playback.is_playing(scene)
            and playback.owner_window() == STATE['foreign_window']
            and any(item['event'] == 'resume_skipped' for item in session.lifecycle),
            lifecycle=list(session.lifecycle))
    require('B_advances_after_deferred_A_resume', foreign.frame_current > race_start_b and scene.frame_current == held_a,
            before=race_start_b, after=foreign.frame_current)
    raw_stop(STATE['foreign_window'])
    RESULT['status'] = 'PASS'
    save()
    print('NATIVE_CAMERA_PLAYBACK_PASS', flush=True)


RUN = run()


def tick():
    try:
        assert time.monotonic() < STATE['deadline'], 'Playback regression timeout'
        return next(RUN)
    except StopIteration:
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        RESULT.update(status='FAIL', failed_step=STATE['step'], error=traceback.format_exc())
        try:
            observer = STATE.get('space_observer_state')
            if observer is not None:
                observer['stop_requested'] = True
                release_space_observer_timer()
                persist_space_observer()
                if observer['closed'] and STATE.get('space_observer_registered'):
                    bpy.utils.unregister_class(WFRL_TEST_OT_SpaceObserver)
                    STATE['space_observer_registered'] = False
        except Exception:
            RESULT['observer_cleanup_error'] = traceback.format_exc()
        try:
            observe('failure')
        except Exception:
            RESULT['observation_error'] = traceback.format_exc()
        save()
        print(RESULT['error'], flush=True)
        bpy.ops.wm.quit_blender()
        return None


save()
bpy.app.timers.register(tick, first_interval=1.0)

"""Scripted native-window checks for NREL edit playback ownership.

Run in a new Blender process without --background. WFRL_TEST_OUTPUT must be a
new directory. WFRL_ADDON_MODULE defaults to workspace wfrl_blender; an installed
module requires WFRL_EXPECTED_ADDON_ROOT. The process exits after recording its
result unless WFRL_TEST_KEEP_OPEN=1. No user scene or simulation result is saved.
"""
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
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    expected = ROOT / 'blender_frontend/wfrl_blender'
else:
    assert MODULE.startswith('bl_ext.'), MODULE
    expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT'])
addon = importlib.import_module(MODULE)
editor = importlib.import_module(MODULE + '.nrel_defects.editor')
playback = importlib.import_module(MODULE + '.playback')
assert Path(addon.__file__).resolve().parent == expected.resolve()
assert Path(editor.__file__).resolve().parent.parent == expected.resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=False)
RESULT = dict(status='RUNNING', module=MODULE, addon_file=addon.__file__,
              blender=bpy.app.version_string, method='Scripted operators in independent native windows',
              checks={}, observations={}, source_sha256={
                  Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                  for module in (editor, playback)})
STATE = dict(deadline=time.monotonic() + 180., step='setup')


def save():
    (OUT / 'checks.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n')


def require(name, condition, **details):
    STATE['step'] = name
    RESULT['checks'][name] = dict(status='PASS' if condition else 'FAIL', **details)
    save()
    assert condition, (name, details)


def override(window):
    area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return dict(window=window, area=area, region=region)


def observe(name):
    RESULT['observations'][name] = dict(
        owner_scene=playback.owner_scene().name if playback.owner_scene() else None,
        owner_window=playback.owner_window().as_pointer() if playback.owner_window() else None,
        playing=playback.any_playing(), windows=[dict(
            pointer=w.as_pointer(), scene=w.scene.name, frame=w.scene.frame_current)
            for w in bpy.context.window_manager.windows])
    save()


def play(window):
    with bpy.context.temp_override(**override(window)):
        assert bpy.ops.screen.animation_play() == {'FINISHED'}


def stop(window):
    with bpy.context.temp_override(**override(window)):
        bpy.ops.screen.animation_cancel(restore_frame=False)
    assert not playback.any_playing()


def action(window, operation):
    with bpy.context.temp_override(**override(window)):
        assert bpy.ops.wfrl.nrel_defect_action(action=operation) == {'FINISHED'}


def run():
    require('isolated_visible_startup', not bpy.app.background and not bpy.data.filepath
            and len(bpy.context.window_manager.windows) == 1)
    if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
        bpy.context.preferences.filepaths.use_save_on_exit = False
    scratch = OUT / 'scratch'
    scratch.mkdir()
    bpy.context.preferences.filepaths.temporary_directory = str(scratch)
    addon.register()
    addon.load_demo_scene(camera_rig=False)
    scene = bpy.context.scene
    main = bpy.context.window
    session = editor.ensure(scene)
    require('editor_bound_to_own_runtime', session.flex.scene == scene)
    before = {w.as_pointer() for w in bpy.context.window_manager.windows}
    with bpy.context.temp_override(**override(main)):
        assert bpy.ops.wm.window_new_main() == {'FINISHED'}
    added = [w for w in bpy.context.window_manager.windows if w.as_pointer() not in before]
    require('independent_main_window', len(added) == 1 and added[0].parent is None)
    foreign_window = added[0]
    foreign = bpy.data.scenes.new('NrelEditor.ForeignPlayer')
    foreign.frame_end = 10000
    foreign_window.scene = foreign
    STATE.update(scene=scene, session=session, main=main, foreign=foreign,
                 foreign_window=foreign_window)
    yield .3

    play(foreign_window)
    yield .4
    action(main, 'NEW')
    before = foreign.frame_current
    held = scene.frame_current
    require('foreign_player_survives_begin', playback.is_playing(foreign))
    yield .4
    action(main, 'CANCEL')
    yield .4
    observe('foreign_player_after_cancel')
    require('foreign_player_survives_cancel', playback.is_playing(foreign)
            and playback.owner_window() == foreign_window and foreign.frame_current > before
            and scene.frame_current == held)
    stop(foreign_window)

    play(main)
    yield .4
    action(main, 'NEW')
    require('own_player_paused', not playback.any_playing() and session.edit_state[1] == main)
    held = scene.frame_current
    with bpy.context.temp_override(**override(foreign_window)):
        session.finish(False)
        session.finish(False)
    yield .4
    observe('own_player_resumed_from_foreign_context')
    require('own_player_resumes_original_window', playback.is_playing(scene)
            and playback.owner_window() == main and scene.frame_current > held
            and session.draft is None and session.edit_state is None)
    stop(main)

    play(main)
    yield .3
    action(main, 'NEW')
    play(foreign_window)
    before = foreign.frame_current
    action(main, 'CANCEL')
    yield .4
    observe('foreign_player_started_during_edit')
    require('finish_does_not_steal_new_player', playback.is_playing(foreign)
            and playback.owner_window() == foreign_window and foreign.frame_current > before)
    stop(foreign_window)

    play(main)
    yield .3
    action(main, 'NEW')
    main.scene = foreign
    with bpy.context.temp_override(**override(foreign_window)):
        session.finish(False)
    yield .3
    require('switched_owner_window_does_not_resume', not playback.any_playing()
            and session.edit_state is None)
    main.scene = scene

    play(main)
    yield .3
    action(main, 'NEW')
    with bpy.context.temp_override(**override(main)):
        assert bpy.ops.wm.window_close() == {'FINISHED'}
    yield .3
    with bpy.context.temp_override(**override(foreign_window)):
        session.finish(False)
    yield .3
    observe('original_window_closed')
    require('closed_owner_window_does_not_resume', not playback.any_playing()
            and session.draft is None and session.edit_state is None
            and len(bpy.context.window_manager.windows) == 1)
    RESULT['status'] = 'PASS'
    save()
    print('NREL_EDITOR_PLAYBACK_PASS', flush=True)


RUN = run()


def tick():
    try:
        assert time.monotonic() < STATE['deadline'], 'Editor playback regression timed out'
        return next(RUN)
    except StopIteration:
        if os.environ.get('WFRL_TEST_KEEP_OPEN') != '1':
            bpy.ops.wm.quit_blender()
        return None
    except Exception:
        RESULT.update(status='FAIL', failed_step=STATE['step'], error=traceback.format_exc())
        save()
        print(RESULT['error'], flush=True)
        bpy.ops.wm.quit_blender()
        return None


save()
bpy.app.timers.register(tick, first_interval=.5)

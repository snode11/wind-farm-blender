"""Native recording interruptions, cross-scene playback labels and viewport ownership."""
import importlib
import json
import os
from pathlib import Path
import sys
from time import monotonic
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
capture = importlib.import_module(MODULE + '.panels.presentation')
playback = importlib.import_module(MODULE + '.playback')
controls = importlib.import_module(MODULE + '.panels.clearance')
live = importlib.import_module(MODULE + '.live_scene')
models = importlib.import_module(MODULE + '.scene_model')
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
state = {'action': 'begin'}


def view(window):
    return next(area for area in window.screen.areas if area.type == 'VIEW_3D')


def begin_recording(window):
    scene = window.scene
    scene.wfrl_capture_directory = str(output)
    scene.wfrl_capture_fps = 1
    scene.wfrl_capture_duration = 30
    area = view(window)
    with bpy.context.temp_override(window=window, area=area):
        assert bpy.ops.wfrl.capture_recording() == {'RUNNING_MODAL'}
    assert capture._ACTIVE is not None
    return capture._ACTIVE._directory


def cancelled(directory):
    assert capture._ACTIVE is None
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['status'] == 'CANCELLED', manifest
    assert manifest['frames']
    assert (directory / manifest['frames'][0]['file']).is_file()


def tick():
    try:
        if state['action'] == 'begin':
            assert not bpy.app.background
            addon.register()
            window = bpy.context.window
            original = window.scene
            directory = begin_recording(window)
            window.scene = bpy.data.scenes.new('Recording Replacement')
            bpy.data.scenes.remove(original)
            state.update(action='window_recording', main=window, removed=directory,
                         deadline=monotonic() + .7)
            return .2
        if monotonic() < state['deadline']:
            return .2
        window = state['main']
        if state['action'] == 'window_recording':
            cancelled(state['removed'])
            with bpy.context.temp_override(window=window):
                before = list(bpy.context.window_manager.windows)
                bpy.ops.wm.window_new_main()
            other = next(item for item in bpy.context.window_manager.windows if item not in before)
            other.scene = bpy.data.scenes.new('Recording Closed Window')
            directory = begin_recording(other)
            with bpy.context.temp_override(window=other):
                bpy.ops.wm.window_close()
            state.update(action='playback', closed=directory, deadline=monotonic() + .7)
            return .2
        if state['action'] == 'playback':
            cancelled(state['closed'])
            with bpy.context.temp_override(window=window):
                before = list(bpy.context.window_manager.windows)
                bpy.ops.wm.window_new_main()
            other = next(item for item in bpy.context.window_manager.windows if item not in before)
            other.scene = bpy.data.scenes.new('Other Playback')
            with bpy.context.temp_override(window=other):
                bpy.ops.screen.animation_play()
            state.update(action='isolation', other=other, deadline=monotonic() + .7)
            return .2
        if state['action'] == 'isolation':
            other = state['other']
            assert playback.is_playing(other.scene)
            assert not playback.is_playing(window.scene)
            assert controls.playback_control(window.scene, window.screen) == ('播放', 'PLAY', '已暂停')
            addon._cancel_playback(other.scene)
            area = view(other)
            area.spaces.active.shading.type = 'SOLID'
            area.spaces.active.overlay.show_overlays = True
            with bpy.context.temp_override(window=window):
                dto = models.SceneDTO.from_mapping(dict(name='isolation', backend='demo',
                    turbine='nrel5mw', dt=1, layout=[dict(id='T1', x=0, y=0)],
                    inflow=dict(speed=8, direction=270)))
                live.build_live_scene(dto)
            assert area.spaces.active.shading.type == 'SOLID'
            assert area.spaces.active.overlay.show_overlays
            result = dict(status='PASS', module=addon.__file__, blender=bpy.app.version_string,
                          recording_removed_scene=True, recording_closed_window=True,
                          scene_playback_label=True, unrelated_viewport_preserved=True)
            (output / 'result.json').write_text(json.dumps(result, indent=2))
            print('FRONTEND_SESSIONS_WINDOW_PASS', json.dumps(result), flush=True)
            addon.unregister()
            bpy.ops.wm.quit_blender()
            return None
    except Exception:
        error = traceback.format_exc()
        (output / 'result.json').write_text(json.dumps(dict(status='FAIL', error=error), indent=2))
        print(error, flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)

"""Visible save/load checks for transient X100 windows and unrelated editors."""
from pathlib import Path
import importlib
import json
import os
import sys
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
radar = importlib.import_module('wfrl_blender.radar_x100.runtime')
output = importlib.import_module('wfrl_blender.radar_x100.output')
api = importlib.import_module('wfrl_blender.radar_x100.core')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
STATE = dict(step=0, checks={})


def source():
    return next(w for w in bpy.context.window_manager.windows
                if any(a.type == 'VIEW_3D' for a in w.screen.areas))


def open_output():
    window = source()
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=window, area=area):
        result = bpy.ops.wfrl.x100_output()
    return result, output.for_scene(window.scene)


def finish(status, error=None):
    result = dict(status=status, addon_path=addon.__file__, blender=bpy.app.version_string,
                  checks=STATE['checks'])
    if error:
        result['error'] = error
    (OUT / 'result.json').write_text(json.dumps(result, indent=2))
    print('X100_OUTPUT_LIFECYCLE_' + status, json.dumps(result), flush=True)
    def quit_blender():
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(quit_blender, first_interval=.1)


def run():
    try:
        step, checks = STATE['step'], STATE['checks']
        if step == 0:
            assert not bpy.app.background
            expected = Path(os.environ['WFRL_EXPECTED_ADDON_ROOT']).resolve()
            assert Path(addon.__file__).resolve().parent == expected
            addon.register()
            scene = bpy.context.scene
            scene.name = 'X100 Output Lifecycle'
            scene.frame_set(17)
            points = np.zeros(2, dtype=api.schema.POINT_DTYPE)
            points['x'], points['y'], points['z'] = [10, 20], [-2, 4], [1, 3]
            for name in ('mag', 'rcs', 'snr'):
                points[name] = np.nan
            path = OUT / 'input.ply'
            path.write_bytes(api.ply.encode_ply(points))
            scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(path)
            radar.start(scene)
            before = {w.as_pointer() for w in bpy.context.window_manager.windows}
            bpy.ops.wm.window_new()
            user = next(w for w in bpy.context.window_manager.windows if w.as_pointer() not in before)
            area = max(user.screen.areas, key=lambda a: a.width * a.height)
            area.type = 'IMAGE_EDITOR'
            area.spaces.active.image = bpy.data.images.new('User editor image', width=8, height=8)
            result, item = open_output()
            assert result == {'RUNNING_MODAL'}
            STATE['item'] = item
        elif step == 1:
            item = STATE['item']
            assert item.draw_count > 0 and item.frame is radar.session_for(source().scene).frame and not item.error
            assert len(bpy.context.window_manager.windows) == 3
            with bpy.context.temp_override(window=source()):
                bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'output-open.blend'))
            checks['save_keeps_current_output_and_frame'] = item.frame is radar.session_for(source().scene).frame
            STATE['step'] = 2
            bpy.ops.wm.open_mainfile(filepath=str(OUT / 'output-open.blend'))
            return .5
        elif step == 2:
            windows = list(bpy.context.window_manager.windows)
            checks['windows_after_load'] = len(windows)
            assert len(windows) == 2, 'Saved X100 canvas must not return as an extra blank window'
            assert not output._WINDOWS and output._HANDLE is None
            assert not bpy.app.timers.is_registered(output.tick)
            user = next(w for w in windows if w != source())
            assert any(a.type == 'IMAGE_EDITOR' and a.spaces.active.image is not None
                       and a.spaces.active.image.name == 'User editor image' for a in user.screen.areas)
            assert source().scene.frame_current == 17
            checks.update(saved_output_removed=True, unrelated_image_editor_preserved=True,
                          source_frame_preserved=True, output_resources_released=True)
            radar.tick()
            result, STATE['item'] = open_output()
            assert result == {'RUNNING_MODAL'}
        elif step == 3:
            item = STATE['item']
            assert item.draw_count > 0 and not item.error and len(item.xy) == 2
            assert item.frame is radar.session_for(source().scene).frame
            assert len(bpy.context.window_manager.windows) == 3
            assert open_output()[0] == {'FINISHED'}
            assert len(bpy.context.window_manager.windows) == 3
            checks['reopen_draws_existing_frame_without_duplicate_window'] = True
            addon.unregister()
            assert len(bpy.context.window_manager.windows) == 2
            assert not output._WINDOWS and output._HANDLE is None
            assert not bpy.app.timers.is_registered(output.tick)
            assert not bpy.app.timers.is_registered(output.discard_saved_windows)
            assert output.on_load_post not in bpy.app.handlers.load_post
            checks['unregister_releases_observer_only'] = True
            finish('PASS')
            return None
        STATE['step'] += 1
        return .5
    except Exception:
        finish('FAIL', traceback.format_exc())
        traceback.print_exc()
        return None


bpy.app.timers.register(run, first_interval=.5, persistent=True)

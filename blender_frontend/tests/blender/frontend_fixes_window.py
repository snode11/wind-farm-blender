"""Installed native window playback isolation and opt-in draw diagnostics."""
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
    sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
diagnostics = importlib.import_module(MODULE+'.frontend_diagnostics')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
STATE = {'phase': 0}


def tick():
    try:
        if STATE['phase'] == 0:
            assert not bpy.app.background
            expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
            if expected:
                assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
            addon.register()
            addon.load_demo_scene(camera_rig=False)
            window = bpy.context.window
            scene = window.scene
            area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
            region = next(region for region in area.regions if region.type == 'WINDOW')
            scene.wfrl_diagnostics_sampling = True
            scene.wfrl_farm_panel_page = 'TOOLS'
            area.spaces.active.show_region_ui = True
            STATE.update(window=window, scene=scene, area=area, region=region)
            with bpy.context.temp_override(window=window, area=area, region=region):
                before = list(bpy.context.window_manager.windows)
                bpy.ops.wm.window_new_main()
            other = next(win for win in bpy.context.window_manager.windows if win not in before)
            other.scene = bpy.data.scenes.new('Independent Playback Test')
            assert window.scene == scene and other.scene != scene, (window.scene.name, other.scene.name)
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.screen.animation_play()
            STATE.update(other=other, deadline=monotonic()+3., phase=1)
            return .2
        if monotonic() < STATE['deadline']:
            return .2
        window, scene, area, region = [STATE[name] for name in ('window', 'scene', 'area', 'region')]
        other = STATE['other']
        with bpy.context.temp_override(window=window, area=area, region=region):
            if STATE['phase'] == 1:
                info = diagnostics.snapshot(bpy.context)
                assert info['module'] == MODULE
                assert info['version'] == '0.3.18', info
                assert info['packaged'] == bool(os.environ.get('WFRL_EXPECTED_ADDON_ROOT')), info
                assert info['source_hz'] == 40 and info['timeline_hz'] == 60, info
                assert info['viewport_fps'] is not None and info['viewport_fps'] > 0, info
                assert bpy.ops.wfrl.farm_transport(action='STEP') == {'FINISHED'}
                assert not window.screen.is_animation_playing
                with bpy.context.temp_override(window=other, screen=other.screen):
                    bpy.ops.screen.animation_play()
                assert other.screen.is_animation_playing
                for action in ('STEP', 'RESET', 'STOP'):
                    assert bpy.ops.wfrl.farm_transport(action=action) == {'FINISHED'}
                    assert other.screen.is_animation_playing
                STATE.update(info=info, other_frame=other.scene.frame_current,
                    deadline=monotonic()+1.5, phase=2)
                return .2
            assert other.scene.frame_current > STATE['other_frame']
            assert diagnostics.snapshot(bpy.context)['viewport_fps'] is None
            addon._cancel_playback(other.scene)
            assert not other.screen.is_animation_playing
            with bpy.context.temp_override(window=other):
                bpy.ops.wm.window_close()
            for item in area.regions:
                if item.type == 'UI':
                    try:
                        item.active_panel_category = 'MAPPO'
                    except (AttributeError, RuntimeError):
                        pass
            report = dict(status='PASS', module_file=addon.__file__,
                unrelated_window_continued=True, step_reset_stop_scene_scoped=True,
                paused_fps_unavailable=True, diagnostics=STATE['info'],
                boundary='Actual native windows and short draw sample; not a full-clip FPS acceptance')
            (OUT/'window-checks.json').write_text(json.dumps(report, indent=2))
            print('FRONTEND_FIXES_WINDOW_PASS', json.dumps(report), flush=True)
            if os.environ.get('WFRL_QUIT_AFTER_TEST', '1') == '1':
                bpy.ops.wm.quit_blender()
            else:
                scene.wfrl_diagnostics_sampling = False
            return None
    except Exception:
        error = traceback.format_exc()
        (OUT/'window-checks.json').write_text(json.dumps(dict(status='FAIL', error=error), indent=2))
        print(error, flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1.)

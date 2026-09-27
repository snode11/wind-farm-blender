"""Visible-window regression: TRIPLE respects participation, WATCH still inspects."""
from pathlib import Path
import importlib
import json
import os
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
native = importlib.import_module(MODULE + '.native_camera_views')
core = importlib.import_module(MODULE + '.custom_cameras')
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-native-enabled'))
OUT.mkdir(parents=True, exist_ok=True)
state = {'phase': 0, 'waits': 0}


def wait_for_entries(count):
    if native._ACTIVE is not None and len(native._ACTIVE.entries) == count:
        state['waits'] = 0
        return False
    state['waits'] += 1
    assert state['waits'] <= 15, 'native window did not finish preparing'
    return True


def tick():
    try:
        if state['phase'] == 0:
            addon.register()
            addon.load_demo_scene()
            scene = bpy.context.scene
            area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
            core.get_camera(scene, 2)['custom_enabled'] = False
            state.update(scene=scene, area=area, window=bpy.context.window,
                         baseline=core.layout_dict(scene), phase=1)
            with bpy.context.temp_override(area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE') == {'FINISHED'}
            return .5

        scene, area, window = state['scene'], state['area'], state['window']
        if state['phase'] == 1:
            if wait_for_entries(3):
                return .5
            entries = {e['slot']: e for e in native._ACTIVE.entries}
            assert entries[1]['proxy'] is not None
            assert entries[2]['proxy'] is None, 'disabled C2 appeared in TRIPLE'
            assert entries[3]['proxy'] is not None
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            with bpy.context.temp_override(window=native._ACTIVE.window):
                bpy.ops.screen.screenshot(filepath=str(OUT / 'triple-c2-disabled.png'))
            assert core.layout_dict(scene) == state['baseline'], 'opening TRIPLE changed the layout'
            core.get_camera(scene, 2)['custom_enabled'] = True
            core.get_camera(scene, 3)['custom_enabled'] = False
            state['phase'] = 2
            return .5

        if state['phase'] == 2:
            entries = {e['slot']: e for e in native._ACTIVE.entries}
            assert entries[2]['proxy'] is not None, 'live enabling C2 did not restore its view'
            assert entries[3]['proxy'] is None, 'live disabling C3 did not clear its view'
            native.shutdown()
            assert not any(o.name.startswith('T1.Native.') for o in scene.objects)
            core.get_camera(scene, 2)['custom_enabled'] = False
            core.get_camera(scene, 3)['custom_enabled'] = True
            bpy.context.window_manager.wfrl_custom_slot = 2
            with bpy.context.temp_override(window=window, area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
            state['phase'] = 3
            return .5

        if state['phase'] == 3:
            if wait_for_entries(1):
                return .5
            entry = native._ACTIVE.entries[0]
            assert entry['slot'] == 2 and entry['proxy'] is not None
            assert not core.get_camera(scene, 2)['custom_enabled'], 'WATCH enabled the camera'
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            with bpy.context.temp_override(window=native._ACTIVE.window):
                bpy.ops.screen.screenshot(filepath=str(OUT / 'watch-c2-disabled.png'))
            native.shutdown()
            assert core.layout_dict(scene) == state['baseline'], 'viewing changed camera parameters'
            assert not any(o.name.startswith('T1.Native.') for o in scene.objects)
            (OUT / 'validation.json').write_text(json.dumps({
                'status': 'PASS', 'module': MODULE, 'blender': bpy.app.version_string,
                'method': 'scripted native-window operators; not physical mouse input',
                'checks': ['disabled TRIPLE slot blank', 'live participation changes',
                           'disabled WATCH slot remains inspectable', 'layout preserved',
                           'temporary cameras cleaned up'],
            }, indent=2))
            print('NATIVE_CAMERA_ENABLED_PASS', flush=True)
    except Exception:
        (OUT / 'error.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.)

"""Visible-window regression for synchronized T1 side-by-side views."""
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT / 'blender_frontend')), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
cameras = importlib.import_module(MODULE + '.cameras')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
state = {'stage': 0, 'deadline': time.monotonic() + 180, 'checks': []}


def views():
    return cameras._layout_active_views(bpy.context.window.screen)


def switch(mode):
    scene = bpy.context.scene
    before = (scene.frame_current, scene.frame_subframe, bpy.context.screen.is_animation_playing)
    with bpy.context.temp_override(area=views()[-1]):
        assert bpy.ops.wfrl.farm_split_view(mode=mode) == {'FINISHED'}
    assert before == (scene.frame_current, scene.frame_subframe, bpy.context.screen.is_animation_playing)


def pair(left):
    areas = views()
    assert len(areas) == 2
    assert abs(areas[0].y - areas[1].y) < 8
    assert abs(areas[0].height - areas[1].height) < 8
    assert all(a.spaces.active.use_local_camera for a in areas)
    assert [a.spaces.active.camera.name for a in areas] == [left, 'WFRL.Camera.T1.FrontQuarter']
    assert not areas[0].spaces.active.show_region_ui and areas[1].spaces.active.show_region_ui
    assert all(a.spaces.active.region_3d.view_perspective == 'CAMERA' for a in areas)
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    preview = importlib.import_module(MODULE + '.custom_camera_preview')
    area = areas[1]
    region = next(r for r in area.regions if r.type == 'WINDOW')
    x, y, width, height = preview.available_rectangle(area, region)
    for bid in (1, 2, 3):
        blade = bpy.context.scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
        for vertex in blade.data.vertices:
            p = location_3d_to_region_2d(region, area.spaces.active.region_3d, blade.matrix_world @ vertex.co)
            assert p is not None and x <= p.x <= x + width and y <= p.y <= y + height, 'rotor cropped or hidden by sidebar'


def tick():
    try:
        assert time.monotonic() < state['deadline'], 'layout timeout'
        if cameras._LAYOUT_JOB is not None:
            return .2
        scene = bpy.context.scene
        assert not scene.get('wfrl_view_layout_error'), scene.get('wfrl_view_layout_error')
        stage = state['stage']
        if stage == 0:
            addon.register()
            addon.load_demo_scene()
            scene.frame_set(181)
            switch('DOWN')
        elif stage == 1:
            pair('WFRL.Camera.T1.Gimbal')
            assert scene.frame_current == 181 and not bpy.context.screen.is_animation_playing
            bpy.ops.screen.screenshot(filepath=str(OUT / 'down-front.png'))
            state['checks'].append('Down/front local cameras, horizontal layout, paused frame retained')
            state['ids'] = [a.as_pointer() for a in views()]
            switch('DOWN')
        elif stage == 2:
            assert state['ids'] == [a.as_pointer() for a in views()]
            switch('NACELLE')
        elif stage == 3:
            pair('WFRL.Camera.T1.NacelleGimbal')
            assert scene.frame_current == 181 and not bpy.context.screen.is_animation_playing
            bpy.ops.screen.screenshot(filepath=str(OUT / 'nacelle-front.png'))
            state['checks'].append('repeated clicks reuse panes; nacelle/front preserves paused frame')
            with bpy.context.temp_override(area=views()[-1]):
                bpy.ops.screen.animation_play()
            state['frame'] = scene.frame_current
            state['wait_until'] = time.monotonic() + 1
        elif stage == 4:
            if time.monotonic() < state['wait_until']:
                return .2
            assert bpy.context.screen.is_animation_playing and scene.frame_current > state['frame']
            switch('DOWN')
        elif stage == 5:
            pair('WFRL.Camera.T1.Gimbal')
            assert bpy.context.screen.is_animation_playing
            switch('SINGLE')
        elif stage == 6:
            assert len(views()) == 1 and bpy.context.screen.is_animation_playing
            with bpy.context.temp_override(area=views()[0]):
                bpy.ops.screen.animation_cancel(restore_frame=False)
            state['checks'].append('playback advances and survives preset change and return to single')
            assert not views()[0].spaces.active.use_local_camera
            assert views()[0].spaces.active.show_region_ui
            assert scene.camera.name == 'WFRL.Camera.T1.Gimbal'
            state['frame'] = scene.frame_current
            switch('DOWN')
        elif stage == 7:
            pair('WFRL.Camera.T1.Gimbal')
            assert scene.frame_current == state['frame'] and not bpy.context.screen.is_animation_playing
            state['checks'].append('single/split round trip preserves explicit pause')
            (OUT / 'result.json').write_text(json.dumps({'status': 'PASS', 'module': MODULE,
                'blender': bpy.app.version_string, 'checks': state['checks']}, ensure_ascii=False, indent=2))
            print('FARM_SPLIT_PASS', flush=True)
            bpy.ops.wm.quit_blender()
            return None
        state['stage'] += 1
        return .8
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        (OUT / 'result.json').write_text(json.dumps({'status': 'FAIL', 'stage': state['stage'], 'error': error}, indent=2))
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)

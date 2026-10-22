"""Exercise model selection and native playback in a dedicated visible window."""
from pathlib import Path
import importlib
import json
import os
import sys
import time
import traceback

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_TEST_ADDON_MODULE', 'wfrl_blender')
SOURCE_MODE = MODULE == 'wfrl_blender'
if SOURCE_MODE:
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
addon_dir = Path(addon.__file__).resolve().parent
expected_dir = os.environ.get('WFRL_EXPECTED_ADDON_DIR')
if expected_dir:
    assert addon_dir == Path(expected_dir).resolve(), (addon_dir, expected_dir)
if SOURCE_MODE:
    assert addon_dir == ROOT / 'blender_frontend/wfrl_blender'
    assert not hasattr(bpy.types.Scene, 'wfrl_blade_model'), 'Source checks require --factory-startup'
else:
    assert not addon_dir.is_relative_to(ROOT / 'blender_frontend')
    assert MODULE in bpy.context.preferences.addons, 'Installed add-on must be enabled in this profile'
    assert hasattr(bpy.types.Scene, 'wfrl_blade_model'), 'Installed add-on is not registered'
assert not bpy.app.background
farm_flex, playback = [importlib.import_module(MODULE + '.' + name)
    for name in ('farm_flex', 'playback')]
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
STATE = dict(models=set(), playback_frames=set(), vertices=set(), initialized=False)


def camera_pose(camera):
    return dict(name=camera.name, location=list(camera.location),
                rotation=list(camera.rotation_euler), lens=camera.data.lens,
                sensor_width=camera.data.sensor_width, sensor_height=camera.data.sensor_height,
                shift_x=camera.data.shift_x, shift_y=camera.data.shift_y)


def observe():
    try:
        if not STATE['initialized']:
            if SOURCE_MODE:
                addon.register()
            bpy.context.window.scene = bpy.data.scenes.new('叶片外形验证')
            addon.load_demo_scene()
            scene = bpy.context.scene
            scene.frame_set(902)
            scene.name = '叶片外形验证'
            for area in bpy.context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.shading.type = 'SOLID'
                    area.spaces.active.shading.color_type = 'MATERIAL'
                    area.spaces.active.show_region_ui = True
                    view = area.spaces.active.region_3d
                    view.view_perspective = 'ORTHO'
                    view.view_location = scene.objects['WFRL.Turbine.T1.Rotor'].matrix_world.translation
                    view.view_distance = 170
                    view.view_rotation = Vector((1, .3, -.15)).to_track_quat('-Z', 'Y')
            path = OUT / 'rootkink-preview.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(path))
            STATE.update(initialized=True, scene=scene, camera=camera_pose(scene.camera),
                         started=time.monotonic(), blend=str(path), action='select')
        scene = STATE['scene']
        active = farm_flex.active_for(scene)
        model = scene.wfrl_blade_model
        assert active.model == model
        assert camera_pose(scene.camera) == STATE['camera'], 'Camera parameters changed'
        assert not scene.get('wfrl_blade_model_error'), scene.get('wfrl_blade_model_error')
        STATE['models'].add(model)
        playing = playback.is_playing(scene)
        if playing and model == 'rootkink':
            STATE['playback_frames'].add(scene.frame_current)
            blade = scene.objects['WFRL.Turbine.T1.Blade1']
            STATE['vertices'].add(tuple(round(v, 5) for v in blade.data.vertices[1000].co))
        passed = (STATE['models'] == {'nrel5mw', 'rootkink'}
                  and len(STATE['playback_frames']) >= 3 and len(STATE['vertices']) >= 2
                  and model == 'rootkink' and not playing)
        result = dict(status='PASS' if passed else 'RUNNING',
                      interaction='Blender RNA and native playback operator in visible window',
                      addon_module=MODULE, addon_file=addon.__file__, blender=bpy.app.version_string,
                      models_seen=sorted(STATE['models']), current_model=model,
                      frame=scene.frame_current, playing=playing,
                      rootkink_playback_frames=sorted(STATE['playback_frames']),
                      distinct_deformed_vertex_positions=len(STATE['vertices']),
                      camera_preserved=True, saved_scene=STATE['blend'])
        (OUT / 'window-checks.json').write_text(json.dumps(result, indent=2) + '\n')
        if passed:
            bpy.ops.wm.save_as_mainfile(filepath=STATE['blend'])
            print('BLADE_MODEL_WINDOW_PASS', json.dumps(result), flush=True)
            return None
        elapsed = time.monotonic() - STATE['started']
        if STATE['action'] == 'select' and elapsed >= 1:
            scene.wfrl_blade_model = 'rootkink'
            STATE['action'] = 'play'
        elif STATE['action'] == 'play' and elapsed >= 2:
            bpy.ops.screen.animation_play()
            STATE['action'] = 'stop'
        elif STATE['action'] == 'stop' and elapsed >= 7 and len(STATE['playback_frames']) >= 3:
            bpy.ops.screen.animation_cancel(restore_frame=False)
            STATE['action'] = 'done'
        if elapsed > 30:
            raise TimeoutError('Visible native playback did not complete')
        return .25
    except Exception:
        result = dict(status='FAIL', error=traceback.format_exc(),
                      addon_module=MODULE, addon_file=addon.__file__)
        (OUT / 'window-checks.json').write_text(json.dumps(result, indent=2) + '\n')
        print('BLADE_MODEL_WINDOW_FAIL', json.dumps(result), flush=True)
        return None


bpy.app.timers.register(observe, first_interval=1.0)

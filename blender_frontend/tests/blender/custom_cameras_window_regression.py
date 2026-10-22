"""Scripted visible-window acceptance; drag/joystick events are simulated.

Launch a separate Blender instance without --background. It quits only its own
process. No .blend is saved. WFRL_TEST_OUTPUT selects the evidence directory.
Actual screen-animation state is sampled across timer turns, not mocked.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import bpy
import importlib
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if RUNTIME == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
wfrl_blender = importlib.import_module(RUNTIME)
core = importlib.import_module(RUNTIME + '.custom_cameras')
panel = importlib.import_module(RUNTIME + '.panels.custom_cameras')

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
STATE = {'step': 0, 'checks': {}, 'save_calls': 0}


def save_guard(*_args):
    STATE['save_calls'] += 1


def action(name, **kwargs):
    assert bpy.ops.wfrl.custom_camera_action(action=name, **kwargs) == {'FINISHED'}


def start(mode, slot=1):
    bpy.context.window_manager.wfrl_custom_slot = slot
    assert bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT', mode=mode) == {'RUNNING_MODAL'}
    active = panel._ACTIVE
    assert active is not None and active.timer is not None and active.handle is not None
    return active


def event(kind, value='PRESS', x=130, y=180):
    region = STATE['region']
    return SimpleNamespace(type=kind, value=value, mouse_x=region.x+x,
        mouse_y=region.y+y, alt=False, ctrl=False, shift=False)


def cleaned(op):
    assert op.closed and op.timer is None and op.handle is None
    assert panel._ACTIVE is None
    assert not any(obj.get('wfrl_custom_draft') for obj in bpy.context.scene.objects)


def install(slot, params):
    scene = bpy.context.scene
    draft = core.begin_draft(scene, slot)
    core.apply_parameters(draft, params, core.validate_position(scene, params.location)[0])
    return core.commit_draft(scene, slot, draft)


def set_input(params):
    wm = bpy.context.window_manager
    for key, value in zip(('x', 'y', 'z'), params.location):
        setattr(wm, 'wfrl_custom_'+key, value)
    for key in ('yaw', 'pitch', 'roll', 'fov', 'vfov'):
        setattr(wm, 'wfrl_custom_'+key, getattr(params, key))


def step():
    try:
        if STATE['step'] == 0:
            # This test process has factory preferences; avoid its own quit.blend
            # recovery artifact when the harness deliberately exits at the end.
            if hasattr(bpy.context.preferences.filepaths, 'use_save_on_exit'):
                bpy.context.preferences.filepaths.use_save_on_exit = False
            wfrl_blender.register()
            wfrl_blender.load_demo_scene()
            scene = bpy.context.scene
            window = bpy.context.window
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            STATE.update(window=window, area=area, region=region,
                         areas=tuple(a.as_pointer() for a in window.screen.areas),
                         output_camera=scene.camera,
                         resolution=(scene.render.resolution_x, scene.render.resolution_y))
            bpy.app.handlers.save_pre.append(save_guard)
            # Use the evaluated formal model's shell as the installation fixture.
            _name, _verts, _faces, _normals, tree = core._geometry(scene)[0]
            point, normal, _face, _distance = tree.find_nearest(Vector((0, -10, .2)))
            location = tuple(point+normal*.1)
            params = core.CameraParameters(location, 180, -50, 37, 75)
            install(1, params)
            STATE['params'] = params
            scene.frame_set(755)
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.screen.animation_play()
            assert window.screen.is_animation_playing
            STATE['step'] = 1
            return .5

        window, area, region = (STATE[k] for k in ('window', 'area', 'region'))
        with bpy.context.temp_override(window=window, area=area, region=region):
            scene = bpy.context.scene
            stage = STATE['step']
            if stage == 1:
                assert window.screen.is_animation_playing and scene.frame_current > 755
                STATE['pause_frame'] = scene.frame_current
                active = start('EDIT')
                STATE['edit'] = active
                assert not window.screen.is_animation_playing
                assert active.session.frame == STATE['pause_frame']
                STATE['step'] = 2
                return .4
            if stage == 2:
                active = STATE['edit']
                assert not window.screen.is_animation_playing
                assert scene.frame_current == STATE['pause_frame']
                before = core.parameters(active.camera)
                assert active.modal(bpy.context, event('SPACE')) == {'RUNNING_MODAL'}
                active.modal(bpy.context, event('LEFTMOUSE'))
                active.modal(bpy.context, event('MOUSEMOVE', x=165, y=205))
                active.modal(bpy.context, event('LEFTMOUSE', 'RELEASE', x=165, y=205))
                dragged = core.parameters(active.camera)
                assert (dragged.yaw, dragged.pitch) != (before.yaw, before.pitch)
                assert dragged.location == before.location and dragged.roll == 37
                cx, cy, radius = active.geometry()
                active.modal(bpy.context, event('LEFTMOUSE', x=cx+radius*.5, y=cy+radius*.3))
                assert active.drag == 'stick'
                active.last_time -= .05
                active.modal(bpy.context, event('TIMER'))
                stick = core.parameters(active.camera)
                assert (stick.yaw, stick.pitch) != (dragged.yaw, dragged.pitch)
                active.modal(bpy.context, event('LEFTMOUSE', 'RELEASE', x=cx, y=cy))
                active.modal(bpy.context, event('TIMER'))
                assert core.parameters(active.camera) == stick
                active.modal(bpy.context, event('WHEELUPMOUSE'))
                assert core.parameters(active.camera).fov == before.fov
                bpy.context.window_manager.wfrl_custom_linked_zoom = True
                active.modal(bpy.context, event('WHEELUPMOUSE'))
                zoomed = core.parameters(active.camera)
                assert zoomed.fov < before.fov and zoomed.vfov < before.vfov
                action('BACK')
                placed = core.parameters(active.camera)
                assert active.stage == 'PLACE'
                action('NEXT')
                assert active.stage == 'AIM' and core.parameters(active.camera) == placed
                action('CONFIRM')
                assert active.stage == 'WATCH' and active.session.closed
                assert scene.frame_current == STATE['pause_frame'] and window.screen.is_animation_playing
                STATE['installed'] = core.parameters(active.camera)
                STATE['checks'].update(real_playback_paused=True, simulated_drag_roll37=True,
                    simulated_joystick_timer=True, stages_preserve_draft=True)
                STATE['step'] = 3
                return .5
            if stage == 3:
                assert scene.frame_current > STATE['pause_frame'] and window.screen.is_animation_playing
                STATE['checks']['confirm_restores_real_playback'] = True
                original = STATE['installed']
                frame = scene.frame_current
                active = start('EDIT')
                active.change(dx=9, dy=-3, zoom=8)
                action('CANCEL')
                assert core.parameters(core.get_camera(scene, 1)) == original
                assert scene.frame_current == frame and window.screen.is_animation_playing
                STATE['checks']['cancel_existing_restores_real_playback'] = True
                action('EXIT')
                bpy.ops.screen.animation_cancel(restore_frame=False)
                frame = scene.frame_current
                active = start('PLACE', 2)
                assert active.stage == 'PLACE' and not active.ready
                active.modal(bpy.context, event('ESC'))
                cleaned(active)
                assert core.get_camera(scene, 2) is None
                assert scene.frame_current == frame and not window.screen.is_animation_playing
                STATE['checks']['new_cancel_keeps_empty_and_paused'] = True
                active = start('INPUT', 2)
                p2 = replace(STATE['params'], yaw=215, pitch=-20, roll=90, fov=65)
                set_input(p2)
                action('VALIDATE')
                if active.stage != 'AIM':
                    action('ANCHOR', index=0)
                action('CONFIRM')
                assert not window.screen.is_animation_playing
                second = core.parameters(core.get_camera(scene, 2))
                for slot in (1, 2, 1, 2):
                    action('SLOT', slot=slot)
                    assert panel._ACTIVE.camera == core.get_camera(scene, slot)
                    assert scene.frame_current == frame and not window.screen.is_animation_playing
                assert core.parameters(core.get_camera(scene, 1)) == original
                assert core.parameters(core.get_camera(scene, 2)) == second
                locked = core.parameters(active.camera)
                for kind in ('WHEELUPMOUSE', 'MIDDLEMOUSE', 'NUMPAD_1', 'G', 'R'):
                    assert active.modal(bpy.context, event(kind)) == {'RUNNING_MODAL'}
                    assert core.parameters(active.camera) == locked
                STATE['checks'].update(two_slots_switch_without_seeking=True,
                    simulated_native_navigation_blocked=True, paused_confirmation_stays_paused=True)
                action('EXIT')
                cleaned(active)
                assert tuple(a.as_pointer() for a in window.screen.areas) == STATE['areas']
                assert scene.camera == STATE['output_camera']
                assert (scene.render.resolution_x, scene.render.resolution_y) == STATE['resolution']
                STATE['checks']['single_viewport_and_output_unchanged'] = True
                active = start('EDIT', 1)
                scene['wfrl_farm_demo'] = 'scripted-window-session-change'
                assert active.modal(bpy.context, event('TIMER')) == {'CANCELLED'}
                cleaned(active)
                assert not window.screen.is_animation_playing
                STATE['checks']['session_change_releases_timer_and_draw'] = True
                del scene['wfrl_farm_demo']
                active = start('EDIT', 1)
                wfrl_blender.build_demo_geometry()
                cleaned(active)
                assert core.get_camera(scene, 1) is None and core.get_camera(scene, 2) is None
                STATE['checks']['scene_rebuild_releases_resources_and_slots'] = True
                active = start('PLACE', 1)
                resources = active.__dict__
                wfrl_blender.unregister()
                # Unregister removes the operator's RNA; retain only its Python
                # state dictionary to inspect resource release after that point.
                assert resources['closed'] and resources['timer'] is None and resources['handle'] is None
                assert panel._ACTIVE is None
                assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)
                assert panel.cancel_on_load not in bpy.app.handlers.load_pre
                assert not hasattr(bpy.types.WindowManager, 'wfrl_custom_slot')
                STATE['checks']['unregister_releases_resources_and_handlers'] = True
                assert STATE['save_calls'] == 0
                STATE['checks']['no_save_operation'] = True
                finish('PASS')
                return None
    except Exception:
        finish('FAIL', traceback.format_exc())
        return None


def finish(status, error=None):
    result = dict(status=status, blender=bpy.app.version_string, pid=os.getpid(),
        environment='separate visible Blender window',
        interaction='scripted-window; drag, joystick and key events simulated',
        real_playback='screen.animation_play across timer turns, no playback mocks',
        checks=STATE['checks'], error=error)
    (OUT/'custom-cameras-window-checks.json').write_text(json.dumps(result, indent=2)+'\n')
    print('CUSTOM_CAMERAS_WINDOW_'+status, json.dumps(result), flush=True)
    if error:
        print(error, flush=True)
    if save_guard in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(save_guard)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(step, first_interval=2)

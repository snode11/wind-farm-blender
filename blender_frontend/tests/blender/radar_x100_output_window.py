"""Visible checks for the independent X100 output and raw RGB isolation.

WFRL_TEST_QUIT=1 exits after the final report and marker, on success or failure;
otherwise the review window stays open.
"""
from dataclasses import asdict, replace
from pathlib import Path
import hashlib
import importlib
import json
import os
import sys
import time
import traceback
import numpy as np
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(Path(__file__).resolve().parent))
import _native
MODULE = _native.addon_module()
addon = importlib.import_module(MODULE)
atomic_json = importlib.import_module(MODULE+'.atomic_json')
farm = importlib.import_module(MODULE+'.farm_flex')
radar = importlib.import_module(MODULE+'.radar_x100.runtime')
output = importlib.import_module(MODULE+'.radar_x100.output')
api = importlib.import_module(MODULE+'.radar_x100.core')
camera = importlib.import_module(MODULE+'.custom_cameras')
sidebar = importlib.import_module(MODULE+'.cameras')
preview = importlib.import_module(MODULE+'.custom_camera_preview')
OUT = _native.output_directory()
STATE = dict(step=0, checks={})
DRAW_TIMEOUT_S = 10.


def finish(result):
    result = dict(result, final=True)
    atomic_json.write_json(OUT/'result.json', result)
    print('X100_OUTPUT_WINDOW_' + result['status'], json.dumps(result), flush=True)
    if os.environ.get('WFRL_TEST_QUIT') == '1':
        bpy.ops.wm.quit_blender()


def frame_state(frame):
    return (dict(object_id=id(frame), identity=frame.identity, validity=frame.validity,
                 completeness=frame.completeness, points=len(frame.points)) if frame is not None else None)


def draw_state():
    scene, item = STATE['scene'], STATE['item']
    session = radar.session_for(scene)
    window, area = item.alive()
    return dict(step=STATE['step'], monotonic=time.monotonic(),
        source=frame_state(session.frame) if session else None,
        display_valid=session.display_valid if session else False,
        consumed=frame_state(item.frame), draw_count=item.draw_count,
        source_error=scene.get('wfrl_x100_error', ''), output_error=item.error,
        scene_output_error=scene.get('wfrl_x100_output_error', ''),
        window_alive=window is not None, area_alive=area is not None,
        window_owns_scene=window is not None and window.scene == scene,
        view=item.view, color=item.color, projected_points=len(item.xy))


def log_draw(event, snapshot):
    with (OUT/'draw-sync.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(event=event, **snapshot)) + '\n')


def await_draw(step):
    STATE['step'] = step
    session = radar.session_for(STATE['scene'])
    STATE['await_draw'] = dict(start=time.monotonic(), draw_count=STATE['item'].draw_count,
        session=session, frame=session.frame if session else None)
    log_draw('await', draw_state())
    return .05


def draw_ready():
    """Yield to Blender until a new draw consumes the expected frame, or fail with diagnostics."""
    pending = STATE.get('await_draw')
    if pending is None:
        return True
    snapshot = draw_state()
    log_draw('poll', snapshot)
    detail = json.dumps(snapshot)
    assert not snapshot['source_error'], detail
    assert not snapshot['output_error'] and not snapshot['scene_output_error'], detail
    assert snapshot['window_alive'] and snapshot['area_alive'] and snapshot['window_owns_scene'], detail
    session = radar.session_for(STATE['scene'])
    assert session is pending['session'], 'Source session changed while awaiting draw: ' + detail
    if session is not None:
        assert session.display_valid and session.frame is pending['frame'], 'Source frame changed or invalid: ' + detail
    item = STATE['item']
    if item.draw_count > pending['draw_count'] and item.frame is pending['frame']:
        log_draw('ready', snapshot)
        del STATE['await_draw']
        return True
    assert snapshot['monotonic'] - pending['start'] < DRAW_TIMEOUT_S, (
        f'Timed out after {DRAW_TIMEOUT_S:g}s awaiting a new draw of the same frame; '
        f'baseline_draw_count={pending["draw_count"]}; expected={frame_state(pending["frame"])}; ' + detail)
    _, area = item.alive()
    area.tag_redraw()
    return False


def image_hash(context, cam):
    images = preview.render_group(context, [cam], long_edge=320, create_texture=False)
    try:
        image = images[1]
        rgba = image.rgba()
        assert len(set(rgba)) > 32
        return hashlib.sha256(rgba).hexdigest(), image.state_hash
    finally:
        for image in images.values():
            image.free()


def source_context():
    return bpy.context.temp_override(window=STATE['window'], area=STATE['area'], region=STATE['region'])


def open_output():
    with source_context():
        assert bpy.ops.wfrl.x100_output() == {'RUNNING_MODAL'}
    item = output.for_scene(STATE['scene'])
    assert item is not None
    return item


def unchanged():
    scene, session = STATE['scene'], STATE['session']
    assert STATE['frame'] is session.frame
    assert STATE['points'] == session.frame.points.tobytes()
    assert STATE['clock'] == (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s'))
    np.testing.assert_array_equal(STATE['mount'], np.array(session.mount.matrix_world))
    assert STATE['optics'] == asdict(camera.parameters(STATE['cam']))
    assert STATE['layout'] == [(a.as_pointer(), a.type) for a in STATE['window'].screen.areas]
    with source_context():
        assert STATE['rgb'] == image_hash(bpy.context, STATE['cam'])


def run():
    try:
        if not draw_ready():
            return .05
        checks = STATE['checks']
        if STATE['step'] == 0:
            expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
            if expected:
                assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
            if MODULE == 'wfrl_blender':
                addon.register()
            addon.load_demo_scene(farm.default_package(), camera_rig=False)
            scene, window = bpy.context.scene, bpy.context.window
            scene.name = 'X100 Output Validation'
            scene.wfrl_x100_candidates = 2048
            session = radar.start(scene)
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            area.spaces.active.show_region_ui = True
            sidebar.select_sidebar_category(area, 'X100')
            geometry = camera._geometry(scene)[0]
            point, normal, *_ = geometry[-1].find_nearest(Vector((0, -10, .2)))
            cam = camera.begin_draft(scene, 1)
            camera.place_on_surface(cam, camera.SurfaceAnchor(tuple(point), tuple(normal), .1, geometry[0]))
            camera.apply_parameters(cam, replace(camera.parameters(cam), yaw=180, pitch=-50,
                fov=90, vfov=60, output_long_edge_px=320))
            camera.commit_draft(scene, 1, cam)
            STATE.update(scene=scene, window=window, area=area, region=region, session=session, cam=cam, step=1)
            return 2.
        if STATE['step'] == 1:
            scene, session = STATE['scene'], STATE['session']
            with source_context():
                image_hash(bpy.context, STATE['cam'])
                rgb = image_hash(bpy.context, STATE['cam'])
            STATE.update(frame=session.frame, points=session.frame.points.tobytes(),
                clock=(scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')),
                mount=np.array(session.mount.matrix_world), optics=asdict(camera.parameters(STATE['cam'])),
                layout=[(a.as_pointer(), a.type) for a in STATE['window'].screen.areas], rgb=rgb)
            STATE['item'] = open_output()
            return await_draw(2)
        item, session, scene = STATE['item'], STATE['session'], STATE['scene']
        if STATE['step'] == 2:
            assert not item.error, item.error
            assert item.draw_count > 0
            assert item.frame is session.frame
            assert len(item.xy) == int(session.frame.finite_mask.sum())
            assert item.view == 'SENSOR'
            window_count = len(bpy.context.window_manager.windows)
            with source_context():
                assert bpy.ops.wfrl.x100_output() == {'FINISHED'}
            assert len(bpy.context.window_manager.windows) == window_count
            unchanged()
            checks.update(actual_gpu_draw=True, consumes_same_frame=True, duplicate_open_reuses_window=True,
                preserves_source_layout_installation_time_optics_and_raw_rgb=True)
            item.reset('TOP')
            item.color = 'VELOCITY'
            item.zoom, item.pan = 1.5, (1., -2.)
            return await_draw(3)
        if STATE['step'] == 3:
            assert not item.error and item.view == 'TOP'
            assert item.frame is session.frame
            np.testing.assert_array_equal(item.xy, np.column_stack((-session.frame.xyz[:, 1], session.frame.xyz[:, 0])))
            unchanged()
            checks['navigation_is_display_only'] = True
            t = session.sampler.interval[0]+.3
            farm.active_for(scene).update(scene, sim_time_s=t, record_telemetry=False)
            session.update()
            assert session.frame is not STATE['frame']
            return await_draw(4)
        if STATE['step'] == 4:
            assert item.frame is session.frame and not item.error
            checks['follows_existing_saved_motion_frame'] = True
            frames = [session.frame]
            frames.append(replace(session.frame, sequence=session.frame.sequence+1,
                points=np.zeros(0, dtype=api.schema.POINT_DTYPE), radial_velocity_m_s=np.empty(0), material_ids=()))
            frames.append(replace(session.frame, sequence=session.frame.sequence+2,
                source_kind='TEST', stream='UNKNOWN', spatial=dict(frame='VENDOR_RAW', units='m', alignment='UNALIGNED'),
                radial_velocity_m_s=None, material_ids=None))
            recorder = api.recording.Recorder(OUT/'replay')
            for index, frame in enumerate(frames):
                recorder.append(frame, index*.1)
            recorder.close()
            radar.stop(scene)
            scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(OUT/'replay')
            STATE['session'] = radar.start(scene)
            return await_draw(5)
        if STATE['step'] == 5:
            assert item.frame is session.frame and item.frame.source_kind == 'GEOMETRY_SIMULATION'
            session.seek_frame(1)
            return await_draw(6)
        if STATE['step'] == 6:
            assert item.frame is session.frame and item.frame.validity == 'VALID_EMPTY'
            assert not len(item.xy) and not item.error
            session.seek_frame(2)
            item.reset('SENSOR')
            return await_draw(7)
        if STATE['step'] == 7:
            assert item.frame is session.frame and item.frame.source_kind == 'TEST'
            assert item.view == 'FRONT' and item.limits is None and item.unknown == len(item.frame.points)
            assert not item.error
            checks.update(file_replay_preserves_source=True, valid_empty_clears_points=True,
                raw_axes_not_guessed=True, missing_velocity_gray=True)
            radar.stop(scene)
            return await_draw(8)
        if STATE['step'] == 8:
            assert item.frame is None and not len(item.xy) and not item.error
            checks['stopped_source_clears_output'] = True
            output.close(item)
            assert not output._WINDOWS and output._HANDLE is None
            STATE['item'] = open_output()
            return await_draw(9)
        if STATE['step'] == 9:
            assert item.draw_count > 0 and item.frame is None and not item.error
            checks['close_and_reopen_without_source_mutation'] = True
            other = bpy.data.scenes.new('X100 Output Other')
            with bpy.context.temp_override(window=STATE['window']):
                STATE['window'].scene = other
            STATE['step'] = 10
            return .05
        if STATE['step'] == 10:
            assert item.scene == scene and item.frame is None
            assert output.for_scene(STATE['window'].scene) is None
            checks['bound_to_owning_scene'] = True
            STATE['window'].scene = scene
            bpy.data.scenes.remove(bpy.data.scenes['X100 Output Other'])
            output.close_all()
            assert not output._WINDOWS and output._HANDLE is None
            assert not bpy.app.timers.is_registered(output.tick)
            checks['releases_handler_and_timer'] = True
            scene.wfrl_x100_source = 'SIMULATION'
            STATE['session'] = radar.start(scene)
            item = open_output()
            item.color = 'VELOCITY'
            STATE['item'] = item
            return await_draw(11)
        if STATE['step'] == 11:
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'X100-Output-Validation.blend'))
            result = dict(status='PASS', addon_path=addon.__file__, checks=checks,
                raw_rgba_sha256=STATE['rgb'][0], raw_state_sha256=STATE['rgb'][1],
                source=STATE['session'].frame.source_kind, points=len(STATE['session'].frame.points))
            finish(result)
            return None
    except Exception:
        result = dict(status='ERROR', error=traceback.format_exc(), checks=STATE['checks'])
        if 'item' in STATE:
            try:
                result['draw_state'] = draw_state()
                log_draw('error', result['draw_state'])
            except Exception:
                result['diagnostic_error'] = traceback.format_exc()
        traceback.print_exc()
        finish(result)
        return None


bpy.app.timers.register(run, first_interval=1.)

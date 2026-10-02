"""Visible-window STRIP/GRID/focus checks; use an isolated Blender config.

WFRL_ADDON_MODULE supports installed bl_ext namespaces; WFRL_ADDON_ROOT only
changes Python import lookup. No installation or package mutation is performed.
All screenshots compare the same native window, same scene and paused frame.
"""
from pathlib import Path
import importlib
import json
import os
import sys
import time
import traceback
from dataclasses import replace
from copy import deepcopy

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
native = importlib.import_module(MODULE+'.native_camera_views')
core = importlib.import_module(MODULE+'.custom_cameras')
preview = importlib.import_module(MODULE+'.custom_camera_preview')
capture = importlib.import_module(MODULE+'.custom_camera_capture')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
assert not (OUT/'result.json').exists(), 'Choose a fresh output directory'
state = {'stage': 'start', 'deadline': time.monotonic()+300, 'checks': [], 'layouts': {}}


def capture_checks():
    session = native._ACTIVE
    scene = state['scene']
    area = session.entries[0]['area']
    region = next(r for r in area.regions if r.type == 'WINDOW')
    fast = session.quality_settings()
    before = (scene.frame_current, scene.frame_subframe, session.window.screen.is_animation_playing)
    original_get = preview.RenderTarget.get
    sampled = []
    def checked_get(target,context,width,height):
        sampled.append(session.quality_settings())
        assert session.quality_settings() == state['quality']
        return original_get(target,context,width,height)
    preview.RenderTarget.get = checked_get
    try:
        with bpy.context.temp_override(window=session.window,area=area,region=region):
            job=capture.Capture(bpy.context,OUT)
            assert job.step(bpy.context)
    finally:
        preview.RenderTarget.get=original_get
    assert len(sampled) == 3 and session.quality_settings() == fast
    rows=[json.loads(line) for line in (job.path/'frames.jsonl').read_text().splitlines()]
    assert len(rows) == 3 and len({r['time_s'] for r in rows}) == 1
    assert len({r['simulation_state_hash'] for r in rows}) == 1
    assert all((r['image_width'],r['image_height']) == (1920,1080) for r in rows)
    state['capture']=str(job.path)
    assert (scene.frame_current,scene.frame_subframe,session.window.screen.is_animation_playing) == before
    with bpy.context.temp_override(window=session.window,area=area,region=region):
        cancelled=capture.Capture(bpy.context,OUT)
        cancelled.request_cancel()
        assert cancelled.closed and cancelled.manifest['status'] == 'incomplete'
    assert session.quality_settings() == fast and not capture.active()
    assert (scene.frame_current,scene.frame_subframe,session.window.screen.is_animation_playing) == before
    def failing_get(*_args):
        assert session.quality_settings() == state['quality']
        raise RuntimeError('intentional capture render failure')
    preview.RenderTarget.get=failing_get
    try:
        with bpy.context.temp_override(window=session.window,area=area,region=region):
            failed=capture.Capture(bpy.context,OUT)
            try:
                failed.step(bpy.context)
            except RuntimeError as exc:
                assert str(exc) == 'intentional capture render failure'
                failed.finish(error=str(exc))
            else:
                raise AssertionError('capture fault injection did not fail')
    finally:
        preview.RenderTarget.get=original_get
    assert failed.closed and failed.manifest['status'] == 'incomplete' and not capture.active()
    assert session.quality_settings() == fast
    assert (scene.frame_current,scene.frame_subframe,session.window.screen.is_animation_playing) == before
    state['checks'].extend(['1920x1080 three-camera original-quality capture with same clock/state',
                            'capture completion/cancel/render-failure restore playback, frame and preview quality'])


def record_layout(name):
    session = native._ACTIVE
    scene = state['scene']
    rows = []
    for entry in session.entries:
        area = entry['area']
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=session.window,area=area,region=region):
            bpy.context.view_layer.update()
            area.spaces.active.region_3d.update()
        bounds = native.gate_bounds(scene, entry, region, area.spaces.active.region_3d)
        assert bounds is not None
        x, y, width, height = bounds
        left, bottom, available_width, available_height = preview.available_rectangle(area, region)
        title_bottom = bottom+available_height-28*bpy.context.preferences.system.ui_scale
        assert x >= left-2 and y >= bottom-2, (name, entry['slot'], 'cropped left/bottom', bounds)
        assert x+width <= left+available_width+2, (name, entry['slot'], 'cropped right', bounds)
        assert y+height <= title_bottom+2, (name, entry['slot'], 'title overlaps image', bounds, title_bottom)
        params = core.parameters(core.get_camera(scene, entry['slot']))
        expected = native.projection.aspect(params.fov, params.vfov)
        assert abs(width/height-expected) < 2e-4, (name, bounds, expected)
        rows.append({'slot': entry['slot'], 'area': [area.x, area.y, area.width, area.height],
                     'region': [region.width, region.height], 'gate': list(bounds),
                     'aspect': width/height})
    if len(rows) == 3:
        assert max(r['area'][2] for r in rows)-min(r['area'][2] for r in rows) <= 6
        assert max(r['area'][3] for r in rows)-min(r['area'][3] for r in rows) <= 6
    with bpy.context.temp_override(window=session.window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(OUT/(name+'.png')))
    value = {'window': [session.window.width, session.window.height], 'frame': scene.frame_current,
             'time_s': scene.get('wfrl_clearance_time_s'), 'playing': session.window.screen.is_animation_playing,
             'cells': rows}
    state['layouts'][name] = value
    return value


def change(mode, layout='GRID', slot=None):
    session = native._ACTIVE
    before = (state['scene'].frame_current, state['scene'].frame_subframe,
              session.window.as_pointer(), session.window.screen.is_animation_playing)
    area = session.entries[0]['area']
    with bpy.context.temp_override(window=session.window, area=area):
        result = (bpy.ops.wfrl.native_camera_focus(slot=slot) if slot else
                  bpy.ops.wfrl.native_camera_view(mode=mode, layout=layout))
    assert result == {'FINISHED'}
    after = (state['scene'].frame_current, state['scene'].frame_subframe,
             session.window.as_pointer(), session.window.screen.is_animation_playing)
    assert before == after, ('transition changed scene/playing/window', before, after)
    assert core.layout_dict(state['scene']) == state['layout']


def ready(count, diagram=False):
    session = native._ACTIVE
    assert session is not None, 'native session ended unexpectedly'
    return (not session.preparing and len(session.entries) == count
            and (not diagram or (session.diagram is not None and not session.diagram_stale)))


def tick():
    try:
        assert time.monotonic() < state['deadline'], 'layout regression timeout: '+state['stage']
        # Blender may schedule the next timer invocation relative to the start
        # of this callback. A long PNG capture can consume a returned interval;
        # require real elapsed time for playback/close progression assertions.
        if time.monotonic() < state.get('earliest_check',0):return .05
        if state['stage'] == 'start':
            addon.register();addon.load_demo_scene()
            scene = bpy.context.scene
            main = bpy.context.window
            area = next(a for a in main.screen.areas if a.type == 'VIEW_3D')
            scene.frame_set(int(os.environ.get('WFRL_LAYOUT_REFERENCE_FRAME', '1')))
            state.update(scene=scene, main=main, main_area=area, layout=core.layout_dict(scene),
                         frame=scene.frame_current, camera=scene.camera,
                         shading=area.spaces.active.shading.type,
                         quality=(scene.render.preview_pixel_size, scene.eevee.taa_samples,
                                  scene.eevee.use_shadows, scene.eevee.use_fast_gi), stage='strip')
            with bpy.context.temp_override(window=main, area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE', layout='STRIP') == {'FINISHED'}
            return .5
        scene, session = state['scene'], native._ACTIVE
        if state['stage'] == 'strip':
            if not ready(3):return .25
            record_layout('strip-paused')
            change('TRIPLE', 'GRID');state['stage']='grid';return .5
        if state['stage'] == 'grid':
            if not ready(3, True):return .25
            assert scene.frame_current == state['frame']
            record_layout('grid-paused')
            assert session.diagram['kind'] == 'STATIC_ACTUAL_MESH_PROJECTION'
            assert any('Nacelle' in obj for obj in session.diagram['objects'])
            assert any('MountArm' in obj for obj in session.diagram['objects'])
            state['checks'].extend(['same-window same-time strip/grid', 'complete calibrated gates', 'static actual installation meshes'])
            old_diagram = session.diagram
            scene.frame_set(scene.frame_current+10);session.sync();session.sync()
            assert session.diagram is old_diagram and not session.diagram_stale, 'animation invalidated static geometry'
            scene.frame_set(state['frame'])
            camera=core.get_camera(scene, 2);params=core.parameters(camera)
            core.apply_parameters(camera, replace(params, pitch=params.pitch+.1))
            session.sync();assert session.diagram_stale, 'edit did not invalidate old diagram'
            session.sync();assert not session.diagram_stale and session.diagram is not old_diagram
            core.apply_parameters(camera, params);session.sync();assert session.diagram_stale
            session.sync();assert not session.diagram_stale
            imported=deepcopy(state['layout'])
            imported['cameras'][1]['parameters']['pitch'] += .15
            core.import_layout(scene,imported,overwrite=True)
            session.sync();assert session.diagram_stale, 'import did not invalidate old diagram'
            session.sync();assert not session.diagram_stale
            core.restore_layout(scene,state['layout'])
            session.sync();assert session.diagram_stale, 'restore did not invalidate old diagram'
            session.sync();assert not session.diagram_stale
            state['checks'].extend(['static cache stable during animation', 'edit/import/restore invalidation and refresh'])
            change('WATCH',slot=2);state['stage']='focus';return .5
        if state['stage'] == 'focus':
            if not ready(1):return .25
            assert session.entries[0]['slot'] == 2 and scene.frame_current == state['frame']
            record_layout('focus-c2-paused')
            change('TRIPLE','GRID');state['stage']='returned';return .5
        if state['stage'] == 'returned':
            if not ready(3,True):return .25
            assert scene.frame_current == state['frame']
            state['checks'].append('paused focus/return preserves frame and camera parameters')
            with bpy.context.temp_override(window=session.window,area=session.entries[0]['area']):
                assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
            assert session.window.screen.is_animation_playing
            state.update(stage='playing', playing_start=scene.frame_current);return 1.
        if state['stage'] == 'playing':
            assert scene.frame_current != state['playing_start'] and session.window.screen.is_animation_playing
            state['playing_frame']=scene.frame_current
            change('WATCH',slot=2);state['stage']='playing_focus';return .5
        if state['stage'] == 'playing_focus':
            if not ready(1):return .25
            assert session.window.screen.is_animation_playing
            assert scene.frame_current > state['playing_frame']
            change('TRIPLE','GRID');state['stage']='playing_return';return .5
        if state['stage'] == 'playing_return':
            if not ready(3,True):return .25
            assert session.window.screen.is_animation_playing
            record_layout('grid-playing')
            capture_checks()
            state['checks'].append('playing focus/return retains playback and advances scene')
            with native.capture_quality(scene):assert session.quality_settings() == state['quality']
            state['checks'].append('capture quality remains original')
            # Deliberately exercise raw native-window ownership as well as the
            # product controls above (which own playback in the main window).
            entry=session.entries[0]
            region=next(r for r in entry['area'].regions if r.type=='WINDOW')
            with bpy.context.temp_override(window=session.window,area=entry['area'],region=region):
                bpy.ops.screen.animation_cancel(restore_frame=False)
                bpy.ops.screen.animation_play()
            state['close_frame']=scene.frame_current
            state['closing_session']=session
            native.shutdown();state.update(stage='closed',earliest_check=time.monotonic()+.8);return .1
        if state['stage'] == 'closed':
            assert native._ACTIVE is None
            state['close_observation']={'wall_s':time.monotonic(),'not_before_s':state['earliest_check'],
                                        'frame':scene.frame_current,'playing':state['main'].screen.is_animation_playing,
                                        'windows':[(w.as_pointer(),w.screen.is_animation_playing,w.scene.frame_current)
                                                   for w in bpy.context.window_manager.windows]}
            assert state['main'].screen.is_animation_playing and scene.frame_current > state['close_frame'], 'closing camera-owned playback stopped surviving main view'
            state['checks'].append('programmatic close after raw native playback preserves frame and resumes in main window')
            assert not any(o.name.startswith('T1.Native.') for o in scene.objects)
            assert core.layout_dict(scene) == state['layout'] and scene.camera == state['camera']
            assert state['main_area'].spaces.active.shading.type == state['shading']
            assert (scene.render.preview_pixel_size,scene.eevee.taa_samples,
                    scene.eevee.use_shadows,scene.eevee.use_fast_gi) == state['quality']
            state['checks'].append('exit restores source shading and original quality; proxies removed')
            with bpy.context.temp_override(window=state['main'],area=state['main_area']):
                bpy.ops.screen.animation_cancel(restore_frame=False)
                bpy.ops.screen.animation_play()
                assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
            state['stage']='main_owned_close';return .5
        if state['stage'] == 'main_owned_close':
            if not ready(1):return .25
            assert state['main'].screen.is_animation_playing
            state['close_frame']=scene.frame_current
            native.shutdown();state.update(stage='main_owned_closed',earliest_check=time.monotonic()+.8);return .1
        if state['stage'] == 'main_owned_closed':
            assert native._ACTIVE is None
            assert state['main'].screen.is_animation_playing and scene.frame_current > state['close_frame'], 'programmatic camera close stopped main-owned playback'
            state['checks'].append('programmatic close preserves surviving main-owned playback')
            with bpy.context.temp_override(window=state['main'],area=state['main_area']):
                assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
            state['stage']='os_close';return .5
        if state['stage'] == 'os_close':
            if not ready(1):return .25
            assert state['main'].screen.is_animation_playing
            state['close_frame']=scene.frame_current
            with bpy.context.temp_override(window=session.window):bpy.ops.wm.window_close()
            state.update(stage='os_closed',earliest_check=time.monotonic()+.8);return .1
        if state['stage'] == 'os_closed':
            assert native._ACTIVE is None
            assert state['main'].screen.is_animation_playing and scene.frame_current > state['close_frame'], 'OS camera close stopped main-owned playback'
            assert not any(o.name.startswith('T1.Native.') for o in scene.objects)
            assert state['main_area'].spaces.active.shading.type == state['shading']
            assert (scene.render.preview_pixel_size,scene.eevee.taa_samples,
                    scene.eevee.use_shadows,scene.eevee.use_fast_gi) == state['quality']
            state['checks'].append('OS window close preserves main-owned playback and restores source shading/quality/resources')
            with bpy.context.temp_override(window=state['main'],area=state['main_area']):
                bpy.ops.screen.animation_cancel(restore_frame=False)
                assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
            state['stage']='product_play_ready';return .5
        if state['stage'] == 'product_play_ready':
            if not ready(1):return .25
            assert any(item.idname=='wfrl.native_camera_play' and item.type=='SPACE' for _,item in native._KEYMAPS)
            with bpy.context.temp_override(window=session.window,area=session.entries[0]['area']):
                assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
            state['close_frame']=scene.frame_current
            state['stage']='product_play_close';return .5
        if state['stage'] == 'product_play_close':
            assert scene.frame_current > state['close_frame'] and session.window.screen.is_animation_playing
            state['close_frame']=scene.frame_current
            with bpy.context.temp_override(window=session.window):bpy.ops.wm.window_close()
            state.update(stage='product_play_closed',earliest_check=time.monotonic()+.8);return .1
        if state['stage'] == 'product_play_closed':
            assert native._ACTIVE is None
            assert state['main'].screen.is_animation_playing and scene.frame_current > state['close_frame']
            state['checks'].append('product play operator uses surviving main window; Space keymap bound to same operator')
            with bpy.context.temp_override(window=state['main'],area=state['main_area']):
                bpy.ops.screen.animation_cancel(restore_frame=False)
                assert bpy.ops.wfrl.native_camera_view(mode='WATCH') == {'FINISHED'}
            state['stage']='immediate_pause_close';return .5
        if state['stage'] == 'immediate_pause_close':
            if not ready(1):return .25
            with bpy.context.temp_override(window=session.window,area=session.entries[0]['area']):
                assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
                session.sync()
                assert bpy.ops.wfrl.native_camera_play() == {'FINISHED'}
                state['close_frame']=scene.frame_current
                bpy.ops.wm.window_close()
            state.update(stage='immediate_pause_closed',earliest_check=time.monotonic()+.8);return .1
        if state['stage'] == 'immediate_pause_closed':
            assert native._ACTIVE is None
            assert not state['main'].screen.is_animation_playing and scene.frame_current == state['close_frame'], 'stale playback cache resumed an intentional pause'
            state['checks'].append('play then deliberate pause and immediate OS close stays paused without waiting for watch')
            strip,grid=state['layouts']['strip-paused'],state['layouts']['grid-paused']
            assert strip['window'] == grid['window'] and strip['frame'] == grid['frame']
            ratios=[grid['cells'][i]['gate'][2]*grid['cells'][i]['gate'][3]/
                    (strip['cells'][i]['gate'][2]*strip['cells'][i]['gate'][3]) for i in range(3)]
            result={'status':'PASS','module':MODULE,'blender':bpy.app.version_string,
                    'method':'scripted visible-window operations, not physical input or monitor refresh',
                    'checks':state['checks'],'capture':state['capture'],'layouts':state['layouts'],'grid_to_strip_image_area':ratios,
                    'close_lifecycle':state['closing_session'].lifecycle,'close_observation':state['close_observation']}
            (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            print('FRONTEND_LAYOUT_PASS',json.dumps(result,ensure_ascii=False),flush=True)
            bpy.ops.wm.quit_blender();return None
    except Exception:
        error=traceback.format_exc();print(error,flush=True)
        (OUT/'result.json').write_text(json.dumps({'status':'ERROR','stage':state['stage'],'error':error,
                                                 'checks':state['checks'],'layouts':state['layouts'],
                                                 'close_frame':state.get('close_frame'),
                                                 'actual_frame':state['scene'].frame_current if 'scene' in state else None,
                                                 'close_observation':state.get('close_observation'),
                                                 'lifecycle':getattr(state.get('closing_session'),'lifecycle',[])},ensure_ascii=False,indent=2))
        bpy.ops.wm.quit_blender();return None
    return .25

bpy.app.timers.register(tick,first_interval=1.)

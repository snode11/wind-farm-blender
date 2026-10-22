"""Visible equal-column layout, capture isolation and restoration acceptance."""
import os,sys,json,traceback,time
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[os.environ.get('WFRL_ADDON_ROOT',str(ROOT/'blender_frontend')),str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import native_camera_views as native,custom_cameras as core,custom_camera_preview as preview,custom_camera_capture as capture
OUT=Path(os.environ['WFRL_TEST_OUTPUT']);OUT.mkdir(parents=True,exist_ok=True)
state={}

def tick():
    try:
        if not state:
            addon.register();addon.load_demo_scene()
            scene=bpy.context.scene;main=bpy.context.window;area=next(a for a in main.screen.areas if a.type=='VIEW_3D')
            state.update(stage='ready',ready_deadline=time.monotonic()+30,main=main,area=area,scene=scene,source_shading=area.spaces.active.shading.type,
                         camera=scene.camera,layout=core.layout_dict(scene),
                         quality=(scene.render.preview_pixel_size,scene.eevee.taa_samples,scene.eevee.use_shadows,scene.eevee.use_fast_gi))
            with bpy.context.temp_override(window=main,area=area):
                assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE',layout='STRIP')=={'FINISHED'}
            return 2.
        scene=state['scene'];session=native._ACTIVE
        if state['stage']=='ready':
            assert session is not None
            if len(session.entries)<3 and time.monotonic()<state['ready_deadline']: return .25
            assert len(session.entries)==3
            entries=session.entries; areas=[e['area'] for e in entries]
            assert [e['slot'] for e in entries]==[1,2,3]
            assert [a.x for a in areas]==sorted(a.x for a in areas)
            assert max(a.width for a in areas)-min(a.width for a in areas)<=6
            assert len({a.height for a in areas})==1
            assert all(not a.spaces.active.show_region_ui and not a.show_menus for a in areas)
            assert core.layout_dict(scene)==state['layout']
            # Real object-space aspect, same 16:9; no artificial stretching.
            assert all(abs(e['fx']/e['fy']-1)<1e-5 for e in entries)
            with bpy.context.temp_override(window=session.window):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
                bpy.ops.screen.screenshot(filepath=str(OUT/'three-reference.png'))
            fast=session.quality_settings()
            assert fast!=state['quality']
            try:
                with native.capture_quality(scene):
                    assert session.quality_settings()==state['quality']
                    raise RuntimeError('capture failure probe')
            except RuntimeError as exc:
                assert str(exc)=='capture failure probe'
            assert session.quality_settings()==fast
            area=entries[0]['area'];region=next(r for r in area.regions if r.type=='WINDOW')
            original_get=preview.RenderTarget.get
            sampled=[]
            def checked_get(target,context,width,height):
                sampled.append(session.quality_settings())
                assert session.quality_settings()==state['quality']
                return original_get(target,context,width,height)
            preview.RenderTarget.get=checked_get
            try:
                with bpy.context.temp_override(window=session.window,area=area,region=region):
                    job=capture.Capture(bpy.context,OUT)
                    assert job.step(bpy.context)
            finally:preview.RenderTarget.get=original_get
            assert len(sampled)==3 and session.quality_settings()==fast
            rows=[json.loads(line) for line in (job.path/'frames.jsonl').read_text().splitlines()]
            assert len(rows)==3 and len({r['time_s'] for r in rows})==1
            assert len({r['simulation_state_hash'] for r in rows})==1
            assert all((r['image_width'],r['image_height'])==(1920,1080) for r in rows)
            state['capture']=str(job.path)
            with bpy.context.temp_override(window=session.window,area=area):
                assert bpy.ops.wfrl.native_camera_quality()=={'FINISHED'}
            assert session.quality_settings()==state['quality']
            state['stage']='high';return 2.
        if state['stage']=='high':
            with bpy.context.temp_override(window=session.window):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
                bpy.ops.screen.screenshot(filepath=str(OUT/'three-high-quality.png'))
            native.shutdown()
            assert state['area'].type=='VIEW_3D'
            assert state['area'].spaces.active.shading.type==state['source_shading']
            assert (scene.render.preview_pixel_size,scene.eevee.taa_samples,scene.eevee.use_shadows,scene.eevee.use_fast_gi)==state['quality']
            assert scene.camera==state['camera'] and core.layout_dict(scene)==state['layout']
            assert not any(o.name.startswith('T1.Native.') for o in scene.objects)
            # Ordinary main-view switch preserves the simulation moment.
            scene.frame_set(755);stamp=capture.current_time(scene)
            with bpy.context.temp_override(window=state['main'],area=state['area']):
                bpy.ops.wfrl.farm_flex_view(turbine='T1')
            assert scene.frame_current==755 and capture.current_time(scene)==stamp
            with bpy.context.temp_override(window=state['main'],area=state['area']):
                bpy.ops.wfrl.native_camera_view(mode='WATCH')
            state['stage']='single';return 2.
        if state['stage']=='single':
            assert len(session.entries)==1
            assert scene.frame_current==755
            with bpy.context.temp_override(window=session.window):bpy.ops.wm.window_close()
            state['stage']='closed';return 1.
        if state['stage']=='closed':
            assert native._ACTIVE is None
            assert not any(o.name.startswith('T1.Native.') for o in scene.objects)
            result={'status':'PASS','capture':state['capture'],'blender':bpy.app.version_string,
                'checks':['equal_ordered_columns','common_16_9','capture_1920_same_clock_state','capture_original_quality','capture_failure_restores_preview','high_quality_toggle','source_view_and_quality_restored','view_switch_keeps_time','os_close_cleanup']}
            (OUT/'result.json').write_text(json.dumps(result,indent=2));print('DEMO_PRESENTATION_PASS',json.dumps(result),flush=True)
            bpy.ops.wm.quit_blender();return None
    except Exception:
        error=traceback.format_exc();print(error,flush=True);(OUT/'error.txt').write_text(error)
        bpy.ops.wm.quit_blender();return None
    return .25
bpy.app.timers.register(tick,first_interval=1.)

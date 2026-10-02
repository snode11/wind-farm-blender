"""Visible installed-window playback, observed S1 alarm and screenshot checks."""
import importlib
import json
import math
import os
from pathlib import Path
import time
import traceback

import bpy

module=os.environ.get('WFRL_ADDON_MODULE','bl_ext.user_default.wfrl_blender')
addon=importlib.import_module(module)
farm=importlib.import_module(module+'.farm_flex')
replay=importlib.import_module(module+'.clearance_replay')
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=False)
checks={};state={'phase':0,'started':time.monotonic()}


def sample():
    return replay.sample(bpy.context.scene)


def seek(t):
    scene=bpy.context.scene
    frame=scene.frame_start+(t+1e-4-farm._ACTIVE.readers['T1'].start_s)*scene['wfrl_clearance_timebase_fps']
    scene.frame_set(math.floor(frame),subframe=frame-math.floor(frame))
    farm.update(scene);replay.update(scene)
    for area in bpy.context.screen.areas:
        area.tag_redraw()


def show_sidebar():
    for area in bpy.context.screen.areas:
        if area.type=='VIEW_3D':
            area.spaces.active.show_region_ui=True


def finish(status,error=None):
    result=dict(status=status,module=module,addon_path=str(Path(addon.__file__).resolve().parent),
        checks=checks,error=error,elapsed_s=time.monotonic()-state['started'],
        scope='scripted visible Blender window and screenshots; no physical input device, sensor or protection latency acceptance')
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('DUAL_BEAM_WINDOW_'+status,flush=True)
    if status!='PASS' or not os.environ.get('WFRL_KEEP_WINDOW'):
        bpy.ops.wm.quit_blender()


def tick():
    try:
        phase=state['phase'];scene=bpy.context.scene
        if phase==0:
            builtin_method = os.environ.get('WFRL_BUILTIN_METHOD', 'AXIS')
            assert bpy.ops.wfrl.load_dual_beam(builtin_method=builtin_method)=={'FINISHED'}
            reader=farm._ACTIVE.readers['T1']
            expected_method = 'hub-tls.v1' if builtin_method == 'TLS' else 'hub-axis.v1'
            assert reader.config.get('reconstruction_method', 'hub-axis.v1') == expected_method
            checks['loaded_reconstruction_method'] = expected_method
            assert reader.config['angles_deg']==[10.,12.,14.], 'User-confirmed angles must not be replaced'
            checks['user_angles_preserved']=True
            scene.wfrl_farm_panel_page='RADAR'
            bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='FRONT')
            show_sidebar()
            state['s1']=next(r['time_s'] for r in reader.rows if r['s1_observation_state']=='triggered')
            state['pair']=next(r['time_s'] for r in reader.rows if r['reconstruction']['valid'])
            seek(state['s1'])
        elif phase==1:
            if os.environ.get('WFRL_PANEL_CURRENT_CATEGORY') and not checks.get('panel_category_for_capture'):
                # Blender exposes the active category read-only. In this test
                # process only, draw the unchanged installed panel in that tab.
                panel=importlib.import_module(module+'.panels.farm_replay').WFRL_PT_FarmFlex
                region=next(r for a in bpy.context.screen.areas if a.type=='VIEW_3D'
                            for r in a.regions if r.type=='UI')
                category=region.active_panel_category or 'Item'
                bpy.utils.unregister_class(panel)
                panel.bl_category=category
                bpy.utils.register_class(panel)
                for other in reversed(bpy.types.Panel.__subclasses__()):
                    if (other is not panel and getattr(other,'bl_space_type','')=='VIEW_3D'
                            and getattr(other,'bl_region_type','')=='UI'
                            and getattr(other,'bl_category','')==category
                            and other.is_registered):
                        bpy.utils.unregister_class(other)
                checks['panel_category_for_capture']=category
                checks['panel_capture_scope']='installed draw code; test-only category relocation and hiding other panels, not a MAPPO tab click test'
                return 2.
            if os.environ.get('WFRL_REQUIRE_PANEL'):
                regions=[r for a in bpy.context.screen.areas if a.type=='VIEW_3D'
                         for r in a.regions if r.type=='UI']
                if not any(r.active_panel_category=='MAPPO' for r in regions):
                    if time.monotonic()-state['started']>180:
                        raise TimeoutError('MAPPO sidebar must be selected in the actual window')
                    return 1.
                checks['mappo_sidebar_selected']=True
            value=sample()
            assert value['alarm']['active']
            checks['observed_s1_alarm_state']=True
            checks['s1_without_pair_in_source']='NOT_PRESENT; independence is checked with unit fixtures'
            bpy.ops.screen.screenshot(filepath=str(out/'s1-alarm.png'))
            seek(state['pair'])
        elif phase==2:
            assert sample()['measurement_status']=='valid'
            checks['paired_measurement']=True
            bpy.ops.screen.screenshot(filepath=str(out/'paired-measurement.png'))
            state['before_play']=sample()['time_s']
            bpy.ops.screen.animation_play()
        elif phase==3:
            assert bpy.context.screen.is_animation_playing
            assert sample()['time_s']>state['before_play']+.1
            bpy.ops.screen.animation_cancel(restore_frame=False)
            state['paused']=sample()['time_s']
            checks['playback_advances']=True
        elif phase==4:
            assert sample()['time_s']==state['paused']
            checks['pause_preserves_time']=True
            seek(farm._ACTIVE.readers['T1'].start_s)
            assert sample()['alarm']['event_count']==0
            checks['backward_seek_no_future_alarm']=True
            seek(state['pair'])
            finish('PASS')
            return None
        state['phase']+=1
        return 2.
    except Exception:
        finish('ERROR',traceback.format_exc())
        return None


bpy.app.timers.register(tick,first_interval=2.)

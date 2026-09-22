"""Independent nacelle PTZ, original Down, replay state and saved-scene contract."""
from pathlib import Path
import importlib, json, os, sys, tempfile
import bpy
import numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[3]
runtime=os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if runtime == 'wfrl_blender':
    sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
wfrl_blender=importlib.import_module(runtime)
cameras, farm_flex, clearance_replay, tip_tracking = [importlib.import_module(runtime+'.'+name) for name in ('cameras','farm_flex','clearance_replay','tip_tracking')]
gimbal=importlib.import_module(runtime+'.panels.gimbal')
farm_replay=importlib.import_module(runtime+'.panels.farm_replay')
alarm_state=importlib.import_module(runtime+'.radar_feedback').alarm_state
from types import SimpleNamespace
from unittest.mock import patch
if not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'):
    wfrl_blender.register()
wfrl_blender.load_demo_scene(farm_flex.default_package())
s=bpy.context.scene
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
region=next(r for r in area.regions if r.type=='WINDOW')

class Buttons:
    def __init__(self): self.buttons={}
    def row(self,**kw): return self
    def operator(self,kind,*,text,**kw):
        op=SimpleNamespace();self.buttons[kind,text]=op;return op
    def label(self,**kw): pass
    def prop(self,*a,**kw): pass
    def panel(self,*a,**kw): return self,None


def pose(cam):
    return dict(location=list(cam.location),rotation=list(cam.rotation_euler),lens=cam.data.lens,
                yaw=cam['gimbal_yaw'],pitch=cam['gimbal_pitch'],fov=cam['gimbal_fov'],roll=cam['gimbal_roll'])

old={};mounts={}
for tid in ('T1','T2','T3'):
    down=cameras.ensure_gimbal(s,tid);cameras.down_gimbal(down)
    np.testing.assert_allclose(down.location,[-3.5,1.2,-37.8],atol=2e-6)
    assert [down[k] for k in ('gimbal_yaw','gimbal_pitch','gimbal_roll','gimbal_fov')]==[180,-67,180,85]
    old[tid]=pose(down)
    cam=cameras.ensure_nacelle_gimbal(s,tid)
    assert cam!=down and cam.data!=down.data and cam.parent==down.parent
    # Existing scenes adopt the new wide default once, without repeatedly
    # overwriting subsequent manual zoom (including an explicit 35 degrees).
    cameras.aim_gimbal(cam,cam['gimbal_yaw'],cam['gimbal_pitch'],35)
    del cam['wfrl_nacelle_view_revision']
    cameras.ensure_nacelle_gimbal(s,tid)
    assert cam['gimbal_fov']==120
    cameras.aim_gimbal(cam,cam['gimbal_yaw'],cam['gimbal_pitch'],35)
    assert cameras.ensure_nacelle_gimbal(s,tid)['gimbal_fov']==35
    cameras.aim_gimbal(cam,cam['gimbal_yaw'],cam['gimbal_pitch'],60)
    del cam['wfrl_nacelle_view_revision']
    assert cameras.ensure_nacelle_gimbal(s,tid)['gimbal_fov']==60
    cameras.reset_nacelle_gimbal(cam)
    anchor=Vector(cam['bracket_anchor_m']);pivot=Vector(cam['bracket_pivot_m'])
    shell=s.objects[f'WFRL.Turbine.{tid}.Nacelle']
    hit,point,normal,_=shell.closest_point_on_mesh(shell.matrix_basis.inverted()@anchor)
    assert hit and (shell.matrix_basis@point-anchor).length<1e-5
    assert (Vector(cam.location)-anchor).length>.2
    assert cam['bracket_length_m']<1.5
    mounts[tid]={**pose(cam), 'anchor':list(anchor),'pivot':list(pivot),'bracket_m':cam['bracket_length_m']}
    with bpy.context.temp_override(area=area,region=region):
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='NACELLE')
        assert gimbal.current(s)==cam
        saved=cam.location.copy()
        gimbal.change(cam,dx=8,dy=3,zoom=-4)
        custom=pose(cam)
        camera_count=sum(ob.type=='CAMERA' for ob in s.objects)
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='NACELLE_SIDE')
        side=pose(cam)
        assert s.camera==cam and side['location']==custom['location']
        assert side['rotation']!=custom['rotation'] and side['lens']==custom['lens']
        assert sum(ob.type=='CAMERA' for ob in s.objects)==camera_count
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='NACELLE_SIDE')
        assert pose(cam)==side  # Repeated clicks must not overwrite the return view.
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='NACELLE')
        assert pose(cam)==custom
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='DOWN')
        assert pose(down)==old[tid]
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='NACELLE')
        assert pose(cam)==custom and (cam.location-saved).length==0
        bpy.ops.wfrl.gimbal_preset(preset='RESET')
        assert pose(cam)=={k:v for k,v in mounts[tid].items() if k in pose(cam)}
        cameras.reset_gimbal(cam)
        cameras.down_gimbal(cam)
        assert (cam.location-saved).length==0
        for frame in range(1,181):s.frame_set(frame)
        trail=tip_tracking.active(s);points={b:list(v) for b,v in trail.points.items()}
        value=clearance_replay.sample(s);alarm=alarm_state(value)
        for angle in ('DOWN','NACELLE','NACELLE_SIDE','NACELLE','DOWN','NACELLE'):
            bpy.ops.wfrl.farm_flex_view(turbine=tid,angle=angle)
            assert s.frame_current==180 and clearance_replay.sample(s)==value
            assert alarm_state(clearance_replay.sample(s))==alarm
            assert tip_tracking.active(s) is trail
            assert {b:list(v) for b,v in trail.points.items()}==points
        for page in ('RADAR','TOOLS'):
            layout=Buttons();s.wfrl_farm_panel_page=page
            if page=='TOOLS':farm_replay.draw_views(layout,bpy.context)
            else:
                with patch.object(farm_replay,'draw_radar'),patch.object(farm_replay,'draw_playback'):
                    farm_replay.draw(layout,bpy.context)
            for other in ('T1','T2','T3'):
                assert vars(layout.buttons['wfrl.farm_flex_view',other+' Down'])==dict(turbine=other,angle='DOWN')
            assert vars(layout.buttons['wfrl.farm_flex_view','机舱相机'])==dict(turbine=tid,angle='NACELLE')
            assert vars(layout.buttons['wfrl.farm_flex_view','侧下视角'])==dict(turbine=tid,angle='NACELLE_SIDE')
# Synthetic yaw/translation isolates the parent-motion contract from playback.
root=s.camera.parent
with bpy.context.temp_override(area=area,region=region):
    for tid in ('T1','T2','T3'):
        bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='NACELLE')
        for frame in (1,67,901,1801,s.frame_end):
            s.frame_set(frame);bpy.context.view_layer.update()
            cam=s.camera
            expected=cam.parent.matrix_world@Vector(cameras.NACELLE_LOCATION)
            assert (cam.matrix_world.translation-expected).length<1e-4
        bpy.ops.wfrl.farm_transport(action='RESET')
        assert s.frame_current==s.frame_start and (s.camera.location-Vector(cameras.NACELLE_LOCATION)).length<1e-6
    s.frame_set(902)
    # Each new camera retains its independent manual state through save/load.
    for tid in ('T1','T2','T3'):gimbal.change(cameras.ensure_nacelle_gimbal(s,tid),dx=3,dy=2,zoom=-2)
    saved={cam.name:pose(cam) for tid in old for cam in (cameras.ensure_gimbal(s,tid),cameras.ensure_nacelle_gimbal(s,tid))}
    active_name=s.camera.name;value=clearance_replay.sample(s)
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'nacelle.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path))
        s=bpy.context.scene
        assert farm_flex.is_active(s) and s.frame_current==902
        assert s.camera.name==active_name and s.wfrl_gimbal_kind=='NACELLE'
        reloaded=clearance_replay.sample(s)
        assert reloaded==value, {k:(value.get(k),reloaded.get(k)) for k in value if value.get(k)!=reloaded.get(k)}
        for name,state in saved.items():assert pose(s.objects[name])==state
result=dict(status='PASS',blender=bpy.app.version_string,old_down=old,nacelle=mounts,
            independent_ptz=True,mount_reset=True,button_bindings=True,
            same_turbine_trails_retained=True,readings_and_alarm_unchanged=True,
            turbine_data_switch=True,parent_motion_once=True,seek_reset=True,save_reload=True)
(out/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
print('NACELLE_CAMERA_PASS',json.dumps(result),flush=True)

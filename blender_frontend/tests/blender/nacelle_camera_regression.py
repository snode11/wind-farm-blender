"""Independent nacelle PTZ, turbine Down presets and saved-scene contract."""
from pathlib import Path
import importlib, json, math, os, sys, tempfile
import bpy
import numpy as np
from mathutils import Quaternion, Vector
from bpy_extras.object_utils import world_to_camera_view
ROOT=Path(__file__).resolve().parents[3]
runtime=os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if runtime == 'wfrl_blender':
    sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
wfrl_blender=importlib.import_module(runtime)
expected_root=os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(wfrl_blender.__file__).resolve().parent==Path(expected_root).resolve()
if runtime!='wfrl_blender':
    assert not Path(wfrl_blender.__file__).resolve().is_relative_to(ROOT/'blender_frontend')
cameras, farm_flex, clearance_replay, tip_tracking = [importlib.import_module(runtime+'.'+name) for name in ('cameras','farm_flex','clearance_replay','tip_tracking')]
gimbal=importlib.import_module(runtime+'.panels.gimbal')
farm_replay=importlib.import_module(runtime+'.panels.farm_replay')
alarm_state=importlib.import_module(runtime+'.radar_feedback').alarm_state
from types import SimpleNamespace
from unittest.mock import patch
if runtime == 'wfrl_blender':
    assert not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'), 'Source checks require --factory-startup'
    wfrl_blender.register()
else:
    assert runtime in bpy.context.preferences.addons and hasattr(bpy.types.Scene, 'wfrl_gimbal_kind')
wfrl_blender.load_demo_scene(farm_flex.default_package())
s=bpy.context.scene
s.wfrl_flex_show_tip_trails=True
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
                shift_x=cam.data.shift_x,shift_y=cam.data.shift_y,
                yaw=cam['gimbal_yaw'],pitch=cam['gimbal_pitch'],fov=cam['gimbal_fov'],roll=cam['gimbal_roll'])


def assert_down_mount(cam, tid):
    bpy.context.view_layer.update()
    forward = (cam.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    local_axis = Vector((math.cos(math.radians(-82.5))*math.cos(math.radians(181.2)),
                         math.cos(math.radians(-82.5))*math.sin(math.radians(181.2)),
                         math.sin(math.radians(-82.5))))
    expected = (cam.parent.matrix_world.to_3x3() @ local_axis).normalized()
    axis_error = (forward-expected).length
    assert axis_error < 1e-6, (tid, tuple(forward), tuple(expected))
    shell = s.objects[f'WFRL.Turbine.{tid}.Nacelle'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    local = shell.matrix_local
    inverse = local.inverted()
    hit, point, normal, _ = shell.ray_cast(
        inverse @ Vector((-3.5, -1.15, -5)),
        (inverse.to_3x3() @ Vector((0, 0, 1))).normalized())
    assert hit, 'The mounting anchor must intersect the nacelle underside'
    root_normal = (inverse.to_3x3().transposed() @ normal).normalized()
    assert (root_normal - Vector((0, -.55536884, -.83160424))).length < 1e-5, tuple(root_normal)
    anchor, offset = Vector(cam['wfrl_mount_anchor']), Vector(cam['wfrl_mount_offset'])
    assert (anchor-local@point).length < 1e-5
    assert abs(anchor.x + 3.5) < 1e-4 and abs(anchor.y + 1.15) < 1e-4
    assert (offset-Vector((0, 0, -.2151))).length < 1e-6
    assert (cam.location-anchor-offset).length < 1e-6
    origin = inverse @ cam.location
    direction = point-origin
    hit, entry, _, _ = shell.ray_cast(origin, direction.normalized(), distance=direction.length+.01)
    assert hit and (entry-point).length < 1e-4
    return dict(anchor=list(anchor), offset=list(offset), parent_local_preset_axis_error=axis_error)


def assert_screen_controls(cam):
    saved = pose(cam)
    parent = cam.parent
    try:
        for roll in (0., 90., 132.4, 180., 270.):
            for pitch in (-50., -82., -90., -89.9, 89.9, 90.):
                for dx, dy in ((.1, 0.), (-.1, 0.), (0., .1), (0., -.1)):
                    cam['gimbal_roll'] = roll
                    cameras.aim_gimbal(cam, saved['yaw'], pitch, saved['fov'])
                    bpy.context.view_layer.update()
                    local_before = cam.rotation_euler.to_quaternion()
                    forward_before = local_before @ Vector((0, 0, -1))
                    point = cam.matrix_world @ Vector((0., 0., -20.))
                    before = world_to_camera_view(s, cam, point)
                    gimbal.change(cam, dx=dx, dy=dy)
                    bpy.context.view_layer.update()
                    after = world_to_camera_view(s, cam, point)
                    motion = Vector((after.x-before.x, after.y-before.y))
                    if dx:
                        assert motion.x * dx > 0, (roll, pitch, dx, tuple(motion))
                        assert abs(motion.y) < abs(motion.x)*.03+2e-7, (roll, pitch, tuple(motion))
                    else:
                        assert motion.y * dy < 0, (roll, pitch, dy, tuple(motion))
                        assert abs(motion.x) < abs(motion.y)*.03+2e-7, (roll, pitch, tuple(motion))
                    near_pole = abs(math.cos(math.radians(pitch))) < .05
                    if near_pole:
                        expected = (local_before
                                    @ Quaternion((0, 1, 0), math.radians(dx*.05))
                                    @ Quaternion((1, 0, 0), math.radians(dy)))
                        actual = cam.rotation_euler.to_quaternion()
                        for axis in ((1, 0, 0), (0, 1, 0), (0, 0, -1)):
                            assert ((actual@Vector(axis))-(expected@Vector(axis))).length < 2e-6, (roll, pitch, dx, dy, axis)
                        forward_motion = ((actual@Vector((0, 0, -1)))-forward_before).length
                        expected_motion = math.radians(abs(dx)*.05+abs(dy))
                        assert .9*expected_motion < forward_motion < 1.1*expected_motion, (roll, pitch, dx, dy, forward_motion)
                    elif roll in (0., 180.):
                        sign = 1 if roll == 0. else -1
                        np.testing.assert_allclose(
                            [cam['gimbal_yaw'], cam['gimbal_pitch']],
                            [(saved['yaw']+sign*dx) % 360, pitch+sign*dy], rtol=0, atol=1e-10)
                    assert list(cam.location) == saved['location'] and cam.parent == parent
                    if not near_pole:assert cam['gimbal_roll'] == roll
                    assert cam['gimbal_fov'] == saved['fov']
                    assert [cam.data.shift_x, cam.data.shift_y] == [saved['shift_x'], saved['shift_y']]
    finally:
        cam['gimbal_roll'] = saved['roll']
        cameras.aim_gimbal(cam, saved['yaw'], saved['pitch'], saved['fov'])
        bpy.context.view_layer.update()
    assert pose(cam) == saved


down_presets={};down_contracts={};mounts={}
for tid in ('T1','T2','T3'):
    down=cameras.ensure_gimbal(s,tid);cameras.down_gimbal(down)
    assert down.get('wfrl_gimbal_mount_revision')==5
    assert [down[k] for k in ('gimbal_yaw','gimbal_pitch','gimbal_roll','gimbal_fov')]==[181.2,-82.5,175.9,cameras.DOWN_HFOV_DEG]
    np.testing.assert_allclose([down.data.shift_x,down.data.shift_y],[0,0],atol=1e-6)
    assert down.parent==s.objects[f'WFRL.Turbine.{tid}.YawRoot']
    assert down.data.type=='PERSP' and down.data.sensor_fit=='HORIZONTAL'
    np.testing.assert_allclose(np.degrees(down.data.angle_x),cameras.DOWN_HFOV_DEG,rtol=0,atol=1e-5)
    assert abs(down.data.lens - 8.0) < 1e-5
    assert abs(math.degrees(2*math.atan(down.data.sensor_width/(2*down.data.lens)/(16/9))) - 60) < 1e-4
    assert not down.data.show_passepartout and down.data.passepartout_alpha==0
    down_presets[tid]=pose(down)
    down_contracts[tid]=dict(parent=down.parent.name,
                            exterior_mount=assert_down_mount(down,tid),
                            mount_revision=down['wfrl_gimbal_mount_revision'],
                            horizontal_fov_deg=float(np.degrees(down.data.angle_x)),
                            sensor_fit=down.data.sensor_fit,
                            passepartout=down.data.show_passepartout,
                            passepartout_alpha=down.data.passepartout_alpha)
    cam=cameras.ensure_nacelle_gimbal(s,tid)
    assert cam!=down and cam.data!=down.data and cam.parent==down.parent
    # Selecting the camera preserves explicit manual PTZ until reset.
    cameras.aim_gimbal(cam,cam['gimbal_yaw'],cam['gimbal_pitch'],35)
    del cam['wfrl_nacelle_view_revision']
    cameras.ensure_nacelle_gimbal(s,tid)
    assert cam['gimbal_fov']==35
    cameras.aim_gimbal(cam,cam['gimbal_yaw'],cam['gimbal_pitch'],35)
    assert cameras.ensure_nacelle_gimbal(s,tid)['gimbal_fov']==35
    cameras.aim_gimbal(cam,cam['gimbal_yaw'],cam['gimbal_pitch'],60)
    assert cameras.ensure_nacelle_gimbal(s,tid)['gimbal_fov']==60
    cameras.reset_nacelle_gimbal(cam)
    rotor=s.objects[f'WFRL.Turbine.{tid}.Rotor']
    np.testing.assert_allclose(cam.location-rotor.location,[1.5,0,-1.5],atol=1e-6)
    assert cam.data.lens==8 and cam['gimbal_fov']==59
    assert s.objects.get(f'WFRL.Turbine.{tid}.NacelleCameraBracket') is None
    mounts[tid]={**pose(cam), 'hub_offset_m':list(cam.location-rotor.location),
                 'mount_scope':cam['mount_scope']}
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
        assert pose(down)==down_presets[tid]
        assert not down.data.show_passepartout and down.data.passepartout_alpha==0
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
            if angle=='DOWN':assert_down_mount(down,tid)
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
assert_screen_controls(cameras.ensure_gimbal(s, 'T1'))
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
    saved={cam.name:pose(cam) for tid in down_presets for cam in (cameras.ensure_gimbal(s,tid),cameras.ensure_nacelle_gimbal(s,tid))}
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
result=dict(status='PASS',module=runtime,addon_file=wfrl_blender.__file__,
            blender=bpy.app.version_string,down_presets=down_presets,
            down_contracts=down_contracts,nacelle=mounts,
            independent_ptz=True,rolled_ptz_screen_axes=True,pole_ptz_screen_axes=True,
            ptz_projection_cases=120,mount_reset=True,button_bindings=True,
            same_turbine_trails_retained=True,readings_and_alarm_unchanged=True,
            turbine_data_switch=True,parent_motion_once=True,seek_reset=True,save_reload=True)
(out/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
print('NACELLE_CAMERA_PASS',json.dumps(result),flush=True)

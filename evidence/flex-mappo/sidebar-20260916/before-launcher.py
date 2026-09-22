"""Open the physical three-turbine MAPPO clip in Blender, without recording."""
from pathlib import Path
import os
import sys
import bpy
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender
from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal
WFRL_PT_Gimbal.bl_category='View'
wfrl_blender.register()
wfrl_blender.load_demo_scene()
wfrl_blender._cancel_playback()
from wfrl_blender import runtime
runtime.enter_result_replay()
from wfrl_blender import farm_flex,clearance_replay,tip_tracking
from wfrl_blender.cameras import ensure_gimbal,down_gimbal,fill_camera_view

scene=bpy.context.scene
scene.wfrl_show_wake=False
default_package=ROOT/'data' if (ROOT/'data/manifest.json').is_file() else ROOT/'evidence/flex-mappo/package-v1'
folder=Path(os.environ.get('WFRL_FARM_FLEX_PACKAGE',default_package))
preview=farm_flex.attach(scene,folder)
for obj in scene.objects:
    if obj.name.startswith(('WFRL.Turbine.T1','WFRL.Turbine.T2','WFRL.Turbine.T3')):
        if '.ClearanceRadar.' not in obj.name:
            obj.hide_set(False);obj.hide_render=False

camera=bpy.data.objects.new('WFRL.Camera.FarmFlexOverview',bpy.data.cameras.new('FarmFlexOverview'))
scene.collection.objects.link(camera)
camera.location=(-450,-1250,520)
camera.rotation_euler=(Vector((504,0,65))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO';camera.data.ortho_scale=1500
camera.data.clip_start=.1;camera.data.clip_end=30000
camera.data.show_passepartout=False


# A fixed front-quarter view frames the full rotor and its tip paths.
front_camera=bpy.data.objects.new('WFRL.Camera.T1.FrontQuarter',bpy.data.cameras.new('T1FrontQuarter'))
scene.collection.objects.link(front_camera)
front_camera.location=(-245,-140,125)
front_camera.rotation_euler=(Vector((-5,0,90))-front_camera.location).to_track_quat('-Z','Y').to_euler()
front_camera.data.type='ORTHO';front_camera.data.ortho_scale=270
front_camera.data.clip_start=.1;front_camera.data.clip_end=3000
front_camera.data.show_passepartout=False


class WFRL_OT_FarmFlexView(bpy.types.Operator):
    bl_idname='wfrl.farm_flex_view'
    bl_label='查看机组'
    turbine:bpy.props.StringProperty(default='T1')
    angle:bpy.props.EnumProperty(items=[('DOWN','Down',''),('FRONT','侧前方','')],default='DOWN')
    def execute(self,context):
        scene=context.scene
        preview.visible_turbines={0,1,2} if self.turbine=='all' else {int(self.turbine[1:])-1}
        preview.cache.clear()
        # Hidden turbines need no mesh uploads. Switching to overview samples
        # all nine at the current time before revealing them.
        for index,tid in enumerate(('T1','T2','T3')):
            for obj in scene.objects:
                if obj.name.startswith(f'WFRL.Turbine.{tid}.') and '.ClearanceRadar.' not in obj.name and '.TipTrail.' not in obj.name:
                    obj.hide_set(index not in preview.visible_turbines)
                    obj.hide_render=index not in preview.visible_turbines
        if self.turbine=='all':
            camera=scene.objects['WFRL.Camera.FarmFlexOverview']
        else:
            scene['wfrl_clearance_turbine']=self.turbine
            clearance_replay._READERS[scene.as_pointer()]=preview.readers[self.turbine]
            trail=tip_tracking.active(scene)
            if trail is not None and trail.turbine_id != self.turbine:
                tip_tracking.disable(scene,'turbine_change')
            if self.turbine=='T1' and self.angle=='FRONT':
                camera=front_camera
            else:
                camera=ensure_gimbal(scene,self.turbine);down_gimbal(camera)
        scene.camera=camera;scene['wfrl_camera']=camera.name
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type=='VIEW_3D':
                    area.spaces.active.use_local_camera=False
                    area.spaces.active.camera=camera
                    area.spaces.active.region_3d.view_perspective='CAMERA'
                    fill_camera_view(area,scene)
        preview.update(scene)
        if self.turbine != 'all' and scene.wfrl_flex_show_tip_trails:
            tip_tracking.enable(scene,self.turbine,max_points=540)
        trail=tip_tracking.active(scene)
        if trail is not None:
            for obj in trail.objects.values():
                # Wider display tubes keep the complete rotor's paths readable.
                obj.data.bevel_depth=.24 if camera==front_camera else .08
        scene['wfrl_farm_view']='all' if self.turbine=='all' else self.turbine+'_'+self.angle
        return {'FINISHED'}


class WFRL_PT_FarmFlex(bpy.types.Panel):
    bl_label='三机 MAPPO · 叶片摆动'
    bl_idname='WFRL_PT_FarmFlex'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='View';bl_order=-300
    def draw(self,context):
        layout=self.layout;scene=context.scene
        layout.label(text='随机阵风 · 三机独立形变 · 60 秒')
        layout.label(text='MAPPO 偏航 · 保守转速目标与变桨')
        layout.prop(scene,'wfrl_flex_show_tip_trails',text='显示彩色叶尖追踪线',toggle=True)
        layout.label(text='当前机组：叶片 1 橙 · 2 蓝 · 3 红')
        row=layout.row(align=True)
        for tid in ('T1','T2','T3','all'):
            row.operator('wfrl.farm_flex_view',text='全景' if tid=='all' else tid+' Down').turbine=tid
        op=layout.operator('wfrl.farm_flex_view',text='T1 侧前方 · 叶轮与轨迹',icon='CAMERA_DATA',
                           depress=scene.get('wfrl_farm_view')=='T1_FRONT')
        op.turbine='T1';op.angle='FRONT'
        layout.operator('wfrl.clearance_playback',text='播放 / 暂停',icon='PLAY')
        layout.prop(scene,'wfrl_clearance_progress',text='进度',slider=True)
        layout.operator('wfrl.clearance_restart',text='从头重播',icon='LOOP_BACK')
        value=clearance_replay.sample(scene)
        if value:
            motion=value['motion']
            layout.label(text=f"{scene['wfrl_clearance_turbine']} · 转速 {motion['rotor_speed_rpm']:.2f} rpm · 偏航 {motion['yaw_deg']:.2f}°")
            layout.label(text=f"播放 {value['time_s']-preview.times[0]:.1f} / {preview.times[-1]-preview.times[0]:.0f} 秒")
            from wfrl_blender.panels.clearance import draw_measurement
            draw_measurement(layout,scene,value)
        layout.label(text='当前为开发验收包，数值细化待验证')


for cls in (WFRL_OT_FarmFlexView,WFRL_PT_FarmFlex):bpy.utils.register_class(cls)
scene.wfrl_flex_show_tip_trails=True
bpy.ops.wfrl.farm_flex_view(turbine='T1')
scene.frame_set(1)
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':
            area.spaces.active.show_region_ui=True
            area.spaces.active.overlay.show_overlays=False
            area.spaces.active.shading.type='MATERIAL'
print('FARM_FLEX_READY',flush=True)

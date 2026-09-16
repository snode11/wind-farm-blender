"""Open an 18-second, true-amplitude single-turbine flexibility proof."""
from pathlib import Path
import runpy
import bpy

ROOT = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT/'scripts/blender/open_blade_tip_demo.py'))
from wfrl_blender import clearance_replay, blade_flex_preview
from wfrl_blender.cameras import ensure_gimbal, down_gimbal, fill_camera_view

scene = bpy.context.scene
scene.render.fps = 60
scene.render.fps_base = 1
scene['wfrl_clearance_timebase_fps'] = 60.
clearance_replay.load(scene, scene.wfrl_clearance_near_tower_path, 'near_tower')
preview = blade_flex_preview.attach(scene, ROOT/'evidence/blade-flex-short/close-flex.npz')
scene.camera = ensure_gimbal(scene, 'T1')
down_gimbal(scene.camera)
from mathutils import Vector
camera = bpy.data.objects.new('WFRL.Camera.FlexOverview', bpy.data.cameras.new('FlexOverview'))
scene.collection.objects.link(camera)
# Rotor front is yaw-local -X; the old mostly -Y view collapsed the rotor.
camera.parent = scene.objects['WFRL.Turbine.T1.YawRoot']
camera.location = (-260, -55, 0)
camera.rotation_euler = (Vector((-5, 0, -12))-camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 360
camera.data.show_passepartout = False
camera.data.passepartout_alpha = 0
scene.camera = camera
scene['wfrl_camera'] = camera.name
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.camera = camera
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            fill_camera_view(area, scene)
scene.frame_set(1)
scene.sync_mode = 'FRAME_DROP'
scene['wfrl_flex_provenance'] = '单机形变验证；12 m/s 稳态风，固定转速；未接入 MAPPO'

class WFRL_PT_FlexPreview(bpy.types.Panel):
    bl_label = '叶片摆动短片段'
    bl_idname = 'WFRL_PT_FlexPreview'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'View'
    bl_order = -100
    def draw(self, context):
        layout = self.layout
        trails = layout.row()
        trails.scale_y = 1.5
        trails.prop(context.scene, 'wfrl_flex_show_tip_trails', text='显示彩色叶尖追踪线', toggle=True, icon='CURVE_PATH')
        layout.label(text='叶片 1 橙色 · 叶片 2 蓝色 · 叶片 3 红色')
        layout.label(text=context.scene.get('wfrl_flex_title', 'FAST.Farm 单机 · 18 秒 · 12 m/s'))
        layout.label(text='真实幅度 · 固定转速 · 60 FPS 目标')
        if context.scene.get('wfrl_flex_wind_note'):
            layout.label(text=context.scene['wfrl_flex_wind_note'])
            layout.label(text='离线预览 · 数值细化尚未验证')
        layout.label(text='本片段用于形变验证，尚未接入 MAPPO')
        layout.operator('wfrl.clearance_playback', text='播放 / 暂停', icon='PLAY')
        layout.prop(context.scene, 'wfrl_clearance_progress', text='进度', slider=True)
        layout.operator('wfrl.clearance_restart', text='从头重播', icon='LOOP_BACK')
        layout.operator('wfrl.select_camera', text='看整片叶片').camera_name = 'WFRL.Camera.FlexOverview'

bpy.utils.register_class(WFRL_PT_FlexPreview)
# The original white blade surface and tip paint remain. Other turbines are
# hidden in this isolated proof so their rigid animation is not misread as data.
for obj in scene.objects:
    if obj.name.startswith(('WFRL.Turbine.T2', 'WFRL.Turbine.T3')):
        obj.hide_set(True)
        obj.hide_render = True
print('BLADE_FLEX_PREVIEW_READY', flush=True)

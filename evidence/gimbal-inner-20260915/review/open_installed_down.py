import sys
sys.path.insert(0, '/Users/eason/Library/Application Support/Blender/5.2/extensions/user_default')
import bpy
import wfrl_blender
wfrl_blender.register()
wfrl_blender.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_clearance_show_camera = True
scene.frame_set(150)
from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal
bpy.utils.unregister_class(WFRL_PT_Gimbal)
WFRL_PT_Gimbal.bl_category = 'View'
bpy.utils.register_class(WFRL_PT_Gimbal)
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
        with bpy.context.temp_override(area=area):
            bpy.ops.wfrl.gimbal_preset(preset='DOWN')
print('INSTALLED_DOWN_DEMO_READY', flush=True)

"""Development-only refresh for the current WFRL window."""
import bpy, importlib
import wfrl_blender
import wfrl_blender.panels.demo as panels
wfrl_blender.unregister()
importlib.reload(panels)
importlib.reload(wfrl_blender)
wfrl_blender.register()
import wfrl_blender.presentation as presentation
import wfrl_blender.workspace as workspace
presentation.unregister_overlay()
importlib.reload(presentation); importlib.reload(workspace)
presentation.register_overlay()

def refresh():
    area = max(bpy.context.screen.areas, key=lambda a:a.width*a.height)
    area.type = 'VIEW_3D'
    if len(bpy.context.screen.areas)>1:
        with bpy.context.temp_override(area=area):
            bpy.ops.screen.screen_full_area(use_hide_panels=False)
    workspace.configure_presentation()
    bpy.context.scene.sync_mode = 'FRAME_DROP'
    bpy.context.scene.wfrl_show_lidar = False
    for obj in bpy.context.scene.objects:
        if obj.name.startswith('WFRL.Fixture.DisXY'):
            obj.hide_set(True)
    def capture():
        bpy.ops.wm.save_as_mainfile(filepath='/Users/eason/Desktop/wfcrl/wind farm RL/evidence/part2/wfrl_part2_static.blend')
        bpy.ops.screen.screenshot(filepath='/Users/eason/Desktop/wfcrl/wind farm RL/evidence/part2/workspace.png')
        exec(compile(open('/Users/eason/Desktop/wfcrl/wind farm RL/scripts/blender/gui_dual_check.py').read(), 'gui_dual_check.py', 'exec'), {})
        return None
    bpy.app.timers.register(capture,first_interval=3)
    return None
bpy.app.timers.register(refresh, first_interval=.3)

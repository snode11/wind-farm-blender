"""Run the GUI layout check in its own clean process and exit normally."""
from pathlib import Path
import runpy
import bpy
root = Path(__file__).resolve().parents[2]
runpy.run_path(str(root/'scripts/blender/open_part2.py'),run_name='__main__')
import wfrl_blender
wfrl_blender.load_demo_scene()
bpy.context.scene.camera = bpy.data.objects['WFRL.Camera.World']
for a in bpy.context.screen.areas:
    if a.type == 'VIEW_3D': a.spaces.active.use_local_camera = False
bpy.context.scene.frame_set(451)
bpy.context.scene['wfrl_run_status']='PAUSED'
def review():
    bpy.ops.wm.save_as_mainfile(filepath=str(root/"evidence/part2/wfrl_part2_static.blend"))
    bpy.ops.screen.screenshot(filepath=str(root/"evidence/part2/workspace.png"))
    runpy.run_path(str(root/'scripts/blender/gui_dual_check.py'),run_name='__main__')
    return None
def finish():
    bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(review,first_interval=3)
bpy.app.timers.register(finish,first_interval=11,persistent=True)

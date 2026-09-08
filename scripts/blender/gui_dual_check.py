import bpy, json, traceback
from pathlib import Path
out = Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/part2')
try:
    area = max((a for a in bpy.context.screen.areas if a.type=='VIEW_3D'), key=lambda a:a.width*a.height)
    with bpy.context.temp_override(area=area):
        result = bpy.ops.wfrl.dual_view()
    def capture_dual():
        try:
            local = [a for a in bpy.context.screen.areas if a.type=='VIEW_3D' and a.spaces.active.use_local_camera]
            assert len([a for a in bpy.context.screen.areas if a.type=='VIEW_3D']) >= 2
            assert not bpy.context.screen.show_fullscreen
            assert len(local)==1
            assert local[0].spaces.active.camera.name=='WFRL.Camera.T1.Sensor'
            bpy.ops.screen.screenshot(filepath=str(out/'workspace_dual.png'))
            bpy.ops.wm.save_as_mainfile(filepath=str(out/'wfrl_part2_dual.blend'))
            (out/'gui_dual_check.json').write_text(json.dumps({'pass':True,'local_camera':local[0].spaces.active.camera.name}))
            bpy.ops.wm.open_mainfile(filepath=str(out/'wfrl_part2_static.blend'))
            bpy.ops.wfrl.demo_reset()
            bpy.ops.wm.save_as_mainfile(filepath=str(out/'wfrl_part2_static.blend'))
        except Exception:
            (out/'gui_dual_check.json').write_text(json.dumps({'pass':False,'error':traceback.format_exc()}))
        return None
    bpy.app.timers.register(capture_dual,first_interval=3)
except Exception:
    (out/'gui_dual_check.json').write_text(json.dumps({'pass':False,'error':traceback.format_exc()}))

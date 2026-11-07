import json
from pathlib import Path
import bpy,numpy as np
OUT=Path('/Users/eason/Desktop/wfcrl/blade_recon/out/trial-20261004/blender/compat')
ob=bpy.data.objects['Recon_Blade1']; a=np.array([v.co[:] for v in ob.data.vertices])
old=bpy.context.scene.frame_current
handlers=[getattr(f,'__name__',str(f)) for f in bpy.app.handlers.frame_change_pre]
bpy.context.scene.frame_set(51); bpy.context.view_layer.update()
b=np.array([v.co[:] for v in ob.data.vertices])
r={'saved_scene':bpy.data.filepath,'saved_frame':old,'loaded_handler_count':len(handlers),'loaded_handlers':handlers,'frame51_vs_saved_frame1_max_vertex_change_m':float(np.max(abs(a-b))),'active_camera':bpy.context.scene.camera.name if bpy.context.scene.camera else None,'conclusion':'Opening saved .blend does not restore the script frame handler. Re-run the compatibility wrapper for playback.'}
(OUT/'saved_scene_reopen_probe.json').write_text(json.dumps(r,indent=2)); print(json.dumps(r,indent=2))

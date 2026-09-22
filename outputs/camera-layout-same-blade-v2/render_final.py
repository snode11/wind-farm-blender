from pathlib import Path
import sys,json,traceback,os
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import custom_cameras as c,native_camera_views as native,farm_flex
OUT=Path(__file__).parent;state={}
def tick():
 try:
  if not state:
   addon.register();addon.load_demo_scene();s=bpy.context.scene;s.frame_set(1);bpy.context.view_layer.update()
   c.import_layout(s,json.loads((OUT/'T1-same-blade-four-cameras-v2.json').read_text()),overwrite=True)
   area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');state['area']=area
   with bpy.context.temp_override(area=area):bpy.ops.wfrl.native_camera_view(mode='QUAD')
   state['phase']=1;return 3.
  if state['phase']==1:
   if native._ACTIVE is None or len(native._ACTIVE.entries)!=4:return 1.
   bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=2)
   with bpy.context.temp_override(window=native._ACTIVE.window):bpy.ops.screen.screenshot(filepath=str(OUT/'quad-final.png'))
   obj,rest,*_=next(r for r in farm_flex._ACTIVE.blades if r[0].name=='WFRL.Turbine.T1.Blade1')
   state['obj']=obj;state['mats']=list(obj.data.materials);state['indices']=[p.material_index for p in obj.data.polygons]
   obj.data.materials.clear()
   for name,color in [('Root-blue',(.02,.2,.85,1)),('Mid-inner-green',(.04,.55,.12,1)),('Mid-outer-orange',(1,.32,.015,1)),('Tip-red',(.85,.025,.08,1))]:
    mat=bpy.data.materials.new('LayoutDiagnostic.'+name);mat.use_nodes=True;mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=color;mat.diffuse_color=color;obj.data.materials.append(mat)
   spans=rest[:,2]-1.5
   for p in obj.data.polygons:p.material_index=int(np.searchsorted([15.,31.,47.],spans[list(p.vertices)].mean()))
   obj.data.update();state['phase']=2;return 3.
  if state['phase']==2:
   bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=2)
   with bpy.context.temp_override(window=native._ACTIVE.window):bpy.ops.screen.screenshot(filepath=str(OUT/'quad-segment-diagnostic.png'))
   obj=state['obj'];obj.data.materials.clear()
   for mat in state['mats']:obj.data.materials.append(mat)
   for p,index in zip(obj.data.polygons,state['indices']):p.material_index=index
   obj.data.update();native.shutdown()
   (OUT/'final-render-check.json').write_text(json.dumps({'status':'PASS','reference_frame':1,'time_s':bpy.context.scene['wfrl_clearance_time_s'],'blade':'T1.Blade1','normal_image':'quad-final.png','diagnostic_image':'quad-segment-diagnostic.png','diagnostic_only':'Temporary span colors in isolated process, not part of JSON or user scene'}))
   print('FINAL_RENDER_PASS',flush=True)
 except Exception:
  (OUT/'final-render-error.txt').write_text(traceback.format_exc());print(traceback.format_exc(),flush=True)
 bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick,first_interval=1.)

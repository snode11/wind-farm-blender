from pathlib import Path
import sys,json,traceback,os
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import custom_cameras as c,native_camera_views as native,custom_camera_preview as preview,custom_camera_capture as capture
OUT=Path(__file__).parent
state={}
def tick():
 try:
  if not state:
   addon.register();addon.load_demo_scene();s=bpy.context.scene;s.frame_set(1);bpy.context.view_layer.update()
   candidates=json.loads((OUT/'candidate-analysis.json').read_text())
   # All four are on the same nacelle side. Target spans come from the same B1 mesh.
   selected=[9,6,8,11]
   labels=['B1 叶根 0–16.5m','B1 中内段 15–32m','B1 中外段 30.5–47.5m','B1 叶尖 46–61.5m']
   for slot,index in enumerate(selected,1):
    candidate=candidates[index];y,p,r,h,v=candidate['segments'][slot-1]['ypr_hv']
    cam=c.begin_draft(s,slot);anchor=c.SurfaceAnchor(**candidate['anchor'])
    params=c.CameraParameters(location=tuple(candidate['location']),yaw=y,pitch=p,roll=r,fov=h,vfov=v,output_long_edge_px=1920)
    c.apply_parameters(cam,params,anchor);cam['custom_label']=labels[slot-1];c.commit_draft(s,slot,cam)
   layout=c.layout_dict(s);c.validate_layout(s,layout)
   (OUT/'T1-same-blade-four-cameras-v2.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2))
   (OUT/'selected-candidates.json').write_text(json.dumps([candidates[i] for i in selected],ensure_ascii=False,indent=2))
   area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D');state['area']=area
   with bpy.context.temp_override(area=area):bpy.ops.wfrl.native_camera_view(mode='QUAD')
   state['phase']=1;return 3.
  if state['phase']==1:
   if native._ACTIVE is None or len(native._ACTIVE.entries)!=4:return 1.
   bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=2)
   with bpy.context.temp_override(window=native._ACTIVE.window):bpy.ops.screen.screenshot(filepath=str(OUT/'quad-reference.png'))
   native.shutdown();state['phase']=2;return 1.
  if state['phase']==2:
   area=state['area'];region=next(r for r in area.regions if r.type=='WINDOW')
   with bpy.context.temp_override(area=area,region=region):
    images=preview.render_group(bpy.context,c.enabled_cameras(bpy.context.scene),1280)
    for slot,image in images.items():
     capture.write_png(OUT/f'C{slot}-reference.png',image.width,image.height,image.rgba());image.free()
   (OUT/'preview-check.json').write_text(json.dumps({'status':'PASS','frame':1,'time_s':bpy.context.scene['wfrl_clearance_time_s'],'blade':'T1.Blade1','source':'actual scene geometry; no recoloring or physical object hiding'}))
   print('PREVIEW_PASS',flush=True)
 except Exception:
  (OUT/'preview-error.txt').write_text(traceback.format_exc());print(traceback.format_exc(),flush=True)
 bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick,first_interval=1.)

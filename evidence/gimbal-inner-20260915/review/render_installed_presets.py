import runpy
from pathlib import Path
import bpy
root=Path('/Users/eason/Desktop/wfcrl/wind farm RL')
runpy.run_path(str(root/'evidence/gimbal-inner-20260915/review/open_installed_down.py'))
scene=bpy.context.scene
scene.render.engine='BLENDER_EEVEE'
scene.eevee.taa_render_samples=16
scene.render.resolution_x=960;scene.render.resolution_y=540;scene.render.resolution_percentage=100
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
with bpy.context.temp_override(area=area):
 for turbine in ('T1','T2','T3'):
  scene.wfrl_gimbal_turbine=turbine
  for preset in ('DOWN','RESET'):
   bpy.ops.wfrl.gimbal_preset(preset=preset)
   scene.camera=area.spaces.active.camera
   scene.render.filepath=str(root/f'evidence/gimbal-inner-20260915/review/{turbine}-{preset}.png')
   bpy.ops.render.render(write_still=True)
print('INSTALLED_PRESET_RENDER_PASS')

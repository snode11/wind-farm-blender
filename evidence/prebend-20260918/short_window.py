import os,runpy,bpy
from pathlib import Path
root=Path(__file__).resolve().parents[2]
os.environ['WFRL_FARM_FLEX_PACKAGE']=str(root/'results/prebend-20260918/short-replay')
runpy.run_path(str(root/'scripts/blender/open_farm_flex.py'))
bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='FRONT')
bpy.context.scene.frame_set(151)
bpy.context.scene.wfrl_deflection_visible=True

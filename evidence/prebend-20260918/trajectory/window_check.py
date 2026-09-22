from pathlib import Path
import runpy,bpy
root=Path(__file__).resolve().parents[3]
runpy.run_path(str(root/'scripts/blender/open_farm_flex.py'))
bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='FRONT')
for f in range(1,181):bpy.context.scene.frame_set(f)

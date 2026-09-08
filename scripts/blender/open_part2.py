"""Open the saved review workspace in a fresh Blender process, without backend imports."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
import bpy
import wfrl_blender
wfrl_blender.register()
filename = ROOT/'evidence/part2/wfrl_part2_static.blend'
if filename.exists():
    bpy.ops.wm.open_mainfile(filepath=str(filename))
else:
    wfrl_blender.load_demo_scene()
from wfrl_blender.workspace import configure_presentation
configure_presentation()
bpy.context.scene.sync_mode = 'FRAME_DROP'
print('WFRL_GUI_READY',flush=True)

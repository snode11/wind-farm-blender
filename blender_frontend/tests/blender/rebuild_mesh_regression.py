"""Demo reconstruction must not accumulate unused generated meshes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy,wfrl_blender
wfrl_blender.register()
user_mesh=bpy.data.meshes.new('User.Unused.Keep')
counts=[]
for i in range(2):
 wfrl_blender.load_demo_scene()
 counts.append(len(bpy.data.meshes))
 assert bpy.data.meshes.get('User.Unused.Keep') is not None
print('REBUILD_MESH_COUNTS',counts)
assert counts[0]==counts[1],counts
print('REBUILD_MESH_REGRESSION=PASS')

"""Low-cost environment review using existing Part 5 geometry and current shaders/camera."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'blender_frontend'))
import bpy
from mathutils import Vector
from wfrl_blender.landscape import terrain_shader
from wfrl_blender.cameras import WORLD_LOCATION, WORLD_TARGET, WORLD_LENS_MM
from bpy_extras.object_utils import world_to_camera_view
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'evidence/part5_details/part5_details.blend'))
s=bpy.context.scene
terrain=bpy.data.objects['WFRL.Terrain'];mat=terrain.data.materials[0]
terrain_shader(mat)
for o in s.objects:
    if o.name.startswith('WFRL.Landscape.DistantRidge'):
        o.data.materials.clear();o.data.materials.append(mat)
camera=bpy.data.objects['WFRL.Camera.World'];s.camera=camera
camera.location=WORLD_LOCATION
camera.rotation_euler=(Vector(WORLD_TARGET)-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.lens=WORLD_LENS_MM
bpy.context.view_layer.update()
for tid in ('T1','T2','T3'):
    root=bpy.data.objects['WFRL.Turbine.'+tid]
    for dx in (-70,70):
        for dy in (-70,70):
            for z in (0,155):
                p=world_to_camera_view(s,camera,root.location+Vector((dx,dy,z)))
                assert .02<p.x<.98 and .02<p.y<.98 and p.z>0,(tid,tuple(p))
print('FRAMING_PASS')
s.render.engine='CYCLES';s.cycles.samples=12;s.cycles.use_denoising=True
s.render.threads_mode='FIXED';s.render.threads=4
s.render.resolution_x=960;s.render.resolution_y=540;s.render.resolution_percentage=100
out=ROOT/'evidence/environment_revision';out.mkdir(exist_ok=True)
s.render.filepath=str(out/'overview.png')
bpy.ops.render.render(write_still=True)
print('ENVIRONMENT_PREVIEW_PASS')

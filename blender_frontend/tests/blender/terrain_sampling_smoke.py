"""Compare cached terrain queries with Blender's actual mesh ray casts."""
import random
from pathlib import Path
import sys
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from wfrl_blender import landscape
assert hasattr(landscape, 'terrain_sampler'), 'Terrain queries must reuse one spatial index'
mesh=bpy.data.meshes.new('TerrainTest')
vertices=[(x,y, .1*x*x+.2*y*y) for y in range(-6,7) for x in range(-6,7)]
faces=[]
for y in range(12):
    for x in range(12):
        k=y*13+x;faces.append((k,k+1,k+14,k+13))
mesh.from_pydata(vertices,[],faces);mesh.update()
obj=bpy.data.objects.new('TerrainTest',mesh);bpy.context.collection.objects.link(obj)
bpy.context.view_layer.update()
sample=landscape.terrain_sampler(obj)
rng=random.Random(29);worst=0.
for _ in range(1000):
    x,y=rng.uniform(-5.99,5.99),rng.uniform(-5.99,5.99)
    hit,point,_,_=obj.ray_cast(Vector((x,y,1500)),Vector((0,0,-1)))
    assert hit
    error=abs(sample(x,y)-point.z);worst=max(worst,error)
    assert error < 0.0002, error
assert abs(sample(100,100)-landscape.height(100,100)) < 1e-9
print('TERRAIN_SAMPLING_PASS max_error_m=',worst)

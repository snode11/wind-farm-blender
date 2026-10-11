"""Native Cycles shader regression for tip/root clamp and periodic seam.

Render tiny emission swatches from a two-row high-contrast RGBA diagnostic atlas.
This verifies actual Blender image-node sampling, not only node graph structure.
"""
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib

import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/blade-recon-boundary')).resolve()
OUT.mkdir(parents=True,exist_ok=True)
MODULE=os.environ.get('WFRL_ADDON_MODULE','wfrl_blender')
if MODULE=='wfrl_blender':
    sys.path.insert(0,str(ROOT/'blender_frontend'))
review=importlib.import_module(MODULE+'.blade_recon_review')
expected=os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(review.__file__).resolve().is_relative_to(Path(expected).resolve())

def chunk(kind,body):
    return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
body=b'\x00'+b'\xff\xff\xff\xff'+b'\x00\x00\x00\xff'
body+=b'\x00'+b'\x00\x00\x00\xff'+b'\xff\xff\xff\xff'
png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,6,0,0,0))+
     chunk(b'IDAT',zlib.compress(body))+chunk(b'IEND',b''))
path=OUT/'boundary-input.png'
path.write_bytes(png)
scene=bpy.data.scenes.new('V02.BoundaryShader')
bpy.context.window.scene=scene
scene.render.engine='CYCLES'
scene.cycles.device='CPU'
scene.cycles.samples=64
scene.render.resolution_x=scene.render.resolution_y=8
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='OPEN_EXR'
scene.render.image_settings.color_depth='32'
scene.render.image_settings.color_mode='RGBA'
scene.view_settings.view_transform='Standard'
mat=review._texture_material('V02.BoundaryMaterial',path,{'W':2,'H':2})
mat.node_tree.nodes['SurfaceTexture'].image.colorspace_settings.name='Non-Color'
output=mat.node_tree.nodes.get('Material Output')
emission=mat.node_tree.nodes.new('ShaderNodeEmission')
mat.node_tree.links.new(mat.node_tree.nodes['TexMix'].outputs[0],emission.inputs['Color'])
mat.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
mesh=bpy.data.meshes.new('V02.BoundaryPlane')
mesh.from_pydata([(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)],[],[(0,1,2,3)])
mesh.materials.append(mat)
mesh.uv_layers.new(name='atlas')
obj=bpy.data.objects.new('V02.BoundaryPlane',mesh)
scene.collection.objects.link(obj)
cam_data=bpy.data.cameras.new('V02.BoundaryCamera')
cam_data.type='ORTHO'
cam_data.ortho_scale=2
cam=bpy.data.objects.new(cam_data.name,cam_data)
cam.location=(0,0,3)
scene.collection.objects.link(cam)
scene.camera=cam
report={'status':'RUNNING','review_file':review.__file__,'blender':bpy.app.version_string,'samples':[]}
for name,u,v,target in [('tip',.25,0,0),('root',.25,1,1),('seam0',0,0,.5),('seam1',1,0,.5)]:
    mesh.uv_layers['atlas'].data.foreach_set('uv',np.array([[u,v]]*4,np.float32).reshape(-1))
    mesh.update()
    scene.render.filepath=str(OUT/(name+'.exr'))
    bpy.ops.render.render(write_still=True)
    result=bpy.data.images.load(scene.render.filepath,check_existing=False)
    values=np.array(result.pixels[:],np.float32).reshape(8,8,4)
    sample=float(values[3:5,3:5,:3].mean())
    assert abs(sample-target)<.02,(name,sample,target)
    report['samples'].append({'name':name,'uv':[u,v],'rendered_linear_value':sample,'expected':target})
    bpy.data.images.remove(result)
white=report['samples'][1]['rendered_linear_value']
for row in report['samples']:
    row['normalized_value']=row['rendered_linear_value']/white
    assert abs(row['normalized_value']-row['expected'])<.002,row
report['status']='PASS'
(OUT/'boundary.json').write_text(json.dumps(report,indent=2))
print('BLADE_RECON_TEXTURE_NATIVE_BOUNDARY_PASS')

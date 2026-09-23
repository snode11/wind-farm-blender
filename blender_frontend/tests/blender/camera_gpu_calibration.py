"""Actual GPU corner/colour/roll calibration; run in a visible Blender window."""
import os
from pathlib import Path
import sys
import json
import traceback
import numpy as np
import bpy
from mathutils import Vector
sys.path[:0]=[str(Path(__file__).resolve().parents[3]/'blender_frontend')]
from wfrl_blender import custom_cameras as core, custom_camera_preview as preview
from wfrl_blender import custom_camera_capture as capture
OUT=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-gpu-calibration'));OUT.mkdir(parents=True,exist_ok=True)


def run():
    try:
        scene=bpy.context.scene
        for obj in list(scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
        scene.view_settings.view_transform='Standard'
        scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
        root=bpy.data.objects.new(core.ROOT_NAME,None);scene.collection.objects.link(root)
        # RGB planar markers with asymmetric positions. Opaque black backing
        # supplies a clean fixture; neither is an overlay or 2D draw primitive.
        positions=[(10,-1.2,1.6),(10,1.4,.7),(10,-.4,-1.5)]
        for i,(x,y,z) in enumerate(positions):
            mesh=bpy.data.meshes.new('MarkerMesh')
            mesh.from_pydata([(x,y-.13,z-.13),(x,y+.13,z-.13),(x,y+.13,z+.13),(x,y-.13,z+.13)],[],[(0,1,2,3)])
            obj=bpy.data.objects.new(f'Marker{i}',mesh);scene.collection.objects.link(obj)
            material=bpy.data.materials.new(f'Color{i}');material.use_nodes=True
            tree=material.node_tree;tree.nodes.clear()
            emission=tree.nodes.new('ShaderNodeEmission');output=tree.nodes.new('ShaderNodeOutputMaterial')
            color=[0,0,0,1];color[i]=1;emission.inputs['Color'].default_value=color
            tree.links.new(emission.outputs[0],output.inputs['Surface']);mesh.materials.append(material)
        def patch(name,x,y,z,size,color):
            mesh=bpy.data.meshes.new(name)
            mesh.from_pydata([(x,y-size,z-size),(x,y+size,z-size),(x,y+size,z+size),(x,y-size,z+size)],[],[(0,1,2,3)])
            obj=bpy.data.objects.new(name,mesh);scene.collection.objects.link(obj)
            material=bpy.data.materials.new(name);material.use_nodes=True
            tree=material.node_tree;tree.nodes.clear()
            emission=tree.nodes.new('ShaderNodeEmission');output=tree.nodes.new('ShaderNodeOutputMaterial')
            emission.inputs['Color'].default_value=(*color,1)
            tree.links.new(emission.outputs[0],output.inputs['Surface']);mesh.materials.append(material)
        for row in range(6):
            for col in range(6):
                level=.02 if (row+col)%2 else .6
                patch(f'Checker{row}-{col}',10.1,row-2.5,col-2.5,.5,(level,level,level))
        gray=(10,1.6,-1.5)
        patch('Gray18',*gray,.2,(.18,.18,.18))
        cam=bpy.data.objects.new('CalibrationCamera',bpy.data.cameras.new('CalibrationCamera'))
        scene.collection.objects.link(cam);cam.parent=root
        cam['wfrl_custom_slot']=1
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        region=next(r for r in area.regions if r.type=='WINDOW')
        checks=[]
        with bpy.context.temp_override(area=area,region=region):
            for roll in (0,37,90,180):
                core.apply_parameters(cam,core.CameraParameters((0,0,0),0,0,roll,90,60))
                bpy.context.view_layer.update()
                image=preview.render_group(bpy.context,[cam],640)[1]
                raw=image.rgba()
                pixels=np.frombuffer(raw,np.uint8).reshape(image.height,image.width,4)[::-1]
                for i,point in enumerate(positions):
                    other=[j for j in range(3) if j!=i]
                    mask=(pixels[:,:,i]>240)&(pixels[:,:,other[0]]<20)&(pixels[:,:,other[1]]<20)
                    ys,xs=np.nonzero(mask)
                    assert len(xs)>20,(roll,i,'missing colour marker')
                    q=image.projection @ image.view @ Vector((*point,1))
                    expected=np.array([(q.x/q.w+1)*image.width/2-.5,(1-q.y/q.w)*image.height/2-.5])
                    measured=np.array([xs.mean(),ys.mean()])
                    error=np.linalg.norm(measured-expected,ord=np.inf)
                    assert error<=.5,(roll,i,measured,expected,error)
                    checks.append({'roll':roll,'channel':i,'centre_error_px':float(error),
                                   'mean_rgb':pixels[mask,:3].mean(axis=0).tolist()})
                q=image.projection @ image.view @ Vector((*gray,1))
                gx=round((q.x/q.w+1)*image.width/2-.5);gy=round((1-q.y/q.w)*image.height/2-.5)
                # 18% scene-linear gray becomes ~118/255 after one sRGB display
                # transform. A second transform would be ~181, no transform ~46.
                assert np.max(np.abs(pixels[gy,gx,:3].astype(float)-118))<=3,(roll,pixels[gy,gx,:3])
                capture.write_png(OUT/f'calibration-roll-{roll}.png',image.width,image.height,raw)
                image.free()
        result={'status':'PASS','blender':bpy.app.version_string,'checks':checks}
        (OUT/'result.json').write_text(json.dumps(result,indent=2));print('GPU_CALIBRATION_PASS',json.dumps(result),flush=True)
    except Exception:
        error=traceback.format_exc();print(error,flush=True)
        (OUT/'result.json').write_text(json.dumps({'status':'FAIL','error':error}))
    bpy.ops.wm.quit_blender()

bpy.app.timers.register(run,first_interval=1.)

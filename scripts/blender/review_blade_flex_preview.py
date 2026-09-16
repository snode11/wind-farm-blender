"""Verify the source preview against physical section transforms and render it."""
from pathlib import Path
import json, os, runpy, time
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'evidence/blade-flex-short'
loaded=runpy.run_path(str(ROOT/'scripts/blender/open_blade_flex_preview.py'))
preview=loaded['preview']
from wfrl_blender import clearance_replay
from wfrl.lidar.physics import read_surface
scene=bpy.context.scene
assert scene.frame_end==1081 and scene.render.fps==60
records=[]
for frame in (1, 31, 301, 601, 1081, 301):
    scene.frame_set(frame)
    value=clearance_replay.sample(scene)
    assert abs(value['time_s']-(18+(frame-1)/60))<1e-8
    bpy.context.view_layer.update()
    for blade, rest, *_ in preview.blades:
        root = np.flatnonzero(rest[:, 2] < 1.501)
        actual = np.array([blade.data.vertices[int(i)].co[:] for i in root])
        assert np.max(np.linalg.norm(actual-rest[root], axis=1)) < .001
    # The numerical audit compares transformed source rings, independently of
    # the presentation tip taper. This is not a full mesh clearance claim.
    source_frame=round(value['time_s']*80)
    max_err=0.
    for bid in (1,2,3):
        p,_=read_surface(ROOT/f'results/lidar/raw/close-v1/FarmInputs/vtk/Case.T1.Blade{bid}Surface.{source_frame:05d}.vtp')
        ref,_=read_surface(ROOT/f'results/lidar/raw/close-v1/FarmInputs/vtk/Case.T1.Blade{bid}Surface.00000.vtp')
        idx=round((value['time_s']-18)*80)
        tr=preview.transforms[idx,bid-1]
        mapped=np.einsum('sij,snj->sni',tr[:,:,:3],ref.reshape(19,-1,3))+tr[:,:,3,None].transpose(0,2,1)
        max_err=max(max_err,float(np.max(np.linalg.norm(mapped-p.reshape(19,-1,3),axis=-1))))
    assert max_err<.0001,max_err
    if value['measurement']:
        assert value['measurement']['time_s']<=value['time_s']+1e-8
    records.append(dict(frame=frame,time_s=value['time_s'],section_error_m=max_err))
# Repeated seeking must give exactly the same mesh.
obj=scene.objects['WFRL.Turbine.T1.Blade1']
a=np.array([v.co[:] for v in obj.data.vertices])
scene.frame_set(1);scene.frame_set(301)
b=np.array([v.co[:] for v in obj.data.vertices])
assert np.max(np.abs(a-b))<1e-5
assert len({o.data.as_pointer() for o,*_ in preview.blades})==3
samples=[]
for frame in range(1,121):
    start=time.perf_counter();scene.frame_set(frame);bpy.context.view_layer.update()
    samples.append(time.perf_counter()-start)
scene.render.engine='BLENDER_EEVEE'
scene.render.resolution_x=960;scene.render.resolution_y=540
scene.render.resolution_percentage=100
scene.eevee.taa_render_samples=8
scene.render.image_settings.file_format='PNG'
# Reuse Down, with an oblique comparison camera for whole-blade bending.
from wfrl_blender.cameras import ensure_gimbal, down_gimbal
scene.camera=ensure_gimbal(scene,'T1');down_gimbal(scene.camera)
scene.frame_set(54)
scene.render.filepath=str(OUT/'down.png');bpy.ops.render.render(write_still=True)
from mathutils import Vector
camera=bpy.data.objects.new('FlexOverview',bpy.data.cameras.new('FlexOverview'))
scene.collection.objects.link(camera)
camera.location=(-36,-135,99)
camera.rotation_euler=(Vector((-3,0,91))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO';camera.data.ortho_scale=240
scene.camera=camera
scene.render.filepath=str(OUT/'overview.png');bpy.ops.render.render(write_still=True)
(OUT/'verification.json').write_text(json.dumps(dict(records=records,
    mean_update_ms=float(np.mean(samples)*1000),p95_update_ms=float(np.percentile(samples,95)*1000),
    note='Background update timing excludes viewport drawing; not a measured 60 FPS claim.'),indent=2))
if os.environ.get('WFRL_FLEX_VIDEO')=='1':
    folder=OUT/'frames';folder.mkdir(exist_ok=True)
    # Six seconds, exactly 60 fps; retain the source playback clock.
    for i in range(360):
        if (folder/f'{i:04d}.png').is_file():
            continue
        scene.frame_set(1+i)
        scene.render.filepath=str(folder/f'{i:04d}.png')
        bpy.ops.render.render(write_still=True)
print('FLEX_PREVIEW_VERIFIED',flush=True)

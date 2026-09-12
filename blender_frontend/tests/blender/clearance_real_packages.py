"""Reviewed offline packages; writes only to explicit WFRL_TEST_OUTPUT."""
from pathlib import Path
import sys,json,math,os
ROOT=Path(__file__).resolve().parents[3]
DELIVERY = json.loads((ROOT / 'dist/lidar-delivery.json').read_text())
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import clearance_replay as playback
from mathutils import Vector
addon.register();addon.load_demo_scene()
scene=bpy.context.scene
scene.wfrl_show_wake=False
scene.wfrl_clearance_normal_path=str(ROOT / DELIVERY['packages']['normal'])
scene.wfrl_clearance_near_tower_path=str(ROOT / DELIVERY['packages']['close'])
results={}
for demo,path in [('normal',scene.wfrl_clearance_normal_path),('near_tower',scene.wfrl_clearance_near_tower_path)]:
    playback.load(scene,path,demo)
    reader=playback.reader_for(scene)
    assert reader and scene.frame_current==1
    assert playback.sample(scene)['time_s']==18
    # Jump directly to an actual measured sample, without rendering intervening rows.
    record=next(r for r in reader.package.measurements if r['beams']['B2']['valid'])
    frame=1+(record['time_s']-18)*scene.render.fps/scene.render.fps_base
    scene.frame_set(math.floor(frame), subframe=frame%1)
    first=playback.sample(scene)
    assert first['measurement'] and first['measurement']['time_s']<=first['time_s']
    assert math.isclose(scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x, math.radians(first['motion']['azimuth_deg']),abs_tol=1e-5)
    scene.frame_set(scene.frame_end)
    assert playback.sample(scene)['statistics']==reader.package.statistics
    scene.frame_set(math.floor(frame),subframe=frame%1)
    assert playback.sample(scene)==first
    results[demo]={'first_measured_time_s':first['measurement']['time_s'],'statistics':reader.package.statistics}
playback.load(scene,scene.wfrl_clearance_normal_path,'normal')
assert playback.sample(scene)['time_s']==18
camera=scene.objects['WFRL.Camera.Side']; camera.data.type='PERSP';camera.data.lens=45
camera.location=(5,-165,72);camera.rotation_euler=(Vector((-2,0,68))-camera.location).to_track_quat('-Z','Y').to_euler()
scene.camera=camera
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':
        space=area.spaces.active;space.use_local_camera=False;space.camera=camera
        space.show_region_ui=True;space.region_3d.view_perspective='CAMERA';space.region_3d.view_camera_zoom=0
output=Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=True)
(output/'real-package-integration.json').write_text(json.dumps(results,indent=2))
bpy.ops.wm.save_as_mainfile(filepath=str(output/'clearance-demo.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output/'clearance-demo.blend'))
assert playback.reader_for(bpy.context.scene) is not None
assert playback.sample(bpy.context.scene)['time_s']==18
assert not bpy.context.screen.is_animation_playing
print('CLEARANCE_REAL_PACKAGES_PASS: both FAST.Farm packages, seek, statistics, saved file reload paused')

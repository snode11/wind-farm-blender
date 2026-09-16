"""Native moving support, independent tip comparison, seeks and reload checks."""
from pathlib import Path
import os,sys,json,tempfile
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,os.environ.get('WFRL_TEST_RUNTIME',str(ROOT/'blender_frontend')))
if 'WFRL_TEST_RUNTIME' not in os.environ:
    sys.path.insert(0,str(ROOT))
import wfrl_blender
from wfrl_blender import farm_flex,clearance_replay
wfrl_blender.register()
package=Path(os.environ['WFRL_FARM_FLEX_PACKAGE'])
wfrl_blender.load_demo_scene(package)
scene=bpy.context.scene
active=farm_flex._ACTIVE
assert active.tower_motion is not None and len(active.towers)==3
errors=[];base_error=0.;radar_error=0.
for frame in sorted({min(scene.frame_end,f) for f in [1,2,3,121,901,1801,2701,3600,3601]}):
    scene.frame_set(frame)
    t=clearance_replay.sample(scene)['time_s']
    i=int(np.clip(np.searchsorted(active.times,t,side='right')-1,0,len(active.times)-2))
    alpha=(t-active.times[i])/(active.times[i+1]-active.times[i])
    from wfrl_blender.tower_motion import interpolate_transform
    nac=interpolate_transform(active.tower_motion['nacelle'][i],active.tower_motion['nacelle'][i+1],alpha)
    for k,tid in enumerate(active.readers):
        yaw=scene.objects[f'WFRL.Turbine.{tid}.YawRoot']
        actual=np.array(yaw.matrix_world)
        np.testing.assert_allclose(actual[:3,:3],nac[k,:,:3],atol=2e-6)
        expected=nac[k,:,:3]@np.array([0,0,87.6])+nac[k,:,3]+active.manifest['layout_m'][k]
        np.testing.assert_allclose(actual[:3,3],expected,atol=.0001)
        optical=actual[:3,:3]@np.array([-2,0,0])+actual[:3,3]
        independent=nac[k,:,:3]@np.array([-2,0,87.6])+nac[k,:,3]+active.manifest['layout_m'][k]
        radar_error=max(radar_error,float(np.linalg.norm(optical-independent)))
    for obj,rest,*_ in active.towers:
        actual=np.array([v.co[:] for v in obj.data.vertices])
        ground=rest[:,2]==0
        base_error=max(base_error,float(np.max(np.linalg.norm(actual[ground]-rest[ground],axis=1))))
        assert np.max(np.linalg.norm(actual-rest,axis=1))>.01
    for row in active.comparison.rows.values():
        limit=.005 if row['interpolated'] else .0002
        assert max(abs(row['error']))<limit,row
        errors.append(row['error'])
assert base_error<.0001
saved_frame=min(902,scene.frame_end-1)
scene.frame_set(saved_frame)
previous=np.array(scene.objects['WFRL.Turbine.T1.YawRoot'].matrix_world)
stamp=clearance_replay.sample(scene)['time_s']
for tid in ('T2','T3','all','T1'):
    bpy.ops.wfrl.farm_flex_view(turbine=tid)
    assert scene.frame_current==saved_frame and clearance_replay.sample(scene)['time_s']==stamp
scene.render.fps=24
assert clearance_replay.sample(scene)['time_s']==stamp
scene.frame_set(1);scene.frame_set(saved_frame)
np.testing.assert_array_equal(previous,np.array(scene.objects['WFRL.Turbine.T1.YawRoot'].matrix_world))
with tempfile.TemporaryDirectory() as temp:
    dest=Path(temp)/'tower.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(dest))
    bpy.ops.wm.open_mainfile(filepath=str(dest))
    scene=bpy.context.scene;active=farm_flex._ACTIVE
    assert active.tower_motion is not None and scene.frame_current==saved_frame
    np.testing.assert_array_equal(previous,np.array(scene.objects['WFRL.Turbine.T1.YawRoot'].matrix_world))
    for obj,rest,*_ in active.towers:
        assert len(obj.data.vertices)==len(rest)
    wfrl_blender.load_demo_scene(package)
    assert farm_flex._ACTIVE.tower_motion is not None
inactive=farm_flex._ACTIVE
clearance_replay.clear(scene,'test missing reader')
scene.frame_set(10)
assert not inactive.enabled and clearance_replay.sample(scene) is None
for obj,rest,*_ in inactive.towers:
    np.testing.assert_allclose([v.co[:] for v in obj.data.vertices],rest,atol=1e-6)
wfrl_blender.load_demo_scene(package)
assert farm_flex.is_active(bpy.context.scene)
result=dict(missing_reader_clears=True,status='PASS',base_max_error_m=base_error,radar_mount_max_error_m=radar_error,
    sampled_tip_max_error_m=np.max(np.abs(errors),axis=0).tolist(),seeking=True,views=True,clock=True,reload=True)
Path(os.environ['WFRL_TEST_OUTPUT']).write_text(json.dumps(result,indent=2)+'\n')
print('TOWER_FLEX_PASS',json.dumps(result))
wfrl_blender.unregister()

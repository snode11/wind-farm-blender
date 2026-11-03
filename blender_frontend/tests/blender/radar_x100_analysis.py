"""Source-native same-frame sidewall, rollback and observation-only diagnostics."""
from pathlib import Path
import sys
import json
import os
from dataclasses import replace
import numpy as np
import bpy
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import farm_flex, performance
from wfrl_blender.radar_x100 import runtime,scene as scene_api,view
from wfrl.radar_x100 import analysis
OUT=Path(os.environ['WFRL_TEST_OUTPUT']);OUT.mkdir(parents=True,exist_ok=False)
addon.register()
addon.load_demo_scene(farm_flex.default_package())
scene=bpy.context.scene
performance.configure(True)
session=runtime.start(scene)
assert session.context is not None,session.analysis_error
context=session.context
assert not context.vertices.flags.writeable
roi=((-100,-100,-150),(100,100,50))
session.derive_analysis(roi)
assert session.analysis_result is not None,session.analysis_error
result=session.analysis_result
ids=np.flatnonzero(np.isfinite(result['distance']))[:12]
world=analysis.transform(session.frame.xyz[ids],context.world_from_sensor)
d,q,tri=analysis.closest_reference(world,context.vertices,context.triangles)
np.testing.assert_allclose(result['distance'][ids],d,atol=2e-5)
assert np.array_equal(result['indices'],np.arange(len(session.frame.points)))
angle=.6;c,s=np.cos(angle),np.sin(angle)
shift=np.eye(4);shift[:3,:3]=[[c,-s,0],[s,c,0],[0,0,1]];shift[:3,3]=[1e8,-2e8,3e8]
moved=analysis.Context(context.identity+('large-world',),shift@context.world_from_analysis,
    shift@context.world_from_sensor,analysis.transform(context.vertices,shift),context.triangles)
translated=scene_api.SideWallQuery(moved).query(session.frame.xyz,roi)
np.testing.assert_allclose(translated['distance'],result['distance'],atol=2e-5,equal_nan=True)
assert session.sampler._part_vertices(len(session.sampler.blades),session.frame.time.source_time_s).dtype==np.float64
sensor_tower=analysis.local(context.vertices[:1],context.world_from_sensor)
mixed=scene_api.SideWallQuery(context).query(np.concatenate((sensor_tower,[[np.nan,0,0]],session.frame.xyz[:1])),
    ((-1e4,-1e4,-1e4),(1e4,1e4,1e4)))
assert len(mixed['indices'])==3 and mixed['status'][0]=='VALID' and mixed['distance'][0]<2e-5
assert mixed['status'][1]=='NONFINITE' and np.isnan(mixed['distance'][1])

frame=session.frame
before=frame.points.tobytes()
# No truth field is consumed by the online query.
session.frame=replace(frame,material_ids=tuple('renamed' for _ in frame.points),provenance=frame.provenance|{'truth':'perturbed'})
session.analysis_key=None
session.derive_analysis(roi)
np.testing.assert_equal(session.analysis_result['distance'],result['distance'])
assert session.frame.points.tobytes()==before
session.frame=frame
# A frozen context supports ROI changes without source sampling.
saved_sample=session.sampler.sample
session.sampler.sample=lambda *a: (_ for _ in ()).throw(AssertionError('No resampling'))
session.derive_analysis(((-10,-10,-100),(10,10,20)))
assert session.analysis_result is not None
del session.sampler.sample
# No end-cap triangles are admitted by the semantic map.
from wfrl_blender.turbine_geometry import tower_side_polygon_ids
obj=session.sampler.towers[0][0] if session.sampler.towers else session.sampler.tower
sides=tower_side_polygon_ids(obj.data)
obj.data.calc_loop_triangles()
expected=sum(t.polygon_index in sides for t in obj.data.loop_triangles)
assert len(context.triangles)==expected
# Optional analysis failure never withdraws the raw frame or installation.
saved_context=scene_api.SceneSampler.analysis_context
scene_api.SceneSampler.analysis_context=lambda *a: (_ for _ in ()).throw(RuntimeError('analysis injection'))
runtime.apply_mount_preset(scene,'NACELLE_SIDE')
assert session.frame is not frame and session.display_valid and session.context is None
assert 'analysis injection' in session.analysis_error
scene_api.SceneSampler.analysis_context=saved_context
runtime.apply_mount_preset(scene,'ORIGINAL')
assert session.context is not None,session.analysis_error
# A configuration publication failure restores the previous session and object transforms.
old_frame,old_sampler,old_session=session.frame,session.sampler,session.source_session
old_matrix=np.array(session.mount.matrix_basis)
old_config=tuple(scene.wfrl_x100_location)
saved_capture=session.capture_configuration
session.capture_configuration=lambda: (_ for _ in ()).throw(RuntimeError('commit injection'))
try:
    runtime.apply_mount_preset(scene,'NACELLE_SIDE')
    raise AssertionError('Expected commit failure')
except RuntimeError: pass
assert session.frame is old_frame and session.sampler is old_sampler and session.source_session==old_session
np.testing.assert_allclose(np.array(session.mount.matrix_basis),old_matrix,atol=1e-6)
assert tuple(scene.wfrl_x100_location)==old_config
session.capture_configuration=saved_capture
# Rewind and same source frame get distinct contexts; frozen old contexts do not mutate.
scene['wfrl_clearance_time_s']=session.sampler.interval[0]+.1
session.update();later=session.context
assert later.identity!=context.identity
scene['wfrl_clearance_time_s']=session.sampler.interval[0]
session.update()
assert session.context.identity[-3:]==context.identity[-3:]
assert later is not session.context
# Missing historical context is never filled from the live scene.
session.mode='REPLAY';session.pair_context(session.frame)
assert session.context is None
session.mode='SIMULATION'
# Rigid fallback uses the same construction mapping and tower binding arity stays five.
assert all(len(row)==5 for row in session.sampler.flex.towers)
# Bounded paired comparison with independent cache preparation for each path.
scene.wfrl_x100_blade_tracking=False
from time import perf_counter
from wfrl.radar_x100 import simulation
samples={'baseline':[],'transaction':[]}
sampler=session.sampler
for j in range(8):
    t=sampler.interval[0]+j/30
    frames={}
    for mode in (('baseline','transaction') if j%2==0 else ('transaction','baseline')):
        sampler._visibility_cache.clear()
        sampler._material_ids=None
        started=perf_counter()
        if mode=='baseline':
            f=simulation.simulate(sampler.sample,t,sensor_id='X100-T1',session_id=session.source_session,
                sequence=j,interval_s=sampler.interval,candidate_budget=scene.wfrl_x100_candidates,
                visibility=sampler.visibility,material_at=sampler.material_sample)
            f.provenance['calibration_id']=sampler.calibration_id
        else:
            f=runtime._simulation_frame(scene,sampler,session.source_session,j,t)
        samples[mode].append(perf_counter()-started)
        frames[mode]=f
    assert frames['baseline'].points.tobytes()==frames['transaction'].points.tobytes()
    assert frames['baseline'].material_ids==frames['transaction'].material_ids
    np.testing.assert_equal(frames['baseline'].radial_velocity_m_s,frames['transaction'].radial_velocity_m_s)
    assert not sampler._sampling_validated
# Exceptions must also release the transaction validation scope.
try:
    with sampler.sampling_transaction(): raise RuntimeError('scope injection')
except RuntimeError: pass
assert not sampler._sampling_validated
session.key=None
session.update()
# Cached independent inputs must produce the same curves as a fresh verified read.
scene['wfrl_clearance_time_s']=sampler.interval[0]+.4
before=performance.snapshot()['timings']['x100.validate']['calls']
session.update()
session.prepare_reference(True)
assert performance.snapshot()['timings']['x100.validate']['calls']-before==2
for offset in (0.,.1,.2):
    scene['wfrl_clearance_time_s']=sampler.interval[0]+offset
    session.update()
    session.prepare_reference(True)
    cached=[curve.copy() for curve in session.reference_curves]
    assert len(cached)==3,session.reference_error
    session.reference_source_key=session.reference_key=None
    session.prepare_reference(True)
    for a,b in zip(cached,session.reference_curves):np.testing.assert_array_equal(a,b)
# An unbound same-time scene cannot consume another scene's frame or context.
other_scene=scene.copy()
try:
    assert runtime.session_for(other_scene) is None
    try:
        runtime.start(other_scene)
        raise AssertionError('Unbound source must be rejected')
    except ValueError: pass
    assert runtime.session_for(other_scene) is None
    assert runtime.session_for(scene) is session and session.context is not None
finally:
    bpy.data.scenes.remove(other_scene)
# Exercise the rigid-tower fallback without changing FarmFlex's five-field tower API.
from copy import copy
rigid=copy(sampler.flex);rigid.towers=[];rigid.tower_motion=None
rigid_sampler=scene_api.SceneSampler(rigid,mount_from_sensor=np.eye(4))
t=rigid_sampler.interval[0]
f=simulation.simulate(rigid_sampler.sample,t,sensor_id='rigid',session_id='test',sequence=0,
    interval_s=rigid_sampler.interval,candidate_budget=32,visibility=rigid_sampler.visibility,
    material_at=rigid_sampler.material_sample)
c=rigid_sampler.analysis_context(f,('rigid',f.identity,t))
assert len(c.triangles)==expected
# A separate result layer shares the same verified independent reference contract.
runtime.stop(scene)
addon.load_demo_scene(farm_flex.default_dual_package(),camera_rig=False)
scene=bpy.context.scene
scene.wfrl_x100_candidates=64
sidecar=runtime.start(scene)
for offset in (0.,.1,.2):
    scene['wfrl_clearance_time_s']=sidecar.sampler.interval[0]+offset
    sidecar.update();sidecar.prepare_reference(True)
    cached=[curve.copy() for curve in sidecar.reference_curves]
    sidecar.reference_source_key=sidecar.reference_key=None
    sidecar.prepare_reference(True)
    assert len(cached)==3,sidecar.reference_error
    for a,b in zip(cached,sidecar.reference_curves):np.testing.assert_array_equal(a,b)
report=dict(status='PASS',core=analysis.__file__,blender=bpy.app.version_string,
    nearest_points_tested=len(ids),side_triangles=expected,checks=['snapshot','numpy_oracle','truth_isolation',
    'frozen_roi','caps_excluded','optional_analysis_failure','commit_rollback','rewind','historical_context_absent','ordinary_and_sidecar_reference_cache_equivalence'],
    timings=performance.snapshot(),sampling_comparison_s=samples,rigid_fallback=True,two_scene_unbound_isolation=True,anchored_world_stability=True,mixed_unknown_retained=True)
(OUT/'result.json').write_text(json.dumps(report,indent=2))
print('X100_ANALYSIS_PASS',json.dumps(report))
addon.unregister()

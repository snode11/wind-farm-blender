"""Score-only oracles, own-height sections, station topology and read-only flow."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.camera_video.data import blade_root_frame

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('diagnose_dual_beam_tls_residuals',
    ROOT/'scripts/lidar/diagnose_dual_beam_tls_residuals.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def square_tower():
    points = np.array([[-1,-1,0], [1,-1,0], [1,1,0], [-1,1,0],
                       [-2,-2,10], [2,-2,10], [2,2,10], [-2,2,10]], float)
    triangles = np.array([[i,(i+1)%4,i+4] for i in range(4)]
                         + [[(i+1)%4,(i+1)%4+4,i+4] for i in range(4)])
    return points, triangles


def test_oracle_vector_and_nonlinear_clearance_decomposition():
    hub = np.array([3.,-2.,5.]); reference = hub+np.array([4.,0.,-1.])
    direction = np.array([.8,.3,-.5]); direction /= np.linalg.norm(direction)
    fixed_length = 6.
    # Nonlinearity makes the interaction visible, so a missing interaction fails.
    clearance = lambda p:np.linalg.norm(p[:2])-.1*p[2]**2
    report = diagnostic.geometric_decomposition(hub,direction,fixed_length,reference,clearance)
    parts = report['position_contributions_m']
    expected = hub+fixed_length*direction-reference
    assert np.sum([parts[name] for name in ('direction','length','interaction')],axis=0) == pytest.approx(expected)
    assert parts['closure_residual'] == pytest.approx([0.,0.,0.],abs=1e-14)
    scalar = report['clearance_contributions_m']
    assert sum(scalar[name] for name in ('direction','length','interaction')) == pytest.approx(report['error_m'])
    assert abs(scalar['interaction']) > .01
    assert scalar['closure_residual'] == pytest.approx(0.,abs=1e-14)
    assert report['evidence_state'] == 'TRUTH_ORACLE_DIAGNOSTIC_ONLY'


def test_oracle_recomputes_every_counterfactual_at_its_own_height():
    points,triangles = square_tower()
    seen=[]
    def clearance(point):
        seen.append(np.asarray(point).copy())
        return diagnostic.independent_clearance(point,points,triangles)
    hub=np.array([5.,0.,9.]); reference=np.array([5.,0.,6.])
    direction=np.array([.6,0.,-.8])
    report=diagnostic.geometric_decomposition(hub,direction,4.,reference,clearance)
    assert len(seen)==4
    assert [point[2] for point in seen] == pytest.approx([6.,6.6,5.,5.8])
    assert report['actual_length_estimated_direction']['clearance_m'] == pytest.approx(5.+3.*.6-(1.+6.6*.1))
    assert report['fixed_length_true_direction']['clearance_m'] == pytest.approx(5.-(1.+5.*.1))
    assert report['estimate_clearance_m'] == pytest.approx(5.+4.*.6-(1.+5.8*.1))


def test_matching_direction_or_length_has_zero_respective_and_interaction_terms():
    hub=np.array([4.,0.,8.]); reference=np.array([4.,0.,5.])
    scorer=lambda point:float(point[0]+point[2]**2)
    same_direction=diagnostic.geometric_decomposition(hub,[0,0,-1],4.,reference,scorer)
    assert same_direction['clearance_contributions_m']['direction'] == 0
    assert same_direction['clearance_contributions_m']['interaction'] == 0
    same_length=diagnostic.geometric_decomposition(hub,[.6,0,-.8],3.,reference,scorer)
    assert same_length['clearance_contributions_m']['length'] == 0
    assert same_length['clearance_contributions_m']['interaction'] == 0


def test_surface_residual_uses_raw_range_not_saved_point_and_does_not_modify_input():
    observations={name:dict(origin_m=[0,0,0],direction=[0,0,1],slant_range_m=r,
                           point_m=[100,100,100]) for name,r in [('S2',2.),('S3',3.)]}
    before=deepcopy(observations)
    hub=[1.,0.,0.]; direction=[0,0,1]; truth=[0,0,1]
    result=diagnostic.surface_residuals(hub,direction,truth,observations)
    assert result['S2']['point_m'] == [0.,0.,2.]
    assert result['S2']['surface_offset_from_true_hub_tip_chord_m'] == [-1.,0.,0.]
    assert result['S3']['fitted_line_residual_norm_m'] == 1.
    assert result['fitted_line_sum_squared_residual_m2'] == 2.
    assert observations == before


def test_first_hit_station_mapping_uses_triangle_vertex_topology():
    surface=np.array([[2,-1,0],[2,1,0],[2,-1,2],[2,1,2]],float)
    triangles=np.array([[0,1,2],[1,3,2]])
    material=surface-np.array([2,0,0])
    result=diagnostic.triangle_station_diagnostic([0,0,.5],[1,0,0],2.,surface,triangles,
        2,2,material,np.array([2.,0.,0.]),np.eye(3),np.array([0.,0.,2.]))
    assert result['first_hit_triangle_index']==0
    assert result['source_station_neighborhood_one_based']==[1,2]
    assert result['source_station_indices_zero_based']==[0,0,1]
    assert result['source_material_reference_axial_m']==pytest.approx(.5)
    assert result['material_point_unloaded_blade_root_local_m']==pytest.approx([0,0,.5])
    assert result['loaded_minus_rigid_material_point_norm_m']==pytest.approx(0)
    with pytest.raises(ValueError,match='range differs'):
        diagnostic.triangle_station_diagnostic([0,0,.5],[1,0,0],2.1,surface,triangles,
            2,2,material,np.array([2.,0.,0.]),np.eye(3),np.array([0.,0.,2.]))


def test_nonadjacent_triangle_neighborhood_is_rejected_not_guessed():
    surface=np.array([[2,-1,0],[2,1,0],[9,9,9],[9,9,9],[2,-1,2],[2,1,2]],float)
    with pytest.raises(ValueError,match='nonadjacent'):
        diagnostic.triangle_station_diagnostic([0,0,.5],[1,0,0],2.,surface,np.array([[0,1,4]]),
            3,2,surface,np.zeros(3),np.eye(3),np.array([0.,0.,2.]))


def test_rigid_reference_separates_controlled_prebend_and_loaded_difference():
    hub=np.array([5.,0.,8.]); axes=np.eye(3); tip=np.array([-1.,0.,1.])
    actual=hub+tip+np.array([-.5,0.,-.2])
    result=diagnostic.rigid_reference_diagnostic(hub,axes,tip,actual,lambda p:float(p[0]-.1*p[2]))
    assert result['prebend_tip_offset_blade_root_local_m']==[-1,0,0]
    assert result['flexible_minus_rigid_prebent_blade_root_local_m']==pytest.approx([-.5,0,-.2])
    delta=result['own_height_clearance_differences_m']
    assert delta['prebend_minus_straight']+delta['flexible_minus_prebend']==pytest.approx(delta['flexible_minus_straight'])
    assert delta['prebend_minus_straight']==pytest.approx(-1.)
    assert result['evidence_state']=='SOURCE_REFERENCE_GEOMETRY_DIAGNOSTIC_ONLY'


def make_source_and_row(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    tip_local=np.array([-.1,0.,2.])
    (tmp_path/'blade-reference.json').write_text(json.dumps({'tip_local_m':tip_local.tolist()}))
    scalars={'ShftTilt':0.,'OverHang':5.,'TowerHt':10.,'Twr2Shft':0.,'PreCone(1)':0.}
    pose=np.array([0.,180.,0.,0.,0.,0.]); nacelle=np.column_stack((np.eye(3),np.zeros(3)))
    refs=[]; transforms=[]
    for blade in (1,2,3):
        rh,ra=blade_root_frame(scalars,[0.]*6,blade,nacelle)
        h,a=blade_root_frame(scalars,pose,blade,nacelle)
        local=np.array([[[x,y,z] for x,y in [(-.2,-.2),(.2,-.2),(.2,.2),(-.2,.2)]]
                        for z in np.linspace(.5,2.,19)])
        refs.append(rh+local@ra.T)
        rotation=a@ra.T
        transforms.append(np.repeat(np.column_stack((rotation,h-rotation@rh))[None],19,axis=0))
    triangles=[]
    for s in range(18):
        for v in range(4):
            a=4*s+v; b=4*s+(v+1)%4; c=b+4; d=a+4
            triangles.extend([[a,b,c],[a,c,d]])
    tower,tt=square_tower()
    tower_transforms=np.zeros((1,2,3,4));tower_transforms[...,:3]=np.eye(3)
    source=SimpleNamespace(path=tmp_path,turbine_id='T1',times=np.array([0.]),poses=pose[None],nacelles=nacelle[None],
        scalars=scalars,layout=np.zeros(3),transforms=np.asarray(transforms)[None],
        blade_reference=np.asarray(refs),blade_triangles=np.array(triangles),
        tower_reference=tower,tower_triangles=tt,tower_station=np.array([0]*4+[1]*4),tower_transforms=tower_transforms)
    observations={name:dict(origin_m=[0.,0.,z],direction=[1.,0.,0.],slant_range_m=4.8,blade_id=1)
                  for name,z in [('S2',9.3),('S3',8.2)]}
    hub,_=blade_root_frame(scalars,pose,1,nacelle)
    measured=np.array([[4.8,0.,9.3],[4.8,0.,8.2]])
    _,_,vt=np.linalg.svd(measured-hub,full_matrices=False);direction=vt[0]
    if direction@(measured-hub).sum(axis=0)<0:direction=-direction
    reference=diagnostic.structural_reference_points(source)[0,0]
    length=float(np.linalg.norm(tip_local));estimate=hub+length*direction
    scorer=lambda p:diagnostic.independent_clearance(p,tower,tt)
    row=dict(time_s=0.,passage_id='blade1-cycle0',observations=observations,
        reconstruction=dict(valid=True,method='hub-tls.v1',blade_id=1,hub_m=hub.tolist(),direction=direction.tolist(),
            effective_length_m=length,tip_estimate_m=estimate.tolist(),p2_m=measured[0].tolist(),p3_m=measured[1].tolist(),
            clearance_estimate=scorer(estimate)),
        evaluation=dict(tip_reference_m=reference.tolist(),clearance_reference_m=scorer(reference),clearance_error_m=999.))
    return source,row,reference,tip_local


def test_full_sample_diagnosis_is_read_only_score_only_and_ignores_stored_error(tmp_path,monkeypatch):
    source,row,reference,tip_local=make_source_and_row(tmp_path)
    before=deepcopy(row);transforms_before=source.transforms.copy(); surfaces_before=source.blade_reference.copy()
    def prohibited(*args,**kwargs):
        raise AssertionError('The score-only diagnostic must not call the production estimator')
    monkeypatch.setattr('wfrl.lidar.dual_beam.reconstruct',prohibited)
    result=diagnostic.sample_diagnosis(source,0,row,reference,tip_local)
    assert result['error_m'] != 999.
    assert result['measured_surface']['S2']['source_material']['first_hit_range_residual_m']==pytest.approx(0.,abs=1e-8)
    assert result['geometry']['clearance_contributions_m']['closure_residual']==pytest.approx(0.,abs=1e-14)
    assert row==before
    assert np.array_equal(source.transforms,transforms_before)
    assert np.array_equal(source.blade_reference,surfaces_before)


def test_all_valid_pairs_and_worsened_samples_retained_without_subset_selection(tmp_path):
    source,row,reference,tip_local=make_source_and_row(tmp_path)
    manifest=dict(source_hashes={},segment={'start_s':0.,'end_s':0.},source_fps=40,turbine_ids=['T1'],
                  reference_definition='structural',clearance_definition='own-height')
    config=dict(reconstruction_method='hub-tls.v1',algorithm_version='hub-constrained-tls.v1')
    package=dict(overlay=dict(config=config,manifest=manifest,results={'T1':dict(samples=[row])}),sources={'T1':source})
    old=deepcopy(row);old['reconstruction']['method']='hub-axis.v1'
    old['reconstruction']['direction']=((reference-np.asarray(old['reconstruction']['hub_m']))/np.linalg.norm(reference-np.asarray(old['reconstruction']['hub_m']))).tolist()
    old['reconstruction']['tip_estimate_m']=reference.tolist()
    old['reconstruction']['clearance_estimate']=old['evaluation']['clearance_reference_m']
    baseline=dict(overlay=dict(config=dict(reconstruction_method='hub-axis.v1'),manifest=manifest,
                  results={'T1':dict(samples=[old])}),sources={'T1':source})
    before=deepcopy(row)
    result=diagnostic.diagnose_packages(package,baseline)
    assert result['diagnosed_pairs']==1
    assert result['all_valid_pairs_included']
    assert result['candidate_worse_sample_count']==1
    assert len(result['candidate_worse_samples'])==1
    assert len(result['tail_samples'])==1
    assert result['pooled']['metrics']['mae_m'] != 999.
    assert row==before


@pytest.mark.parametrize('change',['surface','tip','clearance','hub'])
def test_geometry_evidence_mismatches_rejected(tmp_path,change):
    source,row,reference,tip_local=make_source_and_row(tmp_path)
    if change=='surface':row['observations']['S2']['slant_range_m']+=.01
    elif change=='tip':row['evaluation']['tip_reference_m'][0]+=.01
    elif change=='clearance':row['reconstruction']['clearance_estimate']+=.01
    else:row['reconstruction']['hub_m'][0]+=.01
    with pytest.raises(ValueError):
        diagnostic.sample_diagnosis(source,0,row,reference,tip_local)


def test_existing_output_directory_is_refused(tmp_path):
    with pytest.raises(ValueError,match='new directory'):
        diagnostic.main(['--package',str(tmp_path/'unused'),'--output',str(tmp_path)])

"""Independent analytic geometry, invalid pairs, occlusion and S1 semantics."""
from copy import deepcopy
import inspect
import math

import numpy as np
import pytest

from wfrl.lidar.dual_beam import PairLimits, observe, reconstruct, s1_state
from wfrl.lidar.dual_beam_replay import precompute, error_statistics
from wfrl.lidar.physics import truth_clearance


def echo(point, origin=(0, 0, 100), blade=1, time=1.):
    p,o=np.asarray(point,float),np.asarray(origin,float)
    distance=np.linalg.norm(p-o)
    return dict(time_s=time,origin_m=o.tolist(),direction=((p-o)/distance).tolist(),
                slant_range_m=float(distance),first_object='blade',blade_id=blade,
                observed=True,valid=True,reason='valid')


def pair(a=None,b=None,hub=(-8,0,90),length=63,clearance=truth_clearance,**kwargs):
    return reconstruct(a or echo((-8,0,70)),b or echo((-8,0,40)),hub,length,
                       lambda p:clearance(p)[0],time_s=1.,**kwargs)


def test_vertical_and_order():
    r=pair()
    assert r['valid']
    assert r['method']=='hub-axis.v1'
    assert np.allclose(r['tip_estimate_m'],[-8,0,27])
    assert r['clearance_estimate']==pytest.approx(8-(3+(1.935-3)*27/87.6))
    swapped=pair(echo((-8,0,40)),echo((-8,0,70)))
    assert swapped['tip_estimate_m']==pytest.approx(r['tip_estimate_m'])


def test_rotation_translation_covariance():
    theta=.71
    rot=np.array([[math.cos(theta),-math.sin(theta),0],[math.sin(theta),math.cos(theta),0],[0,0,1]])
    shift=np.array([500,-100,8])
    transform=lambda p:rot@np.asarray(p)+shift
    r=pair(echo(transform((-8,0,70)),transform((0,0,100))),
           echo(transform((-8,0,40)),transform((0,0,100))),hub=transform((-8,0,90)),
           clearance=lambda p:truth_clearance(rot.T@(p-shift)))
    assert r['tip_estimate_m']==pytest.approx(transform((-8,0,27)))
    assert r['clearance_estimate']==pytest.approx(pair()['clearance_estimate'])


@pytest.mark.parametrize('change,reason',[
    ({'valid':False,'reason':'no_return'},'s3_no_return'),
    ({'blade_id':2},'different_blades'),
    ({'time_s':.975},'not_same_time'),
    ({'direction':[0,0,-2]},'invalid_range_or_direction'),
    ({'slant_range_m':float('nan')},'invalid_range_or_direction'),
    ({'slant_range_m':101},'invalid_range_or_direction'),
    ({'observed':False,'reason':'invalid_calibration'},'s3_invalid_calibration'),
])
def test_invalid_pairs(change,reason):
    b=echo((-8,0,40));b.update(change)
    r=pair(b=b)
    assert r['reason']==reason
    assert r['method']=='hub-axis.v1'
    assert r['clearance_estimate'] is None


def test_degenerate_and_ambiguous():
    assert pair(b=echo((-8,0,69.99)))['reason']=='points_too_close'
    assert pair(echo((-9,0,60)),echo((-7,0,60)))['reason']=='ambiguous_root_to_tip'
    assert pair(limits=PairLimits(min_separation_m=0))['reason']=='invalid_calibration'


def test_signed_clearance_and_out_of_height():
    r=pair(echo((0,0,70)),echo((0,0,40)),hub=(0,0,90))
    assert r['valid'] and r['clearance_estimate']<0
    r=pair(hub=(-8,0,200))
    assert r['reason']=='no_tower_section_at_estimated_height' and r['clearance_estimate'] is None


def test_estimator_has_no_reference_input_and_ignores_saved_hit_point():
    assert not {'truth','tip_reference','surface_clearance'} & set(inspect.signature(reconstruct).parameters)
    a=echo((-8,0,70));a['point_m']=[100,100,100]
    assert pair(a=a)['tip_estimate_m']==pytest.approx([-8,0,27])


def test_fixed_conditioning_limits_against_analytic_perturbation():
    # A 1 mm transverse perturbation at 30 m separation: no source-error tuning.
    base=pair();moved=pair(b=echo((-8+.001,0,40)))
    displacement=np.linalg.norm(np.array(base['tip_estimate_m'])-moved['tip_estimate_m'])
    assert displacement <= 63*.001/30*1.001
    assert pair(b=echo((-8,0,69.751)))['reason']=='points_too_close'
    assert pair(b=echo((-8,0,69.749)))['valid']


def plane(z,x=0):
    return np.array([[x-2,-2,z],[x+2,-2,z],[x,2,z]],float)


def test_all_blades_nearest_occlusion_and_range_before_alarm():
    triangles=np.array([[0,1,2]])
    blades=np.array([plane(50),plane(60),plane(40)])
    tower=plane(10,100)
    args=(1.,np.array([0.,0.,90.]),np.array([0.,0.,-1.]),blades,triangles,tower,triangles)
    hit=observe(*args)
    assert hit['blade_id']==2 and hit['slant_range_m']==30
    assert s1_state(hit)=='triggered'
    blocked=observe(*args[:-2],plane(70),triangles)
    assert blocked['first_object']=='tower' and s1_state(blocked)=='not_triggered'
    short=observe(*args,limits=PairLimits(min_range_m=35))
    assert short['first_object']=='blade' and short['blade_id']==2
    assert s1_state(short)=='unknown' # must not skip nearest to select farther blade 1
    ground=observe(*args[:3],blades+np.array([100,0,0]),triangles,tower,triangles)
    assert ground['first_object']=='ground' and s1_state(ground)=='not_triggered'


def sample(time,alarm='not_triggered',valid=False):
    return dict(time_s=time,expected_blade_id=1,passage_id='b1',
                reconstruction=dict(valid=valid,blade_id=1,clearance_estimate=20. if valid else None),
                evaluation=dict(clearance_error_m=2. if valid else None,clearance_reference_m=18. if valid else None),
                s1_observation_state=alarm,observations={'S1':{'blade_id':1}})


def test_s1_independent_instant_unknown_history_and_event_groups():
    rows=[sample(1,'triggered'),sample(1.025,'triggered',True),sample(1.05,'unknown'),
          sample(1.075),sample(1.1,'triggered')]
    cumulative,events=precompute(rows,dict(alarm_hold_s=1,measurement_hold_s=.5))
    assert cumulative[0]['measurement'] is None
    assert cumulative[0]['alarm']['active']
    assert events[0]['start_s']==events[0]['output_time_s']==1
    assert events[0]['hit_times_s']==[1,1.025]
    assert cumulative[0]['alarm']['last_hit_s']==1 # future second hit never leaks backward
    assert cumulative[2]['alarm']['observation_state']=='unknown'
    assert cumulative[2]['alarm']['last_hit_s']==1.025
    assert len(events)==2
    assert cumulative[3]['measurement']['time_s']==1.025
    assert cumulative[3]['measurement']['error_m']==2


def test_empty_statistics_are_unavailable_and_nearest_rank():
    assert error_statistics([])['mae_m'] is None
    assert error_statistics([])['p95_abs_error_m'] is None
    assert error_statistics(list(range(1,21)))['p95_abs_error_m']==19
    r=sample(1);r['passage_id']=None
    cumulative,_=precompute([r],dict(alarm_hold_s=1,measurement_hold_s=1))
    assert cumulative[0]['statistics']['valid_ratio'] is None


def test_partial_passages_are_not_called_whole_misses():
    rows=[sample(1),sample(2),sample(3),sample(4),sample(5)]
    for row,identity in zip(rows,['initial',None,'complete',None,'final']):
        row['passage_id']=identity
    cumulative,_=precompute(rows,dict(alarm_hold_s=1,measurement_hold_s=1))
    assert cumulative[-1]['statistics']['passage_count']==3
    assert cumulative[-1]['statistics']['completed_passage_count']==1
    assert cumulative[-1]['statistics']['missed_passage_count']==1

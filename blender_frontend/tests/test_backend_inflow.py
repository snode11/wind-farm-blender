import math
import pytest
import numpy as np
from wfrl_blender import backend_inflow as flow

def payload(t=1,speed=8,direction=270):
    return {'timestamp':{'value':t},'scene':{'inflow':{'speed':speed,'direction':direction}}}

def test_history_reset_invalid_and_heading_convention():
    scene={'wfrl_scene_kind':'live'}
    assert flow.record(scene,payload())
    assert flow.active(scene)
    flow.record(scene,payload(2,10,0))
    rows=flow.history(scene)
    assert flow.sample(rows,1.5)==(8,0)
    assert math.isclose(flow.sample(rows,2)[1],1.5*math.pi)
    flow.record(scene,payload(.5,0,90))
    assert len(flow.history(scene))==1
    assert flow.sample(flow.history(scene),.5)[0]==0
    assert not flow.record(scene,payload(3,float('nan')))
    assert not flow.active(scene)
    scene['wfrl_scene_kind']='demo'
    flow.record(scene,payload())
    assert not flow.active(scene)

def test_duplicate_and_bounded_history():
    scene={}
    flow.record(scene,payload())
    flow.record(scene,payload(speed=12))
    assert len(flow.history(scene))==1
    assert flow.history(scene)[0][1]==12
    for i in range(2050):flow.record(scene,payload(i))
    assert len(flow.history(scene))==2048


@pytest.mark.parametrize('at', [-10., 0., .25, .5, 12.25, 700., 1023.5, 2000.])
def test_held_samples_match_saved_history_at_boundaries(at):
    rows = [[i * .5, 8. + (i % 13) * .1, (i % 17) * .02, i * 4.] for i in range(2048)]
    expected = next((row for row in reversed(rows) if row[0] <= at), rows[0])
    assert flow.sample(rows, at) == (expected[1], expected[2])


def test_wind_transport_preserves_integrals_and_observes_updated_history():
    from bisect import bisect_right
    from wfrl_blender.cinematic import WindTransport
    rows = [[i * .5, 8. + (i % 13) * .1, (i % 17) * .02, i * 4.] for i in range(2048)]

    def reference(at):
        row = rows[max(0, bisect_right([row[0] for row in rows], at) - 1)]
        return row[1], row[2]

    for speed in (8., 1.):
        expected = WindTransport(1000., speed, lambda at: reference(at)[1], lambda at: reference(at)[0])
        actual = WindTransport(1000., speed, lambda at: flow.sample(rows, at)[1], lambda at: flow.sample(rows, at)[0])
        for field in ('times', 'angles', 'distance', 'integral', 'current'):
            np.testing.assert_array_equal(getattr(actual, field), getattr(expected, field))
        assert actual.current_distance == expected.current_distance
    rows[2000][1:] = [11., -.4, 9000.]
    assert flow.sample(rows, 1000.) == (11., -.4)

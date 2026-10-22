import math
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

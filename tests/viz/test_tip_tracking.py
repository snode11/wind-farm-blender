import math
import sys
from types import SimpleNamespace
import numpy as np
import pytest
from wfrl.viz.tip_geometry import TurbineGeometry, CameraModel, tip_3d_from_pixel
from wfrl.viz.tip_tracking import TipTracker


@pytest.fixture
def setup():
    g=TurbineGeometry(110,5,5,68,2,tower_top_radius_m=1.2)
    c=CameraModel.look_at(640,392,49.2,31.36,g.hub+[-8,0,-3],g.tip_position(180),g.e_lat,upright=True)
    return g,c


@pytest.mark.parametrize('uv',[(320,float('nan')),(320,float('inf')),(-1,200),(640,200)])
def test_invalid_pixels_are_rejected(setup,uv):
    g,c=setup
    assert tip_3d_from_pixel(c,g,*uv) is None


def test_numpy_points_predictions_expiry_and_time(setup):
    g,c=setup;t=TipTracker(g,c)
    point=g.tip_position(180,2);uv=np.array(c.project(point)[:2])
    t.update_pixels({0:uv},rpm=8,t=0)
    assert np.allclose(t.results[0]['P'],point)
    t.update_pixels(None,rpm=8,t=.1)
    assert t.results[0]['validity']=='predicted'
    assert t.results[0]['out_of_plane_m'] is None
    assert t.results[0]['clearance_m'] is None
    assert math.isclose(g.decompose(t.tips[0][0])['out_of_plane_m'],g.decompose(point)['out_of_plane_m'])
    assert math.isclose(g.decompose(t.tips[0][0])['azimuth_deg'],184.8)
    t.update_pixels(None,rpm=8,t=1)
    assert not t.tips and not t.results
    t.update_pixels({0:uv},8,2);size=len(t.trajs[0])
    t.update_pixels({0:uv},8,2);assert len(t.trajs[0])==size
    t.update_pixels({0:uv},8,.5);assert len(t.trajs[0])==1


def test_world_input_keeps_radius_and_curvature(setup):
    g,c=setup;t=TipTracker(g,c)
    points=np.array([g.hub,g.hub+[1,0,-30],g.hub+[3,0,-60]])
    t.update_world({0:{'P':points[-1],'sections':points}},t=1,source='test',provenance={'test':True})
    assert np.array_equal(t.results[0]['P'],points[-1])
    assert not math.isclose(t.results[0]['radius_m'],g.R)
    assert np.array_equal(t.sections[0],points)
    assert t.results[0]['elastic_out_of_plane_m'] is None
    t.update_world({},t=2,source='test',provenance={'test':True})
    assert not t.tips and t.trajs[0][-1] is None


def test_bounded_pass_and_toggle(setup):
    class Imbalance:
        def reset(self):self.calls=[]
        def feed_pass(self,*args):self.calls.append(args)
    g,c=setup;t=TipTracker(g,c,max_pts=3,max_pass_samples=4,imbalance_tracker=Imbalance())
    uv=c.project(g.tip_position(180,2))[:2]
    for i in range(20):t.update_pixels({0:uv},0,i)
    assert len(t.trajs[0])==3 and len(t._pass_buf[0])==4
    t.flush();assert not t.imb.calls  # truncated pass is never accepted as complete
    t.set_trails(False);assert not t.trajs
    t.update_pixels({0:uv},0,20);assert len(t.trajs[0])==0
    t.set_trails(True);assert not t.trajs
    t.update_pixels({0:uv},0,21);assert len(t.trajs[0])==1


def test_tower_reference_and_yaw(setup):
    g,c=setup
    P=np.array([10.,0,55.])
    assert g.decompose(P)['clearance_m']==pytest.approx(8.4)
    assert g.decompose([10,0,111])['clearance_m'] is None
    before=g.tip_position(180,2);g.set_yaw(90)
    R=np.array([[0,-1,0],[1,0,0],[0,0,1.]])
    assert np.allclose(g.tip_position(180,2),R@before)
    assert g.decompose(R@before)['out_of_plane_m']==pytest.approx(68*math.sin(math.radians(2)))


def test_camera_calibration_and_validation(setup):
    pytest.importorskip('cv2')
    g,c=setup
    calibrated=CameraModel.from_calibration(c.W,c.H,c.K,[-.1,.01,.001,0,0],c.pos,c.R_cw)
    P=g.tip_position(175,2);u,v,visible=calibrated.project(P)
    assert visible
    result=tip_3d_from_pixel(calibrated,g,u,v)
    assert np.linalg.norm(result['P']-P)<1e-5
    with pytest.raises(ValueError):CameraModel.look_at(640,392,49,31,[0,0,0],[0,0,0],[1,0,0])
    with pytest.raises(ValueError):c.set_pose([0,0,0],np.zeros((3,3)))


def test_pause_navigation_does_not_advance(monkeypatch):
    from wfrl.viz import tip_visualizer as v
    keys=iter(map(ord,' aq'));feeds=[];renders=[]
    monkeypatch.setattr(v,'cv2',SimpleNamespace(imshow=lambda *a:None))
    vis=SimpleNamespace(render=lambda:renders.append(1) or np.zeros((1,1,3)),
                        scene=SimpleNamespace(orbit=lambda **kw:None),flush=lambda:None)
    def feed():feeds.append(1);return np.zeros((1,1,3))
    v.play_frames(vis,feed,fps=25,key_reader=lambda delay:next(keys))
    assert len(feeds)==1 and len(renders)==2


def test_csv_bad_rows_and_duplicate(tmp_path):
    from wfrl.viz.tip_visualizer import load_dets
    p=tmp_path/'dets.csv';p.write_text('frame,u,v,blade\n0,nan,20,0\n')
    with pytest.raises(ValueError,match=':2:'):load_dets(p)
    p.write_text('frame,u,v,blade\n0,10,20,0\n0,11,21,0\n')
    with pytest.raises(ValueError,match='duplicate'):load_dets(p)


def test_compatibility_imports():
    import tip_3d,tip_3d_vis
    from wfrl.viz.tip_visualizer import TipVisualizer
    assert tip_3d.TurbineGeometry is TurbineGeometry
    assert tip_3d_vis.TipVisualizer is TipVisualizer

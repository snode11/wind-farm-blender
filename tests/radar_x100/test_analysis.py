"""Independent triangle-distance and observation-only diagnostic contracts."""
import numpy as np
import pytest
from wfrl.radar_x100 import analysis
from scripts.radar_x100.coverage import passages, runs


def test_triangle_interior_edges_vertices_and_nonfinite():
    vertices=np.array([[0,0,0],[2,0,0],[0,2,0]],float)
    points=np.array([[.5,.5,3],[1,-2,0],[-1,-1,0],[np.nan,0,0]])
    d,q,ids=analysis.closest_reference(points,vertices,[[0,1,2]])
    np.testing.assert_allclose(d[:3],[3,2,np.sqrt(2)])
    np.testing.assert_allclose(q[:3],[[.5,.5,0],[1,0,0],[0,0,0]])
    assert np.isnan(d[3]) and ids[3]==-1
    angle=.63;c,s=np.cos(angle),np.sin(angle)
    rot=np.array([[c,-s,0],[s,c,0],[0,0,1]])
    offset=np.array([1e8,-1e8,2e8])
    moved,_,_=analysis.closest_reference(points[:3]@rot.T+offset,vertices@rot.T+offset,[[0,1,2]])
    np.testing.assert_allclose(moved,d[:3],atol=5e-8)


def test_invalid_surface_and_independent_color_limits():
    with pytest.raises(ValueError): analysis.closest_reference([[0,0,0]],[[0,0,0]]*3,[[0,1,2]])
    colors,saturated=analysis.distance_colors([0,5,11,np.nan],10)
    assert saturated==1
    np.testing.assert_allclose(colors[-1],[.5,.5,.5,1])
    assert np.all(np.diff(colors[:3,0])>0)


def test_complete_truncated_stop_reverse_and_zero_denominators():
    times=np.arange(11.)
    report=passages(times,np.linspace(140,220,11),30)
    assert report[0]['status']=='COMPLETE'
    assert passages(times,np.linspace(180,220,11),30)[0]['status']=='TRUNCATED'
    assert passages(times,np.ones(11)*180,30)[0]['status']=='STOPPED'
    assert passages(times,np.linspace(220,140,11),30)[0]['direction']=='REVERSE'
    assert runs([])==dict(longest_visible_frames=0,longest_gap_frames=0)


def test_unknown_breaks_known_gap_runs():
    assert runs([False,None,False])['longest_gap_frames']==1

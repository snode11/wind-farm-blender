"""Moving-surface truth stays independent of the range-only estimator."""
import numpy as np
import pytest
from wfrl.lidar.moving_tower import horizontal_clearance, background_hit, separated_from_tower
from wfrl.lidar.physics import simplified_estimate


def prism():
    p=np.array([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0],[-1,-1,10],[1,-1,10],[1,1,10],[-1,1,10]],float)
    t=[]
    for a in range(4):
        b=(a+1)%4;t.extend([[a,b,b+4],[a,b+4,a+4]])
    return p,np.array(t)


def test_truth_tracks_translated_and_bent_tower():
    p,t=prism()
    distance,wall=horizontal_clearance([-4,0,5],p,t)
    assert distance==pytest.approx(3)
    np.testing.assert_allclose(wall,[-1,0,5])
    # Bend centreline from x=0 at ground to x=2 at top.
    p[4:,0]+=2
    distance,wall=horizontal_clearance([-4,0,5],p,t)
    assert distance==pytest.approx(4)
    np.testing.assert_allclose(wall,[0,0,5])
    offset=np.array([50,-20,3])
    moved,moved_wall=horizontal_clearance(np.array([-4,0,5])+offset,p+offset,t)
    assert moved==pytest.approx(distance)
    np.testing.assert_allclose(moved_wall,np.array(wall)+offset)
    assert horizontal_clearance([1,0,5],p,t)[0]<0
    with pytest.raises(ValueError,match='height'): horizontal_clearance([0,0,20],p,t)


def test_occlusion_and_separation_use_moving_surface():
    p,t=prism();o=np.array([-4.,0,5]);d=np.array([1.,0,0])
    assert background_hit(o,d,p,t)[0]==pytest.approx(3)
    p[:,0]-=2
    assert background_hit(o,d,p,t)[0]==pytest.approx(1)
    blade=np.array([[-5,0,4],[-5,1,6],[-4,0,5]],float)
    assert separated_from_tower(blade,np.array([[0,1,2]]),p,t)
    sideways=blade+np.array([4.,50.,0.])
    assert separated_from_tower(sideways,np.array([[0,1,2]]),p,t)
    p[:,0]-=3
    assert not separated_from_tower(blade,np.array([[0,1,2]]),p,t)
    assert background_hit(np.array([-10.,0,5]),np.array([0.,0,-1]),p,t)[2]=='ground'


def test_fixed_estimator_remains_range_only():
    assert simplified_estimate(30,True,8.5,2,2.67)==pytest.approx(30*np.sin(np.radians(8.5))+2-2.67)
    assert simplified_estimate(None,False,8.5,2,2.67) is None


def test_terminal_contour_minimum_has_surface_witnesses():
    from wfrl.lidar.moving_tower import tip_surface_clearance
    p,t=prism()
    # Closest point lies inside a contour edge, not at its centre or vertices.
    ring=np.array([[-4,-2,4],[-4,2,6],[-5,2,6],[-5,-2,4]],float)
    distance,tip,wall=tip_surface_clearance(ring,p,t,sections=1)
    assert distance==pytest.approx(3.,abs=1e-9)
    assert tip[2]==pytest.approx(wall[2])
    assert tip[0]==pytest.approx(-4)
    assert wall[0]==pytest.approx(-1)
    p[4:,0]+=2
    distance,tip,wall=tip_surface_clearance(ring,p,t,sections=1)
    # Dense independent pointwise oracle verifies the sloped-height optimization.
    grid=np.linspace(0,1,1001)
    dense=np.vstack([a+grid[:,None]*(b-a) for a,b in zip(ring,np.roll(ring,-1,axis=0))])
    brute=min(horizontal_clearance(point,p,t)[0] for point in dense)
    assert abs(distance-brute)<.002
    np.testing.assert_allclose(np.linalg.norm(np.asarray(tip)[:2]-np.asarray(wall)[:2]),distance,atol=1e-9)
    offset=np.array([40,-20,3])
    moved,mt,mw=tip_surface_clearance(ring+offset,p+offset,t,sections=1)
    assert moved==pytest.approx(distance)


def test_terminal_surface_intersection_and_constant_x_edge():
    from wfrl.lidar.moving_tower import tip_surface_clearance
    p,t=prism()
    ring=np.array([[-4,-3,5],[-4,3,5],[-5,3,5],[-5,-3,5]],float)
    assert tip_surface_clearance(ring,p,t,1)[0]==pytest.approx(3)
    crossing=np.array([[-2,-2,5],[0,0,5],[-2,2,5]],float)
    assert tip_surface_clearance(crossing,p,t,1)[0]==pytest.approx(0,abs=1e-8)

import math
import numpy as np
from wfrl.lidar.physics import first_hit,truth_clearance,simplified_estimate,collision_excluded

def test_independent_circle_distance_and_endpoint():
    d,p=truth_clearance([3,4,0]);assert d==2;assert np.allclose(p,[1.8,2.4,0])

def test_first_hit_ignores_backfaces_and_behind_origin():
    pts=np.array([[-1,-1,2],[1,-1,2],[0,1,2],[-1,-1,4],[1,-1,4],[0,1,4],[-1,-1,-1],[1,-1,-1],[0,1,-1.]])
    hit=first_hit([0,0,0],[0,0,1],pts,np.array([[0,1,2],[3,5,4],[6,7,8]]));assert hit[0]==2
    assert first_hit([10,0,0],[0,0,1],pts,np.array([[0,1,2]])) is None

def test_formula_rigid_analytic_baseline_and_invalid():
    # A vertical section at upstream x=-8, sensor x=-2, 30deg ray -> range12.
    assert math.isclose(simplified_estimate(12,True,30,2,3),5)
    assert simplified_estimate(None,False,30,2,3) is None

def test_collision_certificate_is_conservative():
    tri=np.array([[0,1,2]])
    assert collision_excluded(np.array([[-4,0,0],[-4,1,50],[-4,0,80]]),tri)
    assert not collision_excluded(np.array([[-2,0,0],[-4,1,50],[-4,0,80]]),tri)

def test_background_occlusion_tower_and_ground():
    from wfrl.lidar.physics import background_first_hit
    hit=background_first_hit([-10,0,10],[1,0,0]);assert hit[2]=='tower'
    assert background_first_hit([-2,0,87.6],[-.1,0,-math.sqrt(.99)])[2]=='ground'


def test_projected_surface_separation():
    tri=np.array([[0,1,2]])
    # Sideways surface is clear although its X coordinate enters the old
    # upstream half-space. This occurs in spatially turbulent bending.
    assert collision_excluded(np.array([[-1,10,20],[1,10,25],[0,12,30]]),tri)
    # Vertices outside the tower are insufficient: an edge can cross it.
    assert not collision_excluded(np.array([[-10,0,20],[10,0,25],[0,12,30]]),tri)
    # The projected interior can contain the tower with every edge outside.
    assert not collision_excluded(np.array([[-10,-10,20],[10,-10,25],[0,15,30]]),tri)
    assert not collision_excluded(np.array([[float('nan'),10,20],[1,10,25],[0,12,30]]),tri)

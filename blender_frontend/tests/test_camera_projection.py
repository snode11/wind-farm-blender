import math
import numpy as np
import pytest
from wfrl_blender import camera_projection as p


@pytest.mark.parametrize('yaw', [0, 90, 180, 270, 123.456])
@pytest.mark.parametrize('pitch', [-90, -37, 0, 90])
@pytest.mark.parametrize('roll', [0, 37, 90, 180])
def test_orientation_axes(yaw, pitch, roll):
    r = np.array(p.rotation(yaw, pitch, roll))
    np.testing.assert_allclose(r.T @ r, np.eye(3), atol=1e-12)
    assert np.linalg.det(r) == pytest.approx(1)
    np.testing.assert_allclose(-r[:, 2], p.optical_axis(yaw, pitch), atol=1e-12)
    if roll == 90:
        np.testing.assert_allclose(r[:, 0], np.array(p.rotation(yaw, pitch, 0))[:, 1], atol=1e-12)


@pytest.mark.parametrize('h,v', [(90,60), (40,110), (75,75), (1,1), (170,120)])
@pytest.mark.parametrize('size', [640,1920,3840])
def test_projection_corner_pixels_and_effective_angles(h,v,size):
    w,height=p.resolution(h,v,size)
    mat=np.array(p.projection(h,v))
    k=np.array(p.intrinsics(h,v,w,height))
    assert max(w,height)==size
    for corner in p.frustum_corners(h,v,10):
        ndc=mat @ np.array([*corner,1]); ndc=ndc[:3]/ndc[3]
        assert abs(ndc[0])==pytest.approx(1)
        assert abs(ndc[1])==pytest.approx(1)
        cv=np.array([corner[0],-corner[1],-corner[2]])
        pix=k @ cv; pix=pix[:2]/pix[2]
        assert min(abs(pix[0]+.5),abs(pix[0]-(w-.5)))<1e-8
        assert min(abs(pix[1]+.5),abs(pix[1]-(height-.5)))<1e-8
    assert math.degrees(2*math.atan(1/mat[0,0]))==pytest.approx(h)
    assert math.degrees(2*math.atan(1/mat[1,1]))==pytest.approx(v)
    sx,sy=p.pixel_scales(h,v,w,height)
    assert sx/sy==pytest.approx(p.aspect(h,v)*height/w)


def test_independent_fov_and_linked_zoom():
    a,b=np.array(p.projection(90,60)),np.array(p.projection(100,60))
    np.testing.assert_array_equal(a[1:],b[1:])
    h,v=p.linked_fov(90,60,.9)
    assert p.aspect(h,v)==pytest.approx(p.aspect(90,60))
    assert p.resolution(90,60)==(1920,1109)


@pytest.mark.parametrize('h,v', [(0,60),(171,60),(90,float('nan')),(90,float('inf'))])
def test_invalid_fov(h,v):
    with pytest.raises(ValueError):p.projection(h,v)


def test_resources_and_time_list():
    with pytest.raises(ValueError):p.resolution(170,1,1920)
    with pytest.raises(ValueError):p.resolution(90,60,8192,max_size=4096)
    with pytest.raises(ValueError):p.projection(90,60,1,1)
    assert p.sample_times(3,3.3,.1)==pytest.approx([3,3.1,3.2,3.3])
    with pytest.raises(ValueError):p.sample_times(3,4,0)

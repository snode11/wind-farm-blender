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


@pytest.mark.parametrize('hfov,vfov,budget,expected', [
    (90, 4, 320, (444, 16)), (4, 90, 320, (16, 444)),
    (90, 4, 640, (640, 22)), (75, p.DEFAULT_VFOV, 320, (320, 180)),
])
def test_preview_budget_preserves_a_minimum_short_edge(hfov, vfov, budget, expected):
    assert p.preview_resolution(hfov, vfov, budget, max_size=4096) == expected


def test_preview_budget_does_not_relax_export_or_gpu_limits():
    assert p.resolution(90, 4, 1920) == (1920, 67)
    with pytest.raises(ValueError, match='短边'):
        p.resolution(90, 4, 320)
    with pytest.raises(ValueError, match='GPU'):
        p.preview_resolution(90, 4, 320, max_size=256)


@pytest.mark.parametrize('hfov,vfov', [(132, 1), (1, 132), (133, 1), (1, 133)])
def test_preview_accepts_a_legal_full_resolution_at_gpu_boundary(hfov, vfov):
    assert min(p.resolution(hfov, vfov, 4096, max_size=4096)) == 16
    width, height = p.preview_resolution(hfov, vfov, 320, max_size=4096)
    assert min(width, height) == 16
    assert max(width, height) <= 4096


def test_down_optics_preserve_independent_camera_sensors(monkeypatch):
    from types import SimpleNamespace
    from wfrl_blender import cameras, nacelle_camera

    class Camera(dict):
        def __init__(self, name, **tags):
            super().__init__(tags)
            self.name = name
            self.type = 'CAMERA'
            self.data = SimpleNamespace(lens=50.0)

    down = Camera('Manual Down.001', wfrl_down_camera=True)
    recorded = Camera('WFRL.Camera.T1.Gimbal')
    nacelle = Camera('Nacelle', wfrl_nacelle_intrinsics=nacelle_camera.INTRINSICS_ID)
    custom = Camera('C1')
    assert cameras.is_down_camera(None) is False
    assert not cameras.is_down_camera(custom)
    assert cameras.DOWN_FOCAL_LENGTH_MM == 8
    assert cameras.DOWN_VFOV_DEG == 60
    assert cameras.DOWN_HFOV_DEG == pytest.approx(
        math.degrees(2*math.atan(math.tan(math.radians(30))*16/9)))
    assert cameras._sensor_width(down) == cameras._sensor_width(recorded)
    assert cameras._sensor_width(nacelle) == nacelle_camera.SENSOR_WIDTH_MM
    assert cameras._sensor_width(custom) == 36
    scene = SimpleNamespace(objects={cam.name: cam for cam in (down, nacelle, custom)})
    class Scene(dict):
        objects = scene.objects
    scene = Scene()
    monkeypatch.setattr(nacelle_camera, 'select_output', lambda scene, camera: None)
    cameras.select_camera(scene, down.name, fov_deg=cameras.DOWN_HFOV_DEG)
    assert down.data.lens == pytest.approx(8)
    assert nacelle.data.lens == custom.data.lens == 50
    cameras.select_camera(scene, nacelle.name, fov_deg=60)
    assert nacelle.data.lens == pytest.approx(nacelle_camera.SENSOR_WIDTH_MM/(2*math.tan(math.radians(30))))
    cameras.select_camera(scene, custom.name, fov_deg=60)
    assert custom.data.lens == pytest.approx(36/(2*math.tan(math.radians(30))))
    assert down.data.lens == pytest.approx(8)

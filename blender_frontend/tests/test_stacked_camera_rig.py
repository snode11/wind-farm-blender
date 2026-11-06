from dataclasses import replace
import math
import numpy as np
import pytest
from wfrl_blender import stacked_camera_rig as rig, camera_projection as projection


def test_compact_stack_and_invalid_housing():
    config = rig.RigParameters()
    pts = rig.positions(config)
    assert pts[0][:2] == pts[1][:2] == pts[2][:2]
    assert pts[0][2] < pts[1][2] < pts[2][2]
    assert pts[2][2]-pts[0][2] == pytest.approx(.16)
    for kw in ({'height_m': .1}, {'spacing_m': .03}, {'spacing_m': 0.}, {'depth_m': -.1},
               {'housing_yaw_deg': math.nan}, {'span_ranges_m': ((21, 40), (0, 20), (39, 60))}, {'framing_scale': (1, 0, 1)}, {'framing_scale': (1, math.nan, 1)}):
        with pytest.raises(ValueError): rig.positions(replace(config, **kw))


def test_fitted_view_contains_asymmetric_target_with_common_sensor_aspect():
    spans = np.repeat(np.linspace(0, 60, 101), 2)
    points = np.column_stack((-5+spans*.015, -3-spans*.7, -spans*.55))
    points[::2, 0] -= .7
    points[1::2, 0] += .7
    for loc, interval in zip(rig.positions(rig.RigParameters()), rig.RigParameters().span_ranges_m):
        p = rig.fit_segment(points, spans, loc, interval)
        chosen = points[(spans >= interval[0]) & (spans <= interval[1])]
        view = (chosen-np.asarray(loc)) @ np.asarray(projection.rotation(p.yaw, p.pitch, p.roll))
        assert (view[:, 2] < 0).all()
        assert (np.abs(view[:, :2]/-view[:, 2, None]) <= np.tan(np.radians([p.fov, p.vfov])/2)).all()
        assert projection.aspect(p.fov, p.vfov) == pytest.approx(16/9)


def test_tighter_patch_changes_only_optics_and_preserves_sensor_aspect():
    spans = np.repeat(np.linspace(0, 60, 101), 2)
    points = np.column_stack((-5+spans*.015, -3-spans*.7, -spans*.55))
    points[::2, 0] -= .7
    points[1::2, 0] += .7
    args = (points, spans, (2.3, -2.12, 1.7), (24., 30.))
    wide = rig.fit_segment(*args)
    tight = rig.fit_segment(*args, framing_scale=.6)
    assert (wide.location, wide.yaw, wide.pitch, wide.roll) == (tight.location, tight.yaw, tight.pitch, tight.roll)
    assert math.tan(math.radians(tight.fov/2)) == pytest.approx(.6*math.tan(math.radians(wide.fov/2)))
    assert projection.aspect(tight.fov, tight.vfov) == pytest.approx(16/9)
    assert tight.output_long_edge_px == wide.output_long_edge_px == 1920


@pytest.mark.parametrize('normal', [(0,0,1),(0,-1,0),(.6,0,.8)])
@pytest.mark.parametrize('spin', [0,37,180,-90])
def test_support_contacts_surface_and_box_keeps_standoff(normal, spin):
    point = np.array([1.2,-.4,3.1])
    center, matrix = rig.surface_transform(point, normal, .1, spin)
    matrix = np.asarray(matrix)
    assert matrix.T @ matrix == pytest.approx(np.eye(3))
    assert np.linalg.det(matrix) == pytest.approx(1.)
    assert matrix[:,1] == pytest.approx(-np.array(normal))
    for x in [-.02,0,.02]:
        for z in [-.02,0,.02]:
            back = np.asarray(center) + matrix @ [x,.37,z]
            assert np.dot(back-point,normal) == pytest.approx(0.,abs=1e-10)
            front = np.asarray(center) + matrix @ [x,-.004,z]
            assert np.dot(front-point,normal) == pytest.approx(.374)


def test_surface_mount_rejects_invalid_geometry():
    for normal,depth,spin in [((0,0,0),.1,0),((0,0,2),.1,0),((0,0,1),0,0),((0,0,1),.1,math.nan)]:
        with pytest.raises(ValueError):rig.surface_transform((0,0,0),normal,depth,spin)


def test_stack_rejects_infinite_span_and_indistinguishable_optical_centres():
    config = rig.RigParameters()
    with pytest.raises(ValueError, match='覆盖区间'):
        rig.positions(replace(config, span_ranges_m=((2., math.inf), (24., 30.), (50., 61.5))))
    with pytest.raises(ValueError, match='间距'):
        rig.positions(replace(config, center=(2.3, -2.12, 1e16)))


def test_fit_rejects_undefined_sightline_before_normalization():
    points = np.array([[-1., 0., 0.], [0., 0., 0.], [1., 0., 0.]])
    with np.errstate(divide='raise', invalid='raise'):
        with pytest.raises(ValueError, match='视线'):
            rig.fit_segment(points, np.arange(3.), (0., 0., 0.), (0., 2.))


def test_fit_rejects_nonfinite_target_before_projection():
    points = np.array([[10., -1., 0.], [10., 0., math.inf], [10., 1., 0.]])
    with np.errstate(divide='raise', invalid='raise'):
        with pytest.raises(ValueError, match='有限'):
            rig.fit_segment(points, np.arange(3.), (0., 0., 0.), (0., 2.))


def test_fit_keeps_near_zero_heading_inside_valid_yaw_range():
    points = np.array([[10., -1e-17, -1.], [10., -1e-17, 0.], [10., -1e-17, 1.]])
    params = rig.fit_segment(points, np.arange(3.), (0., 0., 0.), (0., 2.))
    assert params.yaw == 0.
    view = points @ np.asarray(projection.rotation(params.yaw, params.pitch, params.roll))
    assert (view[:, 2] < 0).all()
    assert (np.abs(view[:, :2] / -view[:, 2, None]) < np.tan(np.radians([params.fov, params.vfov]) / 2)).all()


def test_surface_transform_preserves_input_normal_array():
    normal = np.array([0., -1.00001, 0.])
    before = normal.copy()
    center, rotation = rig.surface_transform((0., 0., 0.), normal, .1)
    np.testing.assert_array_equal(normal, before)
    assert center == pytest.approx((0., -.37, 0.))
    np.testing.assert_allclose(np.asarray(rotation).T @ rotation, np.eye(3), atol=1e-12)


def test_pose_rotation_accepts_periodic_yaw_without_rewriting_angles(monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Euler=lambda angles, _order: NS(to_matrix=lambda: angles)))
    for yaw, z in ((-180., 180.), (180., -180.), (-179., 181.), (37., 397.)):
        value = dict(housing_yaw_deg=yaw, housing_rotation_deg=(10., -20., z))
        assert rig.pose_rotation(value) == tuple(map(math.radians, (10., -20., z)))
    for yaw in (179.9, math.nan, math.inf):
        with pytest.raises(ValueError):
            rig.pose_rotation(dict(housing_yaw_deg=yaw, housing_rotation_deg=(0., 0., 180.)))


@pytest.mark.parametrize('yaw,pitch,roll', [
    (-1e-17, 0., 23.), (179.999, 20., 30.), (-179.999, -20., -90.),
    (0., -90., 37.), (180., 90., -19.),
])
def test_camera_angle_recovery_preserves_rotation_at_wrap_and_poles(monkeypatch, yaw, pitch, roll):
    import sys
    from types import SimpleNamespace as NS

    class Vector(np.ndarray):
        def __new__(cls, values):
            return np.asarray(values, dtype=float).view(cls)
        x = property(lambda self: self[0])
        y = property(lambda self: self[1])
        z = property(lambda self: self[2])

    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=Vector))
    original = np.asarray(projection.rotation(yaw, pitch, roll))
    angles = rig.camera_angles(NS(col=[Vector(original[:, i]) for i in range(3)]))
    assert 0 <= angles[0] < 360
    np.testing.assert_allclose(projection.rotation(*angles), original, atol=1e-12)

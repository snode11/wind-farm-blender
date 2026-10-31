"""Display projections retain point identity, coordinates and missing attributes."""
from dataclasses import replace
import numpy as np
import pytest
from wfrl_blender.radar_x100 import display
from wfrl.radar_x100.schema import RadarFrame, POINT_DTYPE


def frame(xyz=((10, 2, 3), (20, -4, 6)), **kwargs):
    points = np.zeros(len(xyz), dtype=POINT_DTYPE)
    for axis, name in enumerate(('x', 'y', 'z')):
        points[name] = np.asarray(xyz)[:, axis]
    for name in ('mag', 'rcs', 'snr'):
        points[name] = np.nan
    return RadarFrame('sensor', 'session', 0, 'TEST', 'UNKNOWN', points, **kwargs)


def test_sensor_projection_uses_declared_axes_and_only_forward_points():
    data = frame(((10, 10, 0), (10, -10, 10), (-1, 0, 0), (0, 0, 1)))
    before = data.points.tobytes()
    xy, keep = display.project(data.xyz, 'SENSOR')
    np.testing.assert_array_equal(keep, [True, True, False, False])
    np.testing.assert_allclose(xy, [[-45, 0], [45, np.degrees(np.arctan(1/np.sqrt(2)))]])
    assert data.points.tobytes() == before


@pytest.mark.parametrize('view, expected', [('FRONT', [[-2, 3], [4, 6]]),
    ('TOP', [[-2, 10], [4, 20]]), ('SIDE', [[10, 3], [20, 6]])])
def test_orthographic_views_keep_sensor_coordinates(view, expected):
    data = frame()
    xy, keep = display.project(data.xyz, view)
    np.testing.assert_array_equal(xy, expected)
    assert keep.all()


def test_world_overlay_transform_does_not_change_output_projection_or_sensor_range():
    transform = np.eye(4)
    transform[:3, 3] = [100, 200, 300]
    data = frame(spatial=dict(frame=display.SENSOR_AXES, units='m', alignment='SIMULATION',
                             world_from_sensor=transform.tolist()))
    np.testing.assert_allclose(display.world_xyz(data), data.xyz + [100, 200, 300])
    np.testing.assert_allclose(display.project(data.xyz, 'SIDE')[0], data.xyz[:, (0, 2)])
    _, limits, unknown = display.point_style(data, 'RANGE')
    np.testing.assert_allclose(limits, np.linalg.norm(data.xyz, axis=1))
    assert unknown == 0
    assert display.point_style(data, 'HEIGHT')[1] == (303, 306)
    raw = replace(data, spatial=dict(frame='VENDOR_RAW', units='m', alignment='UNALIGNED',
                                   world_from_sensor=transform.tolist()))
    np.testing.assert_array_equal(display.world_xyz(raw), raw.xyz)


def test_unknown_velocity_and_rcs_stay_gray_and_have_no_invented_limits():
    data = frame()
    for key in ('VELOCITY', 'RCS', 'SNR'):
        colors, limits, unknown = display.point_style(data, key)
        assert limits is None and unknown == 2
        np.testing.assert_array_equal(colors, [[.5, .5, .5, 1]]*2)
    velocity = replace(data, radial_velocity_m_s=np.array([-2, 4]))
    colors, limits, unknown = display.point_style(velocity, 'VELOCITY')
    assert limits == (-2, 4) and unknown == 0
    np.testing.assert_array_equal(colors, [[0, 0, 1, 1], [1, 0, 0, 1]])


def test_nonfinite_coordinates_and_valid_empty_frames_do_not_retain_points():
    data = frame(((1, 2, 3), (np.nan, 2, 3)))
    assert display.point_style(data, 'RANGE')[0].shape == (1, 4)
    empty = replace(data, points=np.zeros(0, dtype=POINT_DTYPE))
    for view in display.VIEWS:
        xy, keep = display.project(empty.xyz, view)
        assert xy.shape == (0, 2) and keep.shape == (0,)
        assert all(np.isfinite(display.fit_bounds(xy, view)))
    assert display.point_style(empty, 'RANGE')[1] is None


def test_fit_and_zoom_preserve_aspect_and_pan_is_display_only():
    bounds = display.fit_bounds(np.array([[0, 0], [10, 5]]), 'TOP')
    rectangle = (50, 80, 800, 400)
    scale, tx, ty = display.fit_canvas(bounds, rectangle)
    for point in ((0, 0), (10, 5)):
        x, y = np.array(point)*scale+(tx, ty)
        assert 50 <= x <= 850 and 80 <= y <= 480
    zoomed = display.fit_canvas(bounds, rectangle, zoom=2, pan=(1, -2))
    assert zoomed[0] == 2*scale
    assert display.ticks(-1, 1).tolist() == [-1, -.5, 0, .5, 1]

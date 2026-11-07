"""Display projections retain point identity, coordinates and missing attributes."""
from dataclasses import replace
import sys
from types import SimpleNamespace
import numpy as np
import pytest
from wfrl_blender.radar_x100 import display, runtime
from wfrl.radar_x100.schema import RadarFrame, POINT_DTYPE


def frame(xyz=((10, 2, 3), (20, -4, 6)), **kwargs):
    points = np.zeros(len(xyz), dtype=POINT_DTYPE)
    for axis, name in enumerate(('x', 'y', 'z')):
        points[name] = np.asarray(xyz)[:, axis]
    for name in ('mag', 'rcs', 'snr'):
        points[name] = np.nan
    return RadarFrame('sensor', 'session', 0, 'TEST', 'UNKNOWN', points, **kwargs)


def test_declared_file_origins_remain_distinguishable_from_generated_observations():
    generated = replace(frame(), source_kind='GEOMETRY_SIMULATION')
    declared = replace(generated, provenance={'source_declaration': 'EXPLICIT'})
    assert display.source_label(generated) == 'GEOMETRY_SIMULATION'
    assert display.source_label(declared) == 'GEOMETRY_SIMULATION · 显式声明'


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
    colors, limits, unknown = display.point_style(velocity, 'VELOCITY', color_scale='FRAME')
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


@pytest.mark.parametrize('points', [np.zeros(0, dtype=POINT_DTYPE), frame(((np.nan, 2, 3),)).points])
def test_no_drawable_points_cache_success_and_rebuild_only_on_style_change(monkeypatch, points):
    data = replace(frame(), points=points)
    session = runtime.Session.__new__(runtime.Session)
    session.frame = data
    session.scene = SimpleNamespace(wfrl_x100_color='RANGE', wfrl_x100_color_scale='FIXED',
        wfrl_x100_velocity_limit=10.)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(app=SimpleNamespace(background=False)))
    clock, calls = [0.], []
    batch = runtime.view.batch

    def build(*args, **kwargs):
        calls.append(args[1])
        return batch(*args, **kwargs)

    monkeypatch.setattr(runtime.view, 'batch', build)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    session._display(data, record=False)
    assert session.display_valid and session.gpu_batch is None and not session.render_error
    clock[0] = 1.
    session.retry_display()
    assert calls == ['RANGE']
    session.scene.wfrl_x100_color = 'HEIGHT'
    session.retry_display()
    assert calls == ['RANGE', 'HEIGHT']


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


def test_category_and_fixed_velocity_colors_are_comparable_across_frames():
    data = frame()
    colors = []
    for value in (0, 1):
        points = data.points.copy()
        points['ms'] = value
        styled, limits, unknown = display.point_style(replace(data, points=points), 'MS')
        assert limits == (0, 1) and unknown == 0
        colors.append(styled[0])
    np.testing.assert_array_equal(colors, [[0, 0, 1, 1], [1, 0, 0, 1]])
    first = replace(data, radial_velocity_m_s=np.array([-2., 4.]))
    second = replace(data, radial_velocity_m_s=np.array([-2., 100.]))
    a, limits, _ = display.point_style(first, 'VELOCITY', velocity_limit=10.)
    b, other_limits, _ = display.point_style(second, 'VELOCITY', velocity_limit=10.)
    assert limits == other_limits == (-10., 10.)
    np.testing.assert_array_equal(a[0], b[0])
    np.testing.assert_array_equal(b[1], [1, 0, 0, 1])
    neutral = replace(data, radial_velocity_m_s=np.zeros(2))
    np.testing.assert_array_equal(display.point_style(neutral, 'VELOCITY')[0], [[.5, 1, .5, 1]]*2)


@pytest.mark.parametrize('width', [360, 640, 800, 1280])
def test_narrow_canvas_wraps_readable_controls_without_overlap(width):
    layout = display.canvas_layout(width, 650, 1., velocity=True)
    assert layout['scale'] == 1.
    rectangles = [rect for _, _, rect in layout['buttons']]
    for x, y, w, h in rectangles:
        assert 0 <= x and x+w <= width and 0 <= y and y+h <= 650
        assert h >= 28
    for i, (x, y, w, h) in enumerate(rectangles):
        for xx, yy, ww, hh in rectangles[i+1:]:
            assert x+w <= xx or xx+ww <= x or y+h <= yy or yy+hh <= y
    x, y, w, h = layout['rectangle']
    assert w > 0 and h > 0 and y+h < layout['info_top']-40


@pytest.mark.parametrize('height', [480, 600, 656])
def test_high_ui_scale_preserves_narrow_plot_space(height):
    layout = display.canvas_layout(640, height, 2., velocity=True)
    assert layout['scale'] >= 1.
    assert layout['rectangle'][3] >= 140


def test_nominal_sensor_bounds_include_only_the_explicit_display_margin():
    from wfrl_blender.radar_x100.core import schema
    az, el = schema.PROFILE['azimuth_deg'], schema.PROFILE['elevation_deg']
    mx, my = schema.DISPLAY_DEFAULTS['sensor_margin_deg']
    assert display.fit_bounds([], 'SENSOR') == (-az-mx, -el-my, 2*(az+mx), 2*(el+my))
    assert schema.DISPLAY_DEFAULTS['fov_length_m'] != schema.PROFILE['range_m'][1]


def test_auxiliary_cache_replaces_layers_and_releases_context_values():
    cache = display.CanvasCache()
    assert cache.bind(('window-a', 'area-a'))
    value = object()
    assert cache.get('grid', ('bounds-a',), lambda: value) is value
    assert cache.get('grid', ('bounds-a',), lambda: pytest.fail('rebuild on hit')) is value
    for i in range(1000):
        cache.get('grid', (i,), object)
    cache.get('chrome', 0, object)
    cache.get('legend', 0, object)
    assert len(cache.items) == 3 and cache.items['grid'][0] == (999,)
    assert not cache.bind(('window-a', 'area-a')) and len(cache.items) == 3
    assert cache.bind(('window-b', 'area-a')) and not cache.items
    cache.get('grid', 1, object)
    cache.clear()
    assert not cache.items and cache.context is None
    with pytest.raises(ValueError):
        cache.get('history', 0, object)


def test_failed_auxiliary_build_is_retried_and_does_not_cache_a_partial_result():
    cache = display.CanvasCache()
    cache.get('grid', 0, object)
    def fail():
        raise RuntimeError('context unavailable')
    with pytest.raises(RuntimeError):
        cache.get('grid', 1, fail)
    assert cache.items['grid'][0] == 0
    value = object()
    assert cache.get('grid', 1, lambda: value) is value


@pytest.mark.parametrize('view', display.VIEWS)
@pytest.mark.parametrize('width,height,ui_scale', [(360,480,1), (640,600,2), (1280,800,1.5)])
def test_grid_auxiliaries_fit_the_actual_canvas_transform(view, width, height, ui_scale):
    layout = display.canvas_layout(width, height, ui_scale, velocity=True)
    rect, scale = layout['rectangle'], layout['scale']
    bounds = display.fit_bounds([[1, 3], [20, -5]], view)
    transform, shapes, labels = display.grid_geometry(view, bounds, rect, 2., (1., -2.), scale)
    assert transform == display.fit_canvas(bounds, rect, 2., (1., -2.))
    left, bottom, pw, ph = rect
    for points, _ in shapes:
        for x, y in points:
            assert left-1e-8 <= x <= left+pw+1e-8
            assert bottom-1e-8 <= y <= bottom+ph+1e-8
    assert labels and all(np.isfinite((x, y)).all() for _, x, y in labels)

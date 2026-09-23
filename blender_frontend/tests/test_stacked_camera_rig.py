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
    for kw in ({'height_m': .1}, {'spacing_m': .03}, {'depth_m': -.1},
               {'housing_yaw_deg': math.nan}, {'span_ranges_m': ((0, 20), (21, 40), (39, 60))}):
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

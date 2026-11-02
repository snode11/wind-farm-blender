"""Nominal geometry limits and binary budgets share the canonical profile and dtype."""
import numpy as np
from wfrl.radar_x100 import channel, ply, schema, simulation


def polar(r, az=0., el=0.):
    az, el = np.radians([az, el])
    return r*np.array([np.cos(el)*np.cos(az), np.cos(el)*np.sin(az), np.sin(el)])


def test_nominal_specification_and_ply_wire_size():
    assert schema.PROFILE['dimensions_hwd_m'] == [.120, .095, .040]
    assert schema.PROFILE['range_m'] == [.2,396.]
    assert schema.PROFILE['azimuth_deg'] == 60. and schema.PROFILE['elevation_deg'] == 22.
    assert schema.PROFILE['max_points'] == schema.MAX_POINTS == 4096
    assert schema.POINT_DTYPE.itemsize == 26 and not schema.POINT_DTYPE.isalignedstruct
    assert channel.MAX_PAYLOAD == 4096*30
    points = np.zeros(schema.PROFILE['max_points'], dtype=schema.POINT_DTYPE)
    assert ply.decode_ply(ply.encode_ply(points)).tobytes() == points.tobytes()


def test_range_and_angle_boundaries_are_inclusive_without_admitting_outside_points():
    points = np.array([polar(.2),polar(396.),polar(10.,60.),polar(10.,-60.),
        polar(10.,el=22.),polar(10.,el=-22.),polar(.2-1e-7),polar(396.+1e-7),
        polar(10.,60.+1e-7),polar(10.,-60.-1e-7),polar(10.,el=22.+1e-7),polar(10.,el=-22.-1e-7)])
    np.testing.assert_array_equal(simulation.in_fov(points), [True]*6+[False]*6)

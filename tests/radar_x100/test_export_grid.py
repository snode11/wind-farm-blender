"""Source grids include exact decimal endpoints without an out-of-range tolerance."""
import math
import pytest
from scripts.radar_x100.export_simulation import sample_times


@pytest.mark.parametrize('start,end,fps,expected', [
    (176.900000001, 177., 10., [176.900000001]),
    (176.9, 177., 10., [176.9, 177.]),
    (.1, .3, 10., [.1, .2, .3]),
    (0., .25, 10., [0., .1, .2]),
    (176.9, math.nextafter(177., -math.inf), 10., [176.9]),
    (117., 118., 30., [117. + i/30 for i in range(31)]),
])
def test_grid_stays_inside_requested_interval(start, end, fps, expected):
    actual = list(sample_times(start, end, fps))
    assert actual == expected
    assert all(start <= t <= end for t in actual)

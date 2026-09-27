import math

import numpy as np
import pytest

from wfrl_blender import stacked_camera_rig as rig


@pytest.mark.parametrize('normal', [(0., 0., 1.), (0., -1., 0.), (.6, 0., .8)])
@pytest.mark.parametrize('spin', [0., 37.])
@pytest.mark.parametrize('aim', [-45., -30., 0., 30., 45.])
def test_housing_swivels_at_fixed_support_end(normal, spin, aim):
    point, depth = np.array([1.2, -.4, 3.1]), .1
    normal = np.array(normal)
    zero_center, zero_rotation = rig.surface_transform(point, normal, depth, spin)
    center, rotation = rig.surface_transform(point, normal, depth, spin, aim=aim)
    rotation, zero_rotation = np.array(rotation), np.array(zero_rotation)
    pivot = point + normal * .274
    assert np.array(center) + rotation @ [0., depth-.004, 0.] == pytest.approx(pivot)
    assert rotation.T @ rotation == pytest.approx(np.eye(3), abs=1e-12)
    a = math.radians(aim)
    yaw = np.array(((math.cos(a), -math.sin(a), 0.),
                    (math.sin(a), math.cos(a), 0.), (0., 0., 1.)))
    assert rotation == pytest.approx(yaw @ zero_rotation)
    if aim == 0:
        assert center == pytest.approx(zero_center)


def test_swivel_rejects_nonfinite_and_back_mounted_rotation():
    for aim in (math.nan, math.inf):
        with pytest.raises(ValueError):
            rig.surface_transform((0, 0, 0), (0, -1, 0), .1, aim=aim)
    with pytest.raises(ValueError):
        rig.surface_transform((0, 0, 0), (0, -1, 0), .1, mount_type='BACK', aim=30.)

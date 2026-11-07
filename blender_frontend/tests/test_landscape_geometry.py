"""Static landscape budgets preserve the farm grid and distant ridge profile."""

import math

import pytest

from wfrl_blender import landscape


def test_terrain_spacing_preserves_the_farm_and_extent():
    full = landscape._terrain_axes('full')
    balanced = landscape._terrain_axes('balanced')
    for original, reduced, minimum, maximum in zip(
            full, balanced, (-1000, -950), (2100, 1150)):
        assert reduced[0] == original[0]
        assert reduced[-1] == original[-1]
        assert reduced == sorted(set(reduced))
        assert set(reduced) <= set(original)
        assert all(value in reduced for value in original if minimum <= value <= maximum)
    assert 2 * (len(balanced[0]) - 1) * (len(balanced[1]) - 1) < 65000


@pytest.mark.parametrize('layer,distance,base_height', [(1, 3700, 180), (2, 5200, 340), (3, 7000, 530)])
def test_ridge_retains_crest_samples_and_ground_contact(layer, distance, base_height):
    full, _ = landscape._ridge_geometry(layer, distance, base_height, 'full')
    reduced, faces = landscape._ridge_geometry(layer, distance, base_height, 'balanced')
    assert len(faces) * 2 == 16000
    assert all(vertex[2] == -35 for vertex in reduced[:501] + reduced[-501:])
    assert reduced[8 * 501:9 * 501] == full[16 * 1001:17 * 1001:2]
    crest_error = max(abs(full[16 * 1001 + i][2] -
                          (reduced[8 * 501 + i // 2][2] + reduced[8 * 501 + i // 2 + 1][2]) / 2)
                      for i in range(1, 1000, 2))
    assert crest_error < 1.7
    assert all(math.isfinite(value) for vertex in reduced for value in vertex)


def test_invalid_detail_fails_before_creating_blender_resources():
    with pytest.raises(ValueError, match='Landscape detail'):
        landscape.build_landscape(None, detail='camera')

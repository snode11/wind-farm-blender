import numpy as np

from wfrl.radar_x100 import installation, schema
from wfrl_blender.radar_x100 import housing_model


def test_unit_envelope_matches_the_installed_size_and_is_centred_on_the_sensor():
    vertices, faces, materials = housing_model.build()
    assert len(faces) == len(materials) and np.isfinite(vertices).all()
    assert set(materials) == set(range(len(housing_model.MATERIALS)))
    lo, hi = housing_model.unit_bounds(vertices, faces, materials)
    w, h, d = schema.UNIT_ENVELOPE['dimensions_whd_m']
    np.testing.assert_allclose(hi - lo, [d, w, h], atol=1e-9)
    np.testing.assert_allclose(hi + lo, 0, atol=1e-9)
    np.testing.assert_allclose(installation.housing_half_extents(), [d / 2, w / 2, h / 2])


def test_radome_faces_boresight_and_plate_sits_behind_the_unit():
    vertices, faces, materials = housing_model.build()
    def extent(material):
        used = sorted({i for face, m in zip(faces, materials) if m == material for i in face})
        return vertices[used].min(axis=0), vertices[used].max(axis=0)
    hx = installation.housing_half_extents()[0]
    assert np.isclose(extent(housing_model.RADOME)[1][0], hx)
    plate_lo, plate_hi = extent(housing_model.PLATE)
    assert plate_hi[0] <= -hx + 1e-9 and plate_hi[1] - plate_lo[1] > 2 * installation.housing_half_extents()[1]


def test_datasheet_profile_is_unchanged_by_the_installed_unit():
    assert schema.PROFILE['dimensions_hwd_m'] == [.120, .095, .040]
    meta = installation.profile_metadata()
    assert meta['hardware_profile']['dimensions_hwd_m'] == [.120, .095, .040]
    assert meta['mechanical_policy']['unit_envelope']['dimensions_whd_m'] == [.173, .2025, .1756]

import numpy as np
import pytest

from scripts.nrel_dynamic_tip_mapping_audit import declared_table, material_offset


def test_declared_count_excludes_trailing_unused_old_station():
    text = '2 NumBlNds - declared\nBlSpn BlCrvAC BlSwpAC\n(m) (m) (m)\n0 0 0\n61.4999 -1.0003249 -.1773747\n!unused\n61.5 999 999\n'
    table = declared_table(text, 'NumBlNds', 'BlSpn', 3)
    assert table.shape == (2, 3)
    assert table[-1, 0] == 61.4999
    assert table[-1, 1] == -1.0003249


def test_offset_rejects_wrong_station_instead_of_double_counting_span():
    ac = [-1.0003249, -.1773747, 61.4999]
    delta = material_offset(ac, [-1, 0, 61.5], anchor_span_m=61.4999)
    assert np.allclose(delta, [.0003249, .1773747, .0001])
    with pytest.raises(ValueError, match='station'):
        material_offset(ac, [-1, 0, 61.5], anchor_span_m=61.5)


def test_same_material_affine_map_requires_transforming_offset():
    ac = np.array([-1.0003249, -.1773747, 61.4999])
    tip = np.array([-1., 0., 61.5])
    delta = material_offset(ac, tip, anchor_span_m=61.4999)
    linear = np.array([[0., -1.01, .02], [1., 0., 0.], [0., 0., .999]])
    translation = np.array([3., -4., 7.])
    ac_current = linear @ ac + translation
    assert np.allclose(ac_current + linear @ delta, linear @ tip + translation)
    assert not np.allclose(ac_current + delta, linear @ tip + translation)


@pytest.mark.parametrize('text', [
    '2 NumBlNds\nBlSpn BlCrvAC\n(m) (m)\n0 0\n',
    '1 NumBlNds\nBlSpn BlCrvAC\n(m) (m)\n0 nan\n',
    '1 NumBlNds\n2 NumBlNds\nBlSpn BlCrvAC\n(m) (m)\n0 0\n',
])
def test_malformed_declared_table_fails_closed(text):
    with pytest.raises(ValueError):
        declared_table(text, 'NumBlNds', 'BlSpn', 2)

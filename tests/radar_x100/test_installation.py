"""Coverage ranking prioritizes the worst blade and preserves unknown frames."""
import numpy as np
import pytest
from wfrl.radar_x100 import installation
from scripts.radar_x100.coverage import passages, runs


def windows():
    return [[dict(status='COMPLETE', indices=list(range(10)), start_s=0., end_s=.3)] for _ in range(3)]


def candidate(name, matrix):
    return dict(id=name, location_m=[-3.5,-1.7,-2], rotation_deg=[0,82.5,180],
        profiles=[dict(seed=100, budget=512, coverage=installation.summarize(matrix, windows()), occluded_fraction=0.)])


def test_worst_blade_beats_large_unbalanced_cloud_and_unknown_disqualifies():
    balanced = np.full((10,3), 4)
    balanced[0] = 0
    unbalanced = np.full((10,3), 100)
    unbalanced[:,2] = 0
    unknown = np.full((10,3), 10)
    unknown[0,0] = -1
    rows = installation.rank([candidate('unbalanced', unbalanced), candidate('unknown', unknown), candidate('balanced', balanced)])
    assert [r['id'] for r in rows] == ['balanced', 'unbalanced', 'unknown']
    assert rows[0]['score'][:3] == (.9, .9, -1)
    assert rows[-1]['score'] is None
    report = dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry',
        sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM, winner_id='balanced', candidates=rows)
    assert installation.selected_installation(report, 'source', 'geometry')[1] == [0,82.5,180]
    with pytest.raises(ValueError, match='来源'):
        installation.selected_installation(report, 'different', 'geometry')
    with pytest.raises(ValueError, match='未知帧'):
        installation.selected_installation(report | dict(errors=[dict(source_time_s=-1., reason='outside passage')]),
            'source', 'geometry')


def test_unknown_breaks_known_gaps_and_empty_passages_are_not_perfect():
    values = np.zeros((10,3), dtype=int)
    values[5] = -1
    result = installation.summarize(values, windows())
    assert result['longest_gap_frames'] == 5 and result['worst_blade_fraction'] is None
    assert installation.summarize(values, [[],[],[]])['worst_blade_fraction'] is None


def test_complete_passage_without_grid_samples_disqualifies_search_and_legacy_import():
    values = np.full((10, 3), 4)
    encountered = windows()
    encountered[0].extend(passages(np.array([1., 1.1]), np.array([170., 190.]), 1.))
    result = installation.summarize(values, encountered)
    empty = result['passages'][1]
    assert (empty['N'], empty['fraction'], empty['evaluation']) == (0, None, 'N/A')
    assert result['status'] == 'INSUFFICIENT_OR_UNKNOWN' and result['worst_passage_fraction'] is None

    legacy = candidate('winner', values)
    coverage = legacy['profiles'][0]['coverage']
    for row in coverage['passages']:
        row.pop('evaluation')
    report = dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry',
        sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM,
        winner_id=legacy['id'], candidates=[legacy])
    assert installation.selected_installation(report, 'source', 'geometry')
    coverage['passages'].append({key: value for key, value in empty.items() if key != 'evaluation'})
    with pytest.raises(ValueError, match='覆盖完整'):
        installation.selected_installation(report, 'source', 'geometry')


@pytest.mark.parametrize('mask,visible,gap', [
    ([None, False, False], 0, 2),
    ([True, True, None, True, True, False, None, False, False, False], 2, 3),
    ([None, None], 0, 0),
])
def test_unknown_ends_a_run_and_next_known_frame_starts_another(mask, visible, gap):
    assert runs(mask) == dict(longest_visible_frames=visible, longest_gap_frames=gap)


def test_installation_grid_deduplicates_presets_and_rejects_nonfinite():
    rows = installation.candidates([('mount',[0,0,0])], [0], [60,90], [180], [('preset',[0,0,0],[0,60,180])])
    assert len(rows) == 2
    with pytest.raises(ValueError):
        installation.candidates([('bad',[float('nan'),0,0])], [0], [0], [0])

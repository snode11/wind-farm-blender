"""Passage detection ranking, geometric controls and mechanical installation constraints."""
import numpy as np
import pytest
from wfrl.radar_x100 import installation
from scripts.radar_x100.coverage import passages, runs


def windows():
    return [[dict(status='COMPLETE', indices=list(range(10)), start_s=0., end_s=.3)] for _ in range(3)]


def candidate(name, matrix):
    n = len(matrix)
    detection_windows = [[dict(status='COMPLETE', indices=list(range(b*n, (b+1)*n)),
        start_s=b*n/30, end_s=((b+1)*n-1)/30, nadir_time_s=(b*n+n//2)/30)] for b in range(3)]
    records = [None if count < 0 else dict(target_associated_points=int(count), tower_distance_error_m=.1)
               for count in np.asarray(matrix).T.flat]
    nadirs = [[records[b*n+n//2]] for b in range(3)]
    support = installation.summarize_passage_detections(np.arange(n*3)/30, records, detection_windows, nadirs)
    return dict(id=name, location_m=[-3.5,-1.7,-2], rotation_deg=[0,82.5,180],
        mechanical=dict(status='PASS_RESEARCH', motion_clearance=dict(status='PASS_RESEARCH')),
        detection_profiles=[dict(simulated_detection_support=dict(status='KNOWN', support_fraction=.5),
                                 passage_detection_support=support)],
        profiles=[dict(seed=100, budget=512, geometric_coverage=installation.summarize(matrix, windows()), occluded_fraction=0.)])


def test_worst_blade_beats_large_unbalanced_cloud_and_unknown_disqualifies():
    balanced = np.full((10,3), 4)
    balanced[0] = 0
    unbalanced = np.full((10,3), 100)
    unbalanced[:,2] = 0
    unknown = np.full((10,3), 10)
    unknown[0,0] = -1
    rows = installation.rank([candidate('unbalanced', unbalanced), candidate('unknown', unknown), candidate('balanced', balanced)])
    assert [r['id'] for r in rows] == ['balanced', 'unbalanced', 'unknown']
    assert rows[0]['score'][:3] == (.9, -1/30, 1.)
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


def test_empty_geometric_control_does_not_override_complete_detection_evidence():
    values = np.full((10, 3), 4)
    encountered = windows()
    encountered[0].extend(passages(np.array([1., 1.1]), np.array([170., 190.]), 1.))
    result = installation.summarize(values, encountered)
    empty = result['passages'][1]
    assert (empty['N'], empty['fraction'], empty['evaluation']) == (0, None, 'N/A')
    assert result['status'] == 'INSUFFICIENT_OR_UNKNOWN' and result['worst_passage_fraction'] is None

    winner = candidate('winner', values)
    coverage = winner['profiles'][0]['geometric_coverage']
    for row in coverage['passages']:
        row.pop('evaluation')
    report = dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry',
        sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM,
        winner_id=winner['id'], candidates=[winner])
    assert installation.selected_installation(report, 'source', 'geometry')
    coverage['passages'].append({key: value for key, value in empty.items() if key != 'evaluation'})
    assert installation.selected_installation(report, 'source', 'geometry')
    winner['detection_profiles'][0]['passage_detection_support']['status'] = 'INSUFFICIENT_OR_UNKNOWN'
    with pytest.raises(ValueError, match='叶片经过检测证据'):
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


def pose(location, pitch=0., yaw=0.):
    pitch, yaw = np.radians([pitch, yaw])
    cp, sp, cy, sy = np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    matrix = np.eye(4)
    matrix[:3, :3] = [[cy*cp, -sy, cy*sp], [sy*cp, cy, sy*sp], [-sp, 0, cp]]
    matrix[:3, 3] = location
    return matrix


SHELL = np.array([[-4.767865, -1.65, -1.5], [3.262135, 1.65, 1.8]])


def test_original_is_front_half_and_downward_without_asserting_a_front_face_mount():
    result = installation.assess_front_mount(pose([-3.5, -1.7, -2.], 82.5, 180), SHELL, -5.)
    assert result['front_half'] and result['below_shell'] and result['downward']
    assert result['rotor_side'] == 'NEGATIVE_X'
    assert result['angle_from_down_deg'] == pytest.approx(7.5)
    assert not installation.assess_front_mount(pose([2.7, -1.7, -2.], 90, 180), SHELL, -5.)['front_half']


def test_mechanical_screen_checks_full_rotated_housing_space_opening_and_bracket_reach():
    original = installation.static_constraints(pose([-3.5, -1.7, -2.], 82.5, 180), SHELL, .60)
    assert original['status'] == 'PASS_RESEARCH'
    assert original['opening'] == 'NOT_REQUIRED_EXTERNAL'
    assert not original['physical_installation_approved']
    assert installation.static_constraints(pose([0, 0, 0]), SHELL, 1.5)['status'] == 'REJECTED'
    assert installation.static_constraints(pose([-3.5, -1.7, -3.], 90), SHELL, 1.5)['status'] == 'REJECTED'
    looking_in = installation.static_constraints(pose([0, -2, 0], yaw=90), SHELL, .35)
    assert looking_in['checks']['shell_exterior'] and looking_in['checks']['installation_space']
    assert not looking_in['checks']['opening_free'] and looking_in['status'] == 'REJECTED'
    # The installed unit is 175.6 mm deep: looking down it needs half that plus the service gap below the shell.
    assert installation.static_constraints(pose([0, 0, -1.55], 90), SHELL, .05)['status'] == 'REJECTED'
    down = installation.static_constraints(pose([0, 0, -1.62], 90), SHELL, .12)
    horizontal = installation.static_constraints(pose([0, 0, -1.55]), SHELL, .05)
    assert down['status'] == 'PASS_RESEARCH'
    assert not horizontal['checks']['shell_exterior']


def test_constraints_are_sourced_and_software_roi_cannot_change_x100_hardware():
    from copy import deepcopy
    from wfrl.radar_x100.schema import PROFILE
    before = deepcopy(PROFILE)
    a = installation.profile_metadata()
    b = installation.profile_metadata([1, 2, 3, 4, 5, 6])
    assert a['hardware_profile'] == b['hardware_profile']
    assert a['hardware_profile']['fov']['azimuth_deg'] == [-60, 60]
    assert a['hardware_profile']['fov']['elevation_deg'] == [-22, 22]
    assert a['hardware_profile']['range_m'] == [.2, 396]
    assert 'CTSAI' in a['hardware_profile']['source']
    assert not a['comparison_devices']['applied_to_x100']
    assert all(v['basis'] == 'RESEARCH_ASSUMPTION' for v in a['mechanical_policy']['parameters'].values())
    a['hardware_profile']['range_m'][1] = 150
    assert PROFILE == before
    with pytest.raises(ValueError, match='ROI'):
        installation.profile_metadata([0, 0, 0, 0, 1, 1])


def test_unknown_geometric_frames_do_not_become_misses_and_threshold_is_geometric_only():
    counts = np.full((10, 3), 3)
    counts[0] = -1
    result = installation.summarize(counts, windows())
    assert result['metric'] == 'GEOMETRIC_DISTAL_MATERIAL_POINTS' and result['minimum_points'] == 3
    assert result['blades'][0]['fraction'] is None and result['blades'][0]['fraction_known'] == 1
    assert result['passages'][0]['fraction'] is None and result['passages'][0]['fraction_known'] == 1
    with pytest.raises(ValueError):
        installation.summarize(counts, windows(), 1)


@pytest.mark.parametrize('problem,reason', [
    ('shell', '机械约束证据未通过研究筛选'),
    ('motion', '运动净空证据未通过研究筛选'),
    ('unknown_motion', '运动净空证据未通过研究筛选'),
    ('unknown_detection', '检测证据不完整或含未知帧'),
    ('zero_detection', '缺少支持率大于 0 的检测证据'),
    ('missing_mechanics', '缺少机械约束证据；缺少运动净空证据'),
    ('missing_motion', '缺少运动净空证据'),
    ('missing_detection', '缺少检测证据（simulated_detection_support 的 status 和 support_fraction）'),
    ('empty_detection', '缺少检测证据（simulated_detection_support 的 status 和 support_fraction）'),
])
def test_geometric_success_cannot_override_mechanics_or_missing_detection_evidence(problem, reason):
    row = candidate('winner', np.full((10, 3), 4))
    if problem == 'shell':
        row['mechanical']['status'] = 'REJECTED'
    elif problem in {'motion', 'unknown_motion'}:
        row['mechanical']['motion_clearance']['status'] = 'REJECTED' if problem == 'motion' else 'UNKNOWN'
    elif problem == 'unknown_detection':
        row['detection_profiles'][0]['simulated_detection_support'].update(status='INSUFFICIENT_OR_UNKNOWN', support_fraction=None)
        row['detection_profiles'][0]['passage_detection_support']['status'] = 'INSUFFICIENT_OR_UNKNOWN'
    elif problem == 'zero_detection':
        row['detection_profiles'][0]['simulated_detection_support']['support_fraction'] = 0
        row['detection_profiles'][0]['passage_detection_support']['worst_blade_fraction'] = 0
    elif problem == 'missing_mechanics':
        row.pop('mechanical')
    elif problem == 'missing_motion':
        row['mechanical'].pop('motion_clearance')
    elif problem == 'missing_detection':
        row.pop('detection_profiles')
    else:
        row['detection_profiles'] = []
    assert installation.rank([row])[0]['score'] is None
    report = dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry',
        sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM, winner_id='winner', candidates=[row])
    with pytest.raises(ValueError) as exc:
        installation.selected_installation(report, 'source', 'geometry')
    assert reason in str(exc.value)
    assert '请重新运行 scripts/radar_x100/optimize_mount.py 生成 v2 报告' in str(exc.value)


@pytest.mark.parametrize('schema', [None, 'x100-installation-search.v1'])
def test_legacy_search_report_requires_v2_regeneration(schema):
    row = candidate('winner', np.full((10, 3), 4))
    row.pop('mechanical')
    row.pop('detection_profiles')
    profile = row['profiles'][0]
    profile['coverage'] = profile.pop('geometric_coverage')
    report = dict(source_id='source', geometry_id='geometry', sampling_method='AREA',
        sampling_algorithm=installation.AREA_ALGORITHM, winner_id='winner', candidates=[row])
    if schema is not None:
        report['schema'] = schema
    with pytest.raises(ValueError) as exc:
        installation.selected_installation(report, 'source', 'geometry')
    assert str(exc.value) == ('安装搜索报告版本不受支持，仅接受 x100-installation-search.v2'
        '；请重新运行 scripts/radar_x100/optimize_mount.py 生成 v2 报告')


@pytest.mark.parametrize('missing', ['simulated_detection_support', 'status', 'support_fraction', 'geometric_coverage'])
def test_incomplete_v2_evidence_names_the_missing_fields(missing):
    row = candidate('winner', np.full((10, 3), 4))
    if missing == 'geometric_coverage':
        profile = row['profiles'][0]
        profile['coverage'] = profile.pop(missing)
        reason = '缺少 v2 几何覆盖证据（geometric_coverage）'
    else:
        profile = row['detection_profiles'][0]
        (profile if missing == 'simulated_detection_support' else profile['simulated_detection_support']).pop(missing)
        reason = '缺少检测证据（simulated_detection_support 的 status 和 support_fraction）'
    report = dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry',
        sampling_method='AREA', sampling_algorithm=installation.AREA_ALGORITHM, winner_id='winner', candidates=[row])
    with pytest.raises(ValueError) as exc:
        installation.selected_installation(report, 'source', 'geometry')
    assert str(exc.value) == ('安装搜索推荐不可应用：' + reason
        + '；请重新运行 scripts/radar_x100/optimize_mount.py 生成 v2 报告')


def test_motion_clearance_has_explicit_grid_denominators_and_unknowns():
    clear = dict(clearance_m=.2, inside=False)
    assert installation.summarize_motion([clear, clear])['status'] == 'PASS_RESEARCH'
    unknown = installation.summarize_motion([clear, None, clear])
    assert (unknown['status'], unknown['expected_frames'], unknown['unknown_frames']) == ('UNKNOWN', 3, 1)
    assert installation.summarize_motion([clear, dict(clearance_m=.05, inside=False)])['status'] == 'REJECTED'
    assert installation.summarize_motion([dict(clearance_m=.2, inside=True)])['status'] == 'REJECTED'
    assert installation.summarize_motion([])['status'] == 'UNKNOWN'


def test_motion_containment_rejects_an_inside_center_even_far_from_the_surface():
    vertices = np.array([[0., 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]])
    triangles = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    assert installation.point_inside_closed_mesh(np.array([.3, .3, .3]), vertices, triangles)
    assert not installation.point_inside_closed_mesh(np.array([3., 3., 3.]), vertices, triangles)


def test_detection_evaluation_uses_real_model_complete_grid_and_output_roi(monkeypatch):
    from types import SimpleNamespace
    from wfrl.radar_x100 import simulation, sampling, detection
    from scripts.radar_x100.coverage import evaluate_detection_grid
    vertices = np.array([[10., -1, -1], [10, 1, -1], [10, 1, 1], [10, -1, 1]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    def sample(t):
        return None if t == 1. else simulation.SceneSample(t, 'source', 'geometry', vertices, faces, np.eye(4), 'test')
    def material(t, ids, *, barycentric):
        return simulation.MaterialSample(t, 'source', 'geometry', np.einsum('nij,ni->nj', vertices[faces[ids]], barycentric), np.eye(4))
    sampler = SimpleNamespace(interval=(0., 2.), sample=sample, material_sample=material,
        visibility=lambda sample, points: np.ones(len(points), bool), mount_world_at=lambda t, **kw: np.eye(4),
        sampling_plan=lambda budget, seed: sampling.make_plan(sample(0.), budget, seed))
    kwargs = dict(integration_budget=8192, output_point_limit=4,
                  config=detection.DetectionConfig(false_alarm_probability_per_cell=1., false_alarms_enabled=False))
    calls = []
    original = detection.summarize_detections
    def summarize(frames, **kw):
        calls.append(frames)
        return original(frames, **kw)
    monkeypatch.setattr(detection, 'summarize_detections', summarize)
    result = evaluate_detection_grid(sampler, [0., 1., 2.], roi=[5,-2,-2,15,2,2], **kwargs)
    summary = result['simulated_detection_support']
    assert result['grid_times_s'] == [0., 1., 2.] and calls[0][1] is None
    assert summary['expected_frames'] == 3 and summary['unknown_frames'] == 1
    assert summary['support_fraction'] is None and summary['support_fraction_known'] == 1
    assert summary['min_points'] == 1 and result['integration_budget'] > result['output_point_limit']
    outside = evaluate_detection_grid(sampler, [0., 2.], roi=[20,-2,-2,30,2,2], **kwargs)
    assert outside['simulated_detection_support']['support_fraction'] == 0
    assert calls[0][0].points.tobytes() == calls[1][0].points.tobytes()


def test_false_alarms_do_not_pass_installation_detection_support():
    from wfrl.radar_x100 import detection, schema
    points = np.zeros(5, dtype=schema.POINT_DTYPE)
    diagnostics = np.zeros(5, dtype=schema.MODEL_DIAGNOSTIC_DTYPE)
    diagnostics['false_alarm'] = 1
    frame = schema.RadarFrame('sensor', 'session', 0, 'ENGINEERING_SIMULATION', 'DETECTIONS', points,
        model_diagnostics=schema.ModelDiagnostics(detection.MODEL_ID, diagnostics))
    row = candidate('noise', np.full((10, 3), 4))
    row['detection_profiles'] = [dict(simulated_detection_support=detection.summarize_detections([frame]))]
    assert row['detection_profiles'][0]['simulated_detection_support']['support_fraction'] == 0
    assert installation.rank([row])[0]['score'] is None


def test_missing_reference_plan_keeps_the_complete_detection_grid_unknown():
    from types import SimpleNamespace
    from scripts.radar_x100.coverage import evaluate_detection_grid
    def unavailable(*args):
        raise ValueError('reference source missing')
    result = evaluate_detection_grid(SimpleNamespace(sampling_plan=unavailable), [0., 1., 2.],
                                     roi=installation.DEFAULT_ROI)
    summary = result['simulated_detection_support']
    assert summary['expected_frames'] == summary['unknown_frames'] == 3
    assert summary['support_fraction'] is summary['support_fraction_known'] is None
    assert len(result['errors']) == 3 and result['sampling_plan_id'] is None
    assert result['frames'] == [None, None, None]


def test_detection_continuity_beats_large_geometric_cloud():
    continuous = candidate('continuous', np.full((10, 3), 1))
    many_geometry = candidate('many_geometry', np.full((10, 3), 100))
    many_geometry['detection_profiles'] = candidate('missed', np.tile([0, 0, 1, 1, 1, 1, 1, 1, 1, 1], (3, 1)).T)['detection_profiles']
    assert continuous['profiles'][0]['geometric_coverage']['worst_blade_fraction'] == 0
    assert installation.rank([many_geometry, continuous])[0]['id'] == 'continuous'


def test_passage_unknowns_exact_nadir_and_missing_tower_errors_prevent_continuity():
    row = candidate('continuous', np.full((10, 3), 4))
    assert row['detection_profiles'][0]['passage_detection_support']['continuous_support']
    times = np.arange(6)/30
    windows = [[dict(status='COMPLETE', indices=[2*b, 2*b+1], start_s=times[2*b],
                     end_s=times[2*b+1], nadir_time_s=times[2*b]+.01)] for b in range(3)]
    hit = dict(target_associated_points=4, tower_distance_error_m=.2)
    miss = dict(target_associated_points=0, tower_distance_error_m=None)
    value = installation.summarize_passage_detections(times, [hit]*6, windows, [[miss], [hit], [hit]])
    assert value['worst_passage_fraction'] == 1 and not value['continuous_support']
    assert value['nadir_support_fraction'] == pytest.approx(2/3)
    unknown = installation.summarize_passage_detections(times, [miss, None, miss, miss, hit, hit], windows, [[hit]]*3)
    assert unknown['status'] == 'INSUFFICIENT_OR_UNKNOWN'
    assert unknown['passages'][0]['non_false_alarm_points'] == [0, None]
    assert unknown['longest_known_miss_duration_s'] == 2/30
    missing_truth = installation.summarize_passage_detections(times,
        [hit | dict(tower_distance_error_m=None)]*6, windows, [[hit]]*3)
    assert missing_truth['status'] == 'INSUFFICIENT_OR_UNKNOWN'


def test_passage_grid_retains_source_indices_and_interpolated_nadir():
    from scripts.radar_x100.coverage import passage_grid
    times = np.arange(61)/30
    angles = 90 + times*360
    grid, source_ids, windows = passage_grid(times, angles, passages_per_blade=1)
    assert np.array_equal(grid, times[source_ids])
    assert [len(w) for w in windows] == [1, 1, 1]
    assert windows[0][0]['nadir_time_s'] == pytest.approx(.25)
    assert .25 not in grid
    assert len({w[0]['nadir_sequence'] for w in windows}) == 3
    for w in windows:
        assert [source_ids[i] for i in w[0]['indices']] == w[0]['source_indices']


@pytest.mark.parametrize('longitudinal,lateral', [(0, 0), (14, 0), (20, 10), (0, -20), (14, -10)])
def test_downward_tilts_change_boresight_and_preserve_rigid_aperture(longitudinal, lateral):
    roll, pitch, yaw = np.radians(installation.downward_rotation(longitudinal, lateral))
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    matrix = np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                       [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr], [-sp, cp*sr, cp*cr]])
    p, r = np.radians([longitudinal, lateral])
    assert np.allclose(matrix[:, 0], [-np.sin(p), np.sin(r)*np.cos(p), -np.cos(r)*np.cos(p)])
    assert np.allclose(matrix.T@matrix, np.eye(3))
    assert np.linalg.det(matrix) == pytest.approx(1)


def test_surface_clearance_includes_face_interiors_crossing_and_roi_clipping():
    floor = np.array([[[-5., -5, 0], [5, -5, 0], [0, 5, 0]]])
    small = np.array([[[-.1, -.1, 2.], [.1, -.1, 2.], [0, .1, 2.]]])
    assert installation.mesh_clearance(small, floor) == pytest.approx(2.)
    assert installation.mesh_clearance(floor, small) == pytest.approx(2.)
    piercing = np.array([[[0., 0, -1], [0, 0, 1], [.5, 0, 1]]])
    assert installation.mesh_clearance(piercing, floor) == 0
    clipped = installation.clip_triangle(floor[0], floor[0, :, :1], [0.], [1.])
    assert len(clipped) and all((tri[:, 0] >= 0).all() and (tri[:, 0] <= 1).all() for tri in clipped)
    assert installation.mesh_clearance([], floor) is None


def test_all_source_support_without_passage_evidence_cannot_be_imported():
    row = candidate('aggregate-only', np.full((10, 3), 20))
    row['detection_profiles'][0].pop('passage_detection_support')
    assert installation.rank([row])[0]['score'] is None
    report = dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry', sampling_method='AREA',
        sampling_algorithm=installation.AREA_ALGORITHM, winner_id=row['id'], candidates=[row])
    with pytest.raises(ValueError, match='缺少叶片经过检测证据'):
        installation.selected_installation(report, 'source', 'geometry')


def search_report(row):
    return dict(schema=installation.SCHEMA, source_id='source', geometry_id='geometry', sampling_method='AREA',
                sampling_algorithm=installation.AREA_ALGORITHM, winner_id=row['id'], candidates=[row])


def test_containment_includes_vertices_edges_and_faces_in_either_winding():
    vertices = np.array([[0., 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    for triangles in (faces, faces[:, ::-1]):
        for point in ([0., 0, 0], [1., 0, 0], [.5, .5, 0], [.3, .3, .3]):
            assert installation.point_inside_closed_mesh(point, vertices, triangles)
        assert not installation.point_inside_closed_mesh([.5, .5, -1e-7], vertices, triangles)


def test_containment_rejects_open_and_inconsistently_oriented_shells():
    vertices = np.array([[0., 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    inconsistent = faces.copy()
    inconsistent[0] = inconsistent[0, ::-1]
    for triangles in (faces[:-1], inconsistent):
        with pytest.raises(ValueError, match='closed, consistently oriented'):
            installation.point_inside_closed_mesh([.3, .3, .3], vertices, triangles)


def test_zero_width_clipping_retains_segments_and_points_for_clearance():
    triangle = np.array([[-1., -1, 0], [1, -1, 0], [0, 1, 0]])
    query = np.full((1, 3, 3), [0., 0, 2])
    with np.errstate(all='raise'):
        for bound, distance in ((0., 2.), (1., np.sqrt(6.))):
            clipped = installation.clip_triangle(triangle, triangle[:, :1], [bound], [bound])
            assert clipped
            assert installation.mesh_clearance(query, clipped) == pytest.approx(distance)
            assert installation.mesh_clearance(clipped, query) == pytest.approx(distance)
    assert installation.clip_triangle(triangle, triangle[:, :1], [2.], [3.]) == []


def test_degenerate_clearance_handles_crossing_parallel_segments_and_face_interior():
    segment = np.array([[[-1., 0, 1], [1, 0, 1], [1, 0, 1]]])
    crossing = np.array([[[0., -1, 0], [0, 1, 2], [0, 1, 2]]])
    parallel = segment + [0, 2, 0]
    face = np.array([[[-5., -5, 0], [5, -5, 0], [0, 5, 0]]])
    with np.errstate(all='raise'):
        for other, distance in ((crossing, 0.), (parallel, 2.), (face, 1.)):
            assert installation.mesh_clearance(segment, other) == pytest.approx(distance)
            assert installation.mesh_clearance(other, segment) == pytest.approx(distance)


@pytest.mark.parametrize('bounds', [SHELL * np.nan, SHELL[::-1]])
def test_front_assessment_rejects_invalid_shell_bounds(bounds):
    with pytest.raises(ValueError, match='shell geometry'):
        installation.assess_front_mount(pose([-3.5, -1.7, -2.], 82.5, 180), bounds, -5.)


def test_nonfinite_or_incomplete_motion_rows_remain_unknown():
    clear = dict(clearance_m=.2, inside=False)
    rows = [clear, dict(clearance_m=np.nan, inside=False), dict(clearance_m=np.inf, inside=False),
            dict(clearance_m=.2, inside=None), {}]
    result = installation.summarize_motion(rows)
    assert (result['status'], result['known_frames'], result['unknown_frames']) == ('UNKNOWN', 1, 4)
    assert result['minimum_clearance_m'] == .2
    assert installation.summarize_motion(rows + [dict(clearance_m=-.1, inside=False)])['status'] == 'REJECTED'


def test_equivalent_euler_candidates_keep_baseline_identity_and_reuse_angle_iterators():
    baseline = ('preset', [0., 0., 0.], [0., 90., 180.])
    alias = ('alias', [0., 0., 0.], [25., 90., 205.])
    rows = installation.candidates([('same', [0., 0., 0.]), ('other', [1., 0., 0.])],
                                   iter([0., 360.]), iter([90.]), iter([180., 540.]), [baseline, alias])
    assert len(rows) == 2
    assert rows[0]['location_name'] == 'preset'
    assert rows[0]['id'] == installation.digest((tuple(baseline[1]), tuple(baseline[2])))[:16]
    assert rows[0]['rotation_deg'] == tuple(baseline[2])
    assert rows[1]['location_m'] == (1., 0., 0.)


@pytest.mark.parametrize('indices', [[-1], [10], [0, 0], [1, 0], [.9], [[0]]])
def test_geometric_passages_reject_invalid_indices(indices):
    encountered = windows()
    encountered[0][0]['indices'] = indices
    with pytest.raises(ValueError, match='Passage indices'):
        installation.summarize(np.full((10, 3), 4), encountered)


def detection_grid():
    times = .007 + np.arange(12) / 30
    records = [dict(target_associated_points=np.int64(2), tower_distance_error_m=.1, source_time_s=float(t))
               for t in times]
    windows = [[dict(status='COMPLETE', indices=list(range(4*b, 4*b+4)), start_s=times[4*b],
                    end_s=times[4*b+3], nadir_time_s=times[4*b+1] + 1/60)] for b in range(3)]
    nadirs = [[dict(records[4*b+1], source_time_s=windows[b][0]['nadir_time_s'])] for b in range(3)]
    return times, records, windows, nadirs


@pytest.mark.parametrize('factor', [.5, 2.])
def test_detection_grid_rejects_cadence_inconsistent_with_grid_hz(factor):
    times, records, windows, nadirs = detection_grid()
    times *= factor
    for blade in windows:
        blade[0]['start_s'] *= factor
        blade[0]['end_s'] *= factor
    with pytest.raises(ValueError, match='grid_hz|every grid sample'):
        installation.summarize_passage_detections(times, records, windows, nadirs)


@pytest.mark.parametrize('omitted', [0, 1, 3])
def test_complete_passages_cannot_omit_first_interior_or_last_grid_frames(omitted):
    times, records, passage_windows, nadirs = detection_grid()
    for i in (0, 1, 3):
        records[i]['target_associated_points'] = 0
    result = installation.summarize_passage_detections(times, records, passage_windows, nadirs)
    assert result['passages'][0]['longest_known_miss_run_frames'] == 2
    assert result['longest_known_miss_duration_s'] == 2/30
    passage_windows[0][0]['indices'].remove(omitted)
    with pytest.raises(ValueError, match='every grid sample'):
        installation.summarize_passage_detections(times, records, passage_windows, nadirs)
    encountered = windows()
    encountered[0][0]['indices'] = [0, 2, 3]
    with pytest.raises(ValueError, match='every grid sample'):
        installation.summarize(np.full((10, 3), 4), encountered)


def test_numpy_detection_counts_preserve_nadir_support_fraction():
    result = installation.summarize_passage_detections(*detection_grid())
    assert result['nadir_support_fraction'] == 1. and result['continuous_support']


@pytest.mark.parametrize('problem', ['missing_time', 'wrong_time', 'missing_count', 'nan_count', 'nan_error'])
def test_invalid_nadir_or_detection_values_cannot_establish_known_support(problem):
    times, records, windows, nadirs = detection_grid()
    if problem == 'missing_time':
        windows[0][0].pop('nadir_time_s')
    elif problem == 'wrong_time':
        nadirs[0][0]['source_time_s'] += 1/30
    elif problem == 'missing_count':
        nadirs[0][0].pop('target_associated_points')
    elif problem == 'nan_count':
        records[0]['target_associated_points'] = np.nan
    else:
        records[0]['tower_distance_error_m'] = np.nan
    result = installation.summarize_passage_detections(times, records, windows, nadirs)
    assert result['status'] == 'INSUFFICIENT_OR_UNKNOWN'
    assert not result['continuous_support'] and result['worst_blade_fraction'] is None


@pytest.mark.parametrize('key,value', [
    ('worst_passage_fraction', np.nan), ('longest_known_miss_duration_s', np.nan),
    ('nadir_support_fraction', np.nan), ('worst_blade_fraction', np.nan),
    ('tower_error_rmse_m', np.nan), ('points_per_frame_mean', np.inf),
    ('nadir_support_fraction', 'missing'), ('points_per_frame_mean', None),
    ('tower_error_rmse_m', -1.), ('worst_passage_fraction', 1.1),
])
def test_rank_and_import_reject_missing_nonfinite_or_out_of_range_metrics(key, value):
    valid = candidate('valid', np.full((10, 3), 4))
    invalid = candidate('invalid', np.full((10, 3), 4))
    support = invalid['detection_profiles'][0]['passage_detection_support']
    if value == 'missing':
        support.pop(key)
    else:
        support[key] = value
    for rows in ([valid, invalid], [invalid, valid]):
        ranked = installation.rank(rows)
        assert [row['id'] for row in ranked] == ['valid', 'invalid']
        assert ranked[-1]['score'] is None
    with pytest.raises(ValueError, match=key):
        installation.selected_installation(search_report(invalid), 'source', 'geometry')


def test_nonfinite_geometric_tie_break_is_unknown_and_order_is_deterministic():
    from itertools import permutations
    a, z = (candidate(name, np.full((10, 3), 4)) for name in ('a', 'z'))
    z['profiles'][0]['geometric_coverage']['worst_blade_fraction'] = np.nan
    for rows in permutations([a, z]):
        ranked = installation.rank(rows)
        assert [row['id'] for row in ranked] == ['a', 'z']
        assert ranked[-1]['geometric_score'] is None and ranked[-1]['score'] is not None
    z['profiles'][0]['geometric_coverage']['worst_blade_fraction'] = 1.
    for rows in permutations([a, z]):
        assert [row['id'] for row in installation.rank(rows)] == ['z', 'a']


@pytest.mark.parametrize('problem', ['nan_aggregate', 'unknown_aggregate', 'missing_geometry', 'missing_detection'])
def test_rank_uses_the_same_aggregate_and_geometry_requirements_as_import(problem):
    row = candidate('winner', np.full((10, 3), 4))
    profile = row['detection_profiles'][0]
    if problem == 'nan_aggregate':
        profile['simulated_detection_support']['support_fraction'] = np.nan
    elif problem == 'unknown_aggregate':
        profile['simulated_detection_support']['status'] = 'INSUFFICIENT_OR_UNKNOWN'
    elif problem == 'missing_geometry':
        row['profiles'] = None
    else:
        profile.pop('simulated_detection_support')
    assert installation.rank([row])[0]['score'] is None
    with pytest.raises(ValueError, match='证据|支持率'):
        installation.selected_installation(search_report(row), 'source', 'geometry')


@pytest.mark.parametrize('problem', ['missing_location', 'missing_rotation', 'matrix', 'nan_angles'])
def test_selected_installation_names_missing_or_invalid_pose_parameters(problem):
    row = candidate('winner', np.full((10, 3), 4))
    if problem == 'missing_location':
        row.pop('location_m')
    elif problem == 'missing_rotation':
        row.pop('rotation_deg')
    elif problem == 'matrix':
        row['location_m'] = np.eye(3).tolist()
        row['rotation_deg'] = [[1., 1., 0.], [0., 1., 0.], [0., 0., 1.]]
    else:
        row['rotation_deg'][0] = np.nan
    assert installation.rank([row])[0]['score'] is None
    with pytest.raises(ValueError, match='安装参数.*location_m、rotation_deg'):
        installation.selected_installation(search_report(row), 'source', 'geometry')


@pytest.mark.parametrize('field', ['source_id', 'geometry_id', 'sampling_algorithm', 'shell_geometry', 'mechanical'])
def test_report_identity_rejections_name_the_mismatched_field(field):
    row = candidate('winner', np.full((10, 3), 4))
    report = search_report(row)
    if field == 'mechanical':
        row[field]['geometry_id'] = 'different'
    elif field == 'shell_geometry':
        report[field] = dict(geometry_id='different')
    else:
        report[field] = 'different'
    with pytest.raises(ValueError, match=field):
        installation.selected_installation(report, 'source', 'geometry')


@pytest.mark.parametrize('declaration', [dict(evaluation_scope='FIRST_COMPLETE_PASSAGES_SCREENING_ONLY'),
                                      dict(passages_per_blade=1)])
def test_screening_report_cannot_import_a_claimed_winner(declaration):
    report = search_report(candidate('winner', np.full((10, 3), 4))) | declaration
    with pytest.raises(ValueError, match='部分经过筛选'):
        installation.selected_installation(report, 'source', 'geometry')


def test_duplicate_candidate_ids_are_rejected_before_ranking_or_import():
    rows = [candidate('winner', np.full((10, 3), count)) for count in (4, 5)]
    with pytest.raises(ValueError, match='unique'):
        installation.rank(rows)
    report = search_report(rows[0]) | dict(candidates=rows)
    with pytest.raises(ValueError, match='id 缺失或重复'):
        installation.selected_installation(report, 'source', 'geometry')

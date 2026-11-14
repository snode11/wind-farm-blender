"""Rejected source frames cannot contribute to installation visibility statistics."""
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from scripts.radar_x100 import coverage, export_simulation, optimize_mount
from wfrl.radar_x100 import installation, simulation


def test_unknown_frame_rolls_back_occlusion_numerator_and_denominator(tmp_path, monkeypatch):
    import wfrl_blender as addon
    import wfrl_blender.radar_x100 as radar
    rest = np.array([[0., 0., 0.], [1., 0., 1.], [0., 1., 1.]])
    plan = NS(triangle_ids=np.arange(3), barycentric=np.tile([0., 0., 1.], (3, 1)), identity='fixture',
              points=lambda sample: np.tile([10., 0., 0.], (3, 1)))
    calls = []
    def visibility(sample, points):
        calls.append(sample.time)
        if len(calls) == 2:
            raise ValueError('second candidate unavailable')
        return np.full(len(points), sample.time != 0.)
    sampler = NS(source_id='fixture', geometry_id='fixture', interval=(0., 1.), k=0,
        flex=NS(times=np.array([0., 1.]), poses=np.zeros((2, 1, 2))),
        blades=[(None, rest)]*3, parts=[(None, np.array([[0, 1, 2]]))]*3, triangle_offsets=np.arange(4),
        installation_constraints=lambda pose: {'status': 'PASS_RESEARCH'}, sampling_plan=lambda *a: plan,
        sample=lambda stamp: NS(time=stamp), mount_world_at=lambda stamp: np.eye(4),
        motion_clearances=lambda sample, poses: [{} for _ in poses], visibility=visibility,
        with_installation=lambda *a: None)
    choices = [dict(id=str(i), location_name='ORIGINAL', location_m=[0.]*3, rotation_deg=[0.]*3) for i in range(2)]
    scene_api = NS(__file__=optimize_mount.__file__, SceneSampler=lambda *a, **kw: sampler,
        installation_matrix=lambda *a: np.eye(4), installation_catalog=lambda *a, **kw:
            dict(baselines=choices, search_candidates=[], original_assessment={}, shell_geometry={}, peer_placement_references=[]))
    monkeypatch.setattr(addon, 'register', lambda: None)
    monkeypatch.setattr(addon, 'unregister', lambda: None)
    monkeypatch.setattr(addon, 'load_demo_scene', lambda package: None)
    monkeypatch.setattr(addon, 'farm_flex', NS(default_package=lambda: None, active_for=lambda scene: None), raising=False)
    monkeypatch.setattr(radar, 'scene', scene_api, raising=False)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.radar_x100.scene', scene_api)
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(scene=None)))
    monkeypatch.setattr(export_simulation, 'sample_times', lambda *a: [0., 1.])
    monkeypatch.setattr(coverage, 'passage_grid', lambda *a: (np.array([0., 1.]), [0, 1], [[], [], []]))
    monkeypatch.setattr(coverage, 'evaluate_detection_grid', lambda *a, **kw: {})
    monkeypatch.setattr(installation, 'candidates', lambda *a: choices)
    monkeypatch.setattr(installation, 'summarize', lambda counts, *a: {'counts': counts.tolist()})
    monkeypatch.setattr(installation, 'summarize_motion', lambda rows: {'status': 'PASS_RESEARCH'})
    monkeypatch.setattr(installation, 'candidate_summary', lambda row: row)
    monkeypatch.setattr(installation, 'rank', lambda rows: [dict(row, score=None) for row in rows])
    monkeypatch.setattr(simulation, 'in_fov', lambda xyz: np.ones(len(xyz), bool))
    monkeypatch.setattr(optimize_mount, 'replace', lambda sample, **kw: sample)
    reports = []
    monkeypatch.setattr(optimize_mount, 'write_search_reports', lambda output, report, **kw: reports.append(report))
    monkeypatch.setattr(sys, 'argv', ['optimize', '--output', str(tmp_path / 'result.json'),
        '--budgets', '3', '--seeds', '0', '--noise-seeds', '0', '--roi', '-1', '-1', '-1', '20', '20', '20'])
    with pytest.raises(RuntimeError, match='unknown frames'):
        optimize_mount.main()
    assert len(reports[0]['errors']) == 1
    for candidate in reports[0]['candidates']:
        profile = candidate['profiles'][0]
        assert profile['distal_visible_points'][0] == [-1, -1, -1]
        assert profile['occluded_fraction'] == 0.

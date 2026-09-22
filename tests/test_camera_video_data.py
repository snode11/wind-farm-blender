"""Independent clock, geometry and provenance checks for camera sidecar data."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from wfrl.camera_video.data import (SourceGeometry, blade_root_frame,
                                    build_frame_table, export_frame_data, frame_schedule)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'blender_frontend/wfrl_blender/assets/mappo'


@pytest.fixture(scope='module')
def source():
    return SourceGeometry(SOURCE)


@pytest.mark.parametrize('fps,count', [(15, 900), (20, 1200), (25, 1500)])
def test_complete_clock_has_centers_bounded_exposures_and_no_endpoint(fps, count):
    rows = frame_schedule(117, 177, fps)
    assert len(rows) == count
    assert [r['video_pts'] for r in rows] == list(range(count))
    assert rows[0]['exposure_start_s'] == pytest.approx(117)
    assert rows[-1]['exposure_end_s'] == pytest.approx(177)
    assert rows[-1]['sim_time_s'] < 177
    assert rows[-1]['video_time_s'] == (count - 1) / fps
    assert np.diff([r['sim_time_s'] for r in rows]) == pytest.approx(np.full(count - 1, 1 / fps))


@pytest.mark.parametrize('kwargs', [dict(fps=30), dict(exposure_s=0), dict(exposure_s=.051),
                                   dict(exposure_s=float('nan'))])
def test_bad_sampling_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        frame_schedule(117, 177, **kwargs)


def test_source_events_match_reference_definition(source):
    audit = source.audit_events()
    assert audit['checked_events'] == len(source.measurements) == 118
    assert audit['passed']
    assert audit['max_abs_error_m'] < 2e-5
    assert source.phase_check['passed']
    assert source.phase_check['max_abs_residual_rpm'] < .05


def test_non_source_geometry_matches_centroid_and_frontend_root_frame(source, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'blender_frontend'))
    from wfrl_blender.deflection import rigid_frame
    for t in (117., 117.0125, 129.137, 176.993, 177.):
        sample = source.sample(t, include_blades=True)
        points = sample['blade_points_world_m'].reshape(3, 19, -1, 3)
        assert sample['tip_reference_world_m'] == pytest.approx(points[:, -1].mean(axis=1), abs=1e-10)
        for b in (1, 2, 3):
            nacelle = sample['nacelle_world_transform'][:3].copy()
            nacelle[:, 3] -= source.layout
            hub, axes = blade_root_frame(source.scalars, sample['pose'], b, nacelle)
            front_hub, _, front_axes = rigid_frame(source.scalars, sample['pose'], b, nacelle)
            np.testing.assert_array_equal(hub, front_hub)
            np.testing.assert_array_equal(axes, front_axes)


def test_full_segment_export_preserves_each_field_validity_and_world_witnesses(source, tmp_path):
    mount = np.eye(4)
    mount[:3, 3] = [-2, 0, 87.6]
    metadata = export_frame_data(source, tmp_path, camera_mount=mount, camera_config={'lens_mm': 35})
    assert metadata['time_contract']['frame_count'] == 1200
    assert metadata['time_contract']['video_time_base'] == {'numerator': 1, 'denominator': 20}
    assert metadata['center_sample_counts'] == {'source': 1200, 'interpolated': 0}
    assert metadata['source_status'] == 'REVIEW_ONLY'
    assert metadata['source_acceptance_status'] == source.manifest['acceptance_status']
    assert metadata['source_sha256']['geometry.npz'] == source.manifest['files']['geometry.npz']
    with (tmp_path/'frames.csv').open() as f:
        rows = list(csv.DictReader(f))
    with np.load(tmp_path/'frame_geometry.npz', allow_pickle=False) as a:
        assert a['camera_transform_valid'].all()
        assert np.count_nonzero(a['clearance_valid']) > 1200
        assert not a['clearance_valid'].all()
        for row in rows:
            i = int(row['frame_id'])
            assert row['source_left_index'] == row['source_right_index'] == str(2 * i + 1)
            assert row['rpm_valid'] == 'True'
            assert float(row['rpm']) == source.rpm[2 * i + 1]
            for b in range(3):
                if a['clearance_valid'][i, b]:
                    gap = np.linalg.norm(a['tip_reference_world_m'][i, b] - a['nearest_tower_world_m'][i, b])
                    assert abs(float(row[f'clearance_b{b+1}_m'])) == pytest.approx(gap, abs=1e-9)
                    assert a['tip_reference_world_m'][i,b,2] == pytest.approx(a['nearest_tower_world_m'][i,b,2])
                else:
                    assert row[f'clearance_b{b+1}_m'] == ''
                    assert row[f'clearance_b{b+1}_reason'] == 'no_tower_section_at_tip_height'
                    assert np.isnan(a['nearest_tower_world_m'][i,b]).all()
        assert a['camera_world_transform'] == pytest.approx(a['nacelle_world_transform'] @ mount)
    saved = json.loads((tmp_path/'data_manifest.json').read_text())
    assert saved == metadata
    with pytest.raises(FileExistsError):
        export_frame_data(source, tmp_path)


def test_interpolation_identity_and_explicit_missing_camera(source):
    kwargs = dict(fps=25, start_s=117, end_s=117.12)
    rows, geometry, first = build_frame_table(source, **kwargs)
    assert any(r['center_sample_kind'] == 'interpolated' for r in rows)
    assert not geometry['camera_transform_valid'].any()
    assert np.isnan(geometry['camera_world_transform']).all()
    _, _, second = build_frame_table(source, **kwargs)
    assert first['dataset_id'] == second['dataset_id']
    _, _, changed = build_frame_table(source, camera_mount=np.eye(4), **kwargs)
    assert first['dataset_id'] != changed['dataset_id']


def test_out_of_range_and_source_overwrite_rejected(source):
    for t in (116.99, 177.01, float('nan')):
        with pytest.raises(ValueError):
            source.sample(t)
    with pytest.raises(ValueError, match='source package'):
        export_frame_data(source, SOURCE/'camera-output')


def test_bad_hash_is_not_silently_accepted(tmp_path):
    (tmp_path/'geometry.npz').write_bytes(b'changed')
    manifest = json.loads((SOURCE/'manifest.json').read_text())
    manifest['files'] = {'geometry.npz': manifest['files']['geometry.npz']}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='integrity mismatch'):
        SourceGeometry(tmp_path)


@pytest.mark.parametrize('config', [dict(turbine_id='T2'), dict(exposure_s=.025), dict(camera_mount=np.eye(4).tolist())])
def test_conflicting_camera_configuration_rejected(source, config):
    with pytest.raises(ValueError, match='Camera configuration'):
        build_frame_table(source, start_s=117, end_s=117.1, camera_config=config)


def test_reflected_camera_transform_rejected(source):
    reflected = np.eye(4)
    reflected[0,0] = -1
    with pytest.raises(ValueError, match='rigid finite'):
        build_frame_table(source, start_s=117, end_s=117.1, camera_mount=reflected)


def test_interrupted_frame_publish_resumes_only_identical_stage(source, tmp_path, monkeypatch):
    import wfrl.camera_video.data as module
    real_link = module.os.link
    calls = 0
    def interrupted_link(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('simulated interruption')
        return real_link(src, dst)
    monkeypatch.setattr(module.os, 'link', interrupted_link)
    kwargs = dict(start_s=117, end_s=117.1, camera_mount=np.eye(4))
    with pytest.raises(OSError, match='simulated interruption'):
        export_frame_data(source, tmp_path, **kwargs)
    assert (tmp_path/'frames.csv').exists()
    assert not (tmp_path/'data_manifest.json').exists()
    assert (tmp_path/'.frame-data-stage/data_manifest.json').exists()
    with pytest.raises(ValueError, match='inputs differ'):
        export_frame_data(source, tmp_path, **dict(kwargs, exposure_s=.025))
    monkeypatch.setattr(module.os, 'link', real_link)
    metadata = export_frame_data(source, tmp_path, **kwargs)
    assert (tmp_path/'data_manifest.json').exists()
    assert not (tmp_path/'.frame-data-stage').exists()
    assert metadata['time_contract']['frame_count'] == 2


def test_unowned_partial_data_is_not_overwritten(source, tmp_path):
    frame = tmp_path/'frames.csv'
    frame.write_text('existing data')
    with pytest.raises(FileExistsError, match='Unstaged partial'):
        export_frame_data(source, tmp_path, start_s=117, end_s=117.1)
    assert frame.read_text() == 'existing data'

"""Script boundaries reject unsupported evidence without manufacturing statistics."""
import json
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from scripts.radar_x100 import calibrate_from_ply as calibration
from scripts.radar_x100 import coverage, evaluate_blades, export_blade_ply, export_simulation
from wfrl.radar_x100.schema import POINT_DTYPE


def calibration_frames():
    frames = []
    for i in range(40):
        points = np.zeros(13, POINT_DTYPE)
        points['x'][:12] = np.arange(12) * .04
        points['y'][:12] = 30 + (i % 3) * .001
        points['y'][-1] = 175
        points['snr'] = np.arange(13) + i * .01
        points['mag'] = 2 * points['snr'] + 3
        frames.append(points)
    return frames


@pytest.mark.parametrize('kind', ['empty', 'single', 'no_far', 'no_extended', 'constant_snr', 'nonfinite'])
def test_calibration_rejects_insufficient_or_nonfinite_evidence(kind):
    frames = calibration_frames()
    if kind == 'empty':
        frames = []
    elif kind == 'single':
        frames = frames[:1]
    elif kind == 'no_far':
        frames = [frame[:-1] for frame in frames]
    elif kind == 'no_extended':
        frames = [frame[-1:] for frame in frames]
    elif kind == 'constant_snr':
        for frame in frames:
            frame['snr'] = 1
    else:
        frames[0]['rcs'][0] = np.nan
    with pytest.raises(ValueError, match='calibration|Calibration'):
        calibration.vendor_statistics(frames)


def test_calibration_valid_statistics_and_empty_frame_denominator():
    frames = calibration_frames()
    frames[10] = np.zeros(0, POINT_DTYPE)
    stats = calibration.vendor_statistics(frames)
    assert stats['frames'] == 40
    assert stats['points_per_frame_mean'] == 13 * 39 / 40
    assert stats['far_target']['bins'][0]['rate'] == 39 / 40
    assert stats['mag_fit']['slope'] == pytest.approx(2)
    json.dumps(stats, allow_nan=False)


@pytest.mark.parametrize('kind', ['missing', 'nan', 'no_bins', 'invalid_rate'])
def test_calibration_cache_rejected_before_fit_search(kind, monkeypatch):
    stats = calibration.vendor_statistics(calibration_frames())
    if kind == 'missing':
        del stats['jitter_median']['range_m']
    elif kind == 'nan':
        stats['mag_fit']['slope'] = np.nan
    elif kind == 'no_bins':
        stats['far_target']['bins'] = []
    else:
        stats['far_target']['bins'][0]['rate'] = 2
    def no_search(*args, **kwargs):
        pytest.fail('invalid calibration evidence reached fitting')
    monkeypatch.setattr(calibration, 'generate_detections', no_search)
    with pytest.raises(ValueError, match='calibration|Calibration'):
        calibration.fit(stats)


@pytest.mark.parametrize('module', [export_simulation, export_blade_ply])
@pytest.mark.parametrize('start,end,fps', [(2, 1, 30), (0, 1, -1), (0, 1, 0), (0, 1, np.nan)])
def test_export_grids_reject_invalid_bounds_and_rate(module, start, end, fps):
    with pytest.raises(ValueError, match='grid|interval|rate'):
        list(module.sample_times(start, end, fps))


@pytest.mark.parametrize('truth,predicted', [([1.5], [1]), ([1], [.5]), ([-2], [0]), ([[1]], [[1]])])
def test_separation_rejects_invalid_labels_before_integer_cast(truth, predicted):
    with pytest.raises(ValueError, match='labels'):
        evaluate_blades.separation_metrics(truth, predicted)


@pytest.mark.parametrize('expected', [-1, 0, .5, True])
def test_error_metrics_reject_impossible_denominators(expected):
    with pytest.raises(ValueError, match='expected|denominator'):
        evaluate_blades.error_metrics([1.], expected)


@pytest.mark.parametrize('values', [[np.nan], [np.inf]])
def test_geometric_distribution_rejects_nonfinite_errors(values):
    with pytest.raises(ValueError, match='Nonfinite'):
        evaluate_blades.distribution(values)


@pytest.mark.parametrize('times,angles,window', [([0, 1], [180], 30), ([0, 0], [160, 180], 30),
    ([0, 1], [160, np.nan], 30), ([0, 1], [160, 180], 0)])
def test_passages_reject_invalid_grid(times, angles, window):
    with pytest.raises(ValueError, match='grid|window|angle'):
        coverage.passages(np.array(times), np.array(angles), window)


def test_calibration_bad_ply_error_identifies_input_path(tmp_path, monkeypatch):
    bad = tmp_path / '001_RawPoints.ply'
    bad.write_bytes(b'ply\n')
    monkeypatch.setattr(sys, 'argv', ['calibrate', str(tmp_path), str(tmp_path / 'result.json')])
    with pytest.raises(ValueError, match=str(bad)):
        calibration.main()
    assert not (tmp_path / 'result.json').exists()


@pytest.mark.parametrize('module', [export_simulation, export_blade_ply])
def test_export_releases_registered_addon_when_scene_load_fails(module, tmp_path, monkeypatch):
    events = []
    def fail(*args):
        raise RuntimeError('scene unavailable')
    addon = NS(register=lambda: events.append('register'), unregister=lambda: events.append('unregister'),
               load_demo_scene=fail)
    modules = {'wfrl_blender': addon, 'wfrl_blender.farm_flex': NS(default_package=lambda: 'fixture'),
               'wfrl_blender.radar_x100.core': NS(), 'wfrl_blender.radar_x100.scene': NS()}
    monkeypatch.setattr(module.importlib, 'import_module', modules.__getitem__)
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(scene=NS())))
    monkeypatch.delenv('WFRL_ADDON_MODULE', raising=False)
    monkeypatch.setattr(sys, 'argv', ['export', '--', '--output', str(tmp_path / 'output')])
    with pytest.raises(RuntimeError, match='scene unavailable'):
        module.main()
    assert events == ['register', 'unregister']

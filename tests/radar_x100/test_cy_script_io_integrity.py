"""Report publication and shutdown retain complete files and release owned resources."""
import gzip
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest
import numpy as np

from scripts.radar_x100 import calibrate_from_ply as calibration, coverage, evaluate_blades as evaluation, export_blade_ply, extract_mount_summary, import_ply, serve
from tests.radar_x100.test_cy_script_validity import calibration_frames
from wfrl_blender import atomic_json
from wfrl.radar_x100 import detection, ply, recording, schema


def detection_args(tmp_path, **kwargs):
    return NS(output=tmp_path / 'report.json', observations=None, save_observations=None, **kwargs)


def test_observation_serialization_failure_does_not_publish_partial_gzip(tmp_path, monkeypatch):
    args = detection_args(tmp_path)
    args.save_observations = tmp_path / 'observations.json.gz'
    monkeypatch.setattr(evaluation, 'collect_detection_observations', lambda args: {'bad': object()})
    with pytest.raises(TypeError):
        evaluation.detection_main(args)
    assert list(tmp_path.iterdir()) == []


def test_observation_cache_hash_describes_bytes_actually_parsed(tmp_path, monkeypatch):
    cache = tmp_path / 'observations.json.gz'
    original = gzip.compress(json.dumps({'schema': 'x100-clearance-observations.v1', 'marker': 'original'}).encode())
    replacement = gzip.compress(json.dumps({'schema': 'x100-clearance-observations.v1', 'marker': 'changed'}).encode())
    cache.write_bytes(original)
    args = detection_args(tmp_path)
    args.observations = cache
    real_loads = json.loads
    def loads(*a, **kw):
        result = real_loads(*a, **kw)
        if isinstance(result, dict) and result.get('marker') == 'original':
            cache.write_bytes(replacement)
        return result
    monkeypatch.setattr(json, 'loads', loads)
    monkeypatch.setattr(evaluation, 'evaluate_detection_observations', lambda data: {'overall': {}, 'marker': data['marker']})
    evaluation.detection_main(args)
    report = real_loads(args.output.read_bytes())
    assert report['marker'] == 'original'
    assert report['observation_cache']['sha256'] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize('raw', [b'{', b'[]', b'{"schema":"x100-clearance-observations.v1","x":NaN}',
                                b'{"schema":"x100-clearance-observations.v1"}'])
def test_invalid_observation_json_reports_path(tmp_path, raw):
    cache = tmp_path / 'truncated.json.gz'
    cache.write_bytes(gzip.compress(raw))
    args = detection_args(tmp_path)
    args.observations = cache
    with pytest.raises(ValueError, match=str(cache)):
        evaluation.detection_main(args)


def test_evaluation_does_not_overwrite_output_created_during_work(tmp_path, monkeypatch):
    args = detection_args(tmp_path)
    monkeypatch.setattr(evaluation, 'collect_detection_observations', lambda args: {})
    def report(data):
        args.output.write_bytes(b'other complete report')
        return {'overall': {}}
    monkeypatch.setattr(evaluation, 'evaluate_detection_observations', report)
    with pytest.raises(FileExistsError):
        evaluation.detection_main(args)
    assert args.output.read_bytes() == b'other complete report'


def test_publisher_close_failure_still_closes_recording_worker(tmp_path, monkeypatch):
    closed = []
    fanout = NS(display_drops=0, close=lambda: closed.append('fanout'))
    class Publisher:
        port = 0
        def __init__(self, *args):
            pass
        def close(self):
            raise RuntimeError('publisher shutdown failed')
    monkeypatch.setattr(serve.acquisition, 'Fanout', lambda *a, **kw: fanout)
    monkeypatch.setattr(serve.acquisition, 'LocalPublisher', Publisher)
    monkeypatch.setattr(serve.importlib, 'import_module', lambda name: NS(empty=lambda: iter(())))
    monkeypatch.setattr(serve.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(sys, 'argv', ['serve', '--adapter', 'fixture:empty'])
    with pytest.raises(RuntimeError, match='publisher shutdown failed'):
        serve.main()
    assert closed == ['fanout']


def inject_text_write_failure(monkeypatch):
    """Fail writes after truncation, for both ordinary and atomic text streams."""
    original_write = Path.write_text
    original_open = Path.open
    original_fdopen = atomic_json.os.fdopen
    def broken_write(path, text, **kwargs):
        original_write(path, text[:5], **kwargs)
        raise OSError('injected short write')
    class BrokenStream:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def write(self, value):
            self.stream.write(value[:5])
            raise OSError('injected short write')
        def __getattr__(self, name):
            return getattr(self.stream, name)
    monkeypatch.setattr(Path, 'write_text', broken_write)
    def open_file(path, mode='r', *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return BrokenStream(stream) if 'b' not in mode and ('w' in mode or 'x' in mode) else stream
    monkeypatch.setattr(Path, 'open', open_file)
    monkeypatch.setattr(atomic_json.os, 'fdopen', lambda *a, **kw: BrokenStream(original_fdopen(*a, **kw)))


def test_failed_calibration_report_write_preserves_existing_result(tmp_path, monkeypatch):
    cache, output = tmp_path / 'cache.json', tmp_path / 'report.json'
    cache.write_text(json.dumps(calibration.vendor_statistics(calibration_frames())))
    output.write_bytes(b'prior report')
    monkeypatch.setattr(sys, 'argv', ['calibrate', str(tmp_path), str(output), str(cache)])
    monkeypatch.setattr(calibration, 'fit', lambda stats: (calibration.DetectionConfig(), {}))
    inject_text_write_failure(monkeypatch)
    with pytest.raises(OSError, match='short write'):
        calibration.main()
    assert output.read_bytes() == b'prior report'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['cache.json', 'report.json']


@pytest.fixture
def blade_export(tmp_path, monkeypatch):
    sampler = NS(interval=(0., 1.), calibration_id='fixture', sample=lambda t: None,
        visibility=lambda *a: None, material_sample=lambda *a: None,
        sampling_plan=lambda *a, **kw: None, sampling_transaction=nullcontext)
    diagnostics = schema.ModelDiagnostics(detection.MODEL_ID, np.zeros(0, schema.MODEL_DIAGNOSTIC_DTYPE))
    frame = schema.RadarFrame('X100', 'fixture', 0, 'ENGINEERING_SIMULATION', 'DETECTIONS',
        np.zeros(0, schema.POINT_DTYPE), model_diagnostics=diagnostics)
    calls = []
    api = NS(recording=recording, ply=ply, schema=schema,
             calibration=NS(calibrated_config=lambda **kw: detection.DetectionConfig()),
             simulation=NS(simulate_detections=lambda *a, **kw: frame))
    addon = NS(register=lambda: calls.append('register'), unregister=lambda: calls.append('unregister'),
               load_demo_scene=lambda package: None)
    modules = {'wfrl_blender': addon,
        'wfrl_blender.farm_flex': NS(default_package=lambda: None, active_for=lambda scene: NS(model='fixture')),
        'wfrl_blender.radar_x100.core': api,
        'wfrl_blender.radar_x100.scene': NS(SceneSampler=lambda *a, **kw: sampler,
            installation_matrix=lambda *a: np.eye(4), down_camera_installation=lambda scene: ([0]*3, [0]*3, [10]*2))}
    real_import = export_blade_ply.importlib.import_module
    monkeypatch.setattr(export_blade_ply.importlib, 'import_module', lambda name, *a, **kw:
                        modules[name] if name in modules else real_import(name, *a, **kw))
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(scene=NS())))
    monkeypatch.delenv('WFRL_ADDON_MODULE', raising=False)
    output = tmp_path / 'export'
    monkeypatch.setattr(sys, 'argv', ['export', '--', '--output', str(output), '--fps', '1', '--duration', '1'])
    return output, frame, calls


def test_blade_export_rejects_missing_diagnostics_instead_of_zero_mean(blade_export):
    output, frame, calls = blade_export
    object.__setattr__(frame, 'model_diagnostics', None)
    with pytest.raises(ValueError, match='diagnostic'):
        export_blade_ply.main()
    assert calls == ['register', 'unregister']
    assert not output.exists()


def test_blade_export_write_failure_does_not_publish_partial_bundle(blade_export, monkeypatch):
    output, frame, calls = blade_export
    write = Path.write_bytes
    def fail(path, data):
        if path.name.endswith('_RawPoints.ply'):
            write(path, data[:5])
            raise OSError('injected PLY short write')
        return write(path, data)
    monkeypatch.setattr(Path, 'write_bytes', fail)
    with pytest.raises(OSError, match='PLY short write'):
        export_blade_ply.main()
    assert not output.exists()
    assert calls == ['register', 'unregister']


@pytest.mark.parametrize('document', [{}, {'source_kind': 'TEST', 'frames': [{}]}])
def test_ply_index_missing_fields_report_index_path(tmp_path, document):
    path = tmp_path / 'index.json'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=str(path)):
        import_ply.import_index(path, tmp_path / 'recording')


@pytest.mark.parametrize('flag,value', [('--duration', 'nan'), ('--duration', 'inf'),
    ('--seed', '-1'), ('--first-frame-number', '-1')])
def test_blade_export_invalid_profile_is_rejected(blade_export, monkeypatch, flag, value):
    output, _, _ = blade_export
    monkeypatch.setattr(sys, 'argv', ['export', '--', '--output', str(output), '--fps', '1', flag, value])
    with pytest.raises((ValueError, SystemExit), match='Invalid|invalid|positive|nonnegative'):
        export_blade_ply.main()
    assert not output.exists()


def test_detection_config_invalid_json_reports_input_path(tmp_path):
    path = tmp_path / 'configuration.json'
    path.write_text('{')
    with pytest.raises(ValueError, match=str(path)):
        coverage.detection_configuration(NS(integration_budget=32, output_point_limit=32,
            noise_seeds=[0], detection_min_points=1, detection_config=path))


def test_summary_invalid_json_reports_evidence_path(tmp_path, monkeypatch):
    path = tmp_path / 'evidence.json'
    path.write_text('{')
    monkeypatch.setattr(sys, 'argv', ['summary', str(path)])
    with pytest.raises(SystemExit, match=str(path)):
        extract_mount_summary.main()


def test_geometric_report_short_write_leaves_no_partial_json(tmp_path, monkeypatch):
    import wfrl_blender as addon
    import wfrl_blender.radar_x100 as radar
    from scripts.radar_x100 import export_simulation
    class Scene(dict):
        pass
    sampler = NS(interval=(0., 1.), flex=NS(times=np.array([0., 1.]), poses=np.zeros((2, 1, 2))), k=0,
                 source_id='fixture', geometry_id='fixture', calibration_id='fixture', mount_from_sensor=np.eye(4))
    session = NS(sampler=sampler, update=lambda: None,
        blade_result={'blades': [dict(status='UNSUPPORTED', points=0, fit_residual_m=None,
                                     extrapolation_m=None, tip_world=None)] * 3},
        frame=NS(material_ids=[], provenance={'sampling_plan_id': 'fixture'}),
        blade_tracker=NS(config=detection.DetectionConfig()))
    runtime = NS(__file__=evaluation.__file__, start=lambda scene: session)
    scene_api = NS(__file__=evaluation.__file__)
    farm = NS(default_package=lambda: None)
    monkeypatch.setattr(addon, 'register', lambda: None)
    monkeypatch.setattr(addon, 'unregister', lambda: None)
    monkeypatch.setattr(addon, 'load_demo_scene', lambda package: None)
    monkeypatch.setattr(addon, 'farm_flex', farm, raising=False)
    monkeypatch.setattr(radar, 'runtime', runtime, raising=False)
    monkeypatch.setattr(radar, 'scene', scene_api, raising=False)
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(scene=Scene())))
    monkeypatch.setattr(export_simulation, 'sample_times', lambda *a: [0., 1.])
    output = tmp_path / 'geometry.json'
    monkeypatch.setattr(sys, 'argv', ['evaluate', '--output', str(output)])
    inject_text_write_failure(monkeypatch)
    with pytest.raises(OSError, match='short write'):
        evaluation.main()
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def observation_search(tmp_path, monkeypatch):
    import wfrl_blender as addon
    import wfrl_blender.radar_x100 as radar
    path = tmp_path / 'search.json'
    report = dict(evaluation_scope='ALL_COMPLETE_PASSAGES', winner_id='winner', code_identity={},
        source_id='fixture', geometry_id='fixture', analysis_roi={'lower_m': [-1]*3, 'upper_m': [1]*3},
        passage_windows=[[], [], []], distal_span=.8,
        candidates=[dict(id='winner', location_name='fixture', location_m=[0]*3, rotation_deg=[0]*3,
            detection_profiles=[dict(model={'parameters': {}}, integration_budget=32, sampling_seed=0, noise_seed=0,
                output_point_limit=32, sampling_plan_id='plan', grid_times_s=[], source_indices=[], frames=[])])])
    path.write_text(json.dumps(report))
    sampler = NS(source_id='fixture', geometry_id='fixture', sampling_plan=lambda *a: NS(identity='plan'))
    scene_api = NS(SceneSampler=lambda *a, **kw: sampler, SideWallQuery=lambda *a: None,
                   installation_matrix=lambda *a: np.eye(4))
    monkeypatch.setattr(addon, 'register', lambda: path.write_text('{}'))
    monkeypatch.setattr(addon, 'unregister', lambda: None)
    monkeypatch.setattr(addon, 'load_demo_scene', lambda package: None)
    monkeypatch.setattr(addon, 'farm_flex', NS(default_package=lambda: None, active_for=lambda scene: None), raising=False)
    monkeypatch.setattr(radar, 'scene', scene_api, raising=False)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.radar_x100.scene', scene_api)
    monkeypatch.setitem(sys.modules, 'bpy', NS(app=NS(background=True), context=NS(scene=None)))
    monkeypatch.setattr(evaluation, '_shape_payloads', lambda sampler: ({}, {}))
    return path, report


def test_observation_search_identity_uses_parsed_report_bytes(observation_search):
    path, _ = observation_search
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    result = evaluation.collect_detection_observations(NS(search_report=path, package=None))
    assert result['installation_report_sha256'] == expected


def test_observation_search_rejects_truncated_frame_grid(observation_search):
    path, report = observation_search
    report['candidates'][0]['detection_profiles'][0]['grid_times_s'] = [0.]
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='grid|frame'):
        evaluation.collect_detection_observations(NS(search_report=path, package=None))

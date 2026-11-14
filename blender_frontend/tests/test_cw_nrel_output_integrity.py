"""Failure-safe publication of NREL evidence files and paired captures."""
from copy import deepcopy
import json
from pathlib import Path
import stat
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_nrel_editor_playback import editor_runtime


@pytest.fixture
def outputs(frontend_module, monkeypatch):
    module = frontend_module('wfrl_blender.nrel_defects.defect_outputs')
    monkeypatch.setattr(module, 'bpy', NS(app=NS(version_string='test')))
    return module


@pytest.mark.parametrize('existing', [False, True])
def test_csv_failure_preserves_complete_record_pair(outputs, monkeypatch, tmp_path, existing):
    run = dict(output=tmp_path, manifest={'products': {'visibility': {'state': 'NOT_GENERATED'}}})
    if existing:
        outputs.save_records(run, [{'defect_id': 'old', 'visible_area_px2': None}])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    manifest = deepcopy(run['manifest'])
    real_writer = outputs.csv.DictWriter

    def broken_writer(stream, **kwargs):
        writer = real_writer(stream, **kwargs)
        writer.writerows = Mock(side_effect=OSError('CSV write failed'))
        return writer

    monkeypatch.setattr(outputs.csv, 'DictWriter', broken_writer)
    with pytest.raises(OSError, match='CSV write failed'):
        outputs.save_records(run, [{'defect_id': 'new', 'visible_area_px2': 10.}])
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert run['manifest'] == manifest


def test_review_replace_failure_keeps_old_file_and_no_temporary(outputs, monkeypatch, tmp_path):
    from wfrl_blender import atomic_json
    path = tmp_path / 'review.md'
    path.write_text('original review', encoding='utf-8')
    monkeypatch.setattr(atomic_json.os, 'replace', Mock(side_effect=OSError('replace failed')))
    with pytest.raises(OSError, match='replace failed'):
        outputs.save_review(None, dict(output=tmp_path, manifest={'state': 'PREPARED'}))
    assert path.read_text(encoding='utf-8') == 'original review'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('existing', [False, True])
def test_failed_run_preparation_leaves_no_partial_products(outputs, monkeypatch, tmp_path, existing):
    output = tmp_path / 'run'
    if existing:
        output.mkdir()
    editor = NS(draft=None, healthy_payload=None, verify_cameras=Mock(),
        flex=NS(times=[117., 117.05]), store=NS(document={'defects': []}, version=0),
        surface=NS(metadata=lambda: {}), scene={}, cameras=lambda: [object()],
        payload={'measurements': {}})
    monkeypatch.setattr(outputs, 'camera_metadata', Mock(side_effect=ValueError('camera unavailable')))
    with pytest.raises(ValueError, match='camera unavailable'):
        outputs.create_run(editor, output)
    assert output.exists() is existing
    assert not list(tmp_path.rglob('*')) if not existing else list(tmp_path.iterdir()) == [output] and not list(output.iterdir())


def test_new_run_refuses_directory_populated_during_preparation(outputs, monkeypatch, tmp_path):
    output = tmp_path / 'run'
    editor = NS(draft=None, healthy_payload=None, verify_cameras=Mock(),
        flex=NS(times=[117., 117.05]), store=NS(document={'defects': []}, version=0),
        surface=NS(metadata=lambda: {}), scene={}, cameras=lambda: [object()],
        payload={'measurements': {}})

    def another_run(*_args):
        output.mkdir()
        (output / 'defects.json').write_bytes(b'concurrent result')
        return {}

    monkeypatch.setattr(outputs, 'camera_metadata', another_run)
    with pytest.raises((ValueError, OSError)):
        outputs.create_run(editor, output)
    assert (output / 'defects.json').read_bytes() == b'concurrent result'
    assert [p.name for p in output.iterdir()] == ['defects.json']


@pytest.fixture
def capture_case(outputs, monkeypatch, tmp_path):
    render = NS(filepath='original', image_settings=NS(file_format='JPEG', color_mode='RGB', color_depth='16'),
        use_file_extension=False, resolution_x=640, resolution_y=480, resolution_percentage=50,
        pixel_aspect_x=1., pixel_aspect_y=1., use_border=True, use_crop_to_border=True,
        use_compositing=True, use_sequencer=True, use_motion_blur=True)

    class Scene(dict):
        camera = object()
        frame_current, frame_subframe = 1, .25
        name = 'editor-scene'
        frame_set = Mock()

    scene = Scene(defect_time_s=117.)
    scene.render = render
    payload, healthy = object(), object()
    editor = NS(scene=scene, store=NS(document={'defects': []}), payload=payload,
        geometry=NS(prepare=Mock(return_value=healthy), activate=Mock(), dispose=Mock()),
        set_time=Mock(), cameras=lambda: [NS(name='C1'), NS(name='C2')], verify_cameras=Mock(),
        camera_resolution=lambda _camera: (16, 16))
    run = dict(output=tmp_path, manifest=dict(state='PREPARED', images=[],
        products={name: dict(state='NOT_GENERATED') for name in ('camera_raw', 'healthy')}))
    monkeypatch.setattr(outputs, 'create_run', lambda *_args, **_kwargs: run)
    from wfrl_blender import custom_cameras, camera_projection
    from wfrl_blender.nrel_defects import defect_visibility
    monkeypatch.setattr(custom_cameras, 'parameters', lambda _camera: NS(fov=60., vfov=60.))
    monkeypatch.setattr(camera_projection, 'pixel_scales', lambda *_args: (1., 1.))
    monkeypatch.setattr(defect_visibility, '_camera_descriptor', lambda *_args, **_kwargs: dict(K=[], P=[], matrix_world=[]))
    return NS(module=outputs, editor=editor, render=render, scene=scene, payload=payload, healthy=healthy, run=run)


@pytest.mark.parametrize('fail_at', [1, 2])
def test_failed_paired_capture_removes_partial_images_and_restores_settings(capture_case, fail_at):
    r = capture_case
    before = deepcopy(vars(r.render))
    old_camera = r.scene.camera
    calls = []

    def render(**_kwargs):
        calls.append(1)
        Path(r.render.filepath).write_bytes(b'partial PNG' if len(calls) == fail_at else b'PNG')
        if len(calls) == fail_at:
            raise RuntimeError('render failed')
        return {'FINISHED'}

    r.module.bpy.ops = NS(render=NS(render=render))
    with pytest.raises(RuntimeError, match='render failed'):
        r.module.capture_images(r.editor, r.run['output'])
    assert not list(r.run['output'].rglob('*.png'))
    assert r.run['manifest']['state'] == 'A_FAILED' and not r.run['manifest']['images']
    assert vars(r.render) == before and r.scene.camera is old_camera
    r.editor.geometry.dispose.assert_called_once_with(r.healthy)
    assert r.editor.geometry.activate.call_args.args == (r.payload,)


def test_capture_restore_failure_still_disposes_and_preserves_primary(capture_case):
    r = capture_case
    primary = RuntimeError('render failed')
    r.module.bpy.ops = NS(render=NS(render=Mock(side_effect=primary)))
    count = []

    def activate(_payload):
        count.append(1)
        if len(count) == 2:
            raise ValueError('geometry restore failed')

    r.editor.geometry.activate.side_effect = activate
    with pytest.raises(RuntimeError) as caught:
        r.module.capture_images(r.editor, r.run['output'])
    assert caught.value is primary
    r.editor.geometry.dispose.assert_called_once_with(r.healthy)
    assert any('geometry restore failed' in note for note in getattr(primary, '__notes__', []))


def test_null_measurements_survive_json_and_csv(outputs, tmp_path):
    run = dict(output=tmp_path, manifest={'products': {}})
    rows = [dict(defect_id='missing', status='INVALID', visible_area_px2=None)]
    outputs.save_records(run, rows)
    assert json.loads((tmp_path / 'visibility.json').read_text(encoding='utf-8')) == rows
    with (tmp_path / 'projection_stats.csv').open(encoding='utf-8', newline='') as stream:
        row = next(outputs.csv.DictReader(stream))
    assert row['visible_area_px2'] == ''


def test_failed_scan_intervals_preserve_previous_records(editor_runtime, outputs, monkeypatch, tmp_path):
    r = editor_runtime
    scan = r.namespace.NREL_OT_defect_scan()
    scan.finished, scan.timer, scan.playing = False, None, False
    scan.scan_session, scan.scan_scene = r.session, r.scene
    scan.scene_key = r.scene.as_pointer()
    scan.scan_window, scan.scan_wm = r.window, r.context.window_manager
    scan.scan_view_layer = r.scene.view_layers[0]
    scan.saved_time, scan.index, scan.rows = 120., 1, [{'defect_id': 'new'}]
    scan.mapping = NS(frame_count=1, to_dict=lambda: {})
    scan.run = dict(output=tmp_path, manifest=dict(state='PREPARED', products={}))
    scan.report = Mock()
    outputs.save_records(scan.run, [{'defect_id': 'old'}])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    real_write = outputs.write_json

    def fail_intervals(path, value):
        if path.name == 'visible_intervals.json':
            raise OSError('interval write failed')
        return real_write(path, value)

    monkeypatch.setattr(outputs, 'write_json', fail_intervals)
    assert scan.finish_scan(r.context) == {'CANCELLED'}
    assert scan.run['manifest']['state'] == 'FAILED'
    assert all((tmp_path / name).read_bytes() == content for name, content in before.items())


def test_record_publish_failure_rolls_back_files_and_manifest(outputs, monkeypatch, tmp_path):
    run = dict(output=tmp_path, manifest=dict(products={}))
    outputs.save_records(run, [{'defect_id': 'old'}])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    manifest = deepcopy(run['manifest'])
    real_replace = outputs.os.replace

    def fail_visibility(source, target):
        if Path(target) == tmp_path / 'visibility.json':
            raise OSError('publish failed')
        return real_replace(source, target)

    monkeypatch.setattr(outputs.os, 'replace', fail_visibility)
    with pytest.raises(OSError, match='publish failed'):
        outputs.save_records(run, [{'defect_id': 'new'}])
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert run['manifest'] == manifest


def test_record_replacement_preserves_existing_file_permissions(outputs, tmp_path):
    run = dict(output=tmp_path, manifest=dict(products={}))
    outputs.save_records(run, [{'defect_id': 'old'}])
    for path in tmp_path.iterdir():
        path.chmod(0o600)
    before = {p.name: stat.S_IMODE(p.stat().st_mode) for p in tmp_path.iterdir()}
    outputs.save_records(run, [{'defect_id': 'new'}])
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in tmp_path.iterdir()} == before


def test_failed_rollback_keeps_original_backup_and_reports_location(outputs, monkeypatch, tmp_path):
    output = tmp_path / 'run'
    output.mkdir()
    run = dict(output=output, manifest=dict(products={}))
    outputs.save_records(run, [{'defect_id': 'old'}])
    original = (output / 'projection_stats.csv').read_bytes()
    real_replace = outputs.os.replace

    def fail_publish_and_rollback(source, target):
        if Path(target) == output / 'visibility.json' or Path(source).parent.name == 'previous':
            raise OSError('replacement unavailable')
        return real_replace(source, target)

    monkeypatch.setattr(outputs.os, 'replace', fail_publish_and_rollback)
    with pytest.raises(OSError) as caught:
        outputs.save_records(run, [{'defect_id': 'new'}])
    backups = list(tmp_path.glob('.nrel-output-*/previous/projection_stats.csv'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert any(str(backups[0]) in note for note in caught.value.__notes__)


def test_failed_analysis_review_preserves_previous_bundle(outputs, monkeypatch, tmp_path):
    run = dict(output=tmp_path, manifest=dict(run_id='test-run', state='PREPARED', products={}))
    outputs.save_records(run, [{'defect_id': 'old'}])
    outputs.update_run(run)
    outputs.save_review(None, run)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    manifest = deepcopy(run['manifest'])
    monkeypatch.setattr(outputs, 'save_review', Mock(side_effect=OSError('review failed')))
    with pytest.raises(OSError, match='review failed'):
        outputs.save_analysis(NS(analyze=lambda **_kwargs: [{'defect_id': 'new'}]), run)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert run['manifest'] == manifest


def test_successful_capture_publishes_complete_images_after_restoration(capture_case):
    r = capture_case
    before, camera = deepcopy(vars(r.render)), r.scene.camera

    def render(**_kwargs):
        Path(r.render.filepath).write_bytes(b'PNG')
        return {'FINISHED'}

    r.module.bpy.ops = NS(render=NS(render=render))
    result = r.module.capture_images(r.editor, r.run['output'])
    assert result['manifest']['state'] == 'A_IMAGES_GENERATED; NOT_REVIEWED'
    assert len(result['manifest']['images']) == 4
    for image in result['manifest']['images']:
        assert r.module.digest(result['output'] / image['path']) == image['sha256']
    assert vars(r.render) == before and r.scene.camera is camera
    assert not any(p.name.startswith('.') for p in result['output'].iterdir())

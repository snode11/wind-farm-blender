"""Camera delivery failure and publication ownership contracts."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.camera_video import loop, package


def test_json_write_preserves_unowned_temporary_name(tmp_path):
    target = tmp_path / 'report.json'
    foreign = tmp_path / 'report.json.tmp'
    foreign.write_text('another writer', encoding='utf-8')
    package.write_json(target, {'valid': True})
    assert json.loads(target.read_text()) == {'valid': True}
    assert foreign.read_text() == 'another writer'


def test_json_failed_commit_preserves_report_and_removes_owned_stage(tmp_path, monkeypatch):
    target = tmp_path / 'report.json'
    target.write_text('{"old": true}', encoding='utf-8')
    def fail_replace(self, destination):
        raise OSError('commit failed')
    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(OSError, match='commit failed'):
        package.write_json(target, {'new': True})
    assert json.loads(target.read_text()) == {'old': True}
    assert list(tmp_path.iterdir()) == [target]


@pytest.fixture
def loop_source(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'manifest.json').write_text(json.dumps({
        'schema': 'wfrl.camera-video.v1', 'dataset_id': 'source',
        'fps': 20, 'width': 32, 'height': 18,
    }))
    (source / 'video.mp4').write_bytes(b'fixture media')
    (source / 'camera.json').write_text('{}')
    (source / 'frames.csv').write_text('frame_id,video_pts,sim_time_s\n0,0,117.025\n')
    np.savez(source / 'frame_geometry.npz', frame_id=np.array([0]))
    monkeypatch.setattr(package, 'verify_package', lambda *a, **kw: {'valid': True})
    def encode(command, **kwargs):
        Path(command[-1]).write_bytes(b'fixture encoded loop')
    monkeypatch.setattr(loop, 'subprocess', SimpleNamespace(run=encode))
    monkeypatch.setattr(loop, 'verify_loop', lambda *a, **kw: {'valid': True})
    return source


@pytest.mark.parametrize('failure', ['copy', 'encode', 'verify'])
def test_loop_build_failure_removes_owned_stage(loop_source, tmp_path, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise OSError('injected build failure')
    if failure == 'copy':
        monkeypatch.setattr(loop.shutil, 'copy2', fail)
    elif failure == 'encode':
        monkeypatch.setattr(loop, 'subprocess', SimpleNamespace(run=fail))
    else:
        monkeypatch.setattr(loop, 'verify_loop', fail)
    with pytest.raises(OSError, match='injected build failure'):
        loop.build_loop(loop_source, tmp_path / 'output')
    assert {p.name for p in tmp_path.iterdir()} == {'source'}


def test_loop_commit_preserves_concurrent_empty_directory(loop_source, tmp_path, monkeypatch):
    output = tmp_path / 'output'
    def verify(*args, **kwargs):
        output.mkdir()
        return {'valid': True}
    monkeypatch.setattr(loop, 'verify_loop', verify)
    with pytest.raises(FileExistsError):
        loop.build_loop(loop_source, output)
    assert output.is_dir() and not list(output.iterdir())
    assert {p.name for p in tmp_path.iterdir()} == {'source', 'output'}


def test_loop_dangling_destination_is_not_followed(loop_source, tmp_path):
    output = tmp_path / 'output'
    destination = tmp_path / 'foreign-target'
    output.symlink_to(destination, target_is_directory=True)
    with pytest.raises(FileExistsError):
        loop.build_loop(loop_source, output)
    assert output.is_symlink() and not destination.exists()

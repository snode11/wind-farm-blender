"""Section archives publish complete files and reject non-finite provenance."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.blender_bridge import blade_flex_export as flex


@pytest.fixture
def source(tmp_path, monkeypatch):
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'manifest.json').write_text('{}', encoding='utf-8')
    reader = SimpleNamespace(times=[0., 1.], fps=1., section_count=1, run=tmp_path,
                             hashes={}, package=SimpleNamespace(manifest={
                                 'turbine_id': 'T1', 'run_id': 'fixture'}))
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
    surfaces = {}
    for bid in (1, 2, 3):
        for frame in (0, 1):
            key = f'FarmInputs/vtk/Case.T1.Blade{bid}Surface.{frame:05d}.vtp'
            path = reader.run / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key.encode('utf-8'))
            reader.hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            surfaces[path] = points + frame * np.array([1., 2., 3.])
    monkeypatch.setattr(flex, 'read_surface', lambda path: (surfaces[path], None))
    return reader, package


def export(source, path, **options):
    flex.export_reader(*source, path, 0., 1., **options)


@pytest.mark.parametrize('failure', ['archive', 'metadata'])
def test_export_write_failure_preserves_existing_files_and_removes_staging(source, tmp_path, monkeypatch, failure):
    output = tmp_path / 'output'
    output.mkdir()
    archive, metadata = output / 'preview.npz', output / 'preview.json'
    archive.write_bytes(b'previous archive')
    metadata.write_text('{"previous":true}', encoding='utf-8')
    before = {path: path.read_bytes() for path in output.iterdir()}
    if failure == 'archive':
        def fail_archive(destination, **_arrays):
            if hasattr(destination, 'write'):
                destination.write(b'incomplete zip')
            else:
                Path(destination).write_bytes(b'incomplete zip')
            raise OSError('archive disk full')
        monkeypatch.setattr(flex.np, 'savez_compressed', fail_archive)
    else:
        write_text = Path.write_text

        def fail_metadata(path, text, **kwargs):
            if path.suffix == '.json':
                write_text(path, '{', **kwargs)
                raise OSError('metadata disk full')
            return write_text(path, text, **kwargs)
        monkeypatch.setattr(Path, 'write_text', fail_metadata)
    with pytest.raises(OSError, match='disk full'):
        export(source, archive)
    assert {path: path.read_bytes() for path in output.iterdir()} == before


@pytest.mark.parametrize('field', ['mean_mps', 'ti_percent', 'gust_increment_mps',
                                  'gust_peak_mps', 'gust_duration_s'])
@pytest.mark.parametrize('value', [np.nan, np.inf, -np.inf])
def test_export_rejects_nonfinite_wind_before_publishing(source, tmp_path, field, value):
    wind = dict(mean_mps=8., ti_percent=10., gust_increment_mps=2.,
                gust_peak_mps=10., gust_duration_s=1.)
    wind[field] = value
    output = tmp_path / 'output' / 'preview.npz'
    with pytest.raises(ValueError, match='wind'):
        export(source, output, wind=wind)
    assert not output.exists() and not output.with_suffix('.json').exists()


@pytest.mark.parametrize('name', ['preview.npz', 'preview'])
def test_export_round_trip_preserves_transforms_and_metadata(source, tmp_path, name):
    output = tmp_path / 'output' / name
    export(source, output)
    archive = output if output.suffix == '.npz' else Path(str(output) + '.npz')
    with np.load(archive, allow_pickle=False) as saved:
        np.testing.assert_array_equal(saved['times'], [0., 1.])
        np.testing.assert_allclose(saved['transforms'][1, :, 0, :, 3], [[1., 2., 3.]] * 3)
        assert json.loads(str(saved['metadata'])) == json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
    assert set(output.parent.iterdir()) == {archive, output.with_suffix('.json')}

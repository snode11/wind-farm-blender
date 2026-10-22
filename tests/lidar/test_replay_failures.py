"""Publication fault injection uses isolated temporary data, never real packages."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import test_replay as fixtures
from wfrl.lidar.replay import ReplayPackage, publish_package


@pytest.mark.parametrize('stage', ['write', 'validate', 'rename'])
def test_interrupted_publish_retains_failure_and_sources(tmp_path, stage):
    manifest, motion, measurements = fixtures.PackageTests().fixture()
    source = tmp_path / 'raw-source.json'
    source.write_bytes(b'{"original":true}\n')
    manifest['raw_sources'][0]['path'] = str(source)
    before = copy.deepcopy((manifest, motion, measurements))
    destination = tmp_path / 'result'
    error = OSError('injected publication ' + stage + ' failure')
    original_write = Path.write_text

    def write_with_fault(path, *args, **kwargs):
        # Keep an already written motion file and permit failure.json persistence.
        if path.name == 'measurements.json':
            raise error
        return original_write(path, *args, **kwargs)

    if stage == 'write':
        fault = patch.object(Path, 'write_text', write_with_fault)
    elif stage == 'validate':
        fault = patch.object(ReplayPackage, 'load', side_effect=error)
    else:
        fault = patch.object(Path, 'rename', side_effect=error)
    with fault, pytest.raises(OSError, match='injected publication') as caught:
        publish_package(destination, manifest, motion, measurements, 'synthetic fixture only')
    assert caught.value is error
    assert not destination.exists()
    retained = list(tmp_path.glob('.lidar-*'))
    assert len(retained) == 1
    failure = json.loads((retained[0] / 'failure.json').read_text())
    assert failure == {'status': 'FAILED', 'error': str(error)}
    assert json.loads((retained[0] / 'motion.json').read_text()) == motion
    if stage != 'write':
        assert (retained[0] / 'manifest.json').is_file()
        assert (retained[0] / 'report.md').read_text() == 'synthetic fixture only'
    assert source.read_bytes() == b'{"original":true}\n'
    assert (manifest, motion, measurements) == before


def test_existing_destination_is_never_overwritten(tmp_path):
    destination = tmp_path / 'result'
    destination.mkdir()
    sentinel = destination / 'original.bin'
    sentinel.write_bytes(b'keep existing package')
    manifest, motion, rows = fixtures.PackageTests().fixture()
    with pytest.raises(ValueError, match='destination already exists'):
        publish_package(destination, manifest, motion, rows, 'synthetic fixture only')
    assert sentinel.read_bytes() == b'keep existing package'
    assert list(destination.iterdir()) == [sentinel]
    assert not list(tmp_path.glob('.lidar-*'))

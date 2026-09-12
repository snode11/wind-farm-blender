"""Preparation rejects ambiguous runs before copying physical inputs."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'scripts/lidar/run_physics.py'
spec = importlib.util.spec_from_file_location('lidar_run_cli', SOURCE)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.mark.parametrize('kwargs', [dict(name='../escape'), dict(wind=float('nan')),
                                     dict(fps=0), dict(dt=.008)])
def test_invalid_parameters_do_not_copy(kwargs):
    options = dict(name='run', wind=8)
    options.update(kwargs)
    with patch.object(cli.shutil, 'copytree') as copy:
        with pytest.raises(ValueError):
            cli.prepare(**options)
        copy.assert_not_called()


def test_replace_preserves_output_channel_and_literal_key(tmp_path):
    path = tmp_path / 'ElastoDyn.dat'
    path.write_text('9 RotSpeed - rpm\n1 BlPitch(1) - deg\n"RotSpeed"\n')
    cli.replace(path, {'RotSpeed': 10, 'BlPitch(1)': 0})
    assert '10    RotSpeed - rpm' in path.read_text()
    assert '0    BlPitch(1) - deg' in path.read_text()
    assert '"RotSpeed"' in path.read_text()


def test_missing_executable_preserves_failed_status(tmp_path):
    import json
    with patch.object(cli, 'prepare', return_value=tmp_path), patch('sys.argv', [
        'run_physics.py', '--name', 'failed', '--wind', '8',
        '--executable', str(tmp_path / 'absent-executable'),
    ]):
        with pytest.raises(FileNotFoundError):
            cli.main()
    status = json.loads((tmp_path / 'exit_status.json').read_text())
    assert status['status'] == 'FAILED'
    assert status['exit_code'] is None
    assert 'FileNotFoundError' in status['error']

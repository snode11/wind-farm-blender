"""CLI exit status agrees with durable recording state."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from wfrl.radar_x100 import recording


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def adapter(tmp_path):
    module = tmp_path / 'serve_adapter.py'
    module.write_text('''
import numpy as np
from wfrl.radar_x100 import acquisition, recording, schema

def empty():
    return iter(())

def one():
    yield schema.RadarFrame('X100', 'cli-test', 0, 'TEST', 'UNKNOWN',
                           np.zeros(0, dtype=schema.POINT_DTYPE))

def disk_failure():
    atomic = recording._atomic
    def fail(path, raw):
        if path.suffix == '.ply':
            raise OSError('injected disk failure')
        return atomic(path, raw)
    recording._atomic = fail
    yield from one()

def acquisition_failure():
    raise ValueError('injected adapter failure')

def session_events():
    yield acquisition.SessionStart('X100','cli-test','TEST')
    yield from one()
    yield acquisition.SessionStart('X100','restarted','TEST')
    yield schema.RadarFrame('X100','restarted',0,'TEST','UNKNOWN',
                           np.zeros(0,dtype=schema.POINT_DTYPE))
    yield from one()
''')
    return tmp_path


def run_serve(adapter, factory, *, record=True):
    destination = adapter / 'recording'
    command = [sys.executable, str(ROOT / 'scripts/radar_x100/serve.py'),
               '--adapter', 'serve_adapter:' + factory, '--port', '0']
    if record:
        command += ['--record', str(destination)]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(adapter), str(ROOT))))
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=15)
    summary = json.loads(result.stdout.splitlines()[-1])
    return result, summary, destination


@pytest.mark.parametrize('factory, reason', [
    ('empty', 'no frames'), ('disk_failure', 'injected disk failure'),
])
def test_recording_failure_exits_nonzero_and_retains_incomplete(adapter, factory, reason):
    result, summary, destination = run_serve(adapter, factory)
    assert summary['recording_state'] == 'FAILED'
    assert reason in summary['recording_error']
    assert (destination / 'INCOMPLETE').exists()
    with pytest.raises(ValueError, match='incomplete'):
        recording.Replay(destination)
    assert result.returncode != 0, result.stdout


def test_valid_empty_frame_is_a_successful_recording(adapter):
    result, summary, destination = run_serve(adapter, 'one')
    assert result.returncode == 0, result.stderr
    assert summary['recording_state'] == 'COMPLETE'
    assert summary['recording_error'] is None
    assert not (destination / 'INCOMPLETE').exists()
    assert recording.Replay(destination).frame(0).validity == 'VALID_EMPTY'


def test_session_events_are_control_only_and_late_frames_still_record(adapter):
    result, summary, destination = run_serve(adapter, 'session_events')
    assert result.returncode == 0, result.stderr
    assert summary['recording_state'] == 'COMPLETE'
    replay = recording.Replay(destination)
    assert len(replay.rows) == 3
    assert [replay.frame(i).session_id for i in range(3)] == ['cli-test','restarted','cli-test']


def test_display_only_empty_adapter_can_exit_successfully(adapter):
    result, summary, destination = run_serve(adapter, 'empty', record=False)
    assert result.returncode == 0, result.stderr
    assert summary['recording_state'] == 'DISABLED'
    assert not destination.exists()


def test_acquisition_exception_is_preserved(adapter):
    result, summary, destination = run_serve(adapter, 'acquisition_failure')
    assert result.returncode != 0
    assert 'ValueError: injected adapter failure' in result.stderr
    assert summary['recording_state'] == 'FAILED'
    assert (destination / 'INCOMPLETE').exists()

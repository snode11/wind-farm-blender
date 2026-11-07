"""Worker startup and cleanup retain the original failure and close resources."""
import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge import isolated_trainer
from wfrl.blender_bridge.isolated_trainer import IsolatedTrainer
from wfrl.blender_bridge.floris_session import FlorisDemoSession
from wfrl.scene.schema import load_scene
from test_floris_session import Driver, scene


@pytest.mark.parametrize('failure', ['scene', 'listener', 'launcher'])
def test_isolated_start_failure_releases_directory_and_listener(monkeypatch, failure):
    trainer = IsolatedTrainer(load_scene('scenes/turb3_ctrl3.yaml'), lambda snapshot: None)
    listener = Mock()
    monkeypatch.setattr(isolated_trainer.socket, 'socket', Mock(return_value=listener))
    original = ValueError('startup failed')
    if failure == 'scene':
        monkeypatch.setattr('wfrl.scene.schema.dump_scene', Mock(side_effect=original))
    elif failure == 'listener':
        listener.bind.side_effect = original
    else:
        monkeypatch.setattr(trainer, '_worker_command', Mock(side_effect=original))
    try:
        with pytest.raises(ValueError, match='startup failed') as caught:
            trainer.start()
        assert caught.value is original
        assert not Path(trainer._directory.name).exists()
        if failure != 'scene':
            listener.close.assert_called_once_with()
        assert not trainer.running
    finally:
        if trainer._directory is not None:
            trainer._directory.cleanup()


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_isolated_reader_preserves_worker_error_and_closes_stream(monkeypatch, cleanup_fails):
    trainer = IsolatedTrainer(None, lambda snapshot: None)
    stream = io.StringIO('{"kind":"error","message":"primary worker failure"}\ninvalid json\n')
    connection = Mock()
    connection.makefile.return_value = stream
    trainer._process = Mock(poll=lambda: None)
    trainer._listener = Mock(accept=lambda: (connection, None))
    monkeypatch.setattr(trainer, '_group_alive', lambda: False)
    if cleanup_fails:
        monkeypatch.setattr(trainer, '_cleanup', Mock(side_effect=OSError('files cleanup failed')))
    trainer._read()
    assert trainer.error.startswith('primary worker failure')
    assert 'transport' in trainer.error
    if cleanup_fails:
        assert 'files cleanup failed' in trainer.error
    assert stream.closed
    connection.close.assert_called_once_with()
    trainer._listener.close.assert_called_once_with()


def test_floris_cleanup_preserves_original_model_failure():
    class FailingDriver(Driver):
        def step(self, delta):
            raise ValueError('primary model failure')

        def close(self, **kwargs):
            super().close(**kwargs)
            raise OSError('driver cleanup failed')

    trainer = FlorisDemoSession(scene(), frame_interval=0, driver_factory=FailingDriver)
    trainer.start()
    trainer._thread.join(1)
    assert not trainer.running
    assert trainer.error.startswith('primary model failure')
    assert 'driver cleanup failed' in trainer.error

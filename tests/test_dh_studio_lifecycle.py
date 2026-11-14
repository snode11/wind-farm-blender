"""Studio stop callbacks and failed cleanup retain their lifecycle guarantees."""
from unittest.mock import Mock

import pytest

from wfrl.scene.schema import Scene, Turbine
from wfrl.scene.runtime import SceneRuntime
from wfrl.studio.trainer import Trainer
from test_interactive_episodes import simulation


def make_trainer(**options):
    return Trainer(Scene(name='lifecycle', controls=['yaw'], sensors=[],
                         turbines=[Turbine('T1', 0, 0)]), **options)


def test_stop_from_snapshot_callback_does_not_join_itself(monkeypatch):
    trainer = make_trainer()
    trainer.on_snapshot = lambda _snapshot: trainer.stop()
    monkeypatch.setattr(trainer, '_train', lambda: trainer._publish(step=1))
    trainer.start()
    trainer._thread.join(2)
    assert not trainer.running and trainer.error is None
    assert trainer._stop.is_set()
    assert trainer.diagnostics['snapshot_callback_failure_count'] == 0


@pytest.mark.parametrize('mode', ['demo', 'replay', 'training'])
def test_start_rejects_retained_runtime_before_creating_thread(monkeypatch, mode):
    trainer = make_trainer(demo=mode == 'demo', replay=mode == 'replay')
    retained = Mock()
    trainer.runtime = retained
    trainer.error = 'runtime close failed'
    run = Mock()
    monkeypatch.setattr(trainer, '_run', run)
    with pytest.raises(RuntimeError, match='cleanup|Cleanup'):
        trainer.start()
    assert trainer.runtime is retained and trainer.error == 'runtime close failed'
    run.assert_not_called()


def test_replay_close_failure_keeps_runtime_and_driver_visible(monkeypatch):
    trainer = make_trainer(replay=True)
    runtime = SceneRuntime.__new__(SceneRuntime)
    driver = Mock()
    driver.close.side_effect = RuntimeError('driver cleanup failed')
    runtime.driver = driver
    monkeypatch.setattr(trainer, '_replay_policy', lambda: setattr(trainer, 'runtime', runtime))
    trainer._run()
    assert trainer.error and 'driver cleanup failed' in trainer.error
    assert trainer.runtime is runtime and runtime.driver is driver
    with pytest.raises(RuntimeError, match='cleanup'):
        trainer.start()


def test_restarted_trainer_cannot_overwrite_checkpoint_before_its_first_update(simulation, tmp_path):
    path = tmp_path / 'trained.pt'
    trainer = simulation.make(capture=False, iters=1, n_steps=2, warmup_steps=0,
                              ckpt_path=str(path))
    trainer.start()
    trainer._thread.join(5)
    assert not trainer.running and trainer.error is None
    previous = path.read_bytes()
    trainer.on_snapshot = lambda sample: trainer._stop.set() if sample.phase == 'sampling' else None
    trainer.start()
    trainer._thread.join(5)
    assert not trainer.running and trainer.error is None
    assert len(trainer.history) == 1
    assert path.read_bytes() == previous

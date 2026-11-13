"""Synthetic trainer snapshots and lifecycle checks without a physics backend."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge import fake_backend


def test_constructor_binds_scene_callback_and_initial_state():
    scene = SimpleNamespace(turbines=[object()])
    callback = Mock()
    trainer = fake_backend.FakeTrainer(scene, callback, unused_option=True)
    assert trainer.scene is scene
    assert trainer.callback is callback
    assert trainer.error is None
    assert not trainer._stop.is_set()
    callback.assert_not_called()


@pytest.mark.parametrize("turbine_count", [0, 1, 3])
def test_start_emits_deterministic_snapshots_and_stop_joins(monkeypatch, turbine_count):
    snapshots = []
    waits = []

    def on_snapshot(snapshot):
        snapshots.append(snapshot)
        if len(snapshots) == 32:
            trainer._stop.set()

    trainer = fake_backend.FakeTrainer(SimpleNamespace(turbines=[object()] * turbine_count), on_snapshot)
    monkeypatch.setattr(trainer._stop, "wait", lambda timeout: waits.append(timeout))
    trainer.start()
    try:
        trainer._thread.join(2)
        assert not trainer._thread.is_alive()
        assert trainer._thread.name == "wfrl-fake"
        assert trainer._thread.daemon
        assert len(snapshots) == 32
        assert waits == [0.1] * 32
        for step, snapshot in enumerate(snapshots):
            assert snapshot.step == step
            assert snapshot.ctx == {"t": pytest.approx(step / 10.0)}
            assert snapshot.measure == {
                "yaw": [step % 30] * turbine_count,
                "pitch": [2.0] * turbine_count,
                "rotor_speed": [8.0] * turbine_count,
                "power": [2.0] * turbine_count,
            }
            assert snapshot.farm_power == 2.0 * turbine_count
            assert snapshot.reward == 0.5
            assert snapshot.events == []
        assert snapshots[0].measure is not snapshots[1].measure
        assert snapshots[0].events is not snapshots[1].events
        assert trainer.error is None
    finally:
        trainer.stop(timeout=2)
    assert trainer._stop.is_set()
    assert not trainer._thread.is_alive()
    trainer.stop(timeout=0)


def test_stop_timeout_preserves_live_thread_until_callback_returns():
    entered = threading.Event()
    release = threading.Event()
    snapshots = []

    def on_snapshot(snapshot):
        snapshots.append(snapshot)
        entered.set()
        release.wait(5)

    trainer = fake_backend.FakeTrainer(SimpleNamespace(turbines=[object()]), on_snapshot)
    trainer.start()
    try:
        assert entered.wait(2)
        assert trainer.stop(timeout=0) is None
        assert trainer._stop.is_set()
        assert trainer._thread.is_alive()
    finally:
        release.set()
        trainer.stop(timeout=2)
    assert not trainer._thread.is_alive()
    assert len(snapshots) == 1


def test_stop_before_start_is_safe_and_idempotent():
    trainer = fake_backend.FakeTrainer(SimpleNamespace(turbines=[]), Mock())
    assert trainer.stop(timeout=0) is None
    assert trainer.stop(timeout=0) is None
    assert trainer._stop.is_set()
    assert trainer._thread is None
    assert trainer.error is None
    trainer.callback.assert_not_called()


@pytest.mark.parametrize("failure", ["callback", "empty_callback", "scene"])
def test_worker_errors_populate_error_without_reaching_thread_hook(monkeypatch, failure):
    errors = []
    callback_error = RuntimeError("" if failure == "empty_callback" else "snapshot callback failed")
    callback = Mock(side_effect=callback_error) if failure != "scene" else Mock()
    scene = SimpleNamespace(turbines=[]) if failure != "scene" else SimpleNamespace()
    trainer = fake_backend.FakeTrainer(scene, callback)
    monkeypatch.setattr(threading, "excepthook", errors.append)
    trainer.start()
    try:
        trainer._thread.join(2)
        assert not trainer._thread.is_alive()
        assert errors == []
        assert isinstance(trainer.error, str)
        assert "Traceback (most recent call last):" in trainer.error
        if failure != "scene":
            assert trainer.error.rstrip().endswith(
                "RuntimeError: snapshot callback failed" if failure == "callback" else "RuntimeError"
            )
            callback.assert_called_once()
        else:
            assert "AttributeError" in trainer.error
            assert "turbines" in trainer.error
            callback.assert_not_called()
    finally:
        trainer.stop(timeout=2)


def test_start_propagates_thread_creation_failure(monkeypatch):
    trainer = fake_backend.FakeTrainer(SimpleNamespace(turbines=[]), Mock())
    error = RuntimeError("thread creation failed")
    thread = SimpleNamespace(start=Mock(side_effect=error))
    factory = Mock(return_value=thread)
    monkeypatch.setattr(fake_backend, "threading", SimpleNamespace(Thread=factory))
    with pytest.raises(RuntimeError) as caught:
        trainer.start()
    assert caught.value is error
    assert trainer._thread is thread
    assert trainer.error is None
    assert factory.call_args.kwargs["name"] == "wfrl-fake"
    assert factory.call_args.kwargs["daemon"] is True
    assert callable(factory.call_args.kwargs["target"])


def test_stop_forwards_join_timeout_and_propagates_join_failure():
    trainer = fake_backend.FakeTrainer(SimpleNamespace(turbines=[]), Mock())
    error = RuntimeError("join failed")
    trainer._thread = SimpleNamespace(join=Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        trainer.stop(timeout=0.25)
    assert caught.value is error
    trainer._thread.join.assert_called_once_with(0.25)
    assert trainer._stop.is_set()

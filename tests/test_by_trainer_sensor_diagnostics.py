"""Trainer callback diagnostics and missing replay/camera measurements."""

import logging
import math
from types import SimpleNamespace
import warnings

import numpy as np
import pytest
import torch

from wfrl.channels.sensors import NacelleCamera
from wfrl.mappo_central import Actor
from wfrl.policy_actions import PolicyActionContract
from wfrl.scene.runtime import SceneRuntime
from wfrl.scene.schema import Scene, SensorSpec, Turbine
from wfrl.studio.trainer import Trainer


@pytest.fixture
def scene():
    return Scene(name="diagnostics-test", controls=["pitch"], sensors=[],
                 turbines=[Turbine("T1", 0, 0), Turbine("T2", 500, 0)])


def install_runtime(monkeypatch, samples):
    created = []

    class Runtime:
        def __init__(self, scene, **options):
            self.scene = scene
            self.driver = SimpleNamespace(act_bounds={"pitch": (-1., 1.)})
            self.last_ctx = {}
            self.steps = 0
            self.done = self.closed = False
            created.append(self)

        def frame(self, sample):
            self.last_measure = dict(pitch=np.zeros(self.scene.n),
                                     wind_speed=np.full(self.scene.n, 8.),
                                     wind_direction=np.full(self.scene.n, 270.))
            self.last_measure.update(sample.get("measure", {}))
            return {"_meta": sample.get("meta", {"farm_power": 4., "reward": 1.})}

        def reset(self):
            return self.frame({})

        def step(self, **commands):
            sample = samples[self.steps]
            self.steps += 1
            self.done = sample.get("done", False)
            return self.frame(sample)

        def close(self, **options):
            self.closed = True

    monkeypatch.setattr("wfrl.scene.runtime.SceneRuntime", Runtime)
    return created


@pytest.fixture
def checkpoint(tmp_path, scene):
    path = tmp_path / "policy.pt"
    torch.save(dict(
        obs_keys=["pitch", "wind_speed", "wind_direction"],
        obs_mean=np.zeros(3), obs_var=np.ones(3), obs_dim=3, act_dim=1,
        state_dim=3 * scene.n, controls=["pitch"],
        action_contract=PolicyActionContract.from_bounds(
            ["pitch"], {"pitch": (-1., 1.)}, "tanh_scale").to_dict(),
        actor=Actor(3, 1).state_dict(),
    ), path)
    return str(path)


def test_snapshot_callback_counts_failures_and_logs_each_exception_type_once(scene, caplog):
    trainer = Trainer(scene)
    healthy = trainer._publish(step=0, diagnostics={"source": "test"})
    assert not healthy.diagnostics["snapshot_callback_failed"]
    assert healthy.diagnostics["snapshot_callback_failure_count"] == 0
    assert healthy.diagnostics["snapshot_callback_last_error"] is None
    assert healthy.diagnostics["source"] == "test"
    failures = [ValueError("first"), ValueError("repeated"),
                TypeError("other type"), ValueError("latest failure")]
    snapshots = []

    def callback(snapshot):
        assert trainer.latest() is snapshot
        snapshots.append(snapshot)
        raise failures[snapshot.step - 1]

    trainer.on_snapshot = callback
    with caplog.at_level(logging.ERROR, logger="wfrl.studio.trainer"):
        for step in range(1, len(failures) + 1):
            snap = trainer._publish(step=step)
            assert trainer.latest() is snap
            assert trainer.diagnostics == snap.diagnostics
            assert snap.diagnostics["snapshot_callback_failed"]
            assert snap.diagnostics["snapshot_callback_failure_count"] == step
            assert snap.diagnostics["snapshot_callback_last_error"] == (
                f"{type(failures[step - 1]).__name__}: {failures[step - 1]}")

    records = [record for record in caplog.records if record.name == "wfrl.studio.trainer"]
    assert [record.exc_info[0] for record in records] == [ValueError, TypeError]
    assert all("快照回调失败" in record.getMessage() for record in records)
    assert caplog.text.count("Traceback (most recent call last):") == 2
    assert [snap.diagnostics["snapshot_callback_failure_count"] for snap in snapshots] == [1, 2, 3, 4]
    assert healthy.diagnostics["snapshot_callback_failure_count"] == 0
    trainer.on_snapshot = snapshots.append
    recovered = trainer._publish(step=5)
    assert recovered.diagnostics == snapshots[-2].diagnostics
    assert trainer.error is None
    exposed = trainer.diagnostics
    exposed["snapshot_callback_failure_count"] = -1
    assert trainer.diagnostics["snapshot_callback_failure_count"] == 4
    assert Trainer(scene).diagnostics == {
        "snapshot_callback_failed": False,
        "snapshot_callback_failure_count": 0,
        "snapshot_callback_last_error": None,
    }


def test_callback_failures_do_not_stop_training_or_change_fatal_error_state(scene, monkeypatch, caplog):
    created = install_runtime(monkeypatch, [{}] * 4)
    callbacks = []

    def callback(snapshot):
        callbacks.append(snapshot)
        raise RuntimeError(f"consumer failed at step {snapshot.step}")

    trainer = Trainer(scene, iters=2, n_steps=2, warmup_steps=0,
                      n_epochs=1, mb_steps=2, on_snapshot=callback)
    with caplog.at_level(logging.ERROR, logger="wfrl.studio.trainer"):
        trainer._run()

    assert created[0].steps == 4 and created[0].closed
    assert len(trainer.history) == 2 and trainer.history[-1]["step"] == 4
    assert trainer.runtime is None and trainer.error is None and not trainer.running
    assert trainer.latest().phase == "done" and trainer.latest().iters_done == 2
    assert trainer.diagnostics["snapshot_callback_failure_count"] == len(callbacks)
    assert trainer.latest().diagnostics == trainer.diagnostics
    assert caplog.text.count("Traceback (most recent call last):") == 1


@pytest.mark.parametrize("measurement", [
    {}, {"yaw": None}, {"yaw": []}, {"yaw": [np.nan, 30.]},
    {"yaw": [np.inf, 30.]}, {"yaw": [-np.inf, 30.]},
])
def test_replay_records_missing_yaw_as_nan_and_recovers(scene, checkpoint, monkeypatch, measurement):
    created = install_runtime(monkeypatch, [
        {"measure": measurement}, {"measure": {"yaw": [25., 90.]}},
    ])
    trainer = Trainer(scene, ckpt_path=checkpoint, replay=True,
                      replay_steps=2, warmup_steps=0)
    trainer._run()
    assert trainer.error is None and created[0].closed
    assert math.isnan(trainer.history[0]["yaw"])
    assert trainer.history[1]["yaw"] == 25.
    assert [row["mean_power"] for row in trainer.history] == [4., 4.]


def test_replay_averages_finite_samples_and_keeps_empty_windows_nan(scene, checkpoint, monkeypatch):
    samples = [
        {"meta": {}},
        {"measure": {"yaw": [10., 90.]}, "meta": {"farm_power": 2., "reward": 0.}},
        {"measure": {"yaw": [np.nan, 90.]}, "meta": {"farm_power": np.inf, "reward": np.nan}},
        {"measure": {"yaw": [30., 90.]}, "meta": {"farm_power": 6., "reward": 4.}},
        {"measure": {"yaw": None}, "meta": {"farm_power": None, "reward": None}},
        {"measure": {"yaw": []}, "meta": {"farm_power": np.nan, "reward": np.inf}},
        {"measure": {"yaw": [np.inf, 90.]}, "meta": {"farm_power": np.inf, "reward": -np.inf}},
        {"measure": {"yaw": [-np.inf, 90.]}, "meta": {"farm_power": -np.inf, "reward": np.nan}},
        {"measure": {"yaw": [0., 90.]}, "meta": {"farm_power": 0., "reward": 0.}, "done": True},
    ]
    created = install_runtime(monkeypatch, samples)
    snapshots = []
    trainer = Trainer(scene, ckpt_path=checkpoint, replay=True,
                      replay_steps=160, warmup_steps=0, on_snapshot=snapshots.append)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        trainer._run()
    assert trainer.error is None and created[0].closed
    assert [row["step"] for row in trainer.history] == [4, 8, 9]
    first, missing, zero = trainer.history
    assert (first["yaw"], first["mean_power"], first["mean_reward"]) == (20., 4., 2.)
    assert all(math.isnan(missing[key]) for key in ("yaw", "mean_power", "mean_reward"))
    assert (zero["yaw"], zero["mean_power"], zero["mean_reward"]) == (0., 0., 0.)
    sampling = [snap for snap in snapshots if snap.phase == "sampling"]
    assert math.isnan(sampling[0].reward) and sampling[1].reward == 0.
    assert trainer.latest().step == 9 and trainer.latest().iters_done == 0


@pytest.mark.parametrize("context", [
    None, {}, {"t": 1.}, {"yaw": None}, {"yaw": np.nan},
    {"yaw": [np.nan, 30.]}, {"yaw": np.inf}, {"yaw": -np.inf},
])
def test_camera_missing_and_nonfinite_yaw_have_the_same_explicit_error(scene, context):
    camera = NacelleCamera(scene, SensorSpec("camera"), [0])
    for operation in (lambda: camera.camera_pose(context, 0),
                      lambda: camera._frustum_points(context, 0, scale=1.),
                      lambda: camera.sample(context)):
        with pytest.raises(ValueError, match="^camera yaw must be finite$"):
            operation()
    reading = camera.step(context)
    assert "camera yaw must be finite" in reading["camera"]["error"]
    recovered = camera.step({"yaw": [0., 30.]})["camera"]
    assert len(recovered) == 1 and np.isfinite(recovered[0]["pos"]).all()


def test_runtime_camera_channel_exposes_missing_yaw_and_recovers_from_measured_yaw(scene):
    scene.sensors = [SensorSpec("camera")]
    runtime = SceneRuntime(scene)
    missing = runtime._emit({})["camera"]
    assert "camera yaw must be finite" in missing["error"]
    measured = {"yaw_measured": [0., 30.]}
    recovered = runtime._emit(measured)["camera"]
    assert len(recovered) == scene.n
    expected = runtime.sensor_of("camera").sample({"yaw": measured["yaw_measured"]})["camera"]
    assert recovered == expected

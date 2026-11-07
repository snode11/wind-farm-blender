"""Strict Interactive shutdown exercises the production runtime/driver close chain."""

from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.fastfarm_driver import FastFarmDriver
from wfrl.scene.runtime import SceneRuntime
from wfrl.scene.schema import Scene, Turbine
from wfrl.studio.trainer import Trainer


@pytest.fixture
def driver_factory(monkeypatch):
    import wfcrl.interface as interface_module

    class Intercomm:
        active = True

        def __bool__(self):
            return self.active

    monkeypatch.setattr(interface_module, "MPI", SimpleNamespace(Intercomm=Intercomm))

    def make(failure=None, budget=3):
        comm = Intercomm()

        class Env:
            def __init__(self):
                self.steps = 0
                self.close_calls = 0
                self.purged = False
                self.mdp = SimpleNamespace(interface=SimpleNamespace(_comm=comm))

            def last(self):
                return None, 0.0, False, self.steps >= budget, {}

            def step(self, command):
                if failure == "drain":
                    raise RuntimeError("fixture drain failure")
                self.steps += 1
                if self.steps == budget and failure != "connected":
                    comm.active = False

            def close(self):
                self.close_calls += 1
                if failure == "env":
                    raise RuntimeError("fixture environment close failure")

        env = Env()
        driver = FastFarmDriver.__new__(FastFarmDriver)
        driver.env = driver._uenv = env
        driver._started, driver.done, driver._steps = True, False, 0
        driver.max_steps, driver.n, driver.controls = budget, 1, ["yaw"]
        driver.act_bounds = {"yaw": (-5.0, 5.0)}
        driver._purge_case = lambda: setattr(env, "purged", True)
        return driver

    return make


@pytest.mark.parametrize("failure,match", [
    ("drain", "drain failure"),
    ("env", "environment close failure"),
    ("connected", "still connected"),
])
def test_strict_close_propagates_failures_retains_ownership_and_skips_purge(driver_factory, failure, match):
    runtime = SceneRuntime.__new__(SceneRuntime)
    runtime.driver = driver_factory(failure)
    driver = runtime.driver
    with pytest.raises(RuntimeError, match=match):
        runtime.close(purge=True, strict=True)
    assert runtime.driver is driver
    assert driver.env.close_calls == 1
    assert not driver.env.purged
    steps = driver.env.steps
    with pytest.raises(RuntimeError, match="previously failed"):
        runtime.close(purge=True, strict=True)
    assert driver.env.steps == steps and driver.env.close_calls == 1


def test_strict_close_rejects_incomplete_drain_and_connected_partial_start(driver_factory):
    driver = driver_factory()
    with pytest.raises(RuntimeError, match="drain budget exhausted"):
        driver.close(drain_cap=1, strict=True)
    assert not driver.env.purged
    partial = driver_factory()
    partial._started = False
    with pytest.raises(RuntimeError, match="still connected"):
        partial.close(strict=True)


def test_strict_close_confirms_disconnection_before_purge(driver_factory):
    runtime = SceneRuntime.__new__(SceneRuntime)
    runtime.driver = driver_factory()
    driver = runtime.driver
    runtime.close(purge=True, strict=True)
    assert runtime.driver is None
    assert driver.done and driver.env.steps == driver.max_steps
    assert not driver.env.mdp.interface._comm
    assert driver.env.close_calls == 1 and driver.env.purged
    runtime.close(purge=True, strict=True)
    FastFarmDriver.__new__(FastFarmDriver).close(strict=True)


@pytest.mark.parametrize("failure", ["drain", "env", "connected"])
def test_default_close_preserves_non_strict_callers(driver_factory, failure):
    runtime = SceneRuntime.__new__(SceneRuntime)
    runtime.driver = driver_factory(failure)
    env = runtime.driver.env
    runtime.close(purge=True)
    assert runtime.driver is None
    assert env.close_calls == 1 and env.purged


@pytest.mark.parametrize("failure", [None, "drain", "env", "connected"])
def test_trainer_cannot_spawn_after_a_real_close_chain_failure(driver_factory, monkeypatch, failure):
    import wfrl.scene.runtime as runtime_module

    created = []

    class Runtime(SceneRuntime):
        def __init__(self, scene, **options):
            super().__init__(scene, **options)
            self.driver = driver_factory(failure, budget=self.max_steps)
            created.append(self)

        def reset(self):
            return self._emit(dict(yaw=np.zeros(self.scene.n), wind_speed=np.full(self.scene.n, 8.0),
                                   wind_direction=np.full(self.scene.n, 270.0),
                                   power=np.ones(self.scene.n), reward=1.0))

        def step(self, **commands):
            self.driver._steps += 1
            self.driver.env.steps += 1
            self._t += 1
            return self.reset()

    monkeypatch.setattr(runtime_module, "SceneRuntime", Runtime)
    scene = Scene(name="strict-test", backend="fastfarm", controls=["yaw"], sensors=[],
                  turbines=[Turbine("T1", 0, 0)])
    trainer = Trainer(scene, iters=1, n_steps=3, episode_steps=1, warmup_steps=0)
    monkeypatch.setattr(trainer, "_update", lambda *args: {})
    trainer._run()
    if failure is None:
        assert trainer.error is None and trainer.runtime is None
        assert len(created) == 3
        assert all(rt.driver is None for rt in created)
    else:
        assert trainer.error and len(created) == 1
        assert trainer.runtime is created[0] and trainer.runtime.driver is not None
        assert not trainer.history
        trainer._run()
        assert len(created) == 1
        assert "previously failed" in trainer.error

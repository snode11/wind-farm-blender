"""Scene reset owns cleanup and starts independent sensor observations."""

import math

import numpy as np
import pytest

from wfrl.scene.runtime import SceneRuntime
from wfrl.scene.schema import Scene, SensorSpec, Turbine


def runtime_fixture(monkeypatch, *, failure=None, close_failure=False, warmup_steps=0):
    from wfrl import fastfarm_driver

    drivers = []
    scene = Scene(turbines=[Turbine("T1", 0, 0)])

    class Driver:
        u_inf = 8.0

        def reset(self, **options):
            if failure == "reset":
                raise ValueError("reset failed")
            return {"power": [1.0]}

        def step(self, *args):
            if failure == "warmup":
                raise ValueError("warmup failed")
            return {"power": [2.0], "done": True}

        def close(self, **options):
            self.close_calls.append(options)
            if close_failure:
                raise RuntimeError("cleanup unconfirmed")

    def build(runtime):
        runtime.driver.close_calls = []
        drivers.append(runtime.driver)
        if failure == "build":
            raise ValueError("build failed")

    monkeypatch.setattr(fastfarm_driver, "FastFarmDriver", Driver)
    monkeypatch.setattr(SceneRuntime, "_build_driver", build)
    return SceneRuntime(scene, warmup_steps=warmup_steps), drivers


@pytest.mark.parametrize("failure", ["build", "reset", "warmup"])
def test_failed_scene_reset_closes_acquired_driver_and_clears_old_measurements(monkeypatch, failure):
    runtime, drivers = runtime_fixture(monkeypatch, failure=failure, warmup_steps=1)
    runtime.last_measure = runtime.last_ctx = runtime._last_frame = {"old": "observation"}
    runtime.u_inf = 12
    with pytest.raises(ValueError, match=failure + " failed"):
        runtime.reset()
    assert drivers[0].close_calls == [{"purge": True, "strict": True}]
    assert runtime.driver is None
    assert runtime.last_measure == runtime.last_ctx == runtime._last_frame == {}
    assert math.isnan(runtime.u_inf) and runtime.done


def test_reset_cannot_replace_driver_after_unconfirmed_cleanup(monkeypatch):
    runtime, drivers = runtime_fixture(monkeypatch, close_failure=True)
    runtime.reset()
    owner = runtime.driver
    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        runtime.reset()
    assert runtime.driver is owner and len(drivers) == 1


def test_failed_initialization_keeps_driver_if_cleanup_also_fails(monkeypatch):
    runtime, drivers = runtime_fixture(monkeypatch, failure="reset", close_failure=True)
    with pytest.raises(RuntimeError, match="cleanup unconfirmed") as caught:
        runtime.reset()
    assert runtime.driver is drivers[0]
    assert isinstance(caught.value.__cause__, ValueError)
    assert "reset failed" in str(caught.value.__cause__)


def test_scene_reset_discards_sensor_history_and_sampling_phase_preserving_switches(monkeypatch):
    runtime, _ = runtime_fixture(monkeypatch)
    runtime.scene.sensors = [SensorSpec("vibration"), SensorSpec("acoustic"), SensorSpec("power")]
    from wfrl.channels import registry
    runtime.sensors = registry.build(runtime.scene)
    runtime.enabled = {"vibration": True, "acoustic": True, "power": False}
    values = iter([6., 12.])

    def build(rt):
        rt.driver.close_calls = []
        rt.driver.reset = lambda **options: {"m_flap": np.full((1, 3), next(values))}

    monkeypatch.setattr(SceneRuntime, "_build_driver", build)
    first = runtime.reset()
    second = runtime.reset()
    assert first["vibration"][0, 0] == 6
    assert second["vibration"][0, 0] == 12
    assert "acoustic" in second
    assert "power" not in second and runtime.enabled["power"] is False


def test_warmup_done_is_preserved_and_stops_further_steps(monkeypatch):
    runtime, drivers = runtime_fixture(monkeypatch, warmup_steps=3)
    steps = []
    build = runtime._build_driver

    def counting_build():
        build()
        step = drivers[0].step
        def counted_step(*args):
            steps.append(1)
            return step(*args)
        drivers[0].step = counted_step

    monkeypatch.setattr(runtime, "_build_driver", counting_build)
    frame = runtime.reset()
    assert steps == [1]
    assert runtime.done and frame["_meta"]["done"]

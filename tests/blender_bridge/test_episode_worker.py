"""Run the actual Interactive worker and IPC loop with a simulated SceneRuntime."""

from copy import deepcopy
from pathlib import Path
import sys
import time

import pytest

from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.isolated_trainer import IsolatedTrainer
from wfrl.scene.schema import Scene, Turbine


WORKER = '''from types import SimpleNamespace
import numpy as np
import wfrl.scene.runtime as module

class Runtime:
    instances = []

    def __init__(self, scene, max_steps, warmup_steps):
        assert all(runtime.closed for runtime in self.instances)
        assert warmup_steps == 0
        self.scene, self.max_steps = scene, max_steps
        self.driver = SimpleNamespace(act_bounds={"yaw": (-5., 5.)})
        self.t, self.done, self.closed = 0, False, False
        self.enabled = True
        self.last_measure, self.last_ctx = {}, {}
        self.instances.append(self)

    def reset(self):
        self.last_measure = dict(yaw=np.zeros(self.scene.n), power=np.ones(self.scene.n),
            wind_speed=np.full(self.scene.n, 8.), wind_direction=np.full(self.scene.n, 270.))
        self.last_ctx = {"t": self.t * self.scene.dt}
        return {"_meta": {"reward": 1., "farm_power": float(self.scene.n)}}

    def step(self, **commands):
        assert not self.closed
        self.t += 1
        assert self.t < self.max_steps
        return self.reset()

    def close(self, *, purge, strict):
        assert purge and strict
        self.closed = True

    def channel_table(self):
        return [("fixture", "Runtime", "DIRECT", self.enabled, f"budget={self.max_steps}")]

    def set_enabled(self, topic, enabled):
        assert topic == "fixture"
        self.enabled = enabled

module.SceneRuntime = Runtime
from wfrl.blender_bridge.trainer_worker import main
raise SystemExit(main())
'''


@pytest.fixture
def worker_session(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(WORKER)

    class FixtureTrainer(IsolatedTrainer):
        def _worker_command(self):
            return [sys.executable, "-u", str(script), str(Path(self._directory.name) / "config.json")]

    scene = Scene(name="worker-test", backend="fastfarm", controls=["yaw"], sensors=[],
                  turbines=[Turbine("T1", 0, 0), Turbine("T2", 500, 0)])
    session = BackendSession(scene=scene, trainer_factory=FixtureTrainer)
    received = []
    emit = session.emit

    def record(kind, payload):
        received.append((kind, deepcopy(payload)))
        emit(kind, payload)

    session.emit = record
    yield session, received
    session.stop(1)
    wait_for(session, lambda: not session.alive())


def wait_for(session, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        session.poll()
        assert time.monotonic() < deadline, (session.status, getattr(session._trainer, "error", None))
        time.sleep(.01)


@pytest.mark.parametrize("episode,lengths", [(0, [8]), (2, [2, 2, 2, 2]), (3, [3, 3, 2])])
def test_bridge_options_reach_worker_and_episode_progress_returns(worker_session, episode, lengths):
    session, received = worker_session
    session.start("interactive_training", dict(iters=2, n_steps=4, warmup_steps=1,
                                               episode_steps=episode, seed=4, n_epochs=1, mb_steps=4))
    wait_for(session, lambda: session.status == "STOPPED")
    assert session._trainer.error is None
    sampling = [p for kind, p in received if kind == "snapshot" and p["phase"] == "sampling"]
    assert [p["step"] for p in sampling] == list(range(1, 9))
    assert [p["episode"] for p in sampling] == [ep for ep, count in enumerate(lengths) for _ in range(count)]
    assert [p["iteration"] for p in sampling] == [0] * 4 + [1] * 4
    assert [p["channel_table"][0][4] for p in sampling] == [f"budget={size + 9}"
                                                           for size in lengths for _ in range(size)]
    progress = [p for kind, p in received if kind == "training_stats"]
    assert {p["episode"] for p in progress} == set(range(len(lengths)))
    assert [p["step"] for p in progress] == sorted(p["step"] for p in progress)
    assert all(p["agent_step"] == 2 * p["step"] for p in progress)
    assert not session.alive()


def test_worker_single_step_crosses_episode_warmup_and_stop_releases_permit(worker_session):
    session, received = worker_session
    record = session.emit

    def pause_at_boundary(kind, payload):
        record(kind, payload)
        if kind == "snapshot" and payload["phase"] == "sampling" and payload["step"] == 2:
            session.command(dict(command="run.pause"))

    session.emit = pause_at_boundary
    session.start("interactive_training", dict(iters=1, n_steps=5, warmup_steps=1,
                                               episode_steps=2, n_epochs=1))
    wait_for(session, lambda: session.status == "PAUSED")
    sampling = [p for kind, p in received if kind == "snapshot" and p["phase"] == "sampling"]
    assert [(p["step"], p["episode"]) for p in sampling] == [(1, 0), (2, 0)]
    session.command(dict(command="channel.set", arguments=dict(topic="fixture", enabled=False)))
    session.command(dict(command="run.step"))
    wait_for(session, lambda: any(kind == "snapshot" and p["phase"] == "sampling" and p["step"] == 3
                                 for kind, p in received))
    assert session.status == "PAUSED"
    sampling = [p for kind, p in received if kind == "snapshot" and p["phase"] == "sampling"]
    assert [(p["step"], p["episode"]) for p in sampling] == [(1, 0), (2, 0), (3, 1)]
    assert sampling[-1]["channel_table"][0][3] is False
    warmup = [p for kind, p in received if kind == "snapshot" and p["phase"] == "warmup" and p["episode"] == 1]
    assert len(warmup) == 3 and {p["step"] for p in warmup} == {2}
    session.stop(1)
    wait_for(session, lambda: session.status == "STOPPED")
    assert session._trainer.error is None
    assert not session.alive()
    assert not any(p["step"] > 3 for kind, p in received if kind == "snapshot")

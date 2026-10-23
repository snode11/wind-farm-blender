"""Interactive episode sampling and lifecycle with an in-memory runtime."""

from copy import deepcopy
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.training_progress import interactive_progress
from wfrl.scene.schema import Scene, Turbine
from wfrl.studio.trainer import Trainer


class IdentityRMS:
    def __init__(self, dim):
        self.mean, self.var = np.zeros(dim), np.ones(dim)
        self.observations = []

    def update(self, obs):
        self.observations.append(obs.copy())

    def norm(self, obs):
        return obs


class WindCritic(torch.nn.Module):
    def __init__(self, state_dim):
        super().__init__()
        self.offset = torch.nn.Parameter(torch.zeros(()))

    def v(self, state):
        return state[..., 1:2] + self.offset


@pytest.fixture
def simulation(monkeypatch):
    import wfrl.scene.runtime as runtime_module
    import wfrl.studio.trainer as trainer_module

    harness = SimpleNamespace(created=[], snapshots=[], updates=[], gae=[], order=[],
                              hook=lambda *args: None,
                              reward=lambda episode, step: 100 * episode + step)

    class Runtime:
        def __init__(self, scene, max_steps, warmup_steps):
            assert all(rt.closed for rt in harness.created), "A live runtime cannot overlap its successor"
            self.scene, self.max_steps, self.warmup_steps = scene, max_steps, warmup_steps
            self.episode = len(harness.created)
            self.t, self.done, self.closed = 0, False, False
            self.steps, self.closes = [], []
            self.driver = SimpleNamespace(act_bounds={"yaw": (-5.0, 5.0)})
            self.last_measure, self.last_ctx = {}, {}
            self.yaw = np.zeros(scene.n)
            harness.created.append(self)
            harness.order.append(("create", self.episode))
            harness.hook("create", self)

        def _frame(self):
            reward = float(harness.reward(self.episode, self.t))
            self.last_measure = dict(yaw=self.yaw.copy(),
                                    wind_speed=np.full(self.scene.n, 10 * (self.episode + 1) + self.t),
                                    wind_direction=np.full(self.scene.n, 270.0),
                                    power=np.full(self.scene.n, reward))
            self.last_ctx = {"t": self.t * self.scene.dt}
            return {"_meta": {"reward": reward, "farm_power": reward * self.scene.n}}

        def reset(self):
            harness.order.append(("reset", self.episode))
            harness.hook("reset", self)
            return self._frame()

        def step(self, **commands):
            assert not self.closed and not self.done
            assert self.t < self.max_steps
            self.t += 1
            self.steps.append(deepcopy(commands))
            self.yaw += commands["yaw_delta"]
            harness.order.append(("step", self.episode, self.t))
            harness.hook("step", self)
            return self._frame()

        def close(self, **options):
            self.closes.append(options)
            harness.order.append(("close", self.episode))
            harness.hook("close", self)
            self.closed = True

    def make(*, capture=True, on_snapshot=None, **options):
        scene = Scene(name="episode-test", backend="fastfarm", dt=3.0,
                      controls=["yaw"], sensors=[],
                      turbines=[Turbine("T1", 0, 0), Turbine("T2", 500, 0)])

        def snapshot(sample):
            harness.snapshots.append(deepcopy(sample))
            if on_snapshot:
                on_snapshot(sample)

        defaults = dict(iters=2, n_steps=4, episode_steps=3, warmup_steps=2,
                        n_epochs=1, mb_steps=4)
        defaults.update(options)
        trainer = Trainer(scene, on_snapshot=snapshot, **defaults)
        actual_gae = trainer._gae

        def record_gae(*args):
            harness.gae.append(tuple(value.copy() for value in args))
            return actual_gae(*args)

        def update(obs, act, logp, adv, ret, optimizer):
            harness.updates.append(tuple(value.copy() for value in (obs, act, logp, adv, ret)))
            return dict(vloss=0.0, explained_var=0.0)

        monkeypatch.setattr(trainer, "_gae", record_gae)
        if capture:
            monkeypatch.setattr(trainer, "_update", update)
        return trainer

    monkeypatch.setattr(runtime_module, "SceneRuntime", Runtime)
    harness.make = make
    harness.module = trainer_module
    return harness


@pytest.mark.parametrize("iters,rollout,episode,warmup,lengths", [
    (2, 4, 0, 2, [8]),
    (2, 4, 99, 0, [8]),
    (2, 4, 3, 2, [3, 3, 2]),
    (3, 2, 4, 1, [4, 2]),
    (2, 3, 3, 0, [3, 3]),
    (1, 4, 1, 1, [1, 1, 1, 1]),
])
def test_episode_budgets_warmup_counts_and_rollout_continuity(
    simulation, monkeypatch, iters, rollout, episode, warmup, lengths,
):
    observations = []

    class RecordingRMS(IdentityRMS):
        def update(self, obs):
            observations.append(obs.copy())

    monkeypatch.setattr(simulation.module, "RunningMeanStd", RecordingRMS)
    trainer = simulation.make(iters=iters, n_steps=rollout, episode_steps=episode, warmup_steps=warmup)
    trainer._run()

    assert trainer.error is None
    assert trainer.runtime is None
    assert [rt.max_steps for rt in simulation.created] == [length + warmup + 8 for length in lengths]
    assert [len(rt.steps) for rt in simulation.created] == [length + warmup for length in lengths]
    assert all(rt.warmup_steps == 0 and rt.closed for rt in simulation.created)
    assert all(rt.closes == [{"purge": True, "strict": True}] for rt in simulation.created)
    assert len(simulation.updates) == iters
    assert len(observations) == iters * rollout
    assert all(len(update[0]) == rollout for update in simulation.updates)

    sampling = [s for s in simulation.snapshots if s.phase == "sampling"]
    assert [s.step for s in sampling] == list(range(1, iters * rollout + 1))
    assert [s.iters_done for s in sampling] == [i // rollout for i in range(iters * rollout)]
    assert [s.episode for s in sampling] == [ep for ep, size in enumerate(lengths) for _ in range(size)]
    assert [s.step for s in simulation.snapshots] == sorted(s.step for s in simulation.snapshots)
    assert [s.iters_done for s in simulation.snapshots] == sorted(s.iters_done for s in simulation.snapshots)
    assert [row[0, 1] for row in observations] == [10 * (ep + 1) + warmup + k
                                                 for ep, size in enumerate(lengths) for k in range(size)]
    for ep, rt in enumerate(simulation.created):
        for command in rt.steps[:warmup]:
            np.testing.assert_array_equal(command["yaw_delta"], np.zeros(trainer.scene.n))
        progress = [s for s in simulation.snapshots if s.phase == "warmup" and s.episode == ep]
        assert len(progress) == warmup + 2
        assert {s.step for s in progress} == {sum(lengths[:ep])}
    for i, stats in enumerate(trainer.history):
        samples = sampling[i * rollout:(i + 1) * rollout]
        assert stats["mean_reward"] == np.mean([s.reward for s in samples])
        assert stats["mean_power"] == np.mean([s.farm_power for s in samples])


def test_pre_reset_bootstrap_is_distinct_from_successor_episode(simulation, monkeypatch):
    monkeypatch.setattr(simulation.module, "RunningMeanStd", IdentityRMS)
    monkeypatch.setattr(simulation.module, "Critic", WindCritic)
    trainer = simulation.make(iters=1, n_steps=5, episode_steps=2, warmup_steps=1,
                              gamma=0.9, lam=0.8)
    trainer._run()
    assert trainer.error is None
    rewards, values, next_values, ends = simulation.gae[0]
    np.testing.assert_array_equal(values, [11, 12, 21, 22, 31])
    np.testing.assert_array_equal(next_values, [12, 13, 22, 23, 32])
    np.testing.assert_array_equal(ends, [False, True, False, True, True])
    _, _, _, advantages, returns = simulation.updates[0]
    np.testing.assert_allclose(returns[ends], (rewards + 0.9 * next_values)[ends], rtol=1e-6)
    deltas = rewards.astype(np.float64) + 0.9 * next_values.astype(np.float64) - values
    np.testing.assert_allclose(advantages[[0, 2]], deltas[[0, 2]] + 0.9 * 0.8 * deltas[[1, 3]], rtol=1e-6)


def test_gae_cuts_only_the_advantage_chain_at_time_limits():
    trainer = Trainer.__new__(Trainer)
    trainer.hp = dict(gamma=0.9, lam=0.8)
    values = np.array([10, 20, 30], np.float32)
    next_values = np.array([20, 40, 50], np.float32)
    ends = np.array([False, True, True])
    first, returns = trainer._gae(np.array([1, 2, 100]), values, next_values, ends)
    changed, _ = trainer._gae(np.array([1, 2, 10000]), values, next_values, ends)
    np.testing.assert_allclose(first, [9 + 0.72 * 18, 18, 115])
    np.testing.assert_array_equal(first[:2], changed[:2])
    np.testing.assert_allclose(returns[1:], [38, 145])


@pytest.mark.parametrize("episode,discounted", [
    (2, [1, 1.5, 1, 1.5, 1, 1.5]),
    (0, [1, 1.5, 1.75, 1.875, 1.9375, 1.96875]),
])
def test_reward_accumulator_follows_episodes_and_ema_spans_rollouts(simulation, episode, discounted):
    simulation.reward = lambda *_: 1.0
    trainer = simulation.make(iters=2, n_steps=3, episode_steps=episode, gamma=0.5)
    trainer._run()
    assert trainer.error is None
    for i, (normalized_rewards, *_) in enumerate(simulation.gae):
        prefix = np.array(discounted[:3 * (i + 1)])
        moment = 0.99 ** len(prefix) + 0.01 * np.dot(prefix ** 2, 0.99 ** np.arange(len(prefix) - 1, -1, -1))
        np.testing.assert_allclose(normalized_rewards, 1 / (np.sqrt(moment) + 1e-8), rtol=1e-6)


def test_limiter_resets_command_state_and_keeps_identity_and_event_counts(simulation):
    trainer = simulation.make(iters=2, n_steps=2, episode_steps=2, warmup_steps=0)
    limiter = trainer.limiter
    limiter.state["yaw"][:] = 40.0
    limiter.counts["fixture"] = 7
    trainer._run()
    assert trainer.error is None
    assert trainer.limiter is limiter
    assert limiter._step == 4 and limiter.counts["fixture"] == 7
    expected = sum(command["yaw_delta"] for command in simulation.created[-1].steps)
    np.testing.assert_allclose(limiter.state["yaw"], expected)
    assert not np.allclose(simulation.created[0].steps[0]["yaw_delta"], 0)


@pytest.mark.parametrize("where", ["before", "prepare", "reset", "warmup", "sampling", "closing"])
def test_stop_does_not_spawn_an_extra_episode_or_save_untrained_weights(simulation, tmp_path, where):
    path = tmp_path / "cancelled.pt"

    def stop_at(sample):
        match = ((where == "prepare" and sample.phase == "warmup" and not sample.frame)
                 or (where == "reset" and sample.phase == "warmup" and sample.ctx.get("t") == 0)
                 or (where == "warmup" and sample.phase == "warmup" and sample.ctx.get("t") == 3)
                 or (where == "sampling" and sample.phase == "sampling"))
        if match:
            trainer._stop.set()

    trainer = simulation.make(iters=1, n_steps=4, episode_steps=2,
                              on_snapshot=stop_at, ckpt_path=str(path))
    if where == "before":
        trainer._stop.set()
    if where == "closing":
        simulation.hook = lambda operation, _: trainer._stop.set() if operation == "close" else None
    trainer._run()
    assert trainer.error is None
    assert len(simulation.created) == (0 if where == "before" else 1)
    assert all(rt.closed for rt in simulation.created)
    assert trainer.runtime is None
    assert not simulation.updates and not path.exists()


@pytest.mark.parametrize("pause_phase,stop", [("warmup", False), ("sampling", False), ("sampling", True)])
def test_pause_and_stop_work_at_warmup_and_episode_boundaries(simulation, pause_phase, stop):
    reached = threading.Event()

    def pause_at(sample):
        if reached.is_set():
            return
        if ((pause_phase == "warmup" and sample.phase == "warmup" and sample.ctx.get("t") == 3)
                or (pause_phase == "sampling" and sample.phase == "sampling" and sample.step == 2)):
            trainer.pause()
            reached.set()

    trainer = simulation.make(iters=1, n_steps=4, episode_steps=2, on_snapshot=pause_at)
    trainer.start()
    try:
        assert reached.wait(5)
        assert len(simulation.created) == 1
        expected_steps = 1 if pause_phase == "warmup" else 4
        assert len(simulation.created[0].steps) == expected_steps
        if stop:
            trainer.stop(timeout=5)
        else:
            trainer.pause(False)
            trainer._thread.join(5)
        assert not trainer.running and trainer.error is None
        assert len(simulation.created) == (1 if stop else 2)
        assert all(rt.closed for rt in simulation.created)
    finally:
        trainer.stop(timeout=5)


@pytest.mark.parametrize("failure", ["reset", "warmup", "step", "done", "bounds"])
def test_failed_successor_cleans_up_and_never_uses_its_invalid_samples(simulation, failure, tmp_path):
    def fail(operation, runtime):
        if runtime.episode != 1:
            return
        if failure == "bounds" and operation == "reset":
            runtime.driver.act_bounds["yaw"] = (-2.0, 2.0)
        if failure == "done" and operation == "step" and runtime.t == 3:
            runtime.done = True
        if ((failure == "reset" and operation == "reset")
                or (failure == "warmup" and operation == "step" and runtime.t == 1)
                or (failure == "step" and operation == "step" and runtime.t == 3)):
            raise RuntimeError("fixture failure: " + failure)

    simulation.hook = fail
    checkpoint = tmp_path / "failed.pt"
    trainer = simulation.make(iters=1, n_steps=5, episode_steps=2, ckpt_path=str(checkpoint))
    trainer._run()
    assert trainer.error
    assert len(simulation.created) == 2 and all(rt.closed for rt in simulation.created)
    assert trainer.runtime is None
    assert not simulation.updates and not checkpoint.exists()
    assert [s.step for s in simulation.snapshots if s.phase == "sampling"] == [1, 2]
    if failure == "bounds":
        assert not simulation.created[-1].steps


def test_real_ppo_and_checkpoint_with_multiple_episodes(simulation, tmp_path):
    path = tmp_path / "policy.pt"
    trainer = simulation.make(capture=False, ckpt_path=str(path))
    trainer._run()
    assert trainer.error is None
    assert len(trainer.history) == 2
    assert all(np.isfinite(value) for row in trainer.history for value in row.values())
    saved = torch.load(path, map_location="cpu", weights_only=False)
    assert saved["action_contract"]["transform"] == "tanh_scale"
    assert saved["action_contract"]["controls"] == ["yaw"]
    assert all(torch.isfinite(p).all() for p in trainer.actor.parameters())
    assert all(torch.isfinite(p).all() for p in trainer.critic.parameters())


def test_episode_context_reaches_snapshot_events_and_training_protocol(simulation):
    trainer = simulation.make()
    trainer._run()
    assert trainer.error is None
    adapter = SnapshotAdapter(trainer.scene, "test")
    for sample in simulation.snapshots:
        payload = adapter.encode(sample)
        progress = interactive_progress(sample, "test", trainer.scene.n)
        assert payload["episode"] == progress["episode"] == sample.episode
        assert payload["step"] == progress["step"] == sample.step
        assert progress["agent_step"] == sample.step * trainer.scene.n
        sample.events = [dict(turbine="T1", severity="warn", detail="fixture")]
        assert next(adapter.safety_events(sample))["episode"] == sample.episode

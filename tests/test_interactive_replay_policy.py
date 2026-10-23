"""Exercise production training/save/replay functions with a simulated runtime."""

from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch.distributions import Normal

from wfrl.mappo_central import Actor
from wfrl.scene.schema import Scene, Turbine
from wfrl.studio.trainer import Trainer


BOUNDS = {"yaw": (-5.0, 5.0), "pitch": (-1.0, 1.0), "torque": (-1000.0, 1000.0)}


def _scene(controls=("yaw",)):
    return Scene(name="policy-test", backend="fastfarm", dt=3.0,
                 controls=list(controls), sensors=[],
                 turbines=[Turbine("T1", 0, 0), Turbine("T2", 500, 0)])


class _MeanSample(Normal):
    def sample(self, sample_shape=torch.Size()):
        return self.mean.expand(sample_shape + self.mean.shape)


class _FixedActor(Actor):
    def __init__(self, obs_dim, act_dim):
        super().__init__(obs_dim, act_dim)
        with torch.no_grad():
            for parameter in self.parameters():
                parameter.zero_()
            self.mu[-1].bias.copy_(torch.tensor([0.5, -0.25, 1.0][:act_dim]))
            self.log_std.fill_(-0.5)

    def dist(self, obs):
        return _MeanSample(self.mu(obs), self.log_std.exp())


class _PassthroughLimiter:
    def __init__(self, scene):
        self.requests = []

    def apply(self, commands, measure, **kwargs):
        self.requests.append({c: a.copy() for c, a in commands.items()})
        return commands, []

    def monitor(self, measure):
        return []


@pytest.fixture
def runtime(monkeypatch):
    import wfrl.scene.runtime as runtime_module
    import wfrl.studio.trainer as trainer_module

    created = []

    class Runtime:
        bounds = BOUNDS

        def __init__(self, scene, **kwargs):
            self.scene = scene
            self.driver = SimpleNamespace(act_bounds={c: self.bounds[c] for c in scene.controls})
            self.last_measure = {c: np.zeros(scene.n) for c in BOUNDS}
            self.last_measure.update(wind_speed=np.full(scene.n, 8.0),
                                     wind_direction=np.full(scene.n, 270.0),
                                     power=np.full(scene.n, 2.0))
            self.last_ctx = {}
            self.done = False
            self.steps = []
            self.closed = False
            created.append(self)

        def reset(self):
            return {"_meta": {"farm_power": 2.0 * self.scene.n, "reward": 1.0}}

        def step(self, **commands):
            self.steps.append({k: None if v is None else v.copy() for k, v in commands.items()})
            for c in BOUNDS:
                if commands.get(f"{c}_delta") is not None:
                    self.last_measure[c] += commands[f"{c}_delta"]
            return self.reset()

        def close(self, **kwargs):
            self.closed = True

    monkeypatch.setattr(runtime_module, "SceneRuntime", Runtime)
    monkeypatch.setattr(trainer_module, "Actor", _FixedActor)
    monkeypatch.setattr(trainer_module, "SafetyLimiter", _PassthroughLimiter)
    return Runtime, created


def _train(tmp_path, controls, monkeypatch):
    path = tmp_path / "studio.pt"
    trainer = Trainer(_scene(controls), iters=1, n_steps=2, warmup_steps=0,
                      n_epochs=1, mb_steps=2, lr=0.0, ckpt_path=str(path))
    recorded = []
    update = trainer._update

    def capture(obs, act, logp, adv, ret, opt):
        recorded.append((act.copy(), logp.copy()))
        return update(obs, act, logp, adv, ret, opt)

    monkeypatch.setattr(trainer, "_update", capture)
    trainer._train()
    return trainer, path, recorded


@pytest.mark.parametrize("controls", [
    ["yaw"], ["pitch"], ["torque"], ["yaw", "pitch", "torque"],
    ["torque", "yaw", "pitch"],
])
def test_studio_train_save_load_replay_preserves_commands_and_raw_ppo(
    tmp_path, monkeypatch, runtime, controls,
):
    trained, path, recorded = _train(tmp_path, controls, monkeypatch)
    ck = torch.load(path, map_location="cpu", weights_only=False)
    assert ck["action_contract"] == {
        "version": 1, "transform": "tanh_scale", "controls": controls,
        "low": [BOUNDS[c][0] for c in controls], "high": [BOUNDS[c][1] for c in controls],
    }
    replay = Trainer(_scene(controls), ckpt_path=str(path), replay=True,
                     replay_steps=2, warmup_steps=0)
    replay._replay_policy()

    raw = np.array([0.5, -0.25, 1.0][:len(controls)], np.float32)
    assert len(trained.limiter.requests) == len(replay.limiter.requests) == 2
    for train_cmd, replay_cmd in zip(trained.limiter.requests, replay.limiter.requests):
        assert list(train_cmd) == list(replay_cmd) == controls
        for j, c in enumerate(controls):
            expected = np.full(2, np.tanh(raw[j]) * BOUNDS[c][1], np.float32)
            np.testing.assert_array_equal(train_cmd[c], expected)
            np.testing.assert_array_equal(replay_cmd[c], train_cmd[c])
    for train_step, replay_step in zip(trained.runtime.steps, replay.runtime.steps):
        for c in BOUNDS:
            if c in controls:
                np.testing.assert_array_equal(train_step[f"{c}_delta"], replay_step[f"{c}_delta"])
            else:
                assert train_step[f"{c}_delta"] is replay_step[f"{c}_delta"] is None

    actions, logp = recorded[0]
    np.testing.assert_array_equal(actions, np.broadcast_to(raw, (2, 2, len(controls))))
    assert logp.shape == (2, 2)
    np.testing.assert_allclose(logp, len(controls) * (0.5 - 0.5 * np.log(2 * np.pi)))
    assert all(np.isfinite(value) for value in trained.history[0].values())


def _legacy_cli(path, *, obs_duty=False):
    obs_dim = 3 + int(obs_duty)
    checkpoint = dict(
        obs_keys=["yaw", "wind_speed", "wind_direction"],
        obs_mean=np.zeros(obs_dim), obs_var=np.ones(obs_dim), obs_dim=obs_dim,
        state_dim=2 * obs_dim, act_dim=1, actor=_FixedActor(obs_dim, 1).state_dict(),
        obs_duty=obs_duty, recurrent=False, duty_penalty=0.0,
        env_id="Dec_Turb2_Row1_Fastfarm",
    )
    torch.save(checkpoint, path)
    return checkpoint


@pytest.mark.parametrize("obs_duty", [False, True])
def test_legacy_cli_replay_keeps_clip_with_optional_duty_observation(tmp_path, runtime, obs_duty):
    path = tmp_path / "cli.pt"
    _legacy_cli(path, obs_duty=obs_duty)
    replay = Trainer(_scene(), ckpt_path=str(path), replay=True, replay_steps=2, warmup_steps=0)
    replay._replay_policy()
    for cmd in replay.limiter.requests:
        np.testing.assert_array_equal(cmd["yaw"], [0.5, 0.5])


def test_legacy_studio_yaw_is_tanh_even_with_one_action(tmp_path, monkeypatch, runtime):
    trained, path, _ = _train(tmp_path, ["yaw"], monkeypatch)
    ck = torch.load(path, map_location="cpu", weights_only=False)
    ck.pop("action_contract")
    ck.pop("policy_source")
    torch.save(ck, path)
    replay = Trainer(_scene(), ckpt_path=str(path), replay=True, replay_steps=1, warmup_steps=0)
    replay._replay_policy()
    np.testing.assert_array_equal(replay.limiter.requests[0]["yaw"], trained.limiter.requests[0]["yaw"])


@pytest.mark.parametrize("change, error", [
    (lambda ck: ck.pop("env_id"), "ambiguous"),
    (lambda ck: ck.update(controls=["yaw"]), "conflicting"),
    (lambda ck: ck.update(act_dim=3), "act_dim"),
    (lambda ck: ck.update(state_dim=9), "turbine count"),
    (lambda ck: ck.update(action_contract={"version": 1}), "exactly"),
])
def test_bad_checkpoint_metadata_fails_before_runtime_creation(tmp_path, runtime, change, error):
    _, created = runtime
    path = tmp_path / "bad.pt"
    ck = _legacy_cli(path)
    change(ck)
    torch.save(ck, path)
    replay = Trainer(_scene(), ckpt_path=str(path), replay=True, warmup_steps=2)
    with pytest.raises(ValueError, match=error):
        replay._replay_policy()
    assert created == []


def test_runtime_bound_mismatch_fails_before_warmup_or_actions(tmp_path, runtime):
    runtime_type, created = runtime
    runtime_type.bounds = dict(BOUNDS, yaw=(-4.0, 4.0))
    path = tmp_path / "cli.pt"
    _legacy_cli(path)
    replay = Trainer(_scene(), ckpt_path=str(path), replay=True, warmup_steps=2)
    replay._run()
    assert "action bounds are incompatible" in replay.error
    assert created[0].steps == []
    assert created[0].closed


@pytest.mark.parametrize("kind, controls", [("cli", ["yaw"]), ("studio", ["yaw"]),
                                           ("studio", ["pitch"]),
                                           ("studio", ["torque", "yaw", "pitch"])])
def test_rollout_cli_consumes_the_same_named_contract(tmp_path, monkeypatch, runtime, kind, controls):
    import scripts.experiments.rollout_ckpt as rollout

    if kind == "cli":
        path = tmp_path / "cli.pt"
        _legacy_cli(path)
        expected = {"yaw": np.full(2, 0.5)}
    else:
        trained, path, _ = _train(tmp_path, controls, monkeypatch)
        expected = trained.limiter.requests[0]
    runtime_type, created = runtime
    monkeypatch.setattr(rollout, "SceneRuntime", runtime_type)
    monkeypatch.setattr(rollout, "load_scene", lambda _: _scene(controls))
    monkeypatch.setattr(rollout, "WARMUP", 0)
    monkeypatch.setattr("sys.argv", ["rollout_ckpt", "--ckpt", str(path),
                                    "--out", str(tmp_path / "trace.npz"), "--ep-steps", "2"])
    assert rollout.main() == 0
    for step in created[-1].steps:
        for control, value in expected.items():
            np.testing.assert_array_equal(step[f"{control}_delta"], value)
    assert created[-1].closed


def _cli_sampler(monkeypatch, tmp_path):
    import scripts.train.train_fastfarm as cli

    created = []

    class Sampler:
        n = 2
        obs_dim = 3
        act_dim = 1
        state_dim = 6
        act_low, act_high = -5.0, 5.0
        episode = 0
        u_inf = 8.0
        n_spawns = 1
        obs_keys = ["yaw", "wind_speed", "wind_direction"]
        obs_duty = False
        duty_penalty = 0.0

        def __init__(self, *args, **kwargs):
            self.shaper = kwargs["reward_shaper"]
            self.actions = []
            self.closed = False
            created.append(self)

        def obs(self):
            return np.zeros((2, 3), np.float32)

        def step(self, action):
            self.actions.append(action.copy())
            return self.obs(), 1.0, False, dict(power=[2.0, 3.0])

        def close(self):
            self.closed = True

    monkeypatch.setattr(cli, "FastFarmSampler", Sampler)
    monkeypatch.setattr(cli, "Actor", _FixedActor)
    monkeypatch.setattr(cli, "CKPT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_dump_hist", lambda *args: None)
    monkeypatch.setattr(cli, "_plot_curves", lambda *args: None)
    return cli, created


def test_cli_training_and_periodic_final_checkpoints_keep_clip(tmp_path, monkeypatch, runtime):
    cli, created = _cli_sampler(monkeypatch, tmp_path)
    cli.train("fake", 1, 2, n_epochs=1, lr=0.0, warmup_steps=0, save_interval=1)
    for action in created[0].actions:
        np.testing.assert_array_equal(action, [[0.5], [0.5]])
    paths = sorted(tmp_path.glob("*.pt"))
    assert len(paths) == 2
    for path in paths:
        ck = torch.load(path, map_location="cpu", weights_only=False)
        assert ck["policy_source"] == "train_fastfarm"
        assert ck["controls"] == ["yaw"]
        assert ck["action_contract"] == dict(version=1, controls=["yaw"],
                                              transform="clip", low=[-5.0], high=[5.0])
        replay = Trainer(_scene(), ckpt_path=str(path), replay=True,
                         replay_steps=1, warmup_steps=0)
        replay._replay_policy()
        np.testing.assert_array_equal(replay.limiter.requests[0]["yaw"], [0.5, 0.5])


def test_cli_resume_validates_contract_and_preserves_legacy_clip(tmp_path, monkeypatch):
    cli, created = _cli_sampler(monkeypatch, tmp_path)
    cli.train("fake", 1, 2, n_epochs=1, lr=0.0, save_interval=1)
    path = next(tmp_path.glob("*iter1.pt"))
    ck = torch.load(path, map_location="cpu", weights_only=False)
    for field in ("controls", "action_contract", "policy_source"):
        ck.pop(field)
    torch.save(ck, path)
    cli.train("fake", 2, 2, n_epochs=1, lr=0.0, resume_from=str(path))
    for action in created[-1].actions:
        np.testing.assert_array_equal(action, [[0.5], [0.5]])
    assert len(created) == 2
    ck["act_dim"] = 2
    torch.save(ck, path)
    with pytest.raises(ValueError, match="act_dim"):
        cli.train("fake", 2, 2, resume_from=str(path))
    assert len(created) == 2


def test_cli_rejects_non_yaw_scene_before_creating_sampler(tmp_path, monkeypatch):
    cli, created = _cli_sampler(monkeypatch, tmp_path)
    monkeypatch.setattr("wfrl.scene.schema.load_scene", lambda _: _scene(["pitch"]))
    with pytest.raises(ValueError, match="requires scene controls"):
        cli.train("fake", 1, 2, scene="unused.yaml")
    assert created == []

"""Effective duty gates and checkpoint observation encoding without a solver."""
from collections import deque
from types import SimpleNamespace as NS

import numpy as np
import pytest
import torch

from scripts.experiments import rollout_ckpt
from scripts.train import train_fastfarm
from wfrl.blender_bridge.flex_policy import FlexPolicy
from wfrl.fastfarm_driver import FastFarmDriver, YAW_RATE
from wfrl.mappo_central import Actor
from wfrl.policy_actions import duty_headroom, load_action_contract, load_obs_duty_limit
from wfrl.safety import ACTUATOR_RATE, DUTY_LIMIT, SafetyLimiter
from wfrl.scene.schema import Scene, Turbine
from wfrl.studio.trainer import Trainer


def _scene(n=3, controls=("yaw",), dt=1.):
    return Scene(name="duty-contract", backend="fastfarm", dt=dt,
                 controls=list(controls), sensors=[],
                 turbines=[Turbine(f"T{i + 1}", i * 500., 0.) for i in range(n)])


def _driver(*, sliding=True, limit=.3):
    """Use the real AEC duty gate with inert state transitions and no solver."""
    from wfcrl.multiagent_env import MAWindFarmEnv

    agents = ["a0", "a1", "a2"]
    duty = {"yaw": np.array([.2, .3, .4]), "pitch": np.array([.099, .1, .2])}
    rates = {"pitch": 8.} if sliding else dict(ACTUATOR_RATE)
    env = NS(
        mdp=NS(ACTUATORS_RATE=rates, controls={"yaw": None, "pitch": None},
               interface=NS(measure_map={}),
               get_accumulated_actions=lambda: {c: np.zeros(3) for c in duty}),
        farm_case=NS(dt=1.), _state=object(), _num_steps=dict.fromkeys(agents, 0),
        agent_selection=agents[0], agent_name_mapping={ag: i for i, ag in enumerate(agents)},
        accumulated_actions={ag: {c: values[i] * ACTUATOR_RATE[c] for c, values in duty.items()}
                             for i, ag in enumerate(agents)},
        infos={ag: {} for ag in agents}, actions={}, rewards=dict.fromkeys(agents, 0.),
        _cumulative_rewards=dict.fromkeys(agents, 0.),
        terminations=dict.fromkeys(agents, False), truncations=dict.fromkeys(agents, False),
        last=lambda: (None, 0., False, False, {}),
        observe=lambda ag: {"yaw": [0.], "pitch": [0.], "wind_speed": [8.],
                            "wind_direction": [270.]},
        _clear_rewards=lambda: None, _accumulate_rewards=lambda: None,
    )
    env._agent_selector = NS(is_last=lambda: False,
        next=lambda: agents[(agents.index(env.agent_selection) + 1) % 3])
    env.step = MAWindFarmEnv.step.__get__(env)
    driver = FastFarmDriver.__new__(FastFarmDriver)
    driver.env = driver._uenv = env
    driver.n, driver.agents, driver._idx = 3, agents, env.agent_name_mapping
    driver.controls = ["yaw", "pitch"]
    driver.act_bounds = {"yaw": (-5., 5.), "pitch": (-1., 1.)}
    driver.yaw_duty_sliding, driver.yaw_duty_limit = sliding, limit
    driver._yaw_win = deque([duty["yaw"] * YAW_RATE], maxlen=20)
    driver.done, driver._steps, driver.max_steps = False, 0, 100
    return driver


@pytest.mark.parametrize("sliding,limit,expected", [
    (True, .3, [.25, 0., 0.]), (True, .4, [.25, .25, 0.]),
    (False, .3, [0., 0., 0.]),
])
def test_driver_and_default_studio_share_effective_yaw_and_pitch_limits(sliding, limit, expected):
    direct = _driver(sliding=sliding, limit=limit)
    studio_driver = _driver(sliding=sliding, limit=limit)
    measure = direct._read(0.)
    assert measure["duty_limit"] == {"yaw": limit if sliding else .1, "pitch": .1}
    np.testing.assert_allclose(measure["duty"]["yaw"], [.2, .3, .4])
    if sliding:
        assert measure["duty"]["yaw"][1] == .3
    studio = Trainer(_scene(controls=("yaw", "pitch")))
    assert studio.enforce_duty is True
    commands, events = studio.limiter.apply({"yaw": .25, "pitch": .25}, measure,
                                           enforce_duty=studio.enforce_duty)
    np.testing.assert_array_equal(commands["yaw"], expected)
    np.testing.assert_array_equal(commands["pitch"], [.25, 0., 0.])
    direct.step(yaw_delta=.25, pitch_delta=.25)
    studio_driver.step(yaw_delta=commands["yaw"], pitch_delta=commands["pitch"])
    for control in ("yaw", "pitch"):
        raw = np.array([direct.env.actions[ag][control][0] for ag in direct.agents])
        filtered = np.array([studio_driver.env.actions[ag][control][0] for ag in direct.agents])
        np.testing.assert_array_equal(raw, commands[control])
        np.testing.assert_array_equal(filtered, raw)
    assert all(f"≥ {measure['duty_limit'][event.control]:g}" in event.detail for event in events)


def test_non_enforcing_events_use_measured_limit_and_sustainable_amount():
    limiter = SafetyLimiter(_scene(dt=3.))
    commands, events = limiter.apply({"yaw": .25}, {
        "duty": {"yaw": [.2, .3, .4]}, "duty_limit": {"yaw": .3}})
    np.testing.assert_array_equal(commands["yaw"], [.25] * 3)
    assert [event.turbine for event in events] == ["T2", "T3"]
    assert all("≥ 0.3" in e.detail and "0.27 °/步" in e.detail
               and "后端会把本步指令整个归零" in e.detail for e in events)


@pytest.mark.parametrize("metadata", [{}, {"duty_limit": {}}, {"duty_limit": {"pitch": .3}}])
@pytest.mark.parametrize("enforce", [False, True])
def test_missing_control_limit_preserves_cumulative_behavior(metadata, enforce):
    measure = {"duty": {"yaw": [np.nextafter(.1, 0.), .1, .2]}, **metadata}
    limiter = SafetyLimiter(_scene())
    np.testing.assert_array_equal(limiter.budget(measure, "yaw"), [np.inf, 0., 0.])
    commands, events = limiter.apply({"yaw": .25}, measure, enforce_duty=enforce)
    np.testing.assert_array_equal(commands["yaw"], [.25, 0., 0.] if enforce else [.25] * 3)
    assert [event.turbine for event in events] == ["T2", "T3"]
    assert all("≥ 0.1" in event.detail for event in events)


def test_measurement_has_only_constrained_controls_and_returns_a_fresh_limit_map():
    driver = _driver(limit=.42)
    driver.controls.append("torque")
    first = driver._read(0.)
    assert first["duty_limit"] == {"yaw": .42, "pitch": DUTY_LIMIT}
    assert "torque" not in first["duty"]
    first["duty_limit"]["yaw"] = .01
    driver.yaw_duty_limit = .37
    assert driver._read(0.)["duty_limit"]["yaw"] == .37


def test_unmeasured_estimates_explicitly_describe_the_cumulative_model():
    limiter = SafetyLimiter(_scene(dt=3.))
    assert limiter.sustainable("yaw") == pytest.approx(.09)
    assert limiter.reachable("yaw", 128) == pytest.approx(16.52)
    assert "累计模型上限 0.1" in limiter.notes(128)[0]
    assert "不代表 yaw 滑动窗口" in limiter.notes(128)[0]


def _measure(duty=(.003333333333, .05, .2), limit=.3):
    n = len(duty)
    result = dict(yaw=np.linspace(-2., 2., n), wind_speed=np.full(n, 8.),
                  wind_direction=np.full(n, 270.), duty={"yaw": np.asarray(duty)},
                  reward=1., done=False, power=np.ones(n))
    if limit is not None:
        result["duty_limit"] = {"yaw": limit}
    return result


@pytest.mark.parametrize("saved_limit", [None, .3])
def test_all_packers_preserve_the_checkpoint_scale_and_legacy_bits(saved_limit):
    duties = np.r_[np.linspace(-.1, .4, 101), np.nextafter(.1, 0.), .1, .2, .3]
    measure = _measure(duties, limit=.8)
    ck = {} if saved_limit is None else {"obs_duty_limit": saved_limit}
    limit = load_obs_duty_limit(ck)
    n, keys = len(duties), train_fastfarm.OBS_KEYS
    sampler = train_fastfarm.FastFarmSampler.__new__(train_fastfarm.FastFarmSampler)
    sampler.n, sampler.obs_keys, sampler.obs_duty, sampler.obs_duty_limit = n, keys, True, limit
    studio = Trainer(_scene(n))
    packed = [sampler._pack(measure), studio._pack_keys(measure, keys, True, n, limit),
              rollout_ckpt.pack_obs(measure, n, keys, True, limit)]
    for index, obs in enumerate(packed):
        dtype = np.float32 if index < 2 else float
        duty = np.asarray(duties, dtype=dtype)
        scale = .1 if saved_limit is None else .3
        headroom = np.clip((scale - duty) / scale, 0., 1.).astype(np.float32)
        expected = np.stack([*(np.asarray(measure[key], dtype=dtype) for key in keys),
                             headroom], axis=1).astype(np.float32)
        np.testing.assert_array_equal(obs, expected)
        assert obs.dtype == np.float32
    if saved_limit is not None:
        assert packed[0][-2, -1] == pytest.approx(1 / 3)


@pytest.fixture
def training_backend(monkeypatch, tmp_path):
    state, created = {"limit": .3}, []

    def factory(*args, **kwargs):
        driver = NS(n=3, act_low=-5., act_high=5., u_inf=8., steps=0, closed=False)
        driver.reset = lambda **options: _measure([.2] * 3, state["limit"])

        def step(action):
            driver.steps += 1
            measure = driver.reset()
            measure["reward"] = kwargs["reward_shaper"](1.) if kwargs.get("reward_shaper") else 1.
            return measure

        def close(**options):
            driver.closed = True
            driver.close_options = options

        driver.step, driver.close = step, close
        created.append(driver)
        return driver

    monkeypatch.setattr(train_fastfarm, "FastFarmDriver", factory)
    monkeypatch.setattr(train_fastfarm, "CKPT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setattr(train_fastfarm, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(train_fastfarm, "source_input_identity", lambda *args: "duty-test-source")
    monkeypatch.setattr(train_fastfarm, "_plot_curves", lambda *args: None)
    args = dict(env_id="Dec_Turb3_Row1_Fastfarm", iters=2, n_steps=2,
                warmup_steps=0, episode_steps=2, n_epochs=1, obs_duty=True, save_interval=1)
    return state, created, args


@pytest.mark.parametrize("runtime_limit", [.3, .24, None])
def test_training_saves_actual_limit_in_all_checkpoints_and_resumes_it(
        training_backend, tmp_path, monkeypatch, runtime_limit):
    state, created, args = training_backend
    state["limit"] = runtime_limit
    expected_limit = .1 if runtime_limit is None else runtime_limit
    observations = []
    pack = train_fastfarm.FastFarmSampler._pack

    def capture(sampler, measure):
        obs = pack(sampler, measure)
        observations.append(obs.copy())
        return obs

    monkeypatch.setattr(train_fastfarm.FastFarmSampler, "_pack", capture)
    train_fastfarm.train(**args)
    paths = list((tmp_path / "checkpoints").glob("*.pt"))
    assert len(paths) == 3
    for path in paths:
        checkpoint = torch.load(path, weights_only=False)
        assert checkpoint["obs_duty_limit"] == expected_limit
        assert checkpoint["obs_duty"] is True and checkpoint["obs_dim"] == 4
    expected_headroom = np.clip((expected_limit - .2) / expected_limit, 0., 1.)
    assert observations and all(np.allclose(obs[:, -1], expected_headroom) for obs in observations)
    assert all(driver.closed for driver in created)
    resume = next(path for path in paths if path.name.endswith("iter1.pt"))
    if runtime_limit is None:
        checkpoint = torch.load(resume, weights_only=False)
        checkpoint.pop("obs_duty_limit")
        torch.save(checkpoint, resume)
    history = train_fastfarm.train(**args, resume_from=str(resume))
    assert history["run"]["status"] == "completed"
    assert created[-1].closed


@pytest.mark.parametrize("checkpoint_limit", [.1, .4, None])
def test_resume_rejects_changed_or_legacy_limit_and_closes_sampler(
        training_backend, tmp_path, checkpoint_limit):
    _, created, args = training_backend
    train_fastfarm.train(**args)
    path = next((tmp_path / "checkpoints").glob("*iter1.pt"))
    ck = torch.load(path, weights_only=False)
    if checkpoint_limit is None:
        ck.pop("obs_duty_limit")
    else:
        ck["obs_duty_limit"] = checkpoint_limit
    torch.save(ck, path)
    files = set((tmp_path / "checkpoints").iterdir())
    with pytest.raises(ValueError, match="resume obs_duty_limit must match"):
        train_fastfarm.train(**args, resume_from=str(path))
    assert created[-1].steps == 0 and created[-1].closed
    assert created[-1].close_options == {"purge": True, "strict": True}
    assert set((tmp_path / "checkpoints").iterdir()) == files


def test_training_rejects_limit_changes_between_episodes(training_backend):
    state, created, _ = training_backend
    sampler = train_fastfarm.FastFarmSampler("fake", 2, warmup_steps=0, obs_duty=True)
    assert sampler.obs_duty_limit == .3
    state["limit"] = .1
    with pytest.raises(ValueError, match="fixed across training episodes"):
        sampler.new_episode()
    assert all(driver.closed for driver in created)


def _checkpoint(limit=None):
    ck = dict(env_id="Dec_Turb3_Row1_Fastfarm", obs_duty=True,
              recurrent=False, duty_penalty=0., obs_keys=train_fastfarm.OBS_KEYS,
              obs_dim=4, state_dim=12, act_dim=1, obs_mean=np.zeros(4),
              obs_var=np.ones(4), actor=Actor(4, 1).state_dict())
    if limit is not None:
        ck["obs_duty_limit"] = limit
    return ck


@pytest.mark.parametrize("limit", [0., -.1, np.nan, np.inf, True, "0.3", None, [.3]])
def test_checkpoint_contract_rejects_invalid_observation_limit(limit):
    ck = _checkpoint()
    ck["obs_duty_limit"] = limit
    with pytest.raises(ValueError, match="obs_duty_limit"):
        load_action_contract(ck, controls=["yaw"], n_agents=3)
    with pytest.raises(ValueError, match="obs_duty_limit"):
        duty_headroom(np.array([.2]), limit)


@pytest.mark.parametrize("saved_limit", [None, .3])
def test_checkpoint_consumers_use_saved_scale_despite_different_live_limit(
        tmp_path, monkeypatch, saved_limit):
    import wfrl.scene.runtime as runtime_module
    import wfrl.studio.trainer as studio_module

    path = tmp_path / "policy.pt"
    torch.save(_checkpoint(saved_limit), path)
    measure = _measure(limit=.8)
    expected = np.stack([measure[key] for key in train_fastfarm.OBS_KEYS], axis=1).astype(np.float32)
    limit = .1 if saved_limit is None else saved_limit
    duty = measure["duty"]["yaw"].astype(np.float32)
    expected32 = np.column_stack([expected, np.clip((limit - duty) / limit, 0., 1.)])
    policy = FlexPolicy(path)
    _, norm = policy.infer(measure)
    np.testing.assert_array_equal(norm, expected32)
    assert policy.metadata["obs_duty_limit"] == limit
    with pytest.raises(ValueError, match="duty.yaw"):
        policy.infer({key: value for key, value in measure.items() if key != "duty"})

    inputs, runtimes = [], []

    class RecordingActor(Actor):
        def dist(self, obs):
            inputs.append(obs.detach().cpu().numpy().copy())
            return super().dist(obs)

    class Runtime:
        def __init__(self, *args, **kwargs):
            self.driver = NS(act_bounds={"yaw": (-5., 5.)})
            self.last_measure, self.last_ctx = measure, {}
            self.done, self.closed = False, False
            runtimes.append(self)

        def reset(self):
            return {"_meta": {"farm_power": 3., "reward": 1.}}

        def step(self, **kwargs):
            return self.reset()

        def close(self, **kwargs):
            self.closed = True

    monkeypatch.setattr(runtime_module, "SceneRuntime", Runtime)
    monkeypatch.setattr(studio_module, "Actor", RecordingActor)
    replay = Trainer(_scene(), ckpt_path=str(path), replay=True, replay_steps=1, warmup_steps=0)
    replay._run()
    assert replay.error is None and runtimes[-1].closed
    np.testing.assert_array_equal(inputs.pop(), expected32)

    monkeypatch.setattr(rollout_ckpt, "SceneRuntime", Runtime)
    monkeypatch.setattr(rollout_ckpt, "Actor", RecordingActor)
    monkeypatch.setattr(rollout_ckpt, "load_scene", lambda path: _scene())
    monkeypatch.setattr(rollout_ckpt, "WARMUP", 0)
    monkeypatch.setattr(rollout_ckpt.np, "savez", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["rollout_ckpt", "--ckpt", str(path), "--ep-steps", "1"])
    assert rollout_ckpt.main() == 0 and runtimes[-1].closed
    duty64 = measure["duty"]["yaw"]
    expected64 = np.column_stack([expected, np.clip((limit - duty64) / limit, 0., 1.)]).astype(np.float32)
    np.testing.assert_array_equal(inputs.pop(), expected64)

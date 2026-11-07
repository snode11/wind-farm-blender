"""Checkpoint restoration through multimodal IPPO and HornsRev2 entry points."""

import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import gymnasium as gym
import numpy as np
import pytest
import torch
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.torch_layers import CombinedExtractor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from scripts.train import train_mappo_mm as multimodal
from scripts.train import train_ppo_hornsrev2 as hornsrev
from wfrl.sb3_wrapper import WFCRLSB3Wrapper


class Farm(gym.Env):
    render_mode = None

    def __init__(self):
        self.observation_space = spaces.Dict({
            "yaw": spaces.Box(-100.0, 100.0, (91,), dtype=np.float32),
        })
        self.action_space = spaces.Dict({
            "yaw": spaces.Box(-5.0, 5.0, (91,), dtype=np.float32),
        })
        self.steps = self.close_calls = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.steps = 0
        return {"yaw": np.ones(91, np.float32)}

    def step(self, action):
        self.steps += 1
        return ({"yaw": np.full(91, 8.0, np.float32)}, np.array([2.5]),
                False, self.steps == 2, {})

    def close(self):
        self.close_calls += 1


class ParallelFarm:
    possible_agents = ["t1", "t2"]
    render_mode = None

    def __init__(self):
        self.steps = self.close_calls = 0
        fi = SimpleNamespace(floris=SimpleNamespace(farm=SimpleNamespace(hub_heights=[90.0])))
        self.mdp = SimpleNamespace(interface=SimpleNamespace(fi=fi))

    def observation_space(self, agent):
        return {key: spaces.Box(-1000.0, 1000.0, (1,), dtype=np.float32)
                for key in ("yaw", "wind_speed", "wind_direction")}

    def action_space(self, agent):
        return {"yaw": spaces.Box(-5.0, 5.0, (1,), dtype=np.float32)}

    def observations(self, yaw):
        return {agent: {"yaw": np.array([yaw], np.float32),
                        "wind_speed": np.array([8.0], np.float32),
                        "wind_direction": np.array([270.0], np.float32)}
                for agent in self.possible_agents}

    def reset(self):
        self.steps = 0
        return self.observations(1.0), {agent: {} for agent in self.possible_agents}

    def step(self, actions):
        self.steps += 1
        return (self.observations(8.0), {agent: 1.25 for agent in self.possible_agents},
                {agent: False for agent in self.possible_agents},
                {agent: self.steps == 2 for agent in self.possible_agents},
                {agent: {} for agent in self.possible_agents})

    def close(self):
        self.close_calls += 1


def configure_paths(monkeypatch, cli, directory):
    monkeypatch.setattr(cli, "CKPT_DIR", str(directory))
    monkeypatch.setattr(cli, "LOG_DIR", str(directory / "logs"))
    monkeypatch.setattr(cli, "MODEL_PATH", str(directory / "policy"))
    monkeypatch.setattr(cli, "VECNORM_PATH", str(directory / "normalization.pkl"))


@pytest.fixture(params=["mm-image", "mm-vector", "hr-single", "hr-parallel"])
def harness(request, monkeypatch, tmp_path):
    is_mm = request.param.startswith("mm")
    cli = multimodal if is_mm else hornsrev
    use_image = request.param == "mm-image"
    workers = 2 if request.param == "hr-parallel" else 1
    state = SimpleNamespace(cli=cli, is_mm=is_mm, use_image=use_image, workers=workers,
                            created=[], bases=[], loaded=[], environments=[],
                            before=[], learned=[], observations=[])
    state.flags = ([] if use_image else ["--no-image"]) if is_mm else ["--n-envs", str(workers)]
    configure_paths(monkeypatch, cli, tmp_path)
    threads = torch.get_num_threads()
    torch.set_num_threads(1)

    def make(env_id, **options):
        assert env_id == cli.ENV_ID
        assert options == {"controls": ["yaw"], "max_num_steps": cli.MAX_STEPS}
        farm = ParallelFarm() if is_mm else Farm()
        state.created.append(farm)
        return farm

    def vectorize(factories):
        base = DummyVecEnv(factories)
        state.bases.append(base)
        return base

    monkeypatch.setattr(cli.envs, "make", make)
    if is_mm:
        monkeypatch.setattr(cli, "aec_to_parallel", lambda env: env)
        monkeypatch.setattr(cli.mm, "vibration_features",
                            lambda fi: np.full((2, 6), 0.5, np.float32))
        monkeypatch.setattr(cli.mm, "wake_images",
                            lambda fi, hub_h: np.full((2, 1, 48, 48), 0.25, np.float32))
    else:
        monkeypatch.setattr(cli, "SubprocVecEnv", vectorize)

    def save_pair(*, tagged=True, mutate_norm=None):
        venv = cli.make_venv(use_image=use_image) if is_mm else cli.make_train_venv(n_envs=1)
        try:
            for rms in venv.obs_rms.values():
                rms.mean[:] = 4.0
                rms.var[:] = 9.0
                rms.count = 123.0
            venv.ret_rms.mean = np.array(7.0)
            venv.ret_rms.var = np.array(16.0)
            venv.ret_rms.count = 125.0
            policy_kwargs = {"net_arch": [4]}
            if is_mm:
                policy_kwargs.update(features_extractor_class=CombinedExtractor,
                                     features_extractor_kwargs={"normalized_image": True,
                                                                "cnn_output_dim": 8})
            model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=2,
                        n_epochs=1, policy_kwargs=policy_kwargs, seed=2)
            model.num_timesteps = 40
            model.checkpoint_marker = "saved-policy"
            if tagged:
                model._wfrl_checkpoint_id = venv._wfrl_checkpoint_id = "pair-a"
            model.save(cli.MODEL_PATH)
            if mutate_norm:
                mutate_norm(venv)
            venv.save(cli.VECNORM_PATH)
        finally:
            venv.close()
        state.created.clear()
        state.bases.clear()

    def load(*args, **options):
        options["tensorboard_log"] = None
        model = PPO.load(*args, **options)
        venv = options["env"]
        state.loaded.append(model)
        state.environments.append(venv)
        state.before.append((venv.obs_rms["yaw"].count, venv.ret_rms.count,
                             venv.training, venv.norm_reward))
        learn, predict = PPO.learn, PPO.predict

        def record_learn(self, **kwargs):
            if self is model:
                state.learned.append(kwargs.copy())
                kwargs["progress_bar"] = False
            return learn(self, **kwargs)

        def record_predict(self, observation, **kwargs):
            if self is model:
                state.observations.append({key: value.copy() for key, value in observation.items()})
            return predict(self, observation, **kwargs)

        # SB3 serializes instance attributes; observers belong outside checkpoint data.
        monkeypatch.setattr(PPO, "learn", record_learn)
        monkeypatch.setattr(PPO, "predict", record_predict)
        return model

    def probe():
        monkeypatch.setattr(cli, "PPO", SimpleNamespace(load=load))

    def run(*arguments):
        monkeypatch.setattr(sys, "argv", ["train.py", *state.flags, *arguments])
        cli.main()

    state.save_pair, state.probe, state.run = save_pair, probe, run
    yield state
    torch.set_num_threads(threads)


@pytest.mark.parametrize("flags,tagged", [(["--eval-only"], False),
                                         (["--eval-only"], True),
                                         (["--resume", "--eval-only"], True)])
def test_eval_loads_saved_policy_and_frozen_statistics(harness, flags, tagged, capsys):
    h = harness
    h.save_pair(tagged=tagged)
    h.probe()
    h.run(*flags)
    assert h.before == [(123.0, 125.0, False, False)]
    assert h.loaded[0].checkpoint_marker == "saved-policy"
    assert h.loaded[0].num_timesteps == 40 and not h.learned
    assert h.environments[0].obs_rms["yaw"].count == 123.0
    assert h.environments[0].ret_rms.count == 125.0
    np.testing.assert_allclose(h.observations[0]["yaw"], -1.0)
    if h.use_image:
        np.testing.assert_array_equal(h.observations[0]["wake_image"], 0.25)
        assert "wake_image" not in h.environments[0].obs_rms
    expected = "return = 2.5000" if h.is_mm else "return = 5.0000"
    assert expected in capsys.readouterr().out
    assert len(h.created) == 1 and h.created[0].close_calls == 1


def test_resume_restores_statistics_and_adds_steps(harness):
    h = harness
    h.save_pair()
    h.probe()
    h.run("--resume", "--timesteps", "5")
    assert h.before == [(123.0, 125.0, True, True)]
    assert h.learned[0]["reset_num_timesteps"] is False
    assert h.learned[0]["progress_bar"] is False
    expected_steps = 46 if not h.is_mm and h.workers == 1 else 48
    assert h.loaded[0].num_timesteps == expected_steps
    assert h.environments[0].obs_rms["yaw"].count > 123.0
    assert h.environments[0].ret_rms.count > 125.0
    saved = PPO.load(h.cli.MODEL_PATH)
    with open(h.cli.VECNORM_PATH, "rb") as stream:
        norm = pickle.load(stream)
    assert saved.num_timesteps == expected_steps
    assert saved._wfrl_checkpoint_id == norm._wfrl_checkpoint_id != "pair-a"
    assert norm.obs_rms["yaw"].count == h.environments[0].obs_rms["yaw"].count
    if h.is_mm:
        assert set(norm.obs_rms) == set(multimodal.NORM_KEYS)
    else:
        assert h.loaded[0].n_envs == h.workers
    assert all(farm.close_calls == 1 for farm in h.created)


@pytest.mark.parametrize("flag", ["--eval-only", "--resume"])
@pytest.mark.parametrize("missing", ["model", "normalization"])
def test_restore_requires_complete_pair_before_environment(harness, flag, missing):
    h = harness
    h.save_pair()
    Path(h.cli.MODEL_PATH + ".zip" if missing == "model" else h.cli.VECNORM_PATH).unlink()
    h.probe()
    with pytest.raises(FileNotFoundError):
        h.run(flag, "--timesteps", "1")
    assert not h.created


@pytest.mark.parametrize("artifact", ["model", "normalization"])
def test_corrupt_pair_fails_and_closes_environment(harness, artifact):
    h = harness
    h.save_pair()
    Path(h.cli.MODEL_PATH + ".zip" if artifact == "model" else h.cli.VECNORM_PATH).write_bytes(b"invalid")
    h.probe()
    with pytest.raises((ValueError, pickle.UnpicklingError)):
        h.run("--eval-only")
    assert len(h.created) == 1 and h.created[0].close_calls == 1


@pytest.mark.parametrize("mutate", [
    lambda v: setattr(v, "gamma", 0.5),
    lambda v: setattr(v, "_wfrl_checkpoint_id", "pair-b"),
    lambda v: v.obs_rms["yaw"].mean.fill(np.nan),
])
def test_incompatible_pair_fails_and_closes_environment(harness, mutate):
    h = harness
    h.save_pair(mutate_norm=mutate)
    h.probe()
    with pytest.raises(ValueError):
        h.run("--eval-only")
    assert len(h.created) == 1 and h.created[0].close_calls == 1


@pytest.mark.parametrize("harness", ["mm-image", "mm-vector"], indirect=True)
def test_multimodal_image_mode_mismatch_fails_before_inference(harness):
    h = harness
    h.save_pair()
    h.flags = ["--no-image"] if h.use_image else []
    h.probe()
    with pytest.raises((ValueError, AssertionError), match="space|keys"):
        h.run("--eval-only")
    assert not h.observations
    assert len(h.created) == 1 and h.created[0].close_calls == 1


@pytest.mark.parametrize("method,flag", [("predict", "--eval-only"),
                                         ("learn", "--resume"), ("save", "--resume")])
def test_model_failure_releases_owned_environments(harness, monkeypatch, method, flag):
    h = harness
    h.save_pair()

    def fail(*args, **kwargs):
        raise RuntimeError("simulated model failure")

    def load(*args, **kwargs):
        model = PPO.load(*args, **kwargs)
        monkeypatch.setattr(model, "learn", lambda **options: model)
        monkeypatch.setattr(model, method, fail)
        return model

    monkeypatch.setattr(h.cli, "PPO", SimpleNamespace(load=load))
    with pytest.raises(RuntimeError, match="simulated model failure"):
        h.run(flag)
    assert h.created and all(farm.close_calls == 1 for farm in h.created)


def test_fresh_training_uses_new_statistics(harness, monkeypatch):
    h = harness
    h.save_pair()
    counts, predictions, calls = [], [], []

    def construct(**kwargs):
        counts.append(kwargs["env"].obs_rms["yaw"].count)
        kwargs.update(n_steps=2, batch_size=2, n_epochs=1, verbose=0, tensorboard_log=None)
        kwargs["policy_kwargs"]["net_arch"] = [4]
        model = PPO(**kwargs)
        learn, predict = PPO.learn, PPO.predict

        def record_learn(self, **options):
            if self is model:
                calls.append(options.copy())
                options["progress_bar"] = False
            return learn(self, **options)

        def record_predict(self, observation, **options):
            if self is model:
                predictions.append(observation["yaw"].copy())
            return predict(self, observation, **options)

        monkeypatch.setattr(PPO, "learn", record_learn)
        monkeypatch.setattr(PPO, "predict", record_predict)
        return model

    monkeypatch.setattr(h.cli, "PPO", construct)
    h.run("--timesteps", "3")
    assert counts == [1e-4]
    assert calls[0]["reset_num_timesteps"] is True
    if not h.is_mm:
        np.testing.assert_allclose(predictions[0], 1.0)
    assert all(farm.close_calls == 1 for farm in h.created)


@pytest.mark.parametrize("corrupt", [False, True])
def test_hornsrev_real_subprocess_restore_and_cleanup(tmp_path, monkeypatch, corrupt):
    configure_paths(monkeypatch, hornsrev, tmp_path)
    bases = []

    def factory(max_steps):
        return lambda: Monitor(WFCRLSB3Wrapper(Farm()))

    def vectorize(factories):
        base = SubprocVecEnv(factories, start_method="spawn")
        bases.append(base)
        return base

    monkeypatch.setattr(hornsrev, "_env_factory", factory)
    monkeypatch.setattr(hornsrev, "SubprocVecEnv", vectorize)
    initial = hornsrev.make_train_venv(n_envs=2)
    try:
        initial.obs_rms["yaw"].mean[:] = 4.0
        initial.obs_rms["yaw"].var[:] = 9.0
        initial.obs_rms["yaw"].count = 123.0
        initial.save(hornsrev.VECNORM_PATH)
    finally:
        initial.close()
    if corrupt:
        Path(hornsrev.VECNORM_PATH).write_bytes(b"invalid")
        with pytest.raises(pickle.UnpicklingError):
            hornsrev.make_train_venv(n_envs=3, resume=True)
    else:
        restored = hornsrev.make_train_venv(n_envs=3, resume=True)
        try:
            assert restored.obs_rms["yaw"].count == 123.0
            np.testing.assert_array_equal(restored.returns, [0.0, 0.0, 0.0])
            restored.training = False
            np.testing.assert_allclose(restored.reset()["yaw"], -1.0)
        finally:
            restored.close()
    assert all(base.closed for base in bases)
    assert all(not process.is_alive() for base in bases for process in base.processes)

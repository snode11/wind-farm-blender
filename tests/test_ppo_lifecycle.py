"""Centralized PPO checkpoint lifecycle with real SB3 and an in-memory farm."""

import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import gymnasium as gym
import numpy as np
import pytest
from gymnasium import spaces
from stable_baselines3 import PPO

from scripts.train import train_ppo as cli


class Farm(gym.Env):
    render_mode = None

    def __init__(self):
        self.observation_space = spaces.Dict({
            "yaw": spaces.Box(-100.0, 100.0, (2,), dtype=np.float32),
        })
        self.action_space = spaces.Dict({
            "yaw": spaces.Box(-5.0, 5.0, (2,), dtype=np.float32),
        })
        self.steps = 0
        self.close_calls = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.steps = 0
        return {"yaw": np.array([1.0, 2.0], np.float32)}

    def step(self, action):
        self.steps += 1
        return ({"yaw": np.array([8.0, 9.0], np.float32)},
                np.array([2.5]), False, self.steps == 2, {})

    def close(self):
        self.close_calls += 1


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    created = []

    def make(env_id, **options):
        assert env_id == cli.ENV_ID
        assert options == {"controls": ["yaw"], "max_num_steps": cli.MAX_STEPS}
        farm = Farm()
        created.append(farm)
        return farm

    monkeypatch.setattr(cli.envs, "make", make)
    monkeypatch.setattr(cli, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(cli, "CKPT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "MODEL_PATH", str(tmp_path / "policy"))
    monkeypatch.setattr(cli, "VECNORM_PATH", str(tmp_path / "normalization.pkl"))
    return created


def save_pair(mutate_norm=None):
    venv = cli.make_train_venv()
    try:
        venv.obs_rms["yaw"].mean[:] = 4.0
        venv.obs_rms["yaw"].var[:] = 9.0
        venv.obs_rms["yaw"].count = 123.0
        venv.ret_rms.mean = np.array(7.0)
        venv.ret_rms.var = np.array(16.0)
        venv.ret_rms.count = 125.0
        model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=2,
                    n_epochs=1, policy_kwargs={"net_arch": [4]}, seed=2)
        model.num_timesteps = 40
        model._wfrl_checkpoint_id = venv._wfrl_checkpoint_id = "saved-pair"
        model.save(cli.MODEL_PATH)
        if mutate_norm:
            mutate_norm(venv)
        venv.save(cli.VECNORM_PATH)
    finally:
        venv.close()


def observe_model(monkeypatch, model, probe):
    probe.models.append(model)
    model.tensorboard_log = None
    learn, predict = model.learn, model.predict

    def record_learn(**options):
        probe.learn_calls.append(options.copy())
        options["progress_bar"] = False
        return learn(**options)

    def record_predict(observation, **options):
        probe.observations.append(observation["yaw"].copy())
        return predict(observation, **options)

    monkeypatch.setattr(model, "learn", record_learn)
    monkeypatch.setattr(model, "predict", record_predict)
    return model


def loader_probe(monkeypatch):
    probe = SimpleNamespace(models=[], envs=[], counts=[], learn_calls=[], observations=[])

    def load(*args, **options):
        venv = options["env"]
        probe.envs.append(venv)
        probe.counts.append((venv.obs_rms["yaw"].count, venv.ret_rms.count))
        return observe_model(monkeypatch, PPO.load(*args, **options), probe)

    monkeypatch.setattr(cli, "PPO", SimpleNamespace(load=load))
    return probe


@pytest.mark.parametrize("flags", [["--eval-only"], ["--resume", "--eval-only"]])
def test_eval_uses_saved_policy_and_frozen_normalization(monkeypatch, isolated, flags, capsys):
    save_pair()
    isolated.clear()
    probe = loader_probe(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", *flags])
    cli.main()
    assert probe.counts == [(123.0, 125.0)]
    assert probe.models[0].num_timesteps == 40
    assert not probe.learn_calls
    assert not probe.envs[0].training and not probe.envs[0].norm_reward
    assert probe.envs[0].obs_rms["yaw"].count == 123.0
    assert probe.envs[0].ret_rms.count == 125.0
    np.testing.assert_allclose(probe.observations[0], [[-1.0, -2.0 / 3]], rtol=1e-6)
    assert "raw return = 5.0000" in capsys.readouterr().out
    assert len(isolated) == 1 and isolated[0].close_calls == 1


def test_resume_restores_statistics_and_adds_timesteps(monkeypatch, isolated):
    save_pair()
    isolated.clear()
    probe = loader_probe(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", "--resume", "--timesteps", "3"])
    cli.main()
    assert probe.counts == [(123.0, 125.0)]
    assert probe.learn_calls[0]["reset_num_timesteps"] is False
    assert probe.learn_calls[0]["total_timesteps"] == 3
    assert probe.models[0].num_timesteps == 44
    assert probe.envs[0].obs_rms["yaw"].count > 123.0
    assert probe.envs[0].ret_rms.count > 125.0
    restored_model = PPO.load(cli.MODEL_PATH)
    with open(cli.VECNORM_PATH, "rb") as stream:
        restored_norm = pickle.load(stream)
    assert restored_model.num_timesteps == 44
    assert restored_model._wfrl_checkpoint_id == restored_norm._wfrl_checkpoint_id
    assert restored_model._wfrl_checkpoint_id != "saved-pair"
    assert restored_norm.obs_rms["yaw"].count == probe.envs[0].obs_rms["yaw"].count
    assert len(isolated) == 3 and all(farm.close_calls == 1 for farm in isolated)


def test_fresh_baseline_uses_current_statistics_ignoring_old_artifacts(monkeypatch, isolated):
    save_pair()
    isolated.clear()
    probe = SimpleNamespace(models=[], learn_calls=[], observations=[])

    def construct(**options):
        options.update(n_steps=2, batch_size=2, n_epochs=1, verbose=0,
                       tensorboard_log=None, policy_kwargs={"net_arch": [4]})
        return observe_model(monkeypatch, PPO(**options), probe)

    monkeypatch.setattr(cli, "PPO", construct)
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", "--timesteps", "3"])
    cli.main()
    np.testing.assert_allclose(probe.observations[0], [[1.0, 2.0]])
    assert probe.learn_calls[0]["reset_num_timesteps"] is True
    assert probe.models[0].num_timesteps == 4
    assert len(isolated) == 3 and all(farm.close_calls == 1 for farm in isolated)


@pytest.mark.parametrize("flag", ["--eval-only", "--resume"])
@pytest.mark.parametrize("missing", ["model", "normalization"])
def test_restore_requires_both_artifacts_before_environment(monkeypatch, isolated, flag, missing):
    save_pair()
    isolated.clear()
    Path(cli.MODEL_PATH + ".zip" if missing == "model" else cli.VECNORM_PATH).unlink()
    loader_probe(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", flag, "--timesteps", "2"])
    with pytest.raises(FileNotFoundError):
        cli.main()
    assert not isolated


@pytest.mark.parametrize("artifact", ["model", "normalization"])
def test_corrupt_artifact_fails_and_closes_environment(monkeypatch, isolated, artifact):
    save_pair()
    isolated.clear()
    Path(cli.MODEL_PATH + ".zip" if artifact == "model" else cli.VECNORM_PATH).write_bytes(b"invalid")
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", "--eval-only"])
    with pytest.raises((ValueError, pickle.UnpicklingError)):
        cli.main()
    assert len(isolated) == 1 and isolated[0].close_calls == 1


@pytest.mark.parametrize("mutation", [
    lambda v: setattr(v, "gamma", 0.5),
    lambda v: setattr(v, "_wfrl_checkpoint_id", "other-pair"),
    lambda v: setattr(v.obs_rms["yaw"], "mean", np.full(2, np.nan)),
])
def test_invalid_pair_fails_and_closes_environment(monkeypatch, isolated, mutation):
    save_pair(mutate_norm=mutation)
    isolated.clear()
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", "--eval-only"])
    with pytest.raises(ValueError):
        cli.main()
    assert len(isolated) == 1 and isolated[0].close_calls == 1


@pytest.mark.parametrize("operation,flag", [
    ("predict", "--eval-only"), ("learn", "--resume"), ("save", "--resume"),
])
def test_model_failure_closes_every_owned_environment(monkeypatch, isolated, operation, flag):
    save_pair()
    isolated.clear()

    def fail(*args, **options):
        raise RuntimeError("simulated model failure")

    def load(*args, **options):
        model = PPO.load(*args, **options)
        monkeypatch.setattr(model, "learn", lambda **kwargs: model)
        monkeypatch.setattr(model, operation, fail)
        return model

    monkeypatch.setattr(cli, "PPO", SimpleNamespace(load=load))
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", flag])
    with pytest.raises(RuntimeError, match="simulated model failure"):
        cli.main()
    assert isolated and all(farm.close_calls == 1 for farm in isolated)


def test_periodic_checkpoints_include_matching_normalization(monkeypatch, isolated):
    from stable_baselines3.common.callbacks import CheckpointCallback

    def callback(**options):
        options["save_freq"] = 2
        return CheckpointCallback(**options)

    save_pair()
    monkeypatch.setattr(cli, "CheckpointCallback", callback)
    loader_probe(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["train_ppo.py", "--resume", "--timesteps", "2"])
    cli.main()
    saved_model = PPO.load(Path(cli.CKPT_DIR) / "ppo_wf_42_steps.zip")
    with (Path(cli.CKPT_DIR) / "ppo_wf_vecnormalize_42_steps.pkl").open("rb") as stream:
        saved_norm = pickle.load(stream)
    assert saved_model.num_timesteps == 42
    assert saved_norm.obs_rms["yaw"].count == 126.0
    assert saved_norm.ret_rms.count == 127.0

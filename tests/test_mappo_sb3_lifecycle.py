"""SB3 checkpoint lifecycle with temporary artifacts and a simulated parallel env."""

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from gymnasium import spaces
from stable_baselines3 import PPO

from wfrl import mappo_sb3 as sb3


class _ParallelEnv:
    possible_agents = ["t1", "t2"]
    render_mode = None

    def __init__(self):
        self.steps = 0
        self.close_calls = 0

    def observation_space(self, agent):
        return {"yaw": spaces.Box(-100.0, 100.0, (1,), dtype=np.float32)}

    def action_space(self, agent):
        return {"yaw": spaces.Box(-1.0, 1.0, (1,), dtype=np.float32)}

    def _obs(self, value):
        return {a: {"yaw": np.array([value + i], np.float32)}
                for i, a in enumerate(self.possible_agents)}

    def reset(self):
        self.steps = 0
        return self._obs(1), {a: {} for a in self.possible_agents}

    def step(self, actions):
        self.steps += 1
        return (self._obs(9), {a: 1.0 for a in self.possible_agents},
                {a: False for a in self.possible_agents},
                {a: self.steps == 2 for a in self.possible_agents},
                {a: {} for a in self.possible_agents})

    def close(self):
        self.close_calls += 1


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    created = []

    def make(env_id, **kwargs):
        assert env_id == sb3.ENV_ID
        assert kwargs == {"controls": ["yaw"], "max_num_steps": sb3.MAX_STEPS}
        env = _ParallelEnv()
        created.append(env)
        return env

    monkeypatch.setattr(sb3.envs, "make", make)
    monkeypatch.setattr(sb3, "aec_to_parallel", lambda env: env)
    monkeypatch.setattr(sb3, "CKPT_DIR", str(tmp_path))
    monkeypatch.setattr(sb3, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(sb3, "MODEL_PATH", str(tmp_path / "policy"))
    monkeypatch.setattr(sb3, "VECNORM_PATH", str(tmp_path / "normalization.pkl"))
    return created


def _save_pair(*, mutate_norm=None, mutate_model=None, tagged=False):
    venv = sb3.make_venv()
    try:
        venv.obs_rms["yaw"].mean[:] = 4
        venv.obs_rms["yaw"].var[:] = 9
        venv.obs_rms["yaw"].count = 123.0
        venv.ret_rms.mean = np.array(7.0)
        venv.ret_rms.var = np.array(16.0)
        venv.ret_rms.count = 125.0
        model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=4, n_epochs=1,
                    policy_kwargs={"net_arch": [4]}, seed=2)
        model.num_timesteps = 40
        model.test_checkpoint_marker = "saved-policy"
        if tagged:
            model._wfrl_checkpoint_id = venv._wfrl_checkpoint_id = "pair-a"
        if mutate_model:
            mutate_model(model)
        model.save(sb3.MODEL_PATH)
        if mutate_norm:
            mutate_norm(venv)
        venv.save(sb3.VECNORM_PATH)
    finally:
        venv.close()


def _spy_loader(monkeypatch, *, on_load=None):
    loaded = []

    def load(*args, **kwargs):
        model = PPO.load(*args, **kwargs)
        loaded.append(model)
        if on_load:
            on_load(model, kwargs["env"])
        return model

    # This object has no constructor, so a random-policy fallback fails the test.
    monkeypatch.setattr(sb3, "PPO", SimpleNamespace(load=load))
    return loaded


@pytest.mark.parametrize("tagged", [False, True])
@pytest.mark.parametrize("flags", [["--eval-only"], ["--resume", "--eval-only"]])
def test_cli_eval_loads_policy_and_frozen_normalization(monkeypatch, isolated, flags, tagged):
    _save_pair(tagged=tagged)
    isolated.clear()
    restored = []

    def on_load(model, venv):
        assert model.num_timesteps == 40
        assert model.test_checkpoint_marker == "saved-policy"
        assert venv.training is False
        assert venv.norm_reward is False
        assert venv.obs_rms["yaw"].count == 123
        assert venv.ret_rms.count == 125
        np.testing.assert_array_equal(venv.obs_rms["yaw"].mean, [4])
        np.testing.assert_array_equal(venv.obs_rms["yaw"].var, [9])
        restored.append(venv)

    loaded = _spy_loader(monkeypatch, on_load=on_load)
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", *flags])
    sb3.main()

    assert len(loaded) == len(restored) == len(isolated) == 1
    assert loaded[0].num_timesteps == 40
    assert restored[0].obs_rms["yaw"].count == 123
    assert restored[0].ret_rms.count == 125
    assert isolated[0].close_calls == 1


def test_cli_resume_restores_statistics_and_adds_timesteps(monkeypatch, isolated):
    _save_pair(tagged=True)
    isolated.clear()
    learn_calls = []
    restored = []

    def on_load(model, venv):
        assert venv.training is True
        assert venv.norm_reward is True
        assert venv.obs_rms["yaw"].count == 123
        assert venv.ret_rms.count == 125
        assert venv.ret_rms.var == 16
        np.testing.assert_array_equal(venv.returns, [0, 0])
        restored.append(venv)
        actual_learn = model.learn
        model.tensorboard_log = None

        def learn(**kwargs):
            learn_calls.append(kwargs.copy())
            kwargs["progress_bar"] = False
            return actual_learn(**kwargs)

        monkeypatch.setattr(model, "learn", learn)

    loaded = _spy_loader(monkeypatch, on_load=on_load)
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", "--resume", "--timesteps", "5"])
    sb3.main()

    assert learn_calls == [{"total_timesteps": 5, "progress_bar": True,
                            "reset_num_timesteps": False}]
    assert loaded[0].num_timesteps == 48
    assert restored[0].obs_rms["yaw"].count > 123
    assert restored[0].ret_rms.count > 125
    assert len(isolated) == 2
    assert all(env.close_calls == 1 for env in isolated)
    saved = PPO.load(sb3.MODEL_PATH)
    assert saved.num_timesteps == 48
    assert saved._wfrl_checkpoint_id != "pair-a"
    check = sb3.make_venv(training=False, norm_reward=False, norm_path=sb3.VECNORM_PATH)
    try:
        assert saved._wfrl_checkpoint_id == check._wfrl_checkpoint_id
        assert check.obs_rms["yaw"].count == restored[0].obs_rms["yaw"].count
        np.testing.assert_array_equal(check.obs_rms["yaw"].mean, restored[0].obs_rms["yaw"].mean)
        np.testing.assert_array_equal(check.ret_rms.var, restored[0].ret_rms.var)
    finally:
        check.close()


def test_training_script_eval_entry_loads_saved_policy(monkeypatch, isolated):
    _save_pair()
    isolated.clear()
    loaded = _spy_loader(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", "--eval-only"])
    entry = Path(__file__).resolve().parents[1] / "scripts" / "train" / "train_mappo.py"
    runpy.run_path(str(entry), run_name="__main__")
    assert len(loaded) == 1
    assert loaded[0].test_checkpoint_marker == "saved-policy"
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


def test_cli_fresh_training_uses_new_statistics_and_resets_count(monkeypatch, isolated):
    calls = []
    models = []

    def construct(**kwargs):
        assert kwargs["env"].obs_rms["yaw"].count == 1e-4
        kwargs.update(n_steps=2, batch_size=4, n_epochs=1, verbose=0,
                      tensorboard_log=None, policy_kwargs={"net_arch": [4]})
        model = PPO(**kwargs)
        models.append(model)
        actual_learn = model.learn

        def learn(**learn_kwargs):
            calls.append(learn_kwargs.copy())
            learn_kwargs["progress_bar"] = False
            return actual_learn(**learn_kwargs)

        monkeypatch.setattr(model, "learn", learn)
        return model

    monkeypatch.setattr(sb3, "PPO", construct)
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", "--timesteps", "3"])
    sb3.main()

    assert calls == [{"total_timesteps": 3, "progress_bar": True,
                      "reset_num_timesteps": True}]
    assert len(models) == 1
    assert models[0].num_timesteps == 4
    assert len(isolated) == 2
    assert all(env.close_calls == 1 for env in isolated)


@pytest.mark.parametrize("flag", ["--eval-only", "--resume"])
@pytest.mark.parametrize("missing", ["model", "normalization"])
def test_missing_artifact_fails_before_creating_environment(monkeypatch, isolated, flag, missing):
    existing = sb3.VECNORM_PATH if missing == "model" else sb3.MODEL_PATH + ".zip"
    Path(existing).write_bytes(b"present")
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", flag])
    with pytest.raises(FileNotFoundError, match="Required MAPPO checkpoint file is missing"):
        sb3.main()
    assert not isolated


@pytest.mark.parametrize("artifact", ["model", "normalization"])
def test_corrupt_artifact_fails_without_fallback_and_closes_environment(monkeypatch, isolated, artifact):
    import pickle

    _save_pair()
    isolated.clear()
    filename = sb3.MODEL_PATH + ".zip" if artifact == "model" else sb3.VECNORM_PATH
    Path(filename).write_bytes(b"invalid artifact")
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", "--eval-only"])
    error_type = ValueError if artifact == "model" else pickle.UnpicklingError
    with pytest.raises(error_type):
        sb3.main()
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


@pytest.mark.parametrize("mutation, message", [
    (lambda v: setattr(v, "gamma", 0.5), "discount factors"),
    (lambda v: setattr(v, "norm_obs", False), "normalize every observation"),
    (lambda v: setattr(v, "_wfrl_checkpoint_id", "pair-b"), "identities"),
    (lambda v: setattr(v.obs_rms["yaw"], "mean", np.array([np.nan])), "statistics"),
    (lambda v: setattr(v.obs_rms["yaw"], "var", np.array([1.0, 2.0])), "statistics"),
    (lambda v: setattr(v, "action_space", spaces.Box(-2.0, 2.0, (1,), dtype=np.float32)), "Action spaces"),
])
def test_incompatible_normalization_fails_and_closes_environment(monkeypatch, isolated, mutation, message):
    _save_pair(mutate_norm=mutation, tagged=True)
    isolated.clear()
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", "--eval-only"])
    with pytest.raises(ValueError, match=message):
        sb3.main()
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


def test_incompatible_model_fails_and_closes_environment(monkeypatch, isolated):
    _save_pair(mutate_model=lambda m: setattr(m, "action_space", spaces.Box(-2.0, 2.0, (1,), dtype=np.float32)))
    isolated.clear()
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", "--resume"])
    with pytest.raises(ValueError, match="Action spaces"):
        sb3.main()
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


@pytest.mark.parametrize("method, flag", [("learn", "--resume"), ("predict", "--eval-only"), ("save", "--resume")])
def test_cli_closes_environment_after_model_failure(monkeypatch, isolated, method, flag):
    _save_pair()
    isolated.clear()

    def on_load(model, venv):
        monkeypatch.setattr(model, "learn", lambda **kwargs: model)

        def fail(*args, **kwargs):
            raise RuntimeError("simulated model failure")

        monkeypatch.setattr(model, method, fail)

    _spy_loader(monkeypatch, on_load=on_load)
    monkeypatch.setattr(sys, "argv", ["train_mappo.py", flag])
    with pytest.raises(RuntimeError, match="simulated model failure"):
        sb3.main()
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


def test_build_model_cannot_resume_with_fresh_statistics(isolated):
    _save_pair()
    fresh = sb3.make_venv()
    try:
        with pytest.raises(ValueError, match="Restore VecNormalize"):
            sb3.build_model(fresh, resume=True)
    finally:
        fresh.close()


def test_evaluate_requires_saved_normalization(isolated):
    with pytest.raises(FileNotFoundError, match="normalization file is missing"):
        sb3.evaluate(None)
    assert not isolated


def test_evaluate_closes_owned_environment_on_failure(monkeypatch, isolated):
    _save_pair()
    model = PPO.load(sb3.MODEL_PATH)
    isolated.clear()

    def fail(*args, **kwargs):
        raise RuntimeError("prediction failed")

    monkeypatch.setattr(model, "predict", fail)
    with pytest.raises(RuntimeError, match="prediction failed"):
        sb3.evaluate(model)
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


def _run_secondary_entry(entry):
    if entry == "comparison":
        from scripts.analysis.compare_eval import eval_ippo

        return eval_ippo(2)
    from wfrl.viz.animate import load_model_and_env

    model, venv = load_model_and_env()
    try:
        model.predict(venv.reset(), deterministic=True)
    finally:
        venv.close()


@pytest.mark.parametrize("entry", ["comparison", "animation"])
@pytest.mark.parametrize("missing", ["model", "normalization"])
def test_secondary_entry_requires_both_artifacts_before_environment(isolated, entry, missing):
    _save_pair(tagged=True)
    filename = sb3.MODEL_PATH + ".zip" if missing == "model" else sb3.VECNORM_PATH
    Path(filename).unlink()
    isolated.clear()
    with pytest.raises(FileNotFoundError, match="Required MAPPO checkpoint file is missing"):
        _run_secondary_entry(entry)
    assert not isolated


@pytest.mark.parametrize("entry", ["comparison", "animation"])
def test_secondary_entry_rejects_mismatched_pair_and_closes_environment(isolated, entry):
    _save_pair(tagged=True, mutate_norm=lambda v: setattr(v, "_wfrl_checkpoint_id", "pair-b"))
    isolated.clear()
    with pytest.raises(ValueError, match="identities"):
        _run_secondary_entry(entry)
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1


@pytest.mark.parametrize("entry", ["comparison", "animation"])
def test_secondary_entry_uses_saved_frozen_statistics(monkeypatch, isolated, entry):
    _save_pair(tagged=True)
    isolated.clear()
    restored = []

    def on_load(model, venv):
        assert model.test_checkpoint_marker == "saved-policy"
        assert venv.training is False
        assert venv.norm_reward is False
        np.testing.assert_array_equal(venv.obs_rms["yaw"].mean, [4])
        np.testing.assert_array_equal(venv.obs_rms["yaw"].var, [9])
        restored.append(venv)

    loaded = _spy_loader(monkeypatch, on_load=on_load)
    returns = _run_secondary_entry(entry)
    if entry == "comparison":
        np.testing.assert_array_equal(returns, [2, 2])
    assert len(loaded) == len(restored) == len(isolated) == 1
    assert restored[0].obs_rms["yaw"].count == 123
    assert restored[0].ret_rms.count == 125
    assert isolated[0].close_calls == 1


def test_comparison_closes_environment_when_prediction_fails(monkeypatch, isolated):
    _save_pair(tagged=True)
    isolated.clear()

    def on_load(model, venv):
        def fail(*args, **kwargs):
            raise RuntimeError("prediction failed")

        monkeypatch.setattr(model, "predict", fail)

    _spy_loader(monkeypatch, on_load=on_load)
    with pytest.raises(RuntimeError, match="prediction failed"):
        _run_secondary_entry("comparison")
    assert len(isolated) == 1
    assert isolated[0].close_calls == 1

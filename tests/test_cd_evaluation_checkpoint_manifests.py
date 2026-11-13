"""Evaluation readers select validated SB3 generations and own their environments."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import VecNormalize

from scripts.analysis import compare_eval, eval_visualize_ppo as visualize
from scripts.train import train_ppo
from wfrl import mappo_sb3
from test_mappo_sb3_lifecycle import _ParallelEnv
from test_ppo_lifecycle import Farm


@pytest.fixture(autouse=True)
def single_torch_thread():
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(threads)


@pytest.fixture(params=["visualize", "compare"])
def reader(request, monkeypatch, tmp_path):
    training = train_ppo if request.param == "visualize" else mappo_sb3
    created, observed = [], []

    def make(*args, **kwargs):
        farm = Farm() if request.param == "visualize" else _ParallelEnv()
        created.append(farm)
        return farm

    stem, normalization = str(tmp_path / "policy"), str(tmp_path / "stats.pkl")
    for module in (training, visualize) if request.param == "visualize" else (training,):
        monkeypatch.setattr(module, "MODEL_PATH", stem)
        monkeypatch.setattr(module, "VECNORM_PATH", normalization)
    monkeypatch.setattr(training.envs, "make", make)
    if request.param == "compare":
        monkeypatch.setattr(training, "aec_to_parallel", lambda env: env)
        venv = training.make_venv()
    else:
        monkeypatch.setattr(visualize, "plot_wakefield", lambda *args, **kwargs: None)
        monkeypatch.setattr(visualize, "plot_timeseries", lambda *args, **kwargs: None)

        def rollout(env, model, norm):
            observed.append((model, norm))
            obs, _ = env.reset()
            model.predict(norm.normalize_obs(obs), deterministic=True)
            return np.zeros((1, 2)), np.ones((1, 2)), np.ones(1)

        monkeypatch.setattr(visualize, "rollout", rollout)
        venv = training.make_train_venv()
    model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=2, n_epochs=1,
                policy_kwargs={"net_arch": [4]}, seed=2)
    model.num_timesteps = 8
    venv.obs_rms["yaw"].mean[:] = 4
    venv.obs_rms["yaw"].var[:] = 9
    venv.obs_rms["yaw"].count = 123.0
    model.save(stem)
    venv.save(normalization)
    loaded = []

    def load(path, **kwargs):
        loaded.append(Path(path))
        restored = PPO.load(path, **kwargs)
        if request.param == "compare":
            observed.append((restored, kwargs["env"]))
        return restored

    target = visualize if request.param == "visualize" else mappo_sb3
    monkeypatch.setattr(target, "PPO", SimpleNamespace(load=load))
    created.clear()

    def run():
        if request.param == "visualize":
            visualize.main()
        else:
            np.testing.assert_array_equal(compare_eval.eval_ippo(2), [2, 2])

    h = SimpleNamespace(kind=request.param, training=training, model=model, norm=venv,
                        stem=stem, normalization=Path(normalization),
                        manifest=Path(stem + ".manifest.json"),
                        created=created, observed=observed, loaded=loaded, run=run)
    try:
        yield h
    finally:
        venv.close()
        for farm in created:
            if not farm.close_calls:
                farm.close()


def publish(reader, steps=16, mean=10):
    reader.model.num_timesteps = steps
    reader.norm.obs_rms["yaw"].mean[:] = mean
    return reader.training.save_checkpoint_pair(reader.model, reader.norm)


def assert_pair(reader, steps, mean, identity):
    assert len(reader.observed) == 1
    model, norm = reader.observed[0]
    assert model.num_timesteps == steps
    assert model.__dict__.get("_wfrl_checkpoint_id") == identity
    assert norm.__dict__.get("_wfrl_checkpoint_id") == identity
    np.testing.assert_array_equal(norm.obs_rms["yaw"].mean, mean)
    assert norm.obs_rms["yaw"].count == 123.0
    assert norm.training is False and norm.norm_reward is False


def test_readers_use_legacy_pair_only_without_manifest(reader):
    reader.run()
    assert_pair(reader, steps=8, mean=4, identity=None)


@pytest.mark.parametrize("legacy_present", [False, True])
def test_readers_select_manifest_generation(reader, legacy_present):
    model_path, _ = publish(reader)
    if not legacy_present:
        Path(reader.stem + ".zip").unlink()
        reader.normalization.unlink()
    reader.run()
    assert reader.loaded == [model_path]
    assert_pair(reader, steps=16, mean=10, identity=reader.model._wfrl_checkpoint_id)


@pytest.mark.parametrize("damage", ["json", "schema", "generation", "model_name",
                                     "normalization_name", "model_hash", "normalization_hash",
                                     "missing_model", "missing_normalization"])
def test_readers_reject_damaged_manifest_without_legacy_fallback(reader, damage):
    model_path, norm_path = publish(reader)
    if damage == "json":
        reader.manifest.write_text("{", encoding="utf-8")
    elif damage in {"model_hash", "normalization_hash"}:
        target = model_path if damage == "model_hash" else norm_path
        with target.open("ab") as stream:
            stream.write(b"digest mismatch")
    elif damage.startswith("missing_"):
        (model_path if damage == "missing_model" else norm_path).unlink()
    else:
        document = json.loads(reader.manifest.read_text(encoding="utf-8"))
        field, value = {
            "schema": ("schema", 99),
            "generation": ("generation", "../invalid-generation"),
            "model_name": ("model", Path(reader.stem + ".zip").name),
            "normalization_name": ("normalization", reader.normalization.name),
        }[damage]
        document[field] = value
        reader.manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        reader.run()
    assert not reader.loaded
    assert not reader.created


def test_readers_pin_pair_while_manifest_changes_during_load(reader, monkeypatch):
    first_model, _ = publish(reader)
    first_manifest = reader.manifest.read_bytes()
    first_id = reader.model._wfrl_checkpoint_id
    publish(reader, steps=32, mean=20)
    second_manifest = reader.manifest.read_bytes()
    reader.manifest.write_bytes(first_manifest)
    target = visualize if reader.kind == "visualize" else mappo_sb3
    load = target.PPO.load

    def switch_generation(path, **kwargs):
        reader.manifest.write_bytes(second_manifest)
        return load(path, **kwargs)

    monkeypatch.setattr(target.PPO, "load", switch_generation)
    reader.run()
    assert reader.loaded == [first_model]
    assert_pair(reader, steps=16, mean=10, identity=first_id)


@pytest.mark.parametrize("reader", ["visualize"], indirect=True)
def test_visualize_rejects_dangling_manifest(reader):
    reader.manifest.symlink_to(reader.manifest.parent / "missing-manifest.json")
    with pytest.raises(FileNotFoundError):
        reader.run()
    assert not reader.loaded
    assert not reader.created


@pytest.mark.parametrize("reader", ["visualize"], indirect=True)
def test_visualize_standalone_normalizer_uses_manifest(reader):
    publish(reader)
    norm = visualize.load_normalizer()
    try:
        assert norm._wfrl_checkpoint_id == reader.model._wfrl_checkpoint_id
        np.testing.assert_array_equal(norm.obs_rms["yaw"].mean, 10)
        assert norm.training is False and norm.norm_reward is False
    finally:
        norm.close()


@pytest.mark.parametrize("reader", ["visualize"], indirect=True)
@pytest.mark.parametrize("mismatch", ["identity", "nonfinite", "variance", "gamma"])
def test_visualize_rejects_incompatible_legacy_statistics(reader, mismatch):
    norm = reader.norm
    if mismatch == "identity":
        norm._wfrl_checkpoint_id = "different-model"
    elif mismatch == "nonfinite":
        norm.obs_rms["yaw"].mean[:] = np.nan
    elif mismatch == "variance":
        norm.obs_rms["yaw"].var[:] = -1
    else:
        norm.gamma = 0.5
    norm.save(reader.normalization)
    with pytest.raises(ValueError, match="identities|statistics|discount"):
        reader.run()
    assert not reader.observed
    assert len(reader.created) == 1
    assert reader.created[0].close_calls == 1


@pytest.mark.parametrize("reader", ["visualize"], indirect=True)
def test_visualize_closes_environment_when_normalizer_load_fails(reader, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("normalization load failed")

    monkeypatch.setattr(VecNormalize, "load", fail)
    with pytest.raises(RuntimeError, match="normalization load failed"):
        reader.run()
    assert len(reader.created) == 1
    assert reader.created[0].close_calls == 1


@pytest.mark.parametrize("reader", ["visualize"], indirect=True)
@pytest.mark.parametrize("wrapper", ["adapter", "monitor", "vector"])
def test_visualize_closes_environment_when_wrapper_construction_fails(reader, monkeypatch, wrapper):
    def fail(*args, **kwargs):
        if wrapper == "vector":
            args[0][0]()
        raise RuntimeError("environment wrapping failed")

    monkeypatch.setattr(visualize, {"adapter": "WFCRLSB3Wrapper", "monitor": "Monitor",
                                   "vector": "DummyVecEnv"}[wrapper], fail)
    with pytest.raises(RuntimeError, match="environment wrapping failed"):
        visualize.load_normalizer()
    assert len(reader.created) == 1
    assert reader.created[0].close_calls == 1


@pytest.mark.parametrize("reader", ["visualize"], indirect=True)
@pytest.mark.parametrize("failure", [None, "baseline", "rollout", "trained", "timeseries"])
def test_visualize_closes_owned_environments(reader, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise RuntimeError("evaluation failed")

    if failure in {"baseline", "trained"}:
        def plot(env, tag):
            if tag == failure:
                fail()

        monkeypatch.setattr(visualize, "plot_wakefield", plot)
    elif failure is not None:
        monkeypatch.setattr(visualize, "rollout" if failure == "rollout" else "plot_timeseries", fail)
    if failure is None:
        reader.run()
    else:
        with pytest.raises(RuntimeError, match="evaluation failed"):
            reader.run()
    assert len(reader.created) == (2 if failure == "baseline" else 3)
    assert all(farm.close_calls == 1 for farm in reader.created)

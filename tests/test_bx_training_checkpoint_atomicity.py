"""Training entry points preserve complete checkpoint generations under failures."""

import json
import pickle
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.torch_layers import CombinedExtractor

from scripts.train import train_mappo_mm as multimodal
from scripts.train import train_ppo as ppo
from scripts.train import train_ppo_hornsrev2 as hornsrev
from wfrl import mappo_central as central, training_integrity as integrity
from test_bp_checkpoint_recovery import SerializationFailure, UnreadableSerialization
from test_mappo_truncation import _ParallelEnv
from test_ppo_lifecycle import Farm as SmallFarm
from test_secondary_sb3_lifecycle import Farm as LargeFarm, ParallelFarm, configure_paths


@pytest.fixture(autouse=True)
def single_torch_thread():
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(threads)


@pytest.fixture(params=["ppo", "multimodal", "hornsrev"])
def entry(request, monkeypatch, tmp_path):
    cli = {"ppo": ppo, "multimodal": multimodal, "hornsrev": hornsrev}[request.param]
    configure_paths(monkeypatch, cli, tmp_path)
    created = []

    def make(*args, **kwargs):
        farm = (ParallelFarm() if cli is multimodal else
                LargeFarm() if cli is hornsrev else SmallFarm())
        created.append(farm)
        return farm

    monkeypatch.setattr(cli.envs, "make", make)
    if cli is multimodal:
        monkeypatch.setattr(cli, "aec_to_parallel", lambda env: env)
        monkeypatch.setattr(cli.mm, "vibration_features",
                            lambda fi: np.full((2, 6), 0.5, np.float32))
        monkeypatch.setattr(cli.mm, "wake_images",
                            lambda fi, hub_h: np.full((2, 1, 48, 48), 0.25, np.float32))
        venv = cli.make_venv()
    elif cli is hornsrev:
        venv = cli.make_train_venv(n_envs=1)
    else:
        venv = cli.make_train_venv()
    try:
        policy_kwargs = {"net_arch": [4]}
        if cli is multimodal:
            policy_kwargs.update(features_extractor_class=CombinedExtractor,
                                 features_extractor_kwargs={"normalized_image": True,
                                                            "cnn_output_dim": 8})
        model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=2, n_epochs=1,
                    policy_kwargs=policy_kwargs, seed=2)
        model.num_timesteps = 8
        venv.obs_rms["yaw"].mean[:] = 4.0

        def restore_env():
            if cli is multimodal:
                return cli.make_venv(norm_reward=False, training=False,
                                     norm_path=cli.VECNORM_PATH)
            return cli.make_eval_venv()

        yield SimpleNamespace(cli=cli, model=model, venv=venv, created=created,
                              restore_env=restore_env)
    finally:
        venv.close()


def snapshot(directory):
    return {path: path.read_bytes() for path in directory.iterdir()}


def assert_restored(entry, *, steps, mean, identity):
    venv = entry.restore_env()
    try:
        model = entry.cli.build_model(venv, resume=True)
        assert model.num_timesteps == steps
        assert model.__dict__.get("_wfrl_checkpoint_id") == identity
        assert venv.__dict__.get("_wfrl_checkpoint_id") == identity
        np.testing.assert_array_equal(venv.obs_rms["yaw"].mean, mean)
        if entry.cli is multimodal:
            assert set(venv.obs_rms) == set(multimodal.NORM_KEYS)
            assert "wake_image" not in venv.obs_rms
    finally:
        venv.close()


def inject_failure(monkeypatch, model, venv, failure):
    if failure.startswith("model-") and failure.endswith(("serialize", "validate")):
        state_dict = model.policy.state_dict
        bad = SerializationFailure() if failure.endswith("serialize") else UnreadableSerialization()
        monkeypatch.setattr(model.policy, "state_dict", lambda: dict(state_dict(), bad=bad))
    elif failure == "norm-serialize":
        venv.bad = SerializationFailure()
    elif failure == "norm-validate":
        venv.obs_rms["yaw"].var[:] = -1
    else:
        replace = integrity.os.replace
        suffix = {"model-replace": ".zip", "norm-replace": ".pkl",
                  "manifest-replace": ".json", "manifest-interrupt": ".json"}[failure]

        def fail_replace(source, destination):
            assert Path(source).parent == Path(destination).parent
            if str(destination).endswith(suffix):
                if failure == "manifest-interrupt":
                    raise KeyboardInterrupt("interrupted before manifest publication")
                raise OSError("replacement refused")
            return replace(source, destination)

        monkeypatch.setattr(integrity.os, "replace", fail_replace)


@pytest.mark.parametrize("previous", ["none", "legacy", "generation"])
@pytest.mark.parametrize("failure", ["model-serialize", "model-validate", "norm-serialize",
                                     "norm-validate", "model-replace", "norm-replace",
                                     "manifest-replace", "manifest-interrupt"])
def test_failed_save_preserves_old_files_and_published_pair(entry, tmp_path, monkeypatch,
                                                           previous, failure):
    h = entry
    if previous == "legacy":
        h.model.save(h.cli.MODEL_PATH)
        h.venv.save(h.cli.VECNORM_PATH)
    elif previous == "generation":
        h.cli.save_checkpoint_pair(h.model, h.venv)
    before = snapshot(tmp_path)
    old_id = h.model.__dict__.get("_wfrl_checkpoint_id")
    h.model.num_timesteps = 16
    h.venv.obs_rms["yaw"].mean[:] = 10.0
    inject_failure(monkeypatch, h.model, h.venv, failure)
    with pytest.raises((RuntimeError, ValueError, OSError, KeyboardInterrupt)):
        h.cli.save_checkpoint_pair(h.model, h.venv)
    assert snapshot(tmp_path) == before
    assert h.model.__dict__.get("_wfrl_checkpoint_id") == old_id
    assert h.venv.__dict__.get("_wfrl_checkpoint_id") == old_id
    if previous != "none":
        assert_restored(h, steps=8, mean=4.0, identity=old_id)


def test_reader_pins_generation_and_accepts_legacy_without_ids(entry, tmp_path):
    h = entry
    h.model.save(h.cli.MODEL_PATH)
    h.venv.save(h.cli.VECNORM_PATH)
    legacy = snapshot(tmp_path)
    assert_restored(h, steps=8, mean=4.0, identity=None)
    first = h.cli.save_checkpoint_pair(h.model, h.venv)
    first_id = h.model._wfrl_checkpoint_id
    pending = h.restore_env()
    try:
        h.model.num_timesteps = 16
        h.venv.obs_rms["yaw"].mean[:] = 10.0
        rng = integrity.capture_rng()
        second = h.cli.save_checkpoint_pair(h.model, h.venv)
        assert pickle.dumps(integrity.capture_rng()["python"]) == pickle.dumps(rng["python"])
        assert pickle.dumps(integrity.capture_rng()["numpy"]) == pickle.dumps(rng["numpy"])
        torch.testing.assert_close(torch.get_rng_state(), rng["torch"])
        assert first != second and all(path.is_file() for path in (*first, *second))
        assert all(path.read_bytes() == content for path, content in legacy.items())
        model = h.cli.build_model(pending, resume=True)
        assert model.num_timesteps == 8
        assert model._wfrl_checkpoint_id == pending._wfrl_checkpoint_id == first_id
        np.testing.assert_array_equal(pending.obs_rms["yaw"].mean, 4.0)
    finally:
        pending.close()
    assert_restored(h, steps=16, mean=10.0, identity=h.model._wfrl_checkpoint_id)
    assert len(list(tmp_path.iterdir())) == 7


@pytest.mark.parametrize("component", ["model", "normalization", "manifest", "missing", "filename"])
def test_bad_manifest_or_referenced_file_never_falls_back(entry, monkeypatch, component):
    h = entry
    h.model.save(h.cli.MODEL_PATH)
    h.venv.save(h.cli.VECNORM_PATH)
    pair = h.cli.save_checkpoint_pair(h.model, h.venv)
    manifest = Path(h.cli.MODEL_PATH + ".manifest.json")
    if component == "missing":
        pair[1].unlink()
    elif component == "filename":
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["model"] = Path(h.cli.MODEL_PATH + ".zip").name
        manifest.write_text(json.dumps(document), encoding="utf-8")
    else:
        target = manifest if component == "manifest" else pair[component == "normalization"]
        target.write_bytes(b"corrupt")
    count = len(h.created)
    monkeypatch.setattr(sys, "argv", ["train.py", "--eval-only"])
    with pytest.raises((ValueError, FileNotFoundError)):
        h.cli.main()
    assert len(h.created) == count


def test_interrupt_after_manifest_replace_retains_complete_generation(entry, tmp_path, monkeypatch):
    h = entry
    h.cli.save_checkpoint_pair(h.model, h.venv)
    old_files = {path: path.read_bytes() for path in h.cli._require_checkpoint_files()}
    h.model.num_timesteps = 16
    h.venv.obs_rms["yaw"].mean[:] = 10.0
    replace = integrity.os.replace

    def interrupt(source, destination):
        replace(source, destination)
        if str(destination).endswith(".manifest.json"):
            raise KeyboardInterrupt("interrupted after publication")

    monkeypatch.setattr(integrity.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        h.cli.save_checkpoint_pair(h.model, h.venv)
    assert_restored(h, steps=16, mean=10.0, identity=h.model._wfrl_checkpoint_id)
    assert all(path.read_bytes() == data for path, data in old_files.items())
    assert len(list(tmp_path.iterdir())) == 5


@pytest.mark.parametrize("entry", ["ppo", "hornsrev"], indirect=True)
@pytest.mark.parametrize("failure", ["model-serialize", "norm-serialize", "norm-replace", "manifest-replace"])
def test_periodic_failure_leaves_only_complete_checkpoints(entry, monkeypatch, tmp_path, failure):
    h = entry
    prefix = "ppo_wf" if h.cli is ppo else "ppo_hr2"
    callback = h.cli.AtomicCheckpointCallback(save_freq=1, save_path=tmp_path, name_prefix=prefix)
    callback.init_callback(h.model)
    assert callback.on_step()
    pair = ppo.CheckpointPair(tmp_path / f"{prefix}_8_steps",
                              tmp_path / f"{prefix}_vecnormalize_8_steps.pkl")
    model_path, norm_path = pair.require()
    assert PPO.load(model_path).num_timesteps == 8
    with norm_path.open("rb") as stream:
        assert pickle.load(stream)._wfrl_checkpoint_id == h.model._wfrl_checkpoint_id
    before = snapshot(tmp_path)
    h.model.num_timesteps = 16
    inject_failure(monkeypatch, h.model, h.venv, failure)
    with pytest.raises((RuntimeError, OSError)):
        callback.on_step()
    assert snapshot(tmp_path) == before
    assert pair.require() == (model_path, norm_path)


@pytest.mark.parametrize("entry", ["ppo", "hornsrev"], indirect=True)
def test_cli_periodic_callback_publishes_manifest(entry, monkeypatch, tmp_path):
    h = entry
    h.cli.save_checkpoint_pair(h.model, h.venv)
    callback_class = h.cli.AtomicCheckpointCallback

    def callback(**options):
        options["save_freq"] = 1
        return callback_class(**options)

    monkeypatch.setattr(h.cli, "AtomicCheckpointCallback", callback)
    monkeypatch.setattr(h.cli, "evaluate", lambda *args, **kwargs: None)
    load = PPO.load

    def load_without_tensorboard(*args, **kwargs):
        kwargs["tensorboard_log"] = None
        return load(*args, **kwargs)

    monkeypatch.setattr(h.cli, "PPO", SimpleNamespace(load=load_without_tensorboard))
    flags = ["--n-envs", "1"] if h.cli is hornsrev else []
    monkeypatch.setattr(sys, "argv", ["train.py", "--resume", "--timesteps", "2", *flags])
    h.cli.main()
    prefix = "ppo_wf" if h.cli is ppo else "ppo_hr2"
    for steps in (9, 10):
        model_path, norm_path = ppo.CheckpointPair(
            tmp_path / f"{prefix}_{steps}_steps",
            tmp_path / f"{prefix}_vecnormalize_{steps}_steps.pkl",
        ).require()
        model = PPO.load(model_path)
        with norm_path.open("rb") as stream:
            norm = pickle.load(stream)
        assert model.num_timesteps == steps
        assert model._wfrl_checkpoint_id == norm._wfrl_checkpoint_id


@pytest.fixture
def central_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(central, "_parallel_environment", lambda: _ParallelEnv(horizon=2))
    monkeypatch.setattr(central, "CKPT_DIR", str(tmp_path))
    path = tmp_path / "central.pt"
    monkeypatch.setattr(central, "MODEL_PATH", str(path))
    return path


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["serialize", "validate", "replace"])
def test_central_failure_keeps_model_and_statistics_together(central_checkpoint, monkeypatch,
                                                            existing, failure):
    path = central_checkpoint
    if existing:
        central.train(4, n_steps=2, n_epochs=1, mb_steps=2)
    before = snapshot(path.parent)
    if failure == "replace":
        def fail_replace(source, destination):
            assert Path(source).parent == path.parent
            assert Path(destination) == path
            raise OSError("replacement refused")

        monkeypatch.setattr(integrity.os, "replace", fail_replace)
    else:
        state_dict = central.Actor.state_dict
        bad = SerializationFailure() if failure == "serialize" else UnreadableSerialization()
        monkeypatch.setattr(central.Actor, "state_dict",
                            lambda self: dict(state_dict(self), bad=bad))
    with pytest.raises((RuntimeError, OSError)):
        central.train(4, n_steps=2, n_epochs=1, mb_steps=2, seed=1)
    assert snapshot(path.parent) == before
    if existing:
        saved = torch.load(path, weights_only=False)
        assert {"actor", "critic", "obs_mean", "obs_var"} <= saved.keys()


@pytest.mark.parametrize("legacy", [False, True])
def test_central_eval_reads_atomic_and_legacy_payloads(central_checkpoint, monkeypatch, legacy):
    actor, critic, rms = central.train(4, n_steps=2, n_epochs=1, mb_steps=2)
    path = central_checkpoint
    if legacy:
        torch.save({"actor": actor.state_dict(), "critic": critic.state_dict(),
                    "obs_mean": rms.mean, "obs_var": rms.var,
                    "obs_dim": 1, "act_dim": 1, "state_dim": 2}, path)
    observed = []

    def evaluate(restored, normalizer, sampler):
        for key, weight in restored.state_dict().items():
            torch.testing.assert_close(weight, actor.state_dict()[key])
        np.testing.assert_array_equal(normalizer.mean, rms.mean)
        np.testing.assert_array_equal(normalizer.var, rms.var)
        observed.append(True)

    monkeypatch.setattr(central, "evaluate", evaluate)
    monkeypatch.setattr(sys, "argv", ["mappo_central", "--eval-only"])
    central.main()
    assert observed == [True]
    assert list(path.parent.iterdir()) == [path]

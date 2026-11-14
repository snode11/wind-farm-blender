"""Atomic checkpoint publication, cooperative signals, and exact recovery counters."""
import copy
import json
from pathlib import Path
import signal
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from stable_baselines3 import PPO

from scripts.train import train_fastfarm as train
from wfrl import mappo_sb3 as sb3, training_integrity as integrity
from wfrl.blender_bridge.training_progress import resume_counts
from wfrl.policy_actions import PolicyActionContract
from wfrl.studio.trainer import Trainer
from test_fastfarm_training_integrity import fake_training
from test_mappo_sb3_lifecycle import isolated


class SerializationFailure:
    def __reduce__(self):
        raise RuntimeError("serialization failed inside pickle")


def fail_on_load():
    raise RuntimeError("serialized content cannot be loaded")


class UnreadableSerialization:
    def __reduce__(self):
        return fail_on_load, ()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", [SerializationFailure, UnreadableSerialization])
def test_atomic_torch_failure_keeps_destination_and_removes_staging(tmp_path, existing, failure):
    path = tmp_path / "policy.pt"
    if existing:
        torch.save({"weight": torch.tensor([7.])}, path)
    before = path.read_bytes() if existing else None
    with pytest.raises(RuntimeError):
        integrity.save_checkpoint_exclusive({"weight": torch.ones(4096), "bad": failure()}, path)
    assert path.read_bytes() == before if existing else not path.exists()
    assert set(tmp_path.iterdir()) == ({path} if existing else set())


def test_exclusive_checkpoint_rechecks_destination_after_validation(tmp_path, monkeypatch):
    path = tmp_path / "policy.pt"
    original_load = torch.load

    def validate(*args, **kwargs):
        saved = original_load(*args, **kwargs)
        path.write_bytes(b"another publisher")
        return saved

    monkeypatch.setattr(torch, "load", validate)
    with pytest.raises(FileExistsError):
        integrity.save_checkpoint_exclusive({"weight": torch.ones(1)}, path)
    assert path.read_bytes() == b"another publisher"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("exclusive", [False, True])
def test_atomic_checkpoint_validates_before_same_directory_replace(tmp_path, monkeypatch, exclusive):
    path = tmp_path / "policy.pt"
    events = []
    original_load, original_replace = torch.load, integrity.os.replace

    def validate(candidate, **kwargs):
        assert Path(candidate).parent == path.parent and Path(candidate) != path
        events.append("validated")
        return original_load(candidate, **kwargs)

    def replace(source, destination):
        assert events == ["validated"] and Path(destination) == path
        events.append("replaced")
        return original_replace(source, destination)

    monkeypatch.setattr(torch, "load", validate)
    monkeypatch.setattr(integrity.os, "replace", replace)
    integrity.save_checkpoint_atomic({"weight": torch.tensor([3.])}, path, exclusive=exclusive)
    assert events == ["validated", "replaced"]
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["serialize", "validate", "replace", None])
def test_studio_save_preserves_old_checkpoint_until_valid_replacement(tmp_path, monkeypatch, failure):
    path = tmp_path / "studio.pt"
    torch.save({"old": torch.tensor([8.])}, path)
    before = path.read_bytes()
    trainer = Trainer.__new__(Trainer)
    trainer.ckpt_path = str(path)
    trainer.obs_keys, trainer.controls = ["yaw", "wind_speed", "wind_direction"], ["yaw"]
    trainer.scene = SimpleNamespace(n=2, to_dict=lambda: {"n": 2})
    trainer.action_contract = PolicyActionContract.from_bounds(["yaw"], {"yaw": (-1., 1.)}, "clip")
    weights = {"weight": torch.tensor([2.])}
    if failure in {"serialize", "validate"}:
        weights["bad"] = SerializationFailure() if failure == "serialize" else UnreadableSerialization()
    trainer.actor = trainer.critic = SimpleNamespace(state_dict=lambda: weights)
    rms = SimpleNamespace(mean=np.zeros(3), var=np.ones(3))
    if failure == "replace":
        def fail(*args):
            raise OSError("replacement refused")
        monkeypatch.setattr(integrity.os, "replace", fail)
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            trainer._save(rms)
        assert path.read_bytes() == before
    else:
        trainer._save(rms)
        saved = torch.load(path, weights_only=False)
        assert saved["policy_source"] == "studio"
        assert saved["actor"]["weight"].item() == 2
    assert list(tmp_path.iterdir()) == [path]


@pytest.fixture
def saved_sb3_pair(isolated):
    venv = sb3.make_venv()
    model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=4, n_epochs=1,
                policy_kwargs={"net_arch": [4]}, seed=2)
    model.num_timesteps = 8
    venv.obs_rms["yaw"].mean[:] = 4
    sb3.save_checkpoint_pair(model, venv)
    try:
        yield model, venv
    finally:
        venv.close()


@pytest.mark.parametrize("failure", ["model-serialize", "norm-serialize", "norm-validate",
                                     "model-replace", "norm-replace", "manifest-replace"])
def test_sb3_failed_generation_keeps_complete_published_pair(saved_sb3_pair, tmp_path, monkeypatch, failure):
    model, venv = saved_sb3_pair
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    old_id = model._wfrl_checkpoint_id
    model.num_timesteps = 16
    venv.obs_rms["yaw"].mean[:] = 10
    if failure == "model-serialize":
        state_dict = model.policy.state_dict
        monkeypatch.setattr(model.policy, "state_dict", lambda: dict(state_dict(), bad=SerializationFailure()))
    elif failure == "norm-serialize":
        venv.bad = SerializationFailure()
    elif failure == "norm-validate":
        venv.obs_rms["yaw"].var[:] = -1
    else:
        replace = integrity.os.replace
        suffix = {"model-replace": ".zip", "norm-replace": ".pkl", "manifest-replace": ".json"}[failure]

        def fail_replace(source, destination):
            if str(destination).endswith(suffix):
                raise OSError("replacement refused")
            return replace(source, destination)

        monkeypatch.setattr(integrity.os, "replace", fail_replace)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        sb3.save_checkpoint_pair(model, venv)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert model._wfrl_checkpoint_id == venv._wfrl_checkpoint_id == old_id
    restored, normalized = sb3.load_evaluation_pair()
    try:
        assert restored.num_timesteps == 8
        assert restored._wfrl_checkpoint_id == normalized._wfrl_checkpoint_id == old_id
        np.testing.assert_array_equal(normalized.obs_rms["yaw"].mean, [4.])
    finally:
        normalized.close()


def test_sb3_reader_pins_generation_across_manifest_switch(saved_sb3_pair, tmp_path):
    model, venv = saved_sb3_pair
    old_pair = sb3._require_checkpoint_files()
    pending = sb3.make_venv(norm_path=sb3.VECNORM_PATH)
    old_id = pending._wfrl_checkpoint_id
    rng = integrity.capture_rng()
    model.num_timesteps = 16
    venv.obs_rms["yaw"].mean[:] = 10
    new_pair = sb3.save_checkpoint_pair(model, venv)
    assert new_pair != old_pair and all(path.is_file() for path in (*old_pair, *new_pair))
    torch.testing.assert_close(torch.get_rng_state(), rng["torch"])
    document = json.loads(sb3._manifest_path().read_text())
    assert document["generation"] == model._wfrl_checkpoint_id
    try:
        pinned_model = sb3.build_model(pending, resume=True)
        assert pinned_model._wfrl_checkpoint_id == old_id
        assert pinned_model.num_timesteps == 8
    finally:
        pending.close()
    loaded, normalized = sb3.load_evaluation_pair()
    try:
        assert loaded.num_timesteps == 16
        assert loaded._wfrl_checkpoint_id == normalized._wfrl_checkpoint_id == document["generation"]
        np.testing.assert_array_equal(normalized.obs_rms["yaw"].mean, [10.])
    finally:
        normalized.close()
    assert len(list(tmp_path.iterdir())) == 5


@pytest.mark.parametrize("component", ["model", "normalization", "manifest"])
def test_sb3_manifest_corruption_never_falls_back_to_legacy(saved_sb3_pair, isolated, component):
    pair = sb3._require_checkpoint_files()
    Path(sb3.MODEL_PATH + ".zip").write_bytes(b"legacy model")
    Path(sb3.VECNORM_PATH).write_bytes(b"legacy statistics")
    target = sb3._manifest_path() if component == "manifest" else pair[component == "normalization"]
    target.write_bytes(b"corrupt")
    count = len(isolated)
    with pytest.raises(ValueError):
        sb3.load_evaluation_pair()
    assert len(isolated) == count


@pytest.mark.parametrize("when", ["sampling", "optimizing", "periodic-save"])
@pytest.mark.parametrize("save_interval", [0, 1])
def test_sigint_saves_complete_update_and_can_resume(tmp_path, monkeypatch, when, save_interval):
    created = fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=3, n_steps=4, n_epochs=2, mb_steps=2,
                reward="centered", save_interval=save_interval)
    previous_handler = signal.getsignal(signal.SIGINT)
    sent = []
    steps, updates = [], []

    def interrupt():
        if not sent:
            sent.append(True)
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)

    step = train.FastFarmSampler.step
    def sampling(sampler, action):
        result = step(sampler, action)
        steps.append(True)
        if when == "sampling" and len(steps) == 6:
            interrupt()
        return result
    monkeypatch.setattr(train.FastFarmSampler, "step", sampling)
    update = torch.optim.Adam.step
    def optimizing(optimizer, *a, **kw):
        result = update(optimizer, *a, **kw)
        updates.append(True)
        if when == "optimizing" and len(updates) == 5:
            interrupt()
        return result
    monkeypatch.setattr(torch.optim.Adam, "step", optimizing)
    save = train.save_checkpoint_exclusive
    def saving(payload, path):
        save(payload, path)
        if when == "periodic-save" and payload["iteration"] == 2:
            interrupt()
    monkeypatch.setattr(train, "save_checkpoint_exclusive", saving)
    if when == "periodic-save" and save_interval == 0:
        # Without periodic writes, request cancellation during the second rollout.
        when = "sampling"
    progress = tmp_path / "progress.jsonl"
    hist = train.train(**args, training_progress=progress)
    assert signal.getsignal(signal.SIGINT) == previous_handler
    assert sent and len(steps) == 8 and len(updates) == 8
    assert len(hist["reward"]) == 2 and hist["run"]["status"] == "interrupted"
    checkpoint = torch.load(hist["run"]["checkpoint"], weights_only=False)
    assert checkpoint["iteration"] == 2
    assert checkpoint["run"]["control_steps"] == checkpoint["recovery"]["control_steps"] == 8
    assert checkpoint["run"]["agent_steps"] == checkpoint["recovery"]["agent_steps"] == 16
    assert resume_counts(checkpoint) == (8, 16)
    assert checkpoint["obs_count"] == pytest.approx(16.0001)
    assert checkpoint["ret_count"] == pytest.approx(8.0001)
    assert len(checkpoint["recovery"]["reward_shaper"]["state"]["raw"]) == 8
    assert {state["step"].item() for state in checkpoint["optimizer"]["state"].values()} == {8}
    assert created[0].closed
    last = json.loads(progress.read_text().splitlines()[-1])
    assert (last["phase"], last["iteration"], last["step"], last["agent_step"]) == ("done", 2, 8, 16)
    resumed = train.train(**args, resume_from=hist["run"]["checkpoint"])
    assert resumed["run"]["status"] == "completed"
    assert resumed["run"]["control_steps"] == 12
    assert len(resumed["reward"]) == 3


def test_sigbreak_registers_cooperative_handler_and_restores_on_failure(tmp_path, monkeypatch):
    fake_training(monkeypatch, tmp_path)
    handlers = {signal.SIGINT: object(), 999: object()}
    original = handlers.copy()
    monkeypatch.setattr(signal, "SIGBREAK", 999, raising=False)
    monkeypatch.setattr(signal, "getsignal", handlers.__getitem__)
    monkeypatch.setattr(signal, "signal", lambda signum, handler: handlers.__setitem__(signum, handler))
    step = train.FastFarmSampler.step
    def stop(sampler, action):
        handlers[999](999, None)
        return step(sampler, action)
    monkeypatch.setattr(train.FastFarmSampler, "step", stop)
    def close(sampler):
        raise RuntimeError("cleanup failed")
    monkeypatch.setattr(train.FastFarmSampler, "close", close)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        train.train("fake", 3, 4, n_epochs=1, save_interval=0)
    assert handlers == original
    checkpoint = torch.load(next((tmp_path / "ckpt").glob("*.pt")), weights_only=False)
    assert checkpoint["iteration"] == 1
    assert checkpoint["run"]["status"] == "interrupted"
    assert checkpoint["recovery"]["control_steps"] == 4


@pytest.mark.parametrize("progress_enabled", [False, True])
@pytest.mark.parametrize("field", ["step", "agent_step", "malformed", "boolean"])
def test_resume_progress_mismatch_rejected_before_backend(tmp_path, monkeypatch, progress_enabled, field):
    created = fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=2, n_steps=4, n_epochs=1, save_interval=1)
    train.train(**args)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    checkpoint["training_progress"] = {"step": 4, "agent_step": 8}
    if field == "malformed":
        checkpoint["training_progress"] = []
    elif field == "boolean":
        checkpoint["training_progress"]["step"] = True
    else:
        checkpoint["training_progress"][field] += 1
    torch.save(checkpoint, path)
    progress = tmp_path / "rejected.jsonl"
    with pytest.raises(ValueError, match="Observability resume"):
        train.train(**args, resume_from=path, training_progress=progress if progress_enabled else None)
    assert len(created) == 1
    assert not progress.exists()


@pytest.mark.parametrize("record", ["recovery", "run"])
def test_resume_counts_cross_checks_each_recovery_record(record):
    checkpoint = dict(iteration=2, training_progress=dict(step=8, agent_step=16),
                      recovery=dict(control_steps=8, agent_steps=16),
                      run=dict(control_steps=8, agent_steps=16))
    assert resume_counts(checkpoint) == (8, 16)
    inconsistent = copy.deepcopy(checkpoint)
    inconsistent[record]["agent_steps"] += 2
    with pytest.raises(ValueError, match=f"disagree with {record}"):
        resume_counts(inconsistent)

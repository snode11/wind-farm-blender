"""Training provenance, recurrent state, and software recovery without physics."""
import copy
import json
import random

import numpy as np
import pytest
import torch

from scripts.train import train_fastfarm as trainer


@pytest.mark.parametrize("length,rollout", [(2, 5), (7, 3), (5, 3)])
def test_recurrent_sequence_matches_stepwise_across_rollouts_and_episodes(length, rollout):
    torch.manual_seed(9)
    critic = trainer.RecurrentCritic(3, hidden=7)
    states = torch.randn(1, 17, 3)
    starts = torch.tensor([[i % length == 0 for i in range(17)]])
    with torch.no_grad():
        expected, _ = critic(states, episode_starts=starts)
        hidden = None
        pieces = []
        for first in range(0, 17, rollout):
            last = min(first + rollout, 17)
            actual, hidden = critic(states[:, first:last], hidden,
                                    episode_starts=starts[:, first:last])
            pieces.append(actual)
            hidden = hidden.detach()
        torch.testing.assert_close(torch.cat(pieces, 1), expected)
        hidden = None
        values = []
        for step in range(17):
            value, hidden = critic(states[:, step:step + 1], hidden,
                                   episode_starts=starts[:, step:step + 1])
            values.append(value)
        torch.testing.assert_close(torch.cat(values, 1), expected)
        isolated, _ = critic(states[:, length:2 * length])
        torch.testing.assert_close(expected[:, length:2 * length], isolated)


@pytest.mark.parametrize("change", [dict(warmup_steps=3), dict(load_coef=.4),
    dict(n_steps=5), dict(iters=4), dict(reward_alpha=.04), dict(n_epochs=2),
    dict(mb_steps=3), dict(gamma=.8), dict(obs_duty=True), dict(policy="zero"),
    dict(recurrent=True), dict(duty_penalty=.2), dict(seed=4)])
def test_effective_config_distinguishes_training_settings(change):
    base = dict(env_id="fake", iters=2, n_steps=4, wind_sched="pin:8")
    other = dict(base, **change)
    assert trainer.experiment_config(**base) != trainer.experiment_config(**other)


def test_effective_config_normalizes_defaults_and_wind_alias():
    assert trainer.experiment_config("fake", 2, 4, wind_speed=8) == trainer.experiment_config(
        "fake", 2, 4, wind_sched="pin:8", warmup_steps=8, n_epochs=10)


def fake_training(monkeypatch, tmp_path, episode_steps=3):
    created = []

    class Sampler:
        n = 2; obs_dim = 3; act_dim = 1; state_dim = 6
        act_low = -1.; act_high = 1.; u_inf = 8.; n_spawns = 1
        obs_keys = ["yaw", "wind_speed", "wind_direction"]
        obs_duty = False; duty_penalty = 0.

        def __init__(self, *args, **kwargs):
            self.shaper = kwargs["reward_shaper"]
            self.requested_total_steps = kwargs["total_steps"]
            self.episode = kwargs.get("start_episode", 0)
            self.initial_episode = self.episode
            self.initial_shaper = copy.deepcopy(vars(self.shaper))
            self.initial_rng = (random.random(), np.random.random(), torch.rand(1))
            self._t = 0
            self.ep_steps = episode_steps
            self.closed = False
            created.append(self)

        def obs(self):
            return np.full((2, 3), self.episode + self._t / 10, np.float32)

        def step(self, action):
            self._t += 1
            reward = self.shaper(float(self.episode + self._t + 1))
            return self.obs(), reward, self._t >= self.ep_steps, dict(power=[2., 4.])

        def new_episode(self):
            self.episode += 1
            self._t = 0
            self.n_spawns += 1
            return self.obs()

        def close(self): self.closed = True

    monkeypatch.setattr(trainer, "FastFarmSampler", Sampler)
    monkeypatch.setattr(trainer, "CKPT_DIR", str(tmp_path / "ckpt"))
    monkeypatch.setattr(trainer, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(trainer, "_plot_curves", lambda *args: None)
    monkeypatch.setattr(trainer, "source_input_identity", lambda *args, **kwargs: "source-fixture")
    return created


def test_full_artifacts_are_distinct_and_completed_budget_is_verified(monkeypatch, tmp_path):
    from wfrl.training_integrity import find_completed_run
    fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=1, n_steps=4, n_epochs=1, warmup_steps=0,
                episode_steps=3, wind_sched="pin:8", reward="centered")
    first = trainer.train(**args)
    second = trainer.train(**args)
    assert first["run"]["run_id"] != second["run"]["run_id"]
    assert first["run"]["experiment_id"] == second["run"]["experiment_id"]
    assert len(list((tmp_path / "runs").glob("*_hist.json"))) == 2
    config = trainer.experiment_config(**args)
    found = find_completed_run(tmp_path / "runs", config, "source-fixture")
    assert found is not None
    history = json.loads(found.read_text())
    checkpoint = history["hist"]["run"]["checkpoint"]
    original = torch.load(checkpoint, weights_only=False)
    broken = copy.deepcopy(original)
    broken["recovery"]["control_steps"] -= 1
    torch.save(broken, checkpoint)
    # The second independent completed run remains reusable.
    assert find_completed_run(tmp_path / "runs", config, "source-fixture") != found
    for path in (tmp_path / "runs").glob("*_hist.json"):
        data = json.loads(path.read_text())
        data["hist"]["run"]["status"] = "running"
        path.write_text(json.dumps(data))
    assert find_completed_run(tmp_path / "runs", config, "source-fixture") is None


@pytest.mark.parametrize("recurrent", [False, True])
def test_resume_restores_software_state_in_explicit_new_episode_segment(monkeypatch, tmp_path, recurrent):
    created = fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=2, n_steps=4, n_epochs=1, warmup_steps=0,
                episode_steps=3, wind_sched="cycle:6,8,10", reward="centered", save_interval=1,
                recurrent=recurrent)
    trainer.train(**args)
    checkpoint_path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(checkpoint_path, weights_only=False)
    resumed = trainer.train(**args, resume_from=str(checkpoint_path))
    recovery = checkpoint["recovery"]
    assert created[-1].initial_episode == recovery["next_episode"] == 2
    assert created[-1].requested_total_steps == 4
    assert created[-1].initial_shaper == recovery["reward_shaper"]["state"]
    assert resumed["run"]["parent_run_id"] == checkpoint["run"]["run_id"]
    assert resumed["run"]["run_id"] != checkpoint["run"]["run_id"]
    assert resumed["run"]["recovery_mode"] == "new_episode_segment"
    assert resumed["run"]["control_steps"] == 8
    assert resumed["run"]["agent_steps"] == 16
    assert resumed["cfg"] == trainer.experiment_config(**args)
    random.setstate(recovery["rng"]["python"])
    np.random.set_state(recovery["rng"]["numpy"])
    torch.set_rng_state(recovery["rng"]["torch"])
    expected = (random.random(), np.random.random(), torch.rand(1))
    actual = created[-1].initial_rng
    assert actual[:2] == expected[:2]
    torch.testing.assert_close(actual[2], expected[2])
    bad_args = dict(args, reward_alpha=.9)
    with pytest.raises(ValueError, match="configuration"):
        trainer.train(**bad_args, resume_from=str(checkpoint_path))
    assert len(created) == 2


def test_legacy_resume_without_recovery_is_rejected_before_sampler(monkeypatch, tmp_path):
    created = fake_training(monkeypatch, tmp_path)
    trainer.train("fake", 1, 4, n_epochs=1, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    checkpoint.pop("recovery")
    torch.save(checkpoint, path)
    with pytest.raises(ValueError, match="recovery"):
        trainer.train("fake", 2, 4, n_epochs=1, resume_from=str(path))
    assert len(created) == 1


def test_truncation_bootstrap_is_pre_reset_but_gae_chain_stops():
    advantage, _ = trainer.compute_gae_trunc(
        np.array([1., 2.]), np.array([3., 4.]), np.array([8., 9.]),
        np.array([1., 0.]), gamma=.5, lam=1.)
    np.testing.assert_allclose(advantage, [2., 2.5])


@pytest.mark.parametrize("length,rollout", [(2, 5), (7, 3), (5, 3)])
def test_training_recurrent_update_values_match_sampling_with_frozen_weights(monkeypatch, tmp_path, length, rollout):
    fake_training(monkeypatch, tmp_path, episode_steps=length)
    original = trainer.RecurrentCritic
    observations = []
    sequence_values = []

    class RecordingCritic(original):
        def forward(self, states, h0=None, episode_starts=None):
            value, hidden = super().forward(states, h0, episode_starts)
            if states.shape[1] == 1:
                observations.append(value.detach().clone())
            else:
                sequence_values.append(value.detach().clone())
            return value, hidden

    monkeypatch.setattr(trainer, "RecurrentCritic", RecordingCritic)
    trainer.train("fake", 3, rollout, n_epochs=1, lr=0., recurrent=True,
                  episode_steps=length, warmup_steps=0, wind_sched="pin:8")
    sampled = torch.cat(observations[::2], 1)
    fitted = torch.cat(sequence_values, 1)
    torch.testing.assert_close(fitted, sampled)


@pytest.mark.parametrize("policy", ["learn", "zero"])
def test_completed_baseline_and_learning_runs_are_reusable(monkeypatch, tmp_path, policy):
    from wfrl.training_integrity import find_completed_run
    fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=1, n_steps=4, n_epochs=1, policy=policy)
    trainer.train(**args)
    assert find_completed_run(tmp_path / "runs", trainer.experiment_config(**args), "source-fixture")


@pytest.mark.parametrize("damage", ["empty-history", "underfilled", "overfilled", "missing-checkpoint",
                                   "empty-checkpoint", "corrupt-checkpoint", "wrong-source", "wrong-status",
                                   "wrong-checkpoint-run", "changed-weights"])
def test_completion_requires_untampered_belonging_artifacts(monkeypatch, tmp_path, damage):
    from wfrl.training_integrity import find_completed_run
    fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=1, n_steps=4, n_epochs=1)
    hist = trainer.train(**args)
    config = trainer.experiment_config(**args)
    path = find_completed_run(tmp_path / "runs", config, "source-fixture")
    assert path
    document = json.loads(path.read_text())
    ckpt_path = document["hist"]["run"]["checkpoint"]
    if damage == "empty-history": document["hist"] = {}
    elif damage == "underfilled": document["hist"]["reward"] = []
    elif damage == "overfilled": document["hist"]["reward"].append(0.)
    elif damage == "missing-checkpoint": __import__("pathlib").Path(ckpt_path).unlink()
    elif damage == "empty-checkpoint": __import__("pathlib").Path(ckpt_path).write_bytes(b"")
    elif damage == "corrupt-checkpoint": __import__("pathlib").Path(ckpt_path).write_bytes(b"invalid bytes")
    elif damage == "wrong-source": document["hist"]["run"]["source_identity"] = "other"
    elif damage == "wrong-status": document["hist"]["run"]["status"] = "running"
    else:
        checkpoint = torch.load(ckpt_path, weights_only=False)
        if damage == "wrong-checkpoint-run": checkpoint["run"]["run_id"] = "unrelated"
        else:
            key = next(iter(checkpoint["actor"]))
            checkpoint["actor"][key].add_(.25)
        torch.save(checkpoint, ckpt_path)
    path.write_text(json.dumps(document))
    assert find_completed_run(tmp_path / "runs", config, "source-fixture") is None


def test_exclusive_saves_preserve_existing_artifact(tmp_path):
    from wfrl.training_integrity import save_checkpoint_exclusive
    target = tmp_path / "protected.pt"
    target.write_bytes(b"existing evidence")
    with pytest.raises(FileExistsError): save_checkpoint_exclusive({"new": "payload"}, target)
    assert target.read_bytes() == b"existing evidence"


def test_source_and_input_content_change_identity(tmp_path, monkeypatch):
    from wfrl.training_integrity import source_input_identity
    import importlib.util
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "wfcrl" else original(name))
    (tmp_path / "wfrl").mkdir()
    (tmp_path / "inputs").mkdir()
    module = tmp_path / "wfrl" / "training.py"
    inputs = tmp_path / "inputs" / "wind.dat"
    module.write_text("setting = 1\n")
    inputs.write_text("wind = 8\n")
    first = source_input_identity(tmp_path)
    module.write_text("setting = 2\n")
    second = source_input_identity(tmp_path)
    inputs.write_text("wind = 9\n")
    third = source_input_identity(tmp_path)
    assert len({first, second, third}) == 3


def test_batch_config_planner_and_completion_check_do_not_trust_legacy_label(monkeypatch, tmp_path, capsys):
    from scripts.train import run_stage3 as batch
    monkeypatch.setattr(batch.paths, "RUNS", str(tmp_path))
    monkeypatch.setattr(batch, "source_input_identity", lambda: "source-fixture")
    # A valid-looking legacy name with no provenance must not skip either policy.
    (tmp_path / "fastfarm_Dec_Turb3_Row1_Fastfarm_zero_s0_level_E128_pin8_stage3_hist.json").write_text("{}")
    monkeypatch.setattr("sys.argv", ["run_stage3.py", "--seeds", "0", "--dry-run"])
    assert batch.main() == 0
    assert "跳过" not in capsys.readouterr().out


def test_recovery_segment_cannot_satisfy_fresh_batch_completion(monkeypatch, tmp_path):
    from wfrl.training_integrity import find_completed_run
    fake_training(monkeypatch, tmp_path)
    args = dict(env_id="fake", iters=2, n_steps=4, n_epochs=1, save_interval=1)
    trainer.train(**args)
    checkpoint = next((tmp_path / "ckpt").glob("*iter1.pt"))
    for path in (tmp_path / "runs").glob("*_hist.json"): path.unlink()
    trainer.train(**args, resume_from=str(checkpoint))
    assert find_completed_run(tmp_path / "runs", trainer.experiment_config(**args), "source-fixture") is None


def test_numeric_trainer_import_does_not_load_simulator_packages():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", """
import sys
sys.modules['wfcrl'] = None
sys.modules['pettingzoo'] = None
sys.modules['mpi4py'] = None
from scripts.train.train_fastfarm import RecurrentCritic, experiment_config
assert experiment_config('fake', 1, 4)['n_steps'] == 4
assert 'wfrl.fastfarm_driver' not in sys.modules
assert RecurrentCritic(3).gru.input_size == 3
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_step_reward_formula_and_recovery_state():
    from wfrl.rewards import make_shaper
    from wfrl.training_integrity import shaper_state, restore_shaper
    shaper = make_shaper("step")
    assert [shaper(value) for value in (4., 6., 3.)] == [0., .5, -.5]
    state = shaper_state(shaper)
    restored = make_shaper("step")
    restore_shaper(restored, state)
    assert restored(6.) == 1.
    restored.reset()
    assert restored(8.) == 0.


@pytest.mark.parametrize("damage", ["source", "history-budget", "counter", "rng", "shaper"])
def test_invalid_recovery_rejected_before_sampler(monkeypatch, tmp_path, damage):
    created = fake_training(monkeypatch, tmp_path)
    trainer.train("fake", 2, 4, n_epochs=1, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    if damage == "source": checkpoint["run"]["source_identity"] = "unrelated"
    elif damage == "history-budget": checkpoint["history"]["reward"].append(1.)
    elif damage == "counter": checkpoint["recovery"]["agent_steps"] += 2
    elif damage == "rng": checkpoint["recovery"]["rng"] = {"python": "invalid"}
    else: checkpoint["recovery"]["reward_shaper"]["class"] = "unrelated"
    torch.save(checkpoint, path)
    with pytest.raises(ValueError):
        trainer.train("fake", 2, 4, n_epochs=1, resume_from=str(path))
    assert len(created) == 1


def test_scene_config_uses_scene_wind_and_content(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("wfrl.scene.schema.load_scene", lambda _: SimpleNamespace(
        to_dict=lambda: {"backend": "fastfarm", "wind": {"speed": 9.}}))
    config = trainer.experiment_config("fake", 2, 4, scene="scene.yaml", wind_speed=15.)
    assert config["wind_sched"] == "scene"
    assert config["scene"]["wind"]["speed"] == 9.


def test_external_scene_wind_input_content_changes_identity(tmp_path, monkeypatch):
    from wfrl.training_integrity import source_input_identity
    import importlib.util
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "wfcrl" else original(name))
    wind = tmp_path.parent / (tmp_path.name + "-external-wind.bts")
    wind.write_bytes(b"wind-profile-a")
    scene = tmp_path / "scene.yaml"
    scene.write_text("inflow:\n  turbulence: " + str(wind) + "\n")
    first = source_input_identity(tmp_path, scene)
    wind.write_bytes(b"wind-profile-b")
    assert source_input_identity(tmp_path, scene) != first


def test_recovery_next_episode_index_is_validated(monkeypatch, tmp_path):
    created = fake_training(monkeypatch, tmp_path)
    trainer.train("fake", 2, 4, n_epochs=1, episode_steps=3, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    checkpoint["recovery"]["next_episode"] += 1
    torch.save(checkpoint, path)
    with pytest.raises(ValueError, match="episode index"):
        trainer.train("fake", 2, 4, n_epochs=1, episode_steps=3, resume_from=str(path))
    assert len(created) == 1


@pytest.mark.parametrize("payload", [[], None, {"hist": []}, {"hist": {"run": []}}, {"hist": None}])
def test_malformed_partial_history_is_not_completed(tmp_path, payload):
    from wfrl.training_integrity import find_completed_run
    (tmp_path / "partial_hist.json").write_text(json.dumps(payload))
    config = trainer.experiment_config("fake", 1, 4)
    assert find_completed_run(tmp_path, config, "source-fixture") is None


@pytest.mark.parametrize("damage", [
    "actor-empty", "actor-shape", "actor-nan", "critic-empty", "critic-shape", "critic-inf",
    "optimizer-empty", "optimizer-shape", "optimizer-nan", "optimizer-negative-moment",
    "optimizer-step", "optimizer-hyperparameter", "obs-mean-shape", "obs-mean-nan",
    "obs-var-shape", "obs-var-negative", "obs-count-zero", "obs-count-inf",
    "ret-mean-shape", "ret-mean-inf", "ret-var-negative", "ret-count-nan",
    "shaper-missing", "shaper-ema-nan", "shaper-records-nan",
])
def test_software_recovery_corruption_rejected_before_backend(monkeypatch, tmp_path, damage):
    created = fake_training(monkeypatch, tmp_path)
    arguments = dict(env_id="fake", iters=2, n_steps=4, n_epochs=1, reward="centered")
    trainer.train(**arguments, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    if damage.startswith(("actor-", "critic-")):
        model, fault = damage.split("-")
        key = next(iter(checkpoint[model]))
        if fault == "empty": checkpoint[model] = {}
        elif fault == "shape": checkpoint[model][key] = torch.zeros(23, 19)
        else: checkpoint[model][key].fill_(float(fault))
    elif damage.startswith("optimizer-"):
        optimizer = checkpoint["optimizer"]
        state = next(iter(optimizer["state"].values()))
        if damage == "optimizer-empty": checkpoint["optimizer"] = {}
        elif damage == "optimizer-shape": state["exp_avg"] = torch.zeros(23, 19)
        elif damage == "optimizer-nan": state["exp_avg"].fill_(float("nan"))
        elif damage == "optimizer-negative-moment": state["exp_avg_sq"].fill_(-1.)
        elif damage == "optimizer-step": state["step"] = torch.tensor(float("nan"))
        else: optimizer["param_groups"][0]["lr"] = .99
    elif damage.startswith("shaper-"):
        state = checkpoint["recovery"]["reward_shaper"]["state"]
        if damage == "shaper-missing": state.pop("ema")
        elif damage == "shaper-ema-nan": state["ema"] = float("nan")
        else: state["raw"].append(float("nan"))
    else:
        field, fault = damage.rsplit("-", 1)
        field = field.replace("-", "_")
        if fault == "shape": checkpoint[field] = np.zeros(19)
        elif fault == "negative": checkpoint[field] = np.full_like(checkpoint[field], -1.)
        elif fault == "zero": checkpoint[field] = 0.
        else: checkpoint[field] = np.full_like(checkpoint[field], float(fault))
    torch.save(checkpoint, path)
    with pytest.raises(ValueError):
        trainer.train(**arguments, resume_from=str(path))
    assert len(created) == 1, "corrupt software state must be rejected before constructing the backend"


def test_corrupt_software_cannot_construct_real_sampler_driver(monkeypatch, tmp_path):
    real_sampler = trainer.FastFarmSampler
    fake_training(monkeypatch, tmp_path)
    trainer.train("fake", 2, 4, n_epochs=1, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    checkpoint["actor"] = {}
    torch.save(checkpoint, path)
    driver_calls = []

    def forbidden_driver(*args, **kwargs):
        driver_calls.append((args, kwargs))
        raise AssertionError("invalid recovery reached physics construction")

    monkeypatch.setattr(trainer, "FastFarmSampler", real_sampler)
    monkeypatch.setattr(trainer, "FastFarmDriver", forbidden_driver)
    with pytest.raises(ValueError, match="actor/critic"):
        trainer.train("fake", 2, 4, n_epochs=1, resume_from=str(path))
    assert driver_calls == []


def test_recovered_software_dimensions_are_checked_against_live_sampler(monkeypatch, tmp_path):
    created = fake_training(monkeypatch, tmp_path)
    trainer.train("fake", 2, 4, n_epochs=1, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    original = trainer.FastFarmSampler

    class ChangedRuntimeSampler(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.state_dim += 3

    monkeypatch.setattr(trainer, "FastFarmSampler", ChangedRuntimeSampler)
    with pytest.raises(ValueError, match="sampler dimensions"):
        trainer.train("fake", 2, 4, n_epochs=1, resume_from=str(path))
    assert created[-1].closed


@pytest.mark.parametrize("reward,field,value", [
    ("step", "reference", None), ("centered", "ema", None),
    ("centered", "raw", [[1.], [2.]]), ("centered", "shaped", [[1.], [2.]]),
    ("step", "reference", "3"), ("centered", "ema", "3"),
    ("centered", "raw", ["1", "2"]), ("centered", "shaped", ["1", "2"]),
])
def test_shaper_structural_faults_rejected_before_backend(monkeypatch, tmp_path, reward, field, value):
    created = fake_training(monkeypatch, tmp_path)
    trainer.train("fake", 2, 4, n_epochs=1, reward=reward, save_interval=1)
    path = next((tmp_path / "ckpt").glob("*iter1.pt"))
    checkpoint = torch.load(path, weights_only=False)
    state = checkpoint["recovery"]["reward_shaper"]["state"]
    state[field] = value
    if field in {"raw", "shaped"}:
        other = "raw" if field == "shaped" else "shaped"
        state[other] = [1., 2.]
    torch.save(checkpoint, path)
    with pytest.raises(ValueError):
        trainer.train("fake", 2, 4, n_epochs=1, reward=reward, resume_from=str(path))
    assert len(created) == 1

"""Episode-boundary bootstrap tests using fixed values and real SB3 rollouts."""

from types import MethodType

import numpy as np
import pytest
import torch
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import VecNormalize

from wfrl import mappo_central as central
from wfrl import mappo_sb3 as sb3


class _ParallelEnv:
    possible_agents = ["t1", "t2"]
    render_mode = None

    def __init__(self, terminated=(False, False), truncated=(True, True), horizon=1):
        self.terminated = terminated
        self.truncated = truncated
        self.horizon = horizon
        self.resets = 0
        self.steps = 0
        self.info = {a: {"power": 3.0} for a in self.possible_agents}

    def observation_space(self, agent):
        return {"yaw": spaces.Box(-100.0, 100.0, (1,), dtype=np.float32)}

    def action_space(self, agent):
        return {"yaw": spaces.Box(-1.0, 1.0, (1,), dtype=np.float32)}

    def _obs(self, value):
        return {a: {"yaw": np.array([value], np.float32)} for a in self.possible_agents}

    def reset(self):
        self.steps = 0
        self.resets += 1
        return self._obs(1), {a: {"episode": self.resets} for a in self.possible_agents}

    def step(self, actions):
        self.steps += 1
        at_boundary = self.steps == self.horizon
        return (self._obs(9), {a: 1.0 for a in self.possible_agents},
                {a: at_boundary and self.terminated[i] for i, a in enumerate(self.possible_agents)},
                {a: at_boundary and self.truncated[i] for i, a in enumerate(self.possible_agents)},
                self.info)

    def close(self):
        pass


class _MultimodalVecEnv(sb3.MAParallelVecEnv):
    def _pack_obs(self, obs_dict):
        obs = super()._pack_obs(obs_dict)
        obs["vibration"] = obs["yaw"] * 2
        obs["wake_image"] = np.broadcast_to(obs["yaw"][:, :, None], (2, 2, 2)).copy()
        return obs


@pytest.mark.parametrize("terminated, truncated, expected_timeout", [
    ((False, False), (True, True), [True, True]),
    ((True, True), (False, False), [False, False]),
    ((True, True), (True, True), [False, False]),
    ((True, False), (False, True), [False, True]),
])
def test_vecenv_copies_all_terminal_fields_before_reset(terminated, truncated, expected_timeout):
    par = _ParallelEnv(terminated, truncated)
    venv = _MultimodalVecEnv(par)
    venv.reset()
    obs, _, done, infos = venv.step(np.zeros((2, 1), np.float32))

    np.testing.assert_array_equal(done, [True, True])
    assert par.resets == 2
    assert [info["TimeLimit.truncated"] for info in infos] == expected_timeout
    for i, info in enumerate(infos):
        terminal = info["terminal_observation"]
        assert set(terminal) == {"yaw", "vibration", "wake_image"}
        np.testing.assert_array_equal(terminal["yaw"], [9])
        np.testing.assert_array_equal(terminal["vibration"], [18])
        np.testing.assert_array_equal(terminal["wake_image"], np.full((2, 2), 9))
        assert info["power"] == 3
        assert "terminal_observation" not in par.info[par.possible_agents[i]]
        for key in obs:
            assert not np.shares_memory(terminal[key], obs[key])
    np.testing.assert_array_equal(obs["yaw"], [[1], [1]])
    assert venv.reset_infos == [{"episode": 2}, {"episode": 2}]


def test_vecenv_continuing_transition_has_no_terminal_state_or_reset():
    par = _ParallelEnv(horizon=2)
    venv = sb3.MAParallelVecEnv(par)
    venv.reset()
    obs, _, done, infos = venv.step(np.zeros((2, 1), np.float32))
    assert not done.any()
    assert par.resets == 1
    np.testing.assert_array_equal(obs["yaw"], [[9], [9]])
    assert all(not info["TimeLimit.truncated"] for info in infos)
    assert all("terminal_observation" not in info for info in infos)


def _collect_sb3_rollout(par):
    venv = VecNormalize(sb3.MAParallelVecEnv(par), training=False,
                        norm_obs=True, norm_reward=False)
    venv.obs_rms["yaw"].mean[:] = 1
    venv.obs_rms["yaw"].var[:] = 4
    model = PPO("MultiInputPolicy", venv, n_steps=2, batch_size=4,
                n_epochs=1, gamma=0.99, gae_lambda=0.95,
                policy_kwargs={"net_arch": [4]})

    def values(self, obs):
        return obs["yaw"] + 6

    def forward(self, obs, deterministic=False):
        return torch.zeros_like(obs["yaw"]), values(self, obs), torch.zeros(len(obs["yaw"]))

    model.policy.predict_values = MethodType(values, model.policy)
    model.policy.forward = MethodType(forward, model.policy)
    _, callback = model._setup_learn(total_timesteps=4)
    try:
        assert model.collect_rollouts(venv, callback, model.rollout_buffer, n_rollout_steps=2)
        return model.rollout_buffer
    finally:
        venv.close()


@pytest.mark.parametrize("terminated, truncated, expected", [
    ((False, False), (True, True), [10.9, 10.9]),
    ((True, True), (False, False), [1.0, 1.0]),
    ((True, True), (True, True), [1.0, 1.0]),
    ((True, False), (False, True), [1.0, 10.9]),
])
def test_sb3_algorithm_bootstraps_normalized_terminal_observation(terminated, truncated, expected):
    buffer = _collect_sb3_rollout(_ParallelEnv(terminated, truncated))
    np.testing.assert_allclose(buffer.rewards, [expected, expected], rtol=1e-6)
    np.testing.assert_allclose(buffer.returns, [expected, expected], rtol=1e-6)
    np.testing.assert_array_equal(buffer.episode_starts, np.ones((2, 2)))


def test_sb3_algorithm_uses_last_value_at_ordinary_rollout_boundary():
    buffer = _collect_sb3_rollout(_ParallelEnv(horizon=3))
    np.testing.assert_allclose(buffer.returns, [[11.74645, 11.74645], [10.9, 10.9]], rtol=1e-6)
    np.testing.assert_array_equal(buffer.episode_starts, [[1, 1], [0, 0]])


def test_sb3_algorithm_does_not_propagate_next_episode_rewards():
    class DifferentEpisodeRewards(_ParallelEnv):
        def step(self, actions):
            obs, reward, term, trunc, info = super().step(actions)
            reward = {a: 1.0 if self.resets == 1 else 100.0 for a in reward}
            return obs, reward, term, trunc, info

    buffer = _collect_sb3_rollout(DifferentEpisodeRewards())
    np.testing.assert_allclose(buffer.returns, [[10.9, 10.9], [109.9, 109.9]], rtol=1e-6)


def _sampler(monkeypatch, par):
    monkeypatch.setattr(central.envs, "make", lambda *args, **kwargs: par)
    monkeypatch.setattr(central, "aec_to_parallel", lambda env: env)
    return central.Sampler()


@pytest.mark.parametrize("terminated, truncated", [(False, True), (True, False), (True, True)])
def test_central_sampler_preserves_pre_reset_observation(monkeypatch, terminated, truncated):
    par = _ParallelEnv((terminated, terminated), (truncated, truncated))
    sampler = _sampler(monkeypatch, par)
    sampler.reset()
    obs, reward, done, info = sampler.step(np.zeros((2, 1)), return_info=True)
    assert done
    assert reward == 1
    assert info["terminated"] is terminated
    assert info["truncated"] is truncated
    np.testing.assert_array_equal(obs, [[1], [1]])
    np.testing.assert_array_equal(info["terminal_observation"], [[9], [9]])
    obs[:] = 50
    np.testing.assert_array_equal(info["terminal_observation"], [[9], [9]])
    assert len(sampler.step(np.zeros((2, 1)))) == 3


def test_central_team_termination_uses_all_agents(monkeypatch):
    sampler = _sampler(monkeypatch, _ParallelEnv((False, True), (True, False)))
    sampler.reset()
    _, _, done, info = sampler.step(np.zeros((2, 1)), return_info=True)
    assert done
    assert info["terminated"] is True
    assert info["truncated"] is True


@pytest.mark.parametrize("wrapper", ["sb3", "central"])
def test_shared_farm_rejects_unsynchronized_episode_boundaries(monkeypatch, wrapper):
    par = _ParallelEnv(truncated=(True, False))
    sampler = _sampler(monkeypatch, par) if wrapper == "central" else sb3.MAParallelVecEnv(par)
    sampler.reset()
    with pytest.raises(ValueError, match="synchronized episode boundaries"):
        sampler.step(np.zeros((2, 1)))


@pytest.mark.parametrize("done, terminated, terminal_value, last_v, expected", [
    (True, False, 10.0, 999.0, 10.9),
    (True, True, 10.0, 999.0, 1.0),
    (True, True, np.nan, np.nan, 1.0),
    (False, False, np.nan, 10.0, 10.9),
])
def test_gae_separates_bootstrap_and_recursion_masks(done, terminated, terminal_value, last_v, expected):
    adv, ret = central.compute_gae([1.0], [10.0], [done], last_v,
                                   terminated=[terminated], terminal_values=[terminal_value])
    np.testing.assert_allclose(ret, [expected], atol=1e-6)
    np.testing.assert_allclose(adv, [expected - 10], atol=1e-6)


def test_gae_keeps_continuing_transitions_and_cuts_between_episodes():
    args = dict(vals=[5.0, 6.0, 20.0], dones=[False, True, False], last_v=30.0,
                terminated=[False, False, False], terminal_values=[0.0, 10.0, 0.0])
    adv, ret = central.compute_gae([1.0, 2.0, 100.0], **args)
    np.testing.assert_allclose(ret, [12.48895, 11.9, 129.7], rtol=1e-6)
    changed_adv, changed_ret = central.compute_gae([1.0, 2.0, 1000.0], **args)
    np.testing.assert_array_equal(ret[:2], changed_ret[:2])
    np.testing.assert_array_equal(adv[:2], changed_adv[:2])


def test_gae_handles_multiple_terminal_and_truncated_episodes():
    _, ret = central.compute_gae([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], [True, True, True], 999,
                                 terminated=[False, True, False], terminal_values=[10.0, np.nan, 40.0])
    np.testing.assert_allclose(ret, [10.9, 2.0, 42.6], atol=1e-5)


def test_gae_legacy_terminal_call_keeps_its_signature():
    _, ret = central.compute_gae([1.0], [10.0], [True], 999.0, 0.99, 0.95)
    np.testing.assert_array_equal(ret, [1.0])


@pytest.mark.parametrize("kwargs, message", [
    ({"terminated": [False]}, "require pre-reset"),
    ({"terminated": [False], "terminal_values": [np.nan]}, "finite terminal_values"),
    ({"terminated": [False], "terminal_values": [1, 2]}, "matching finite"),
    ({"terminated": [True, False]}, "matching 1D"),
])
def test_gae_rejects_missing_or_invalid_terminal_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        central.compute_gae([1.0], [10.0], [True], 10, **kwargs)


def test_central_training_uses_terminal_state_and_matching_normalization(monkeypatch, tmp_path):
    class DistinctStates(_ParallelEnv):
        def _obs(self, value):
            return {a: {"yaw": np.array([value + 2 * i], np.float32)}
                    for i, a in enumerate(self.possible_agents)}

        def step(self, actions):
            _, reward, term, trunc, info = super().step(actions)
            return self._obs(9 if self.steps == 2 else 5), reward, term, trunc, info

    normalized = []

    class RecordingRMS(central.RunningMeanStd):
        def norm(self, obs):
            result = super().norm(obs)
            normalized.append((obs.copy(), result.copy(), self.count))
            return result

    class StateValue(torch.nn.Module):
        def __init__(self, state_dim):
            super().__init__()
            self.bias = torch.nn.Parameter(torch.tensor(10.0))

        def forward(self, state):
            return self.bias + state.sum(-1) * 0.1

    captured = {}
    actual_gae = central.compute_gae

    def gae(rews, vals, dones, last_v, gamma, lam, **kwargs):
        captured.update(rews=rews.copy(), vals=vals.copy(), dones=dones.copy(), last_v=last_v,
                        terminated=kwargs["terminated"].copy(),
                        terminal_values=kwargs["terminal_values"].copy())
        return actual_gae(rews, vals, dones, last_v, gamma, lam, **kwargs)

    par = DistinctStates(horizon=2)
    _sampler(monkeypatch, par)
    monkeypatch.setattr(central, "RunningMeanStd", RecordingRMS)
    monkeypatch.setattr(central, "Critic", StateValue)
    monkeypatch.setattr(central, "compute_gae", gae)
    monkeypatch.setattr(central, "CKPT_DIR", str(tmp_path))
    monkeypatch.setattr(central, "MODEL_PATH", str(tmp_path / "central.pt"))
    central.train(6, n_steps=3, n_epochs=1, mb_steps=3)

    np.testing.assert_array_equal(captured["dones"], [False, True, False])
    np.testing.assert_array_equal(captured["terminated"], [False, False, False])
    np.testing.assert_array_equal(normalized[2][0], [[9], [11]])
    np.testing.assert_array_equal(normalized[3][0], [[1], [3]])
    assert normalized[2][2] == normalized[3][2]
    expected_terminal = 10 + normalized[2][1].sum() * 0.1
    assert captured["terminal_values"][1] == pytest.approx(expected_terminal, abs=1e-6)
    assert captured["last_v"] == pytest.approx(10 + normalized[4][1].sum() * 0.1, abs=1e-6)
    _, ret = actual_gae(**captured)
    assert ret[1] == pytest.approx(captured["rews"][1] + 0.99 * expected_terminal, abs=1e-6)

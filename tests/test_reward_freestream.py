"""Rewards normalize powers by the freestream measured in the same simulator exchange."""
import copy

import numpy as np
import pytest

from wfcrl import environments as envs
from wfcrl.rewards import DoNothingReward

POWERS_MW = np.array([2.0, 1.0, 0.5])
NEXT_SPEED = 10.0


@pytest.fixture(autouse=True)
def _case_directory(tmp_path, monkeypatch):
    """WFCRL writes generated cases under the working directory's __simul__."""
    monkeypatch.chdir(tmp_path)


def _with_changed_inflow(env):
    """Return the next state with a new freestream and fixed powers, as a gust would."""
    def take_action(state, joint_action):
        next_state = copy.deepcopy(state)
        next_state["freewind_measurements"] = np.array([NEXT_SPEED, 270.0])
        return next_state, POWERS_MW.copy(), None, False
    env.mdp.take_action = take_action
    return env


def test_multiagent_reward_uses_same_step_freestream():
    env = envs.make("Dec_Turb3_Row1_Floris", controls=["yaw"], max_num_steps=4,
                    reward_shaper=DoNothingReward())
    env.reset(options={"wind_speed": 8.0, "wind_direction": 270.0})
    _with_changed_inflow(env)
    for agent in env.agents:
        env.step({"yaw": np.zeros(1, np.float32)})
    expected = (POWERS_MW * 1e3 / NEXT_SPEED ** 3).mean()
    assert env.rewards[env.agents[0]] == pytest.approx(expected)


def test_single_agent_reward_uses_same_step_freestream():
    env = envs.make("Turb3_Row1_Floris", controls=["yaw"], max_num_steps=4,
                    reward_shaper=DoNothingReward())
    env.reset(options={"wind_speed": 8.0, "wind_direction": 270.0})
    _with_changed_inflow(env)
    _, reward, *_ = env.step({"yaw": np.zeros(3, np.float32)})
    assert float(np.ravel(reward)[0]) == pytest.approx((POWERS_MW * 1e3 / NEXT_SPEED ** 3).mean())


def test_freestream_denominator_stays_within_state_bounds():
    env = envs.make("Dec_Turb3_Row1_Floris", controls=["yaw"], max_num_steps=4)
    low, high = env.mdp.state_space["freewind_measurements"].low[0], env.mdp.state_space["freewind_measurements"].high[0]
    assert env.mdp.freestream_speed({"freewind_measurements": np.array([0.0, 270.0])}) == low
    assert env.mdp.freestream_speed({"freewind_measurements": np.array([99.0, 270.0])}) == high

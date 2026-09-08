"""
WFCRL FLORIS demo with visualization.

Runs a rollout on a FLORIS-backed wind farm environment and produces:
  1. Time-series plots  : yaw / power / reward over iterations (RL-process view)
  2. Wake flow field     : top-down wind-speed heatmap with turbines + yaw
                           (the "Rviz-like" spatial scene)

Usage (inside the `wfcrl` conda env):
    python examples/demo_visualize.py

Outputs PNGs into examples/outputs/.
"""
import os

import matplotlib

matplotlib.use("Agg")  # headless: write files instead of opening windows
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import floris.tools.visualization as wakeviz

from wfcrl import environments as envs
from wfcrl.rewards import StepPercentage

OUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUT_DIR, exist_ok=True)

# Fixed wind so the scene is reproducible: 8 m/s from 270 deg (west).
WIND = {"wind_speed": 8.0, "wind_direction": 270.0}
ENV_ID = "Dec_Ablaincourt_Floris"
MAX_STEPS = 60


def build_env():
    return envs.make(
        ENV_ID,
        max_num_steps=MAX_STEPS,
        reward_shaper=StepPercentage(),
        load_coef=1,
    )


def demo_policy(agent, step, num_turbines):
    """Sweep each turbine's yaw up to ~20 deg, one after another.

    A stand-in for a learned policy: shows how the logging / plotting hooks
    work. Replace this with your RL agent's action to visualize training.
    """
    idx = int(agent.split("_")[1]) - 1  # turbine_1 -> 0
    start = idx * 3
    if start <= step < start + 10:
        return {"yaw": np.array([2.0])}  # +2 deg per step, 10 steps -> 20 deg
    return {"yaw": np.array([0.0])}


def run_rollout(env):
    env.reset(options=WIND)
    total_reward = {a: 0.0 for a in env.possible_agents}
    done = {a: False for a in env.possible_agents}
    steps = {a: 0 for a in env.possible_agents}
    for agent in env.agent_iter():
        _, reward, term, trunc, _ = env.last()
        done[agent] = done[agent] or term or trunc
        total_reward[agent] += float(np.asarray(reward).squeeze())
        if done[agent]:
            action = None
        else:
            action = demo_policy(agent, steps[agent], env.num_turbines)
            steps[agent] += 1
        env.step(action)
    return total_reward


def plot_timeseries(env):
    """RL-process view: yaw, farm power, and reward over iterations."""
    cols = [f"T{i + 1}" for i in range(env.num_turbines)]
    yaws = np.c_[
        [[h["yaw"] for h in env.history[a]["observation"]] for a in env.possible_agents]
    ].T
    powers = np.c_[[env.history[a]["power"] for a in env.possible_agents]].T
    # reward is shared across agents; take turbine_1's trace
    rewards = np.array(env.history[env.possible_agents[0]]["reward"]).squeeze()

    yaws = pd.DataFrame(yaws, columns=cols)
    powers = pd.DataFrame(powers, columns=cols)

    fig, ax = plt.subplots(ncols=3, figsize=(20, 5))
    yaws.plot(ax=ax[0])
    ax[0].set(title="Yaw per turbine", xlabel="Iteration", ylabel="Yaw (deg)")
    (powers.sum(axis=1)).plot(ax=ax[1], color="tab:green")
    ax[1].set(title="Total farm power", xlabel="Iteration", ylabel="Power (MW)")
    ax[2].plot(rewards, color="tab:red")
    ax[2].set(title="Reward (shaped)", xlabel="Iteration", ylabel="Reward")
    fig.tight_layout()
    path = os.path.join(OUT_DIR, "timeseries.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"[saved] {path}")


def plot_wakefield(env, tag="final"):
    """Rviz-like spatial view: top-down wind-speed field with turbines.

    Uses the live FLORIS interface held by the environment, so it reflects
    the current yaw settings after the rollout.
    """
    fi = env.mdp.interface.fi  # underlying FLORIS FlorisInterface
    horizontal_plane = fi.calculate_horizontal_plane(height=90.0)

    fig, ax = plt.subplots(figsize=(12, 6))
    wakeviz.visualize_cut_plane(
        horizontal_plane,
        ax=ax,
        label_contours=False,
        title="Wake flow field (hub height, 90 m)",
    )
    wakeviz.plot_turbines_with_fi(fi, ax=ax)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    path = os.path.join(OUT_DIR, f"wakefield_{tag}.png")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {path}")


def main():
    # --- baseline scene: all turbines facing the wind (yaw = 0) ---
    env = build_env()
    env.reset(options=WIND)
    plot_wakefield(env, tag="baseline")

    # --- run the rollout and visualize the process + resulting scene ---
    env = build_env()
    total_reward = run_rollout(env)
    print("Total reward per agent:")
    for a, r in total_reward.items():
        print(f"  {a}: {r:.4f}")

    plot_timeseries(env)
    plot_wakefield(env, tag="final")


if __name__ == "__main__":
    main()

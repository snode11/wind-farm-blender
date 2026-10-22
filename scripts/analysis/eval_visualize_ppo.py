"""
Evaluate a trained PPO policy on Ablaincourt_Floris and visualize it.

The policy was trained on NORMALIZED observations (VecNormalize), so we load the
saved normalization stats and apply them to each obs before model.predict().
We still hold a direct handle to the inner FLORIS env for wake-field rendering.

Produces (into eval_outputs/):
  - wakefield_baseline.png : wake field at reset (yaw = 0, wind from the west)
  - wakefield_trained.png  : wake field after the learned policy has steered yaws
  - ppo_timeseries.png     : yaw / farm power / reward over the episode

Run AFTER training (needs checkpoints/ppo_ablaincourt.zip + vecnormalize.pkl):
    python eval_visualize_ppo.py
"""

import os

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np

import floris.tools.visualization as wakeviz
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from wfcrl import environments as envs
from wfrl.sb3_wrapper import WFCRLSB3Wrapper

from wfrl import paths

MODEL_PATH = os.path.join(paths.CKPT, "ppo_ablaincourt")
VECNORM_PATH = os.path.join(paths.CKPT, "vecnormalize.pkl")
ENV_ID = "Ablaincourt_Floris"
MAX_STEPS = 200
# Fixed wind for a reproducible scene: 8 m/s from 270 deg (west).
WIND = {"wind_speed": 8.0, "wind_direction": 270.0}
OUT_DIR = paths.EVAL
os.makedirs(OUT_DIR, exist_ok=True)


def build_env():
    raw = envs.make(ENV_ID, controls=["yaw"], max_num_steps=MAX_STEPS)
    return WFCRLSB3Wrapper(raw)


def load_normalizer():
    """Load VecNormalize stats (for obs normalization at inference)."""
    if not os.path.exists(VECNORM_PATH):
        raise FileNotFoundError(
            f"{VECNORM_PATH} not found. Train first: python train_ppo.py"
        )
    venv = DummyVecEnv([lambda: Monitor(build_env())])
    vecnorm = VecNormalize.load(VECNORM_PATH, venv)
    vecnorm.training = False
    vecnorm.norm_reward = False
    return vecnorm


def plot_wakefield(env, tag):
    fi = env.get_inner_env().mdp.interface.fi
    horizontal_plane = fi.calculate_horizontal_plane(height=90.0)
    fig, ax = plt.subplots(figsize=(12, 6))
    wakeviz.visualize_cut_plane(
        horizontal_plane, ax=ax, label_contours=False,
        title=f"Wake flow field ({tag}, hub height 90 m)",
    )
    wakeviz.plot_turbines_with_fi(fi, ax=ax)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    path = os.path.join(OUT_DIR, f"wakefield_{tag}.png")
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {path}")


def rollout(env, model, vecnorm):
    """Deterministic policy rollout; normalize obs with saved stats before predict."""
    obs, _ = env.reset(options=WIND)
    n = env.n_turbines
    yaw_hist, power_hist, reward_hist = [], [], []
    done, ep_ret = False, 0.0
    while not done:
        norm_obs = vecnorm.normalize_obs(obs)  # apply training-time obs stats
        action, _ = model.predict(norm_obs, deterministic=True)
        obs, reward, term, trunc, info = env.step(action)
        yaw_hist.append(np.array(obs["yaw"], dtype=float).copy())
        power_hist.append(np.array(info["power"], dtype=float).copy())
        reward_hist.append(reward)
        ep_ret += reward
        done = term or trunc
    print(f"[eval] deterministic raw episode return = {ep_ret:.4f}")
    return np.array(yaw_hist), np.array(power_hist), np.array(reward_hist)


def plot_timeseries(yaws, powers, rewards, n):
    fig, ax = plt.subplots(ncols=3, figsize=(20, 5))
    for i in range(n):
        ax[0].plot(yaws[:, i], label=f"T{i + 1}")
    ax[0].set(title="Yaw per turbine (learned)", xlabel="Step", ylabel="Yaw (deg)")
    ax[0].legend(fontsize=8, ncol=2)
    ax[1].plot(powers.sum(axis=1), color="tab:green")
    ax[1].set(title="Total farm power", xlabel="Step", ylabel="Power (kW)")
    ax[2].plot(rewards, color="tab:red")
    ax[2].set(title="Reward (raw)", xlabel="Step", ylabel="Reward")
    fig.tight_layout()
    path = os.path.join(OUT_DIR, "ppo_timeseries.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"[saved] {path}")


def main():
    if not os.path.exists(MODEL_PATH + ".zip"):
        raise FileNotFoundError(
            f"{MODEL_PATH}.zip not found. Train first: python train_ppo.py"
        )
    model = PPO.load(MODEL_PATH)
    vecnorm = load_normalizer()

    # Baseline scene: reset, no yaw steering yet
    base_env = build_env()
    base_env.reset(options=WIND)
    plot_wakefield(base_env, tag="baseline")

    # Trained policy rollout
    env = build_env()
    yaws, powers, rewards = rollout(env, model, vecnorm)
    plot_wakefield(env, tag="trained")
    plot_timeseries(yaws, powers, rewards, env.n_turbines)

    tot = powers.sum(axis=1)
    print(
        f"\n[summary] total farm power over episode: "
        f"start {tot[0]:.1f} kW -> end {tot[-1]:.1f} kW "
        f"(mean {tot.mean():.1f} kW)"
    )


if __name__ == "__main__":
    main()

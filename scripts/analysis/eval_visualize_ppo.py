"""
Evaluate a trained PPO policy on Ablaincourt_Floris and visualize it.

The policy was trained on NORMALIZED observations (VecNormalize), so we load the
saved normalization stats and apply them to each obs before model.predict().
We still hold a direct handle to the inner FLORIS env for wake-field rendering.

Produces (into eval_outputs/):
  - wakefield_baseline.png : wake field at reset (yaw = 0, wind from the west)
  - wakefield_trained.png  : wake field after the learned policy has steered yaws
  - ppo_timeseries.png     : yaw / farm power / reward over the episode

Run AFTER training (the manifest selects a matching model/statistics generation;
fixed ppo_ablaincourt.zip + vecnormalize.pkl are used only without a manifest):
    python eval_visualize_ppo.py
"""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import shutil
import tempfile

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np

import floris.tools.visualization as wakeviz
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from wfcrl import environments as envs
from scripts.train.train_ppo import CheckpointPair
from wfrl.sb3_checkpoint import validate_model_normalization
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
_FIGURE_STAGING = ContextVar('evaluation_figure_staging', default=None)


def _save_figure(fig, path, **options):
    """Publish a complete image, or stage it for the current evaluation report."""
    batch = _FIGURE_STAGING.get()
    if batch is not None:
        directory, pending = batch
        staged = directory / Path(path).name
        fig.savefig(staged, **options)
        pending[Path(path)] = staged
        return
    with tempfile.TemporaryDirectory(prefix='.analysis-plot-',
                                     dir=os.path.dirname(os.path.abspath(path))) as staging:
        staged = Path(staging) / Path(path).name
        fig.savefig(staged, **options)
        os.replace(staged, path)
    print(f'[saved] {path}')


@contextmanager
def _figure_transaction():
    """Keep all prior report images if rendering or publication fails."""
    with tempfile.TemporaryDirectory(prefix='.evaluation-report-', dir=OUT_DIR) as staging:
        directory, pending = Path(staging), {}
        token = _FIGURE_STAGING.set((directory, pending))
        try:
            yield
            backups, published = {}, []
            for index, path in enumerate(pending):
                backup = directory / f'backup-{index}'
                if os.path.lexists(path):
                    shutil.copy2(path, backup, follow_symlinks=False)
                    backups[path] = backup
            try:
                for path, staged in pending.items():
                    os.replace(staged, path)
                    published.append(path)
            except BaseException:
                for path in reversed(published):
                    if path in backups:
                        os.replace(backups[path], path)
                    else:
                        path.unlink()
                raise
            for path in pending:
                print(f'[saved] {path}')
        finally:
            _FIGURE_STAGING.reset(token)


def build_env():
    raw = envs.make(ENV_ID, controls=["yaw"], max_num_steps=MAX_STEPS)
    try:
        return WFCRLSB3Wrapper(raw)
    except BaseException:
        raw.close()
        raise


def load_normalizer(norm_path=None):
    """Load pinned inference statistics, resolving the pair when called alone."""
    if norm_path is None:
        _, norm_path = CheckpointPair(MODEL_PATH, VECNORM_PATH).require()
    env = build_env()
    try:
        venv = DummyVecEnv([lambda: Monitor(env)])
        vecnorm = VecNormalize.load(norm_path, venv)
        vecnorm.training = False
        vecnorm.norm_reward = False
        return vecnorm
    except BaseException:
        env.close()
        raise


def plot_wakefield(env, tag):
    fi = env.get_inner_env().mdp.interface.fi
    horizontal_plane = fi.calculate_horizontal_plane(height=90.0)
    fig, ax = plt.subplots(figsize=(12, 6))
    try:
        wakeviz.visualize_cut_plane(
            horizontal_plane, ax=ax, label_contours=False,
            title=f"Wake flow field ({tag}, hub height 90 m)",
        )
        wakeviz.plot_turbines_with_fi(fi, ax=ax)
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        path = os.path.join(OUT_DIR, f"wakefield_{tag}.png")
        _save_figure(fig, path, dpi=120, bbox_inches="tight")
    finally:
        plt.close(fig)


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
        yaws = np.array(obs["yaw"], dtype=float).copy()
        powers = np.array(info["power"], dtype=float).copy()
        if (yaws.shape != (n,) or powers.shape != (n,)
                or not np.isfinite(yaws).all() or not np.isfinite(powers).all()
                or not np.isfinite(reward)):
            raise ValueError('Expected finite yaw/power for every turbine and a finite reward')
        yaw_hist.append(yaws)
        power_hist.append(powers)
        reward_hist.append(reward)
        ep_ret += reward
        if not np.isfinite(ep_ret):
            raise ValueError('Episode return must be finite')
        done = term or trunc
    with np.errstate(over='ignore', invalid='ignore'):
        totals = np.asarray(power_hist).sum(axis=1)
        if not np.isfinite(totals).all() or not np.isfinite(totals.mean()):
            raise ValueError('Farm power and its episode mean must be finite')
    print(f"[eval] deterministic raw episode return = {ep_ret:.4f}")
    return np.array(yaw_hist), np.array(power_hist), np.array(reward_hist)


def plot_timeseries(yaws, powers, rewards, n):
    fig, ax = plt.subplots(ncols=3, figsize=(20, 5))
    try:
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
        _save_figure(fig, path, dpi=120)
    finally:
        plt.close(fig)


def main():
    model_path, norm_path = CheckpointPair(MODEL_PATH, VECNORM_PATH).require()
    model = PPO.load(model_path)
    with _figure_transaction(), ExitStack() as resources:
        vecnorm = load_normalizer(norm_path)
        resources.callback(vecnorm.close)
        validate_model_normalization(model, vecnorm)

        # Baseline scene: reset, no yaw steering yet
        base_env = build_env()
        resources.callback(base_env.close)
        base_env.reset(options=WIND)
        plot_wakefield(base_env, tag="baseline")

        # Trained policy rollout
        env = build_env()
        resources.callback(env.close)
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

"""
Centralized PPO training on WFCRL's HornsRev2 env (FLORIS backend).

Env : HornsRev2_Floris  — 91 turbines, yaw control, steady-state FLORIS wake solver
Algo: PPO (Stable-Baselines3), MultiInputPolicy for the Dict observation
      + VecNormalize on observations AND returns.

Differences vs the 7-turbine Ablaincourt script:
  - ENV_ID          -> HornsRev2_Floris (91 turbines; action Box(91,), obs ~275-dim)
  - net_arch        -> [256, 256]  (bigger action/obs space needs more capacity)
  - n_steps         -> 2048        (more samples per update for the larger space)
  - default steps   -> 1_000_000   (91-dim exploration needs far more than 100k)
  - SubprocVecEnv   -> parallel FLORIS workers on multiple CPU cores (NO GPU needed:
                       the bottleneck is the CPU-bound FLORIS sim, not the tiny MLP).

Usage:
    python train_ppo_hornsrev2.py                      # 1M steps, 8 parallel envs
    python train_ppo_hornsrev2.py --n-envs 16          # more CPU workers
    python train_ppo_hornsrev2.py --timesteps 2000000  # longer run
    python train_ppo_hornsrev2.py --resume             # continue from checkpoint
    python train_ppo_hornsrev2.py --eval-only          # evaluate saved model

Monitoring:
    tensorboard --logdir logs/ppo_hr2
    (watch train/explained_variance climb > 0; ep_rew_mean should rise)
"""

import argparse
import os

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import (
    DummyVecEnv,
    SubprocVecEnv,
    VecNormalize,
)

from wfcrl import environments as envs
from wfrl.sb3_wrapper import WFCRLSB3Wrapper

from wfrl import paths

ENV_ID = "HornsRev2_Floris"
LOG_DIR = os.path.join(paths.LOGS, "ppo_hr2")
CKPT_DIR = paths.CKPT
MODEL_PATH = os.path.join(CKPT_DIR, "ppo_hornsrev2")
VECNORM_PATH = os.path.join(CKPT_DIR, "vecnormalize_hr2.pkl")
MAX_STEPS = 200  # steps per episode


def make_single_env(max_steps: int = MAX_STEPS) -> Monitor:
    """One SB3-ready env instance (adapter + episode-stats Monitor). Raw reward."""
    raw = envs.make(ENV_ID, controls=["yaw"], max_num_steps=max_steps)
    return Monitor(WFCRLSB3Wrapper(raw))


def _env_factory(max_steps: int = MAX_STEPS):
    """Top-level picklable factory (SubprocVecEnv on Windows uses spawn+pickle)."""
    return lambda: make_single_env(max_steps)


def make_train_venv(n_envs: int = 8, max_steps: int = MAX_STEPS) -> VecNormalize:
    """Parallel (SubprocVecEnv) + normalized env for training.

    n_envs FLORIS workers step in parallel across CPU cores. This is the right
    lever for speed here (CPU-bound sim), not GPU. Falls back to DummyVecEnv when
    n_envs == 1 to avoid subprocess overhead.
    """
    if n_envs > 1:
        venv = SubprocVecEnv([_env_factory(max_steps) for _ in range(n_envs)])
    else:
        venv = DummyVecEnv([_env_factory(max_steps)])
    return VecNormalize(
        venv,
        norm_obs=True,
        norm_reward=True,
        clip_obs=10.0,
        gamma=0.99,
    )


def make_eval_venv(max_steps: int = MAX_STEPS) -> VecNormalize:
    """Eval env: single worker, reuse saved stats, report RAW reward."""
    venv = DummyVecEnv([_env_factory(max_steps)])
    if os.path.exists(VECNORM_PATH):
        venv = VecNormalize.load(VECNORM_PATH, venv)
        venv.training = False
        venv.norm_reward = False
    else:
        venv = VecNormalize(
            venv, norm_obs=True, norm_reward=False, clip_obs=10.0, gamma=0.99
        )
        venv.training = False
    return venv


def build_model(venv, resume: bool) -> PPO:
    if resume and os.path.exists(MODEL_PATH + ".zip"):
        print(f"[train] resuming from {MODEL_PATH}.zip")
        return PPO.load(MODEL_PATH, env=venv, tensorboard_log=LOG_DIR)

    print("[train] building fresh PPO model (HornsRev2, 91 turbines)")
    return PPO(
        policy="MultiInputPolicy",
        env=venv,
        learning_rate=3e-4,
        n_steps=2048,                # larger rollout for the 91-dim space
        batch_size=256,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.005,
        vf_coef=0.5,
        max_grad_norm=0.5,
        policy_kwargs=dict(net_arch=[256, 256]),  # bigger net for 91 turbines
        tensorboard_log=LOG_DIR,
        verbose=1,
    )


def evaluate(model: PPO, n_episodes: int = 3, max_steps: int = MAX_STEPS):
    """Roll out deterministically over a normalized eval env; report RAW reward."""
    venv = make_eval_venv(max_steps)
    returns = []
    for ep in range(n_episodes):
        obs = venv.reset()
        ep_ret, done = 0.0, False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, dones, _ = venv.step(action)
            ep_ret += float(reward[0])
            done = bool(dones[0])
        returns.append(ep_ret)
        print(f"[eval] episode {ep + 1}: raw return = {ep_ret:.4f}")
    venv.close()
    print(f"[eval] mean raw return over {n_episodes} eps = {np.mean(returns):.4f}")
    return float(np.mean(returns))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--n-envs", type=int, default=8,
                        help="parallel FLORIS workers (CPU cores). No GPU used.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CKPT_DIR, exist_ok=True)

    if args.eval_only:
        venv = make_train_venv(n_envs=1)
        model = build_model(venv, resume=True)
        evaluate(model)
        return

    venv = make_train_venv(n_envs=args.n_envs)
    model = build_model(venv, resume=args.resume)

    print("\n[train] --- baseline evaluation (before training) ---")
    evaluate(model, n_episodes=2)

    # save_freq is PER-ENV steps; multiply by n_envs for true global cadence.
    ckpt_cb = CheckpointCallback(
        save_freq=max(50_000 // args.n_envs, 1),
        save_path=CKPT_DIR,
        name_prefix="ppo_hr2",
    )

    print(f"\n[train] --- training for {args.timesteps} timesteps "
          f"({args.n_envs} parallel envs) ---")
    model.learn(total_timesteps=args.timesteps, callback=ckpt_cb, progress_bar=True)

    model.save(MODEL_PATH)
    venv.save(VECNORM_PATH)
    print(f"[train] saved model to {MODEL_PATH}.zip and stats to {VECNORM_PATH}")

    print("\n[train] --- final evaluation (after training) ---")
    evaluate(model)


if __name__ == "__main__":
    main()

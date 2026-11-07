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
    python scripts/train/train_ppo_hornsrev2.py                      # 8 parallel envs
    python scripts/train/train_ppo_hornsrev2.py --n-envs 16
    python scripts/train/train_ppo_hornsrev2.py --timesteps 2000000
    python scripts/train/train_ppo_hornsrev2.py --resume
    python scripts/train/train_ppo_hornsrev2.py --eval-only

Monitoring:
    tensorboard --logdir results/logs/ppo_hr2
    (watch train/explained_variance climb > 0; ep_rew_mean should rise)
"""

import argparse
from copy import deepcopy
import os
from uuid import uuid4

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.utils import check_for_correct_spaces
from stable_baselines3.common.vec_env import (
    DummyVecEnv,
    SubprocVecEnv,
    VecNormalize,
)

from wfcrl import environments as envs
from wfrl.sb3_wrapper import WFCRLSB3Wrapper
from wfrl.sb3_checkpoint import (
    require_checkpoint_files, validate_model_normalization, validate_normalization,
)

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
    try:
        return Monitor(WFCRLSB3Wrapper(raw))
    except BaseException:
        raw.close()
        raise


def _env_factory(max_steps: int = MAX_STEPS):
    """Top-level picklable factory (SubprocVecEnv on Windows uses spawn+pickle)."""
    return lambda: make_single_env(max_steps)


def _make_venv(n_envs, max_steps, *, training, norm_path=None, normalization=None):
    if n_envs < 1:
        raise ValueError("n_envs must be positive")
    if norm_path is not None and not os.path.isfile(norm_path):
        raise FileNotFoundError(f"Required normalization file is missing: {norm_path}")
    if n_envs > 1:
        base = SubprocVecEnv([_env_factory(max_steps) for _ in range(n_envs)])
    else:
        base = DummyVecEnv([_env_factory(max_steps)])
    try:
        if normalization is not None:
            venv = deepcopy(normalization)
            venv.set_venv(base)
        elif norm_path is not None:
            venv = VecNormalize.load(norm_path, base)
            venv._wfrl_normalization_path = os.path.abspath(norm_path)
        else:
            venv = VecNormalize(base, norm_obs=True, clip_obs=10.0, gamma=0.99)
        check_for_correct_spaces(base, venv.observation_space, venv.action_space)
        validate_normalization(venv)
        venv.training = training
        venv.norm_reward = training
        return venv
    except BaseException:
        base.close()
        raise


def make_train_venv(n_envs: int = 8, max_steps: int = MAX_STEPS, *, resume=False) -> VecNormalize:
    """Use independent farm workers and restore saved observation/reward moments."""
    return _make_venv(n_envs, max_steps, training=True,
                      norm_path=VECNORM_PATH if resume else None)


def make_eval_venv(max_steps: int = MAX_STEPS, *, normalization=None) -> VecNormalize:
    """Use one farm with frozen saved or supplied statistics and raw rewards."""
    return _make_venv(1, max_steps, training=False, normalization=normalization,
                      norm_path=VECNORM_PATH if normalization is None else None)


def build_model(venv, resume: bool) -> PPO:
    if resume:
        require_checkpoint_files(MODEL_PATH, VECNORM_PATH)
        if venv.__dict__.get("_wfrl_normalization_path") != os.path.abspath(VECNORM_PATH):
            raise ValueError("Restore VecNormalize before loading a HornsRev2 PPO checkpoint")
        print(f"[train] resuming from {MODEL_PATH}.zip")
        model = PPO.load(MODEL_PATH, env=venv, tensorboard_log=LOG_DIR)
        validate_model_normalization(model, venv)
        return model

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


def evaluate(model: PPO, n_episodes: int = 3, max_steps: int = MAX_STEPS, *,
             venv=None, normalization=None):
    """Evaluate frozen statistics and raw rewards; close owned environments."""
    owns_venv = venv is None
    if owns_venv:
        venv = make_eval_venv(max_steps, normalization=normalization)
    try:
        validate_model_normalization(model, venv)
        if venv.training or venv.norm_reward:
            raise ValueError("Evaluation requires training=False and norm_reward=False")
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
    finally:
        if owns_venv:
            venv.close()
    print(f"[eval] mean raw return over {n_episodes} eps = {np.mean(returns):.4f}")
    return float(np.mean(returns))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=1_000_000,
                        help="Additional transitions across all farm workers, rounded up to a rollout")
    parser.add_argument("--n-envs", type=int, default=8,
                        help="parallel FLORIS workers (CPU cores). No GPU used.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--progress-bar", action="store_true",
                        help="Show the optional progress bar (requires tqdm and rich)")
    args = parser.parse_args()

    if args.resume or args.eval_only:
        require_checkpoint_files(MODEL_PATH, VECNORM_PATH)
    if args.eval_only:
        venv = make_eval_venv()
        try:
            model = build_model(venv, resume=True)
            evaluate(model, venv=venv)
        finally:
            venv.close()
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CKPT_DIR, exist_ok=True)
    venv = make_train_venv(n_envs=args.n_envs, resume=args.resume)
    try:
        model = build_model(venv, resume=args.resume)

        print("\n[train] --- policy evaluation (before training) ---")
        evaluate(model, n_episodes=2, normalization=venv)

        # Each callback invocation advances every independent farm worker once.
        ckpt_cb = CheckpointCallback(
            save_freq=max(50_000 // args.n_envs, 1),
            save_path=CKPT_DIR,
            name_prefix="ppo_hr2",
            save_vecnormalize=True,
        )

        print(f"\n[train] --- training for {args.timesteps} timesteps "
              f"({args.n_envs} parallel envs) ---")
        model.learn(total_timesteps=args.timesteps, callback=ckpt_cb,
                    progress_bar=args.progress_bar, reset_num_timesteps=not args.resume)

        checkpoint_id = uuid4().hex
        model._wfrl_checkpoint_id = venv._wfrl_checkpoint_id = checkpoint_id
        model.save(MODEL_PATH)
        venv.save(VECNORM_PATH)
        print(f"[train] saved model to {MODEL_PATH}.zip and stats to {VECNORM_PATH}")
    finally:
        venv.close()

    print("\n[train] --- final evaluation (after training) ---")
    evaluate(model)


if __name__ == "__main__":
    main()

"""
Real PPO training on WFCRL's centralized wind-farm env (FLORIS backend).

Env : Ablaincourt_Floris  — 7 turbines, yaw control, steady-state FLORIS wake solver
Algo: PPO (Stable-Baselines3), MultiInputPolicy for the Dict observation
      + VecNormalize on observations AND returns.

Why VecNormalize: the raw Dict obs channels live on very different scales
(wind_direction ~289, wind_speed ~4-8, yaw ~0-40) and the reward is un-normalized.
Without normalization the value net can't fit (explained_variance ~ 0) and PPO's
advantages are noise. VecNormalize fixes both.

Usage:
    python train_ppo.py                       # train from scratch (100k steps)
    python train_ppo.py --timesteps 200000    # longer run
    python train_ppo.py --resume              # continue from last checkpoint
    python train_ppo.py --eval-only           # just evaluate the saved model

Monitoring:
    tensorboard --logdir logs/ppo_wf
    (episode reward mean = ep_rew_mean; watch train/explained_variance climb > 0)
"""

import argparse
import os

import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from wfcrl import environments as envs
from wfrl.sb3_wrapper import WFCRLSB3Wrapper

from wfrl import paths

ENV_ID = "Ablaincourt_Floris"
LOG_DIR = os.path.join(paths.LOGS, "ppo_wf")
CKPT_DIR = paths.CKPT
MODEL_PATH = os.path.join(CKPT_DIR, "ppo_ablaincourt")
VECNORM_PATH = os.path.join(CKPT_DIR, "vecnormalize.pkl")
MAX_STEPS = 200  # steps per episode (FLORIS is fast, so we can afford long episodes)


def make_single_env(max_steps: int = MAX_STEPS) -> Monitor:
    """One SB3-ready env instance (adapter + episode-stats Monitor). Raw reward."""
    raw = envs.make(ENV_ID, controls=["yaw"], max_num_steps=max_steps)
    return Monitor(WFCRLSB3Wrapper(raw))


def make_train_venv(max_steps: int = MAX_STEPS) -> VecNormalize:
    """Vectorized + normalized env for training (normalizes obs and returns)."""
    venv = DummyVecEnv([lambda: make_single_env(max_steps)])
    return VecNormalize(
        venv,
        norm_obs=True,
        norm_reward=True,
        clip_obs=10.0,
        gamma=0.99,
    )


def make_eval_venv(max_steps: int = MAX_STEPS) -> VecNormalize:
    """Eval env: reuse saved normalization stats, but report RAW reward.

    training=False freezes the running stats; norm_reward=False so the reward we
    accumulate is the true (un-normalized) reward, comparable to the baseline.
    """
    venv = DummyVecEnv([lambda: make_single_env(max_steps)])
    if os.path.exists(VECNORM_PATH):
        venv = VecNormalize.load(VECNORM_PATH, venv)
        venv.training = False
        venv.norm_reward = False
    else:
        # No stats yet (e.g. pre-training baseline): identity-ish normalization.
        venv = VecNormalize(
            venv, norm_obs=True, norm_reward=False, clip_obs=10.0, gamma=0.99
        )
        venv.training = False
    return venv


def build_model(venv, resume: bool) -> PPO:
    if resume and os.path.exists(MODEL_PATH + ".zip"):
        print(f"[train] resuming from {MODEL_PATH}.zip")
        return PPO.load(MODEL_PATH, env=venv, tensorboard_log=LOG_DIR)

    print("[train] building fresh PPO model")
    return PPO(
        policy="MultiInputPolicy",   # handles the Dict observation space
        env=venv,
        learning_rate=3e-4,
        n_steps=1024,                # rollout length per update
        batch_size=128,
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.005,              # small exploration bonus for continuous control
        vf_coef=0.5,
        max_grad_norm=0.5,
        policy_kwargs=dict(net_arch=[128, 128]),
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
    parser.add_argument("--timesteps", type=int, default=100_000)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CKPT_DIR, exist_ok=True)

    if args.eval_only:
        venv = make_train_venv()
        model = build_model(venv, resume=True)
        evaluate(model)
        return

    venv = make_train_venv()
    model = build_model(venv, resume=args.resume)

    # Baseline: untrained policy, raw reward (no stats saved yet).
    print("\n[train] --- baseline evaluation (before training) ---")
    evaluate(model, n_episodes=2)

    ckpt_cb = CheckpointCallback(
        save_freq=20_000, save_path=CKPT_DIR, name_prefix="ppo_wf"
    )

    print(f"\n[train] --- training for {args.timesteps} timesteps ---")
    model.learn(total_timesteps=args.timesteps, callback=ckpt_cb, progress_bar=True)

    model.save(MODEL_PATH)
    venv.save(VECNORM_PATH)  # persist normalization stats for eval/inference
    print(f"[train] saved model to {MODEL_PATH}.zip and stats to {VECNORM_PATH}")

    print("\n[train] --- final evaluation (after training) ---")
    evaluate(model)


if __name__ == "__main__":
    main()

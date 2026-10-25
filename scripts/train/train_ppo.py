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
    python scripts/train/train_ppo.py                       # train from scratch
    python scripts/train/train_ppo.py --timesteps 200000    # longer run
    python scripts/train/train_ppo.py --resume              # continue training
    python scripts/train/train_ppo.py --eval-only           # evaluate saved model

Monitoring:
    tensorboard --logdir results/logs/ppo_wf
    (episode reward mean = ep_rew_mean; watch train/explained_variance climb > 0)
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
    try:
        return Monitor(WFCRLSB3Wrapper(raw))
    except BaseException:
        raw.close()
        raise


def _require_checkpoint_files():
    for filename in (MODEL_PATH + ".zip", VECNORM_PATH):
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"Required PPO checkpoint file is missing: {filename}")


def _validate_normalization(venv):
    keys = set(venv.observation_space.spaces)
    if not venv.norm_obs or set(venv.norm_obs_keys or ()) != keys:
        raise ValueError("PPO checkpoint must normalize every observation key")
    if not isinstance(venv.obs_rms, dict) or set(venv.obs_rms) != keys:
        raise ValueError("PPO checkpoint has incompatible observation statistics")
    statistics = [(key, rms, venv.observation_space[key].shape)
                  for key, rms in venv.obs_rms.items()]
    statistics.append(("reward", venv.ret_rms, ()))
    for key, rms, shape in statistics:
        if (np.shape(rms.mean) != shape or np.shape(rms.var) != shape
                or not np.isfinite(rms.mean).all() or not np.isfinite(rms.var).all()
                or np.any(rms.var < 0) or np.shape(rms.count) != ()
                or not np.isfinite(rms.count) or rms.count <= 0):
            raise ValueError(f"PPO checkpoint has invalid {key} normalization statistics")


def _validate_pair(model, venv):
    check_for_correct_spaces(venv, model.observation_space, model.action_space)
    _validate_normalization(venv)
    if not np.isclose(model.gamma, venv.gamma, rtol=0, atol=1e-12):
        raise ValueError("PPO model and normalization discount factors do not match")
    if model.__dict__.get("_wfrl_checkpoint_id") != venv.__dict__.get("_wfrl_checkpoint_id"):
        raise ValueError("PPO model and normalization checkpoint identities do not match")


def _make_venv(max_steps, *, training, norm_path=None, normalization=None):
    if norm_path is not None and not os.path.isfile(norm_path):
        raise FileNotFoundError(f"Required PPO normalization file is missing: {norm_path}")
    base = DummyVecEnv([lambda: make_single_env(max_steps)])
    try:
        if normalization is not None:
            # VecNormalize copies statistics without copying its live environment.
            venv = deepcopy(normalization)
            venv.set_venv(base)
        elif norm_path is not None:
            venv = VecNormalize.load(norm_path, base)
            venv._wfrl_normalization_path = os.path.abspath(norm_path)
        else:
            venv = VecNormalize(base, norm_obs=True, clip_obs=10.0, gamma=0.99)
        check_for_correct_spaces(base, venv.observation_space, venv.action_space)
        _validate_normalization(venv)
        venv.training = training
        venv.norm_reward = training
        return venv
    except BaseException:
        base.close()
        raise


def make_train_venv(max_steps: int = MAX_STEPS, *, resume=False) -> VecNormalize:
    """Normalize observations and returns; resume restores their saved statistics."""
    return _make_venv(max_steps, training=True,
                      norm_path=VECNORM_PATH if resume else None)


def make_eval_venv(max_steps: int = MAX_STEPS, *, normalization=None) -> VecNormalize:
    """Freeze saved or explicitly supplied statistics and return raw rewards."""
    return _make_venv(max_steps, training=False, normalization=normalization,
                      norm_path=VECNORM_PATH if normalization is None else None)


def build_model(venv, resume: bool) -> PPO:
    if resume:
        _require_checkpoint_files()
        if venv.__dict__.get("_wfrl_normalization_path") != os.path.abspath(VECNORM_PATH):
            raise ValueError("Restore VecNormalize before loading a PPO checkpoint")
        print(f"[train] resuming from {MODEL_PATH}.zip")
        model = PPO.load(MODEL_PATH, env=venv, tensorboard_log=LOG_DIR)
        _validate_pair(model, venv)
        return model

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


def evaluate(model: PPO, n_episodes: int = 3, max_steps: int = MAX_STEPS, *,
             venv=None, normalization=None):
    """Evaluate frozen observations and raw rewards; close owned environments."""
    owns_venv = venv is None
    if owns_venv:
        venv = make_eval_venv(max_steps, normalization=normalization)
    try:
        _validate_pair(model, venv)
        if venv.training or venv.norm_reward:
            raise ValueError("PPO evaluation requires training=False and norm_reward=False")
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
    parser.add_argument("--timesteps", type=int, default=100_000,
                        help="Additional control steps, rounded up to a complete rollout")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--progress-bar", action="store_true",
                        help="Show the optional progress bar (requires tqdm and rich)")
    args = parser.parse_args()

    if args.resume or args.eval_only:
        _require_checkpoint_files()
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
    venv = make_train_venv(resume=args.resume)
    try:
        model = build_model(venv, resume=args.resume)

        print("\n[train] --- policy evaluation (before training) ---")
        evaluate(model, n_episodes=2, normalization=venv)

        ckpt_cb = CheckpointCallback(
            save_freq=20_000, save_path=CKPT_DIR, name_prefix="ppo_wf",
            save_vecnormalize=True,
        )

        print(f"\n[train] --- training for {args.timesteps} timesteps ---")
        model.learn(total_timesteps=args.timesteps, callback=ckpt_cb,
                    progress_bar=args.progress_bar,
                    reset_num_timesteps=not args.resume)

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

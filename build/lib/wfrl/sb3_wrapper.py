"""
WFCRLSB3Wrapper — makes WindFarmEnv (centralized, yaw-only FLORIS backend)
compatible with Stable-Baselines3:

  Issue                         Fix
  ───────────────────────────── ─────────────────────────────────────────────
  reset() returns obs only      return (obs, {})
  reward is np.array([float])   squeeze to Python float
  action_space is Dict          flatten to Box so SB3 policy outputs a vector
  obs_space is Dict             leave as-is; MultiInputPolicy handles it

Typical usage:
    from wfcrl import environments as envs
    from wfrl.sb3_wrapper import WFCRLSB3Wrapper

    raw = envs.make("Ablaincourt_Floris", controls=["yaw"], max_num_steps=200)
    env = WFCRLSB3Wrapper(raw)
"""

import gymnasium as gym
import numpy as np
from gymnasium import spaces


class WFCRLSB3Wrapper(gym.Wrapper):
    """Gymnasium wrapper that patches the three SB3 incompatibilities."""

    def __init__(self, env: gym.Env):
        super().__init__(env)

        # Validate: only yaw-control is supported in this wrapper
        action_keys = list(env.action_space.spaces.keys())
        if action_keys != ["yaw"]:
            raise ValueError(
                f"WFCRLSB3Wrapper expects controls=['yaw']; got {action_keys}. "
                "Multi-control support requires further flattening."
            )

        # Replace Dict action space with a flat Box
        yaw_space: spaces.Box = env.action_space.spaces["yaw"]
        self.action_space = spaces.Box(
            low=yaw_space.low,
            high=yaw_space.high,
            shape=yaw_space.shape,
            dtype=yaw_space.dtype,
        )
        # Observation space stays as Dict — SB3 MultiInputPolicy handles it natively.
        # Keys: 'yaw' (7,), 'freewind_measurements' (2,),
        #       'wind_speed' (7,), 'wind_direction' (7,)  →  total 23 dims
        self.observation_space = env.observation_space

        self._n_turbines = yaw_space.shape[0]

    # ------------------------------------------------------------------
    # Gymnasium API overrides
    # ------------------------------------------------------------------

    def reset(self, *, seed=None, options=None):
        obs = self.env.reset(seed=seed, options=options)
        # inner reset() returns obs only; SB3 expects (obs, info)
        return obs, {}

    def step(self, action: np.ndarray):
        # Re-wrap flat yaw array into the Dict the inner env expects
        obs, reward, terminated, truncated, info = self.env.step({"yaw": action})
        # Squeeze np.array([float]) → scalar float
        scalar_reward = float(np.asarray(reward).squeeze())
        return obs, scalar_reward, terminated, truncated, info

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def n_turbines(self) -> int:
        return self._n_turbines

    def get_inner_env(self):
        """Return the unwrapped WindFarmEnv (useful for FLORIS wake-field viz)."""
        env = self.env
        while hasattr(env, "env"):
            env = env.env
        return env

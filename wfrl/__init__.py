"""wfrl —— 风电场多智能体强化学习控制。

本包只放**可复用**的东西（环境驱动、算法、可视化）；
一次性的训练/实验/作图入口在仓库的 scripts/ 下。

模块：
  paths            所有输出目录的唯一定义处
  fastfarm_driver  FLORIS / FAST.Farm 双后端统一驱动（可钉死来流）
  mappo_central    集中式 critic 的 MAPPO（CTDE），含 GRU critic
  mappo_sb3        基于 SB3 的参数共享 MAPPO（PettingZoo -> VecEnv）
  sb3_wrapper      单智能体 PPO 用的 Gymnasium 包装
  multimodal       Dict 观测（标量 + 尾流图像）与 CNN 分支
  viz.*            3D 流场 / RViz 风格交互界面
"""

from wfrl import paths  # noqa: F401  —— import 本包即保证输出目录存在

__all__ = ["paths"]

# WFCRL MAPPO 环境配置指南

在 WFCRL 上跑 MAPPO（多智能体参数共享 PPO）的完整步骤。

## 依赖版本（关键）

这三个包版本互斥，必须严格固定：

| 包 | 版本 |
|---|---|
| gymnasium | 0.29.1 |
| pettingzoo | 1.24.3 |
| stable-baselines3 | 2.3.2 |
| numpy | 1.26.4 |

> **注意**：`supersuit >= 3.10` 要求 `gymnasium >= 1.0`，与 wfcrl 不兼容，**不要装 supersuit**。

## 安装步骤

```bash
# 1. 激活 wfcrl conda 环境
conda activate wfcrl

# 2. 安装兼容版本（顺序无所谓，一条命令）
pip install "pettingzoo==1.24.3" "gymnasium==0.29.1" "numpy==1.26.4"

# 3. 验证
python -c "import gymnasium, numpy, pettingzoo, stable_baselines3 as sb3; \
  print('gym', gymnasium.__version__); \
  print('numpy', numpy.__version__); \
  print('pettingzoo', pettingzoo.__version__); \
  print('sb3', sb3.__version__)"
# 期望输出：gym 0.29.1 / numpy 1.26.4 / pettingzoo 1.24.3 / sb3 2.3.2
```

## 为什么不用 supersuit

`supersuit.concat_vec_envs_v1` 内部会用 cloudpickle 序列化环境，而 WFCRL 的 FLORIS 接口含有 generator 对象，无法被 pickle，会报：

```
TypeError: cannot pickle 'generator' object
```

解决方案：自己写一个轻量 `MAParallelVecEnv`，直接包装 PettingZoo Parallel env，完全绕开 supersuit。

## 核心原理

```
MAWindFarmEnv (PettingZoo AEC, env_id 加 "Dec_" 前缀)
  -> aec_to_parallel()       # 所有 agent 同时 step
  -> MAParallelVecEnv        # 自定义 VecEnv，每个 agent = 一个"worker"
  -> VecNormalize
  -> PPO("MultiInputPolicy") # 一个网络，所有 turbine 共享参数 = MAPPO
```

每个 step，`num_turbines` 个 (obs, action, reward) 同时进 rollout buffer，但只有一套网络权重。这就是 parameter sharing MAPPO。

## 环境 ID

| 场景 | 集中式 (单智能体) | 多智能体 MAPPO |
|---|---|---|
| Ablaincourt (7 turbines) | `Ablaincourt_Floris` | `Dec_Ablaincourt_Floris` |
| HornsRev2 (91 turbines) | `HornsRev2_Floris` | `Dec_HornsRev2_Floris` |

多智能体环境加 `Dec_` 前缀即可，其余参数相同。

## 运行

```bash
# 试运行（500 steps）
python train_mappo.py --timesteps 500

# 正式训练
python train_mappo.py --timesteps 500000

# 评估
python train_mappo.py --eval-only
```

## 常见问题

**Q: 安装后 gymnasium 版本被升级了怎么办？**  
重新执行 `pip install "gymnasium==0.29.1" "numpy==1.26.4"` 即可。每次装新包后都要检查这两个版本。

**Q: 为什么 `total_timesteps` 显示比设定值大？**  
正常现象。设定 500 steps，实际跑 500 × 7 = 3500 个 agent transitions（7 个 turbine 每步各贡献一条）。SB3 按 agent transitions 计数。

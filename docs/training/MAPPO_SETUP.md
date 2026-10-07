# PPO、参数共享 IPPO 与集中式 MAPPO 环境配置

对齐日期：2026-10-08。本文按当前源码区分算法和入口；本轮仅静态核对，没有安装依赖、训练或求解。Blender 当前发布版为 0.3.17.1（包内0.3.17+1），离线演示安装见[演示指令单](../demos/演示指令单.md)，不需要训练环境。

## 1. 先选实际算法

| 路线 | 当前入口 | Actor / Critic 输入 | 后端 |
|---|---|---|---|
| 集中式单智能体 PPO | `scripts/train/train_ppo.py` | 全场 Dict 观测 / 同一全场观测 | Ablaincourt FLORIS，7 机组 |
| 参数共享 IPPO | `scripts/train/train_mappo.py` → `wfrl/mappo_sb3.py` | 单机局部观测 / 单机局部观测 | Ablaincourt FLORIS，7 机组 |
| 多模态参数共享 IPPO | `scripts/train/train_mappo_mm.py` | 局部观测及代理振动、尾流图 / 同一输入 | FLORIS |
| 集中式 MAPPO（CTDE） | `python -m wfrl.mappo_central` | 单机局部观测 / 各机局部观测拼接 | Ablaincourt FLORIS，7 机组 |
| 集中式 MAPPO，可选 GRU critic | `scripts/train/train_fastfarm.py` | 单机局部观测 / 全场拼接状态或其序列 | 默认三机 FAST.Farm，也支持 `--backend floris` |

文件名中的 `mappo`、环境 ID 的 `Dec_` 或共享参数本身不能证明集中式 critic。SB3 路线将机组映射成一个 VecEnv 的多行样本，但 critic 仍只读本机输入；详细数据流见[IPPO 说明](IPPO_TRAINING_EXPLAINED.md)。真实集中式实现已经在仓库中，不再是“需要换框架才能开始”的待办。

## 2. 依赖与安装

仓库的 `wfcrl-env/requirements-floris-local.txt` 保留 Windows + Python 3.11 的历史兼容组合：FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 GUI 依赖。根 `pyproject.toml` 的 `train` / `gui` / `all` 是功能附加组，不会替代 WFCRL 环境及外部求解器安装。

在独立 Python 3.11 环境、仓库根目录执行以下安装模板：

```bash
python -m pip install -r wfcrl-env/requirements-floris-local.txt
python -m pip install -e ./wfcrl-env --no-deps
python -m pip install -e '.[train]'
python -m pip check
```

这是按当前文件对齐的模板，不是本次重新验证的全平台安装结果。该 requirements 文件不安装 `mpi4py`、`openfast_toolbox` 或 FAST.Farm / 控制器二进制。FAST.Farm 另需与实际平台匹配的 MPI、求解器和 DLL；不能靠改环境 ID 完成部署。完整分流见[本地配置](../setup/setup_local.md)。

当前 SB3 包装器无需 supersuit；历史部署记录有 generator 序列化问题，因此这条路线使用自定义 `MAParallelVecEnv`。不把当时的问题推定为所有新版本的结论。

## 3. 当前命令模板

以下命令会创建或覆盖 `results/` 下对应产物，只在需要训练或评估时使用。本文维护没有执行它们。

```bash
# 参数共享 IPPO，SB3；500 是目标 agent transitions，实际按完整 rollout 取整
python scripts/train/train_mappo.py --timesteps 500
python scripts/train/train_mappo.py --timesteps 500000

# 真实集中式 MAPPO，FLORIS
python -m wfrl.mappo_central --timesteps 50000 --seed 0

# 三机动态 MAPPO，需先完成 FAST.Farm / MPI 环境配置
mpiexec -n 1 python scripts/train/train_fastfarm.py \
  --env Dec_Turb3_Row1_Fastfarm --iters 6 --n-steps 64 \
  --episode-steps 128 --warmup-steps 8 --wind-speed 8 --seed 0
```

动态入口默认每回合重新启动后端，warmup 不进入训练统计；`--episode-steps 0` 才是保留的单长回合历史口径。`--recurrent` 仅切换 GRU critic，actor 仍是前馈共享网络。源码存在不代表 GRU 优于前馈或 MAPPO 优于 IPPO 已得到对照证据。

### 评估前核对模型与归一化

- SB3 IPPO：`results/checkpoints/mappo_ablaincourt.zip` 与 `vecnormalize_mappo.pkl`。
- 单智能体 PPO：`ppo_ablaincourt.zip` 与 `vecnormalize.pkl`。
- 集中式 FLORIS MAPPO：`mappo_central_ablaincourt.pt`；观测统计在 checkpoint 内。
- FAST.Farm MAPPO：由环境、种子、奖励、回合及 tag 共同命名的 `.pt`，检查其中 `obs_keys`、维度、机组顺序、控制量与观测统计。

SB3 两个脚本缺少权重时可能创建新模型，缺少归一化时也有回退路径；不能仅凭 `--eval-only` 返回就认定已评估训练模型。确认配套文件后才使用：

```bash
python scripts/train/train_mappo.py --resume --eval-only
python scripts/train/train_ppo.py --eval-only
python -m wfrl.mappo_central --eval-only
```

## 4. 计数与比较口径

SB3 IPPO 的 `n_steps=1024`、`num_envs=7`，一次 rollout 收集 7168 个 agent transitions。`total_timesteps=500` 不是 500 次全场求解，也不是“500 × 7”；`learn()` 会先完成当前 rollout，实际计数可超过目标。七个同步样本来自同一耦合风场，不能直接宣称统计独立或样本效率提高七倍。

跨算法与后端比较需明确全场控制步、agent transitions、物理时长、预热、来流、动作约束和奖励口径。FLORIS 是稳态参考，FAST.Farm 是动态模型；固定来流历史 +5.3% 报告与当前柔性回放/重建软件验收分别判定，见[历史尾流控制记录](WAKE_STEERING_BREAKTHROUGH.md)。

源码依据：[SB3 包装实现](../../wfrl/mappo_sb3.py)、[集中式实现](../../wfrl/mappo_central.py)、[动态训练入口](../../scripts/train/train_fastfarm.py)、[统一输出路径](../../wfrl/paths.py)。

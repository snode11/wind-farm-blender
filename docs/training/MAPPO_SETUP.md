# PPO、参数共享 IPPO 与集中式 MAPPO 环境配置

本文按源码区分算法与训练入口。Blender 公开安装 ZIP 为 **0.3.28**（标签 v0.3.28），当前项目源码使用 `wind-farm-rl-all-work` 分支；日常安装为 **0.3.28**，181 文件与公开上传源包一致。离线演示无需训练环境，[入口与身份见演示指令单](../demos/演示指令单.md)[和发布状态](../blender/发布状态与验证范围.md)。

## 1. 先选实际算法

| 路线 | 当前入口 | Actor / Critic 输入 | 后端 |
|---|---|---|---|
| 集中式单智能体 PPO | `scripts/train/train_ppo.py` | 全场 Dict 观测 / 同一全场观测 | Ablaincourt FLORIS，7 机组 |
| 参数共享 IPPO | `scripts/train/train_mappo.py` → `wfrl/mappo_sb3.py` | 单机局部观测 / 单机局部观测 | Ablaincourt FLORIS，7 机组 |
| 多模态参数共享 IPPO | `scripts/train/train_mappo_mm.py` | 局部观测及代理振动、尾流图 / 同一输入 | FLORIS |
| 集中式 MAPPO（CTDE） | `python -m wfrl.mappo_central` | 单机局部观测 / 各机局部观测拼接 | Ablaincourt FLORIS，7 机组 |
| 集中式 MAPPO，可选 GRU critic | `scripts/train/train_fastfarm.py` | 单机局部观测 / 全场拼接状态或其序列 | 默认三机 FAST.Farm，也支持 `--backend floris` |
| Interactive 共享 actor、集中式 critic | Bridge → `IsolatedTrainer`（POSIX）→ `trainer_worker` → `wfrl/studio/trainer.py` | 单机局部观测 / 各机局部观测拼接 | FAST.Farm |

文件名中的 `mappo`、环境 ID 的 `Dec_` 或共享参数本身不能证明集中式 critic。SB3 参数共享 IPPO 路线将机组映射成一个 VecEnv 的多行样本，但 critic 仍只读本机输入；详细数据流见[IPPO 说明](IPPO_TRAINING_EXPLAINED.md)。集中式实现使用表中对应入口，不能与参数共享 IPPO 混称。

### Interactive 的采样与更新

Bridge 在 POSIX 平台通过 [IsolatedTrainer](../../wfrl/blender_bridge/isolated_trainer.py) 启动 MPI worker，worker 调用 [Studio Trainer](../../wfrl/studio/trainer.py)；其他平台的现有分支直接调用同一 Trainer，平台运行状态须单独验证。该路径复用共享 `Actor`、集中式 `Critic` 和观测统计结构：actor 读取本机观测，critic 读取全场拼接观测，每个控制步使用一个团队奖励及相应标量优势。Formal Training 运行 `scripts/train/train_fastfarm.py`，不使用这里的 Interactive 更新循环；SB3 路线的采样、归一化和更新由其自身实现负责。

Interactive 记录未经过 `tanh` 和动作约束层的高斯采样动作。采样与更新均只沿动作维汇总 log-prob，缓存形状为 `(steps, agents)`。每个机组分别计算 `ratio = exp(new_logp - old_logp)` 并进行 PPO 裁剪；多控制量先组成该机组的联合动作概率，不逐分量独立裁剪，也不把所有机组的概率合并为一个 ratio。团队优势广播到 `(batch, agents)` 后统一标准化，单元素时使用零标准差以避免非有限值；代理目标对时间步和机组取均值。熵项对高斯分布的动作维求和，再对时间步与机组取均值。

训练奖励缩放使用折扣累加器 `G = gamma * G + reward` 和二阶矩指数移动平均 `M = 0.99 * M + 0.01 * G**2`，两者跨 rollout 保留；本轮实际采集的奖励统一除以本轮末的 `sqrt(M) + 1e-8` 后计算 GAE。该实现不等同于 SB3 `VecNormalize`。仪表板的 `mean_raw_reward` 来自本轮原始奖励均值，归一化 `mean_reward` 通道没有输出，不将两者混称。

全场功率只有在同一步的所有预期机组都具有有限标量读数且合计有限时有效。缺测在 Trainer 内部保留为 `NaN`；迭代 `mean_power` 使用本轮实际收集窗口的全部控制步，任一步全场功率无效就将整轮统计通过协议输出为 `null / invalid`，不忽略缺测样本计算部分均值。实时功率通道完全未返回时保留 `unsupported`；真实零功率仍是有效数值。有效机组自己的通道不因其他机组缺测而被抹除。

以上定义属于当前后端源码，不据此宣称公开 0.3.28 ZIP、本机已安装扩展或保存 checkpoint 已重新验证；训练收敛、物理控制收益和跨平台运行须另有匹配证据。

## 2. 依赖与安装

仓库的 `wfcrl-env/requirements-floris-local.txt` 保留 Windows + Python 3.11 的历史兼容组合：FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 GUI 依赖。根 [pyproject.toml](../../pyproject.toml) 将 PyYAML 声明为核心依赖；普通安装、editable 安装和 wheel 均提供内置机型 YAML。`train` / `gui` / `all` 是功能附加组，训练仍需匹配的 WFCRL 环境，FAST.Farm 路径另需外部求解器与控制器，不能用普通 Python 包或 Blender ZIP 代替。[安装范围见本地配置](../setup/setup_local.md#2-python-环境floris-与算法开发)。

在独立 Python 3.11 环境、仓库根目录执行以下安装模板：

```bash
python -m pip install -r wfcrl-env/requirements-floris-local.txt
python -m pip install -e ./wfcrl-env --no-deps
python -m pip install -e '.[train]'
python -m pip check
```

这是源码环境安装模板，不构成全平台验证结果。该 requirements 文件不安装 `mpi4py`、`openfast_toolbox` 或 FAST.Farm / 控制器二进制。FAST.Farm 另需与实际平台匹配的 MPI、求解器和 DLL；不能靠改环境 ID 完成部署。[完整分流见本地配置](../setup/setup_local.md)。

当前 SB3 包装器无需 supersuit；历史部署记录有 generator 序列化问题，因此这条路线使用自定义 `MAParallelVecEnv`。不把当时的问题推定为所有新版本的结论。

## 3. 当前命令模板

以下命令会创建或覆盖 `results/` 下对应产物，在训练或评估任务中使用。

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

源码依据：[SB3 包装实现](../../wfrl/mappo_sb3.py)、[集中式实现](../../wfrl/mappo_central.py)、[动态训练入口](../../scripts/train/train_fastfarm.py)、[Interactive Trainer](../../wfrl/studio/trainer.py)、[全场功率有效性](../../wfrl/scene/power.py)、[Interactive 统计协议](../../wfrl/blender_bridge/training_progress.py)、[统一输出路径](../../wfrl/paths.py)。

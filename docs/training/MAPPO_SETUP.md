# PPO、参数共享 IPPO 与集中式 MAPPO 环境配置

本文按项目 Python 源码区分算法与训练入口，源码入口使用 `wind-farm-rl-all-work` 分支。Blender 公开安装包与开发机日常安装分别按[发布状态与验证范围](../blender/发布状态与验证范围.md)识别；项目后端与训练工具不随扩展 ZIP 安装。离线演示无需训练环境，[入口见演示指令单](../demos/演示指令单.md)。

## 1. 先选实际算法

| 路线 | 当前入口 | Actor / Critic 输入 | 后端 |
|---|---|---|---|
| 集中式单智能体 PPO | `scripts/train/train_ppo.py` | 全场 Dict 观测 / 同一全场观测 | Ablaincourt FLORIS，7 机组 |
| 参数共享 IPPO | `scripts/train/train_mappo.py` → `wfrl/mappo_sb3.py` | 单机局部观测 / 单机局部观测 | Ablaincourt FLORIS，7 机组 |
| 多模态参数共享 IPPO | `scripts/train/train_mappo_mm.py` | 局部观测及代理振动、尾流图 / 同一输入 | FLORIS |
| HornsRev2 集中式单智能体 PPO | `scripts/train/train_ppo_hornsrev2.py` | 全场 Dict 观测 / 同一全场观测 | HornsRev2 FLORIS，91 机组；多个独立风场 worker |
| 集中式 MAPPO（CTDE） | `python -m wfrl.mappo_central` | 单机局部观测 / 各机局部观测拼接 | Ablaincourt FLORIS，7 机组 |
| 集中式 MAPPO，可选 GRU critic | `scripts/train/train_fastfarm.py` | 单机局部观测 / 全场拼接状态或其序列 | 默认三机 FAST.Farm，也支持 `--backend floris` |
| Interactive 共享 actor、集中式 critic | Bridge → `IsolatedTrainer`（POSIX）→ `trainer_worker` → `wfrl/studio/trainer.py` | 单机局部观测 / 各机局部观测拼接 | FAST.Farm |

文件名中的 `mappo`、环境 ID 的 `Dec_` 或共享参数本身不能证明集中式 critic。SB3 参数共享 IPPO 路线将机组映射成一个 VecEnv 的多行样本，但 critic 仍只读本机输入；详细数据流见[IPPO 说明](IPPO_TRAINING_EXPLAINED.md)。集中式实现使用表中对应入口，不能与参数共享 IPPO 混称。

### Interactive 的采样与更新

Bridge 在 POSIX 平台通过 [IsolatedTrainer](../../wfrl/blender_bridge/isolated_trainer.py) 启动 MPI worker，worker 调用 [Studio Trainer](../../wfrl/studio/trainer.py)；其他平台的现有分支直接调用同一 Trainer，平台运行状态须单独验证。该路径复用共享 `Actor`、集中式 `Critic` 和观测统计结构：actor 读取本机观测，critic 读取全场拼接观测，每个控制步使用一个团队奖励及相应标量优势。Formal Training 运行 `scripts/train/train_fastfarm.py`，不使用这里的 Interactive 更新循环；SB3 路线的采样、归一化和更新由其自身实现负责。

Interactive 记录未经过 `tanh` 和动作约束层的高斯采样动作。采样与更新均只沿动作维汇总 log-prob，缓存形状为 `(steps, agents)`。每个机组分别计算 `ratio = exp(new_logp - old_logp)` 并进行 PPO 裁剪；多控制量先组成该机组的联合动作概率，不逐分量独立裁剪，也不把所有机组的概率合并为一个 ratio。团队优势广播到 `(batch, agents)` 后统一标准化，单元素时使用零标准差以避免非有限值；代理目标对时间步和机组取均值。熵项对高斯分布的动作维求和，再对时间步与机组取均值。

Interactive 的 `episode_steps` 指每回合用于训练的控制步数，默认 `0` 表示单长回合；正值按回合关闭并重建 runtime，每回合执行 `warmup_steps` 次预热。预热不进入训练样本，总训练预算仍为 `iters * n_steps`，最后一个回合按剩余步数缩短。一个 rollout 可以跨回合；回合末使用重置前终点的 critic 值自举，并切断跨回合 GAE 递推。后端提前结束时明确报错，不继续采样已停止的 runtime。

回合切换与训练收尾使用严格关闭：排空、环境关闭或 MPI 子通信器断开无法确认时报告失败并阻止启动下一回合；不把清理异常视为正常回合完成。暂停和取消在控制步、回合启动及预热边界检查。

`step` 和迭代计数贯穿训练，`episode` 从零编号；仿真时间属于当前回合，可以在回合重启时回零。快照以顶层 `payload.episode` 传递回合；当前源码前端仅在顶层缺字段时兼容旧的 `payload.context.episode`，零值有效。实时曲线在回合变化或时间回退处断线，保留同一全局步数的跨回合边界样本与训练统计，合同见[快照与曲线说明](../../blender_frontend/README.md)。Blender 工作区的 **WFRL / 后端运行 → Episode steps** 设置仅用于 Interactive；安装版需要匹配扩展载荷，后端改动需重启 Bridge。

训练奖励缩放使用折扣累加器 `G = gamma * G + reward` 和二阶矩指数移动平均 `M = 0.99 * M + 0.01 * G**2`。`G` 在同一回合跨 rollout 保留，并在回合边界清零；`M` 跨回合保留。本轮实际采集的奖励统一除以本轮末的 `sqrt(M) + 1e-8` 后计算 GAE。该实现不等同于 SB3 `VecNormalize`。仪表板的 `mean_raw_reward` 来自本轮原始奖励均值，归一化 `mean_reward` 通道没有输出，不将两者混称。

全场功率只有在同一步的所有预期机组都具有有限标量读数且合计有限时有效。缺测在 Trainer 内部保留为 `NaN`；迭代 `mean_power` 使用本轮实际收集窗口的全部控制步，任一步全场功率无效就将整轮统计通过协议输出为 `null / invalid`，不忽略缺测样本计算部分均值。实时功率通道完全未返回时保留 `unsupported`；真实零功率仍是有效数值。有效机组自己的通道不因其他机组缺测而被抹除。

以上定义属于当前后端源码，不据此宣称 Blender 安装包、日常扩展或已有 checkpoint 的运行效果已验证；训练收敛、物理控制收益和跨平台运行须另有匹配证据。

### 策略动作与 checkpoint

项目使用 [PolicyActionContract](../../wfrl/policy_actions.py) 描述高斯网络输出到控制增量的变换。checkpoint 的 `action_contract` 包含 `version=1`、有序 `controls`、`transform`、逐通道 `low` 和 `high`。`tanh_scale` 要求对称边界，计算 `tanh(raw) * high`；`clip` 将原始输出裁剪到保存的上下界。变换结果随后进入约束层，PPO 缓存仍保留原始高斯动作及其 log-prob。

Studio / Interactive 使用 `tanh_scale`，包括仅控制 yaw 或单个非 yaw 控制量的场景；Replay 从 checkpoint 读取同一合同，不按动作维度猜测变换。`train_fastfarm.py` 的高斯 yaw 策略使用 `clip`，其 checkpoint 与命令行 rollout 保持相同规则。控制顺序、动作和观测维度、归一化统计、机组数量及运行时边界须一致；保存了场景的模型还校验后端、机型、时间步长与机组布局。

Replay 在后端启动、预热和控制步边界响应暂停与停止。最终快照的 `step` 使用实际完成的策略控制步数，预热不计入；只有达到请求的 `replay_steps` 才将 `iters_done` 置为 `1`。取消或后端提前结束保留实际计数。

无版本动作合同的模型只接受可识别的保存格式：Studio 的 `scene`、`controls`、`critic` 组合对应 `tanh_scale`；CLI 的完整 `env_id`、`obs_duty`、`recurrent`、`duty_penalty` 组合对应 yaw 的 `clip`。该兼容路径使用固定控制边界，并须与实际运行时一致；缺失、冲突或无法判定的元数据明确报错。没有保存场景的 CLI 模型不能仅凭现有字段证明完整布局、机型和时间步长匹配。

### 回合结束与时间截断

SB3 包装器在自动重置前保存各机组的完整 `terminal_observation`，包括派生观测字段；只有截断且没有真正终止时才设置 `TimeLimit.truncated=True`，供 SB3 用终点观测计算价值自举。共用风场要求机组同步结束，不能在部分机组结束后把其复位状态当作普通下一步。

集中式 FLORIS MAPPO 分开处理价值自举和 GAE 递推边界：真正终止的下一状态价值为零；步数上限截断使用重置前终点的 critic 值；两种回合边界都截断优势递推，避免下一回合的奖励传播回来。普通 rollout 切片使用连续轨迹的下一状态价值。这些规则分别由对应入口实现，不代替其他训练路线的算法定义。

### FAST.Farm 实验身份与 GRU

`train_fastfarm.py` 使用完整有效配置和源码／实际输入的内容身份确定 `experiment_id`，每次执行另有独立 `run_id`。配置包含训练预算、预热、回合、奖励及其参数、PPO 参数、观测、风况、种子和循环 critic 设置；源码身份覆盖项目训练代码、WFCRL、求解器／模板／风文件及运行依赖。产物名由可读标签、实验摘要和运行 UUID 组成，已有检查点和历史文件不会被静默覆盖。

`run_stage3.py` 仅复用配置、来源、完成预算、终态、历史和完整检查点全部对应的独立完成任务；检查点字节须匹配历史中的摘要。空文件、部分训练、缺少身份的既有结果及恢复训练段都不能被视为新的完整实验已完成。改变有效配置会形成不同实验身份，可用 `--dry-run` 检查待运行组合而不启动求解。

`scripts/analysis/compare_stage3.py` 按相同来源身份及完整公共配置配对零偏航基线与学习策略，仅允许 `policy` 和迭代预算 `iters` 不同；种子、场景、观测、奖励参数和 PPO 参数均须匹配。比较要求完整完成的独立新运行、有效实验身份、匹配预算的有限功率历史和检查点摘要，不将恢复段或缺少身份的历史文件纳入统计。缺少配对或存在多个候选时停止，可重复指定 `--pair <zero_run_id>:<learn_run_id>` 明确选择；同一来源、配置和 seed 只计一对。不同来源或训练预算分别统计，图表只使用通过配对校验的运行。`--last N` 取学习策略末 N 轮，基线取其全部轮次；控制台保留来源、公共配置和双方运行标识。

`--recurrent` 只作用于集中式 critic。采样逐步维护隐藏状态，rollout 边界保留并 detach，回合起点清零；训练使用该 rollout 起点的隐藏状态及逐步回合起点标记。截断的 bootstrap 使用重置前观测和同一历史条件，但不提前消耗下一控制步的隐藏状态。数值一致性不代表 GRU 对前馈网络的控制收益已得到物理训练验证。

观测统计在当前动作执行后纳入当前原始观测，再计算下一状态的 bootstrap；连续步的 bootstrap 与下一次决策估值使用同一份统计。截断处仍使用 reset 前观测，GAE 递推在回合边界停止。

## 2. 依赖与安装

仓库的 `wfcrl-env/requirements-floris-local.txt` 保留 Windows + Python 3.11 的历史兼容组合：FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 GUI 依赖。根 [pyproject.toml](../../pyproject.toml) 将 PyYAML 声明为核心依赖；普通安装、editable 安装和 wheel 均提供内置机型 YAML。`train` / `gui` / `all` 是功能附加组，训练仍需匹配的 WFCRL 环境，FAST.Farm 路径另需外部求解器与控制器，不能用普通 Python 包或 Blender ZIP 代替。[安装范围见本地配置](../setup/setup_local.md#2-python-环境floris-与算法开发)。

在独立 Python 3.11 环境、仓库根目录执行以下安装模板：

```bash
python -m pip install -r wfcrl-env/requirements-floris-local.txt
python -m pip install -e ./wfcrl-env --no-deps
python -m pip install -e '.[train]'
python -m pip check
```

`train_ppo.py`、`train_mappo_mm.py` 与 `train_ppo_hornsrev2.py` 默认使用普通训练日志；`--progress-bar` 可启用进度条，需另有 `tqdm` 与 `rich`。这些入口的默认训练不依赖可选进度条组件。

这是源码环境安装模板，不构成全平台验证结果。该 requirements 文件不安装 `mpi4py`、`openfast_toolbox` 或 FAST.Farm / 控制器二进制。FAST.Farm 另需与实际平台匹配的 MPI、求解器和 DLL；不能靠改环境 ID 完成部署。[完整分流见本地配置](../setup/setup_local.md)。

当前 SB3 包装器无需 supersuit；历史部署记录有 generator 序列化问题，因此这条路线使用自定义 `MAParallelVecEnv`。不把当时的问题推定为所有新版本的结论。

## 3. 当前命令模板

以下命令使用 `results/` 下的产物。FAST.Farm MAPPO 每次执行使用独立运行身份；其他训练入口的输出与覆盖规则按其加载／保存说明使用。

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

动态入口默认每回合重新启动后端，warmup 不进入训练统计；`--episode-steps 0` 使用单长回合。`--recurrent` 仅切换 GRU critic，actor 仍是前馈共享网络。源码存在不代表 GRU 优于前馈或 MAPPO 优于 IPPO 已得到对照证据。

### 评估、续训与归一化

- SB3 IPPO：`results/checkpoints/mappo_ablaincourt.zip` 与 `vecnormalize_mappo.pkl`。
- 单智能体 PPO：`ppo_ablaincourt.zip` 与 `vecnormalize.pkl`。
- 多模态 IPPO：`mappo_mm_ablaincourt.zip` 与 `vecnormalize_mappo_mm.pkl`。
- HornsRev2 PPO：`ppo_hornsrev2.zip` 与 `vecnormalize_hr2.pkl`。
- 集中式 FLORIS MAPPO：`mappo_central_ablaincourt.pt`；观测统计在 checkpoint 内。
- FAST.Farm MAPPO：环境与可读标签、完整实验摘要及运行 UUID 共同命名的 `.pt`，检查其中 `obs_keys`、维度、机组顺序、控制量、观测统计及配置／来源身份；最终保存和定期检查点均包含完整软件恢复状态。

FAST.Farm MAPPO 的 `--resume-from` 使用明确的 `new_episode_segment`：从已完成训练迭代恢复模型、优化器、归一化、随机状态、奖励整形、累计控制步／agent 步及下一回合风况序号，并开始新的物理回合。恢复段记录父运行、父实验、父检查点摘要及起始计数，不冒充未中断的物理轨迹。未结束回合的物理状态不恢复，丢弃的回合内步数保留在恢复元数据中；这不支持任意控制步的 FAST.Farm 物理状态续接。

恢复前要求完整、兼容的软件检查点和有效配置／来源身份；只有总目标 `iters` 可以增加，其他有效配置须一致。参数、模型、优化器和统计不兼容时在创建求解器前拒绝；缺少恢复与来源信息的既有检查点仍可按动作合同用于推理，但不能用于该续训入口。物理回合初始化、跨平台及真实求解器运行仍需匹配环境单独验证。

SB3 IPPO 的 `--eval-only` 自动加载模型与归一化文件，缺少任一文件或校验不兼容时明确失败。评估固定 `training=False`、`norm_reward=False`，冻结观测统计并报告原始奖励。`--resume` 恢复模型、观测及奖励统计和累计步数，设置 `training=True`、`norm_reward=True`；`--timesteps` 表示追加的 agent transitions，实际仍按完整 rollout 取整。

算法对比入口 `scripts/analysis/compare_eval.py` 的 IPPO 分支和 `wfrl/viz/animate.py` 的策略动画通过 `load_evaluation_pair()` 加载同一模型／归一化配对，使用冻结统计和原始奖励；缺文件或配对不匹配时停止。该函数返回的环境由调用方关闭，加载失败时函数负责关闭已创建的环境。

SB3 IPPO、多模态 IPPO、单智能体 PPO 与 HornsRev2 PPO 的最终保存均给模型和归一化文件写入相同配对标识，加载时校验标识、观测／动作空间、有限统计量和折扣因子。无配对标识的现有文件可以按兼容性校验加载，但仅凭空间和统计形状不能证明二者来自同次训练，仍须核对来源。

单智能体 PPO 的 `--resume` 和 `--eval-only` 都要求完整的 `ppo_ablaincourt.zip` 与 `vecnormalize.pkl`；缺失、损坏或配对不兼容时停止。续训恢复观测与奖励统计及累计步数，`--timesteps` 表示追加的全场控制步数，按完整 rollout 向上取整。评估冻结统计并报告原始奖励；训练前评估使用当前训练环境统计量的独立副本，新训练不会读取磁盘上的旧统计量。定期检查点同时保存同一步数的 `ppo_wf_<steps>_steps.zip` 和 `ppo_wf_vecnormalize_<steps>_steps.pkl`。正常结束、加载、推理、训练或保存失败时均关闭本次创建的环境。

多模态 IPPO 的 `--eval-only` 直接加载保存模型，无需额外指定 `--resume`；评估固定统计并报告原始奖励。`--resume` 恢复观测／奖励统计和累计 agent transitions，`--timesteps` 表示追加量。归一化仅作用于 `yaw`、`wind_speed`、`wind_direction`、`vibration`；`wake_image` 不使用 VecNormalize 的逐维运行统计。训练、续训和评估的图像模式须一致：使用 `--no-image` 训练的模型，后续也需传入该参数；模式、统计通道或空间不兼容时明确报错。两种模式共用默认输出路径，开始新训练会覆盖该路径的配对文件。

HornsRev2 PPO 的 `--resume` 恢复保存的统计量和累计步数，允许调整 `--n-envs`；新建 worker 的回合回报累加器清零，已学习的统计量保留。`--timesteps` 表示所有独立风场 worker 的追加环境 transition 总数，按 `n_steps * n_envs` 的完整 rollout 向上取整，每个 worker 的一步包含整个 91 机组风场。评估只使用一个风场、冻结统计并报告原始奖励。训练前评估使用当前训练统计的独立副本；定期检查点同时保存同一步数的 `ppo_hr2_<steps>_steps.zip` 和 `ppo_hr2_vecnormalize_<steps>_steps.pkl`。

多模态 IPPO 与 HornsRev2 PPO 的续训／评估均要求完整、兼容的模型和归一化文件；缺失、损坏或不兼容时停止，不回退随机策略或新统计量。正常结束以及加载、预测、训练或保存失败时关闭已创建的环境；HornsRev2 的归一化加载失败也会关闭其 worker。

```bash
python scripts/train/train_mappo.py --eval-only
python scripts/train/train_mappo.py --resume --timesteps 50000
python scripts/train/train_ppo.py --eval-only
python scripts/train/train_mappo_mm.py --eval-only
python scripts/train/train_mappo_mm.py --resume --no-image --timesteps 50000
python scripts/train/train_ppo_hornsrev2.py --resume --n-envs 4 --timesteps 50000
python scripts/train/train_ppo_hornsrev2.py --eval-only
python -m wfrl.mappo_central --eval-only
```

## 4. 计数与比较口径

SB3 IPPO 的 `n_steps=1024`、`num_envs=7`，一次 rollout 收集 7168 个 agent transitions。`total_timesteps=500` 不是 500 次全场求解，也不是“500 × 7”；`learn()` 会先完成当前 rollout，实际计数可超过目标。七个同步样本来自同一耦合风场，不能直接宣称统计独立或样本效率提高七倍。

跨算法与后端比较需明确全场控制步、agent transitions、物理时长、预热、来流、动作约束和奖励口径。FLORIS 是稳态参考，FAST.Farm 是动态模型；固定来流历史 +5.3% 报告与当前柔性回放/重建软件验收分别判定，见[历史尾流控制记录](WAKE_STEERING_BREAKTHROUGH.md)。

源码依据：[SB3 包装实现](../../wfrl/mappo_sb3.py)、[集中式实现](../../wfrl/mappo_central.py)、[动态训练入口](../../scripts/train/train_fastfarm.py)、[Interactive Trainer](../../wfrl/studio/trainer.py)、[全场功率有效性](../../wfrl/scene/power.py)、[Interactive 统计协议](../../wfrl/blender_bridge/training_progress.py)、[统一输出路径](../../wfrl/paths.py)。

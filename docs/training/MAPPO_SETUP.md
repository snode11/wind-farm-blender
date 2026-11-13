# PPO、参数共享 IPPO 与集中式 MAPPO 环境配置

本文按项目 Python 源码区分算法与训练入口，源码入口使用 `main` 分支。Blender 公开安装包与开发机日常安装分别按[发布状态与验证范围](../blender/发布状态与验证范围.md)识别；项目后端与训练工具不随扩展 ZIP 安装。离线演示无需训练环境，[入口见演示指令单](../demos/演示指令单.md)。

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

Bridge 在 POSIX 平台通过 [IsolatedTrainer](../../wfrl/blender_bridge/isolated_trainer.py) 启动 MPI worker，worker 调用 [后端 Trainer](../../wfrl/studio/trainer.py)；其他平台的现有分支直接调用同一 Trainer，平台运行状态须单独验证。该路径复用共享 `Actor`、集中式 `Critic` 和观测统计结构：actor 读取本机观测，critic 读取全场拼接观测，每个控制步使用一个团队奖励及相应标量优势。Formal Training 运行 `scripts/train/train_fastfarm.py`，不使用这里的 Interactive 更新循环；SB3 路线的采样、归一化和更新由其自身实现负责。

Interactive 记录未经过 `tanh` 和动作约束层的高斯采样动作。采样与更新均只沿动作维汇总 log-prob，缓存形状为 `(steps, agents)`。每个机组分别计算 `ratio = exp(new_logp - old_logp)` 并进行 PPO 裁剪；多控制量先组成该机组的联合动作概率，不逐分量独立裁剪，也不把所有机组的概率合并为一个 ratio。团队优势广播到 `(batch, agents)` 后统一标准化，单元素时使用零标准差以避免非有限值；代理目标对时间步和机组取均值。熵项对高斯分布的动作维求和，再对时间步与机组取均值。

Interactive 的 `episode_steps` 指每回合用于训练的控制步数，默认 `0` 表示单长回合；正值按回合关闭并重建 runtime，每回合执行 `warmup_steps` 次预热。预热不进入训练样本，总训练预算仍为 `iters * n_steps`，最后一个回合按剩余步数缩短。一个 rollout 可以跨回合；回合末使用重置前终点的 critic 值自举，并切断跨回合 GAE 递推。后端提前结束时明确报错，不继续采样已停止的 runtime。

回合切换与训练收尾使用严格关闭：排空、环境关闭或 MPI 子通信器断开无法确认时报告失败并阻止启动下一回合；不把清理异常视为正常回合完成。暂停和取消在控制步、回合启动及预热边界检查。

`step` 和迭代计数贯穿训练，`episode` 从零编号。FAST.Farm 快照的仿真时间按当前物理回合的已完成 MPI 交换序号与实际步长重建，包含初始化和预热；来源字段保留这一计算方法，`control_elapsed_seconds` 单独表示相对控制时间。每个新算例重新计时，不沿用上一回合的原点。快照以顶层 `payload.episode` 传递回合；当前源码前端仅在顶层缺字段时兼容旧的 `payload.context.episode`，零值有效。实时曲线在回合变化或时间回退处断线，保留同一全局步数的跨回合边界样本与训练统计，合同见[快照与曲线说明](../../blender_frontend/README.md)。Blender 工作区的 **WFRL / 后端运行 → Episode steps** 设置仅用于 Interactive；安装版需要匹配扩展载荷，后端改动需重启 Bridge。

环境交给奖励整形器的值为 `mean(power_MW * 1e3 / u**3)`，有载荷测量时再减去 `load_coef` 乘叶根弯矩（N·m 除以 1e7）绝对值的均值；`u` 来自 `next_state.freewind_measurements` 并裁到状态空间风速范围。FAST.Farm 功率与来流的采样窗口存在下节所述偏差。训练奖励缩放使用折扣累加器 `G = gamma * G + reward` 和二阶矩指数移动平均 `M = 0.99 * M + 0.01 * G**2`。`G` 在同一回合跨 rollout 保留，并在回合边界清零；`M` 跨回合保留。本轮实际采集的奖励统一除以本轮末的 `sqrt(M) + 1e-8` 后计算 GAE。该实现不等同于 SB3 `VecNormalize`。仪表板的 `mean_raw_reward` 来自本轮原始奖励均值，归一化 `mean_reward` 通道没有输出，不将两者混称。

全场功率只有在同一步的所有预期机组都具有有限标量读数且合计有限时有效。缺测在 Trainer 内部保留为 `NaN`；迭代 `mean_power` 使用本轮实际收集窗口的全部控制步，任一步全场功率无效就将整轮统计通过协议输出为 `null / invalid`，不忽略缺测样本计算部分均值。实时功率通道完全未返回时保留 `unsupported`；真实零功率仍是有效数值。有效机组自己的通道不因其他机组缺测而被抹除。

以上定义属于当前后端源码，不据此宣称 Blender 安装包、日常扩展或已有 checkpoint 的运行效果已验证；训练收敛、物理控制收益和跨平台运行须另有匹配证据。

### 奖励归一化要求与实际状态

**有效要求：** reward 应使用同一步的功率和来流进行归一化。

**实际状态：** FAST.Farm 的 MPI 路径经 `MDP.step_interface()` 调用 `avg_powers()`；[PowerBuffer.get_agg(window)](../../wfcrl-env/wfcrl/interface.py) 的切片包含当前位置及前 `window` 个位置。`window=1` 且已有两条样本时，功率取最近两条的均值；`get_measure("freewind_measurements")` 则经 `last_wind(window=0)` 只取最新一条。奖励分母虽然读取 `next_state`，分子与分母仍不严格同步，不能据此写成要求已满足。只有一条可用样本时，功率均值只含该条。

**当前决定：** 为保持与既有训练结果可比，暂不修改这一实现偏差；同一步归一化的要求仍有效。该偏差属于 MPI 功率缓冲路径，不应套用到直接读取当前稳态功率的 FLORIS 接口，也不同于 Trainer 或 SB3 在奖励之后执行的统计缩放。依据为 [MDP](../../wfcrl-env/wfcrl/mdp.py)、[多智能体环境](../../wfcrl-env/wfcrl/multiagent_env.py)及[单智能体环境](../../wfcrl-env/wfcrl/simple_env.py)的实际调用链。

### 策略动作与 checkpoint

项目使用 [PolicyActionContract](../../wfrl/policy_actions.py) 描述高斯网络输出到控制增量的变换。checkpoint 的 `action_contract` 包含 `version=1`、有序 `controls`、`transform`、逐通道 `low` 和 `high`。`tanh_scale` 要求对称边界，计算 `tanh(raw) * high`；`clip` 将原始输出裁剪到保存的上下界。变换结果随后进入约束层，PPO 缓存仍保留原始高斯动作及其 log-prob。

Interactive Trainer 使用 `tanh_scale`，包括仅控制 yaw 或单个非 yaw 控制量的场景；Replay 从 checkpoint 读取同一合同，不按动作维度猜测变换。`train_fastfarm.py` 的高斯 yaw 策略使用 `clip`，其 checkpoint 与命令行 rollout 保持相同规则。控制顺序、动作和观测维度、归一化统计、机组数量及运行时边界须一致；保存了场景的模型还校验后端、机型、时间步长与机组布局。

Replay 在后端启动、预热和控制步边界响应暂停与停止。最终快照的 `step` 使用实际完成的策略控制步数，预热不计入；只有达到请求的 `replay_steps` 才将 `iters_done` 置为 `1`。取消或后端提前结束保留实际计数。

无版本动作合同的模型只接受可识别的保存格式：Trainer 的 `scene`、`controls`、`critic` 组合对应 `tanh_scale`；CLI 的完整 `env_id`、`obs_duty`、`recurrent`、`duty_penalty` 组合对应 yaw 的 `clip`。该兼容路径使用固定控制边界，并须与实际运行时一致；缺失、冲突或无法判定的元数据明确报错。没有保存场景的 CLI 模型不能仅凭现有字段证明完整布局、机型和时间步长匹配。

研究探针 `FlexPolicy` 只接受可识别的 `Dec_Turb3_Row1_Fastfarm` yaw-only、非循环模型，可带 `obs_duty` 观测；固定动作边界为 ±5°、变换为 `clip`。它复用合同校验及 checkpoint 的 `obs_duty_limit`，并拒绝保存 `scene` 的模型及未知版本，不拓展普通 Replay 的模型适用域。

### 执行机构占空比与观测余量

[FastFarmDriver](../../wfrl/fastfarm_driver.py) 的测量同时提供逐机组 `duty` 与逐控制量 `duty_limit`。yaw 默认使用最近 20 个控制步的滑动窗口，上限直接读取运行配置 `yaw_duty_limit`（默认 `0.30`）；pitch 使用 env 累计版 `0.10`，关闭 yaw 滑动窗口时 yaw 也使用累计版 `0.10`。torque 不受该占空比约束，因此没有对应条目。清零判据为动作前的 `duty >= duty_limit`，不是按本步动作幅度提前限幅。

[SafetyLimiter](../../wfrl/safety.py) 的 `budget()`、动作门控和事件说明均使用测量中的逐控制量上限；测量没有 `duty_limit` 或缺少该控制量时回退既有 `DUTY_LIMIT=0.1`。Studio 默认 `enforce_duty=True`：窗口 yaw 占空比为 `0.2` 时放行，为 `0.3` 时清零，与默认 Driver 一致；pitch 在 `0.1` 清零。关闭强制时保留请求，只对达到相应上限的控制量报告后端清零事件。`sustainable()`、`reachable()`、`notes()` 不读取测量，仍为上限 `0.1` 的 env 累计模型估算；`notes()` 明示此口径，可达量不含状态边界，也不代表 yaw 滑动窗口的可达上界。

正式 CLI 开启 `--obs-duty` 后，从初始运行测量取得 yaw 上限，在训练期间固定使用，并在定期和最终 checkpoint 中保存 `obs_duty_limit`。换回合的上限必须一致。余量列统一调用 [duty_headroom()](../../wfrl/policy_actions.py)，计算 `clip((limit - duty_yaw) / limit, 0, 1)`；默认窗口上限 `0.30` 下 duty 为 `0.20` 时余量约为 `1/3`。余量是占空比比例，不是可下发增量的幅度。Studio 自身训练不启用 `obs_duty`。

Studio Replay、`rollout_ckpt.py` 和 `FlexPolicy` 均读取 checkpoint 的 `obs_duty_limit`，不以当前运行上限替换观测编码。没有该字段的 checkpoint 固定按 `0.1` 计算，保留对应入口的输入精度与观测向量；显式字段必须为有限正数。正式 CLI 续训除核对 `obs_duty` 开关、网络及恢复状态外，还将保存的观测上限与 sampler 的实际上限比较，不一致时拒绝训练并严格关闭 sampler；旧 checkpoint 按 `0.1` 参与同一比较。

### 检查点写入与产物选择

[training_integrity.atomic_write()](../../wfrl/training_integrity.py) 在目标文件的同一目录创建临时文件，写完并 `fsync` 后读取校验，再以 `os.replace()` 原子发布；失败时移除临时文件。各入口的保存范围如下。

| 入口 | 当前保存行为与限制 |
| --- | --- |
| 正式 CLI `train_fastfarm.py` | 定期和结束检查点均用 `save_checkpoint_exclusive()`，通过 Torch 重读校验；发布前若目标已存在则拒绝覆盖。保存完整软件恢复状态。 |
| Studio / Interactive `Trainer._save()` | 用 `save_checkpoint_atomic()`，通过 Torch 重读校验后原子替换指定路径；失败保留原文件。内容为 actor、critic、观测统计、场景和动作合同，不等于正式 CLI 的完整恢复检查点。 |
| SB3 参数共享 IPPO `wfrl/mappo_sb3.py` | 模型、归一化统计分别写入同目录临时文件并加载校验，以相同 `generation` 写为独立产物；两者就绪后原子替换清单，切换可读取的一代。 |
| 集中式 FLORIS MAPPO `wfrl/mappo_central.py` | actor、critic 和观测统计保存在同一个 `.pt` 中；用 `save_checkpoint_atomic()` 写同目录临时文件，通过 Torch 重读校验后原子替换；失败保留原文件。 |
| 单智能体 PPO、多模态 IPPO、HornsRev2 PPO | 共用 `train_ppo.CheckpointPair`，通过 `atomic_write()` 写入、加载校验同代模型与归一化统计，再原子切换清单；PPO 和 HornsRev2 的定期保存也使用此合同。多模态校验只要求标量与振动通道的归一化统计。 |

参数共享 IPPO 的入口清单为 `results/checkpoints/mappo_ablaincourt.manifest.json`，引用 `mappo_ablaincourt.<generation>.zip` 与 `vecnormalize_mappo.<generation>.pkl`，并保存两者 SHA-256。加载先解析一代并校验文件名、摘要及模型／统计兼容性；加载中的配对不会因清单切换混用新旧代。发布新代后保留旧代文件；提交失败不切换已发布配对。仅在清单不存在时读取固定路径 `mappo_ablaincourt.zip` 与 `vecnormalize_mappo.pkl`；清单或其引用文件损坏时明确失败，不回退到固定路径。

单智能体 PPO、多模态 IPPO、HornsRev2 PPO 使用相同的清单格式与提交顺序，最终入口分别为 `ppo_ablaincourt.manifest.json`、`mappo_mm_ablaincourt.manifest.json`、`ppo_hornsrev2.manifest.json`。模型为 `<模型基名>.<generation>.zip`，统计为 `<统计基名>.<generation>.pkl`；清单记录 `schema=1`、同代标识、两个文件名及 SHA-256。恢复归一化时同时固定模型路径，后续模型加载不重新选择一代。只有清单不存在才读取原固定名称；损坏清单或引用产物明确失败。发布新代保留旧代与旧固定名称文件；序列化、校验或提交失败不改变已发布配对，移除未提交产物和临时文件。清单已经替换后发生中断时，保留已提交的完整配对。

实现与合同用例：[SB3 配对保存](../../wfrl/mappo_sb3.py)、[原子保存与恢复用例](../../tests/test_bp_checkpoint_recovery.py)、[四个训练入口的失败与兼容读取用例](../../tests/test_bx_training_checkpoint_atomicity.py)。入口见 [PPO](../../scripts/train/train_ppo.py)、[多模态 IPPO](../../scripts/train/train_mappo_mm.py)、[HornsRev2](../../scripts/train/train_ppo_hornsrev2.py)、[集中式 FLORIS MAPPO](../../wfrl/mappo_central.py)。

### 回合结束与时间截断

SB3 包装器在自动重置前保存各机组的完整 `terminal_observation`，包括派生观测字段；只有截断且没有真正终止时才设置 `TimeLimit.truncated=True`，供 SB3 用终点观测计算价值自举。共用风场要求机组同步结束，不能在部分机组结束后把其复位状态当作普通下一步。

集中式 FLORIS MAPPO 分开处理价值自举和 GAE 递推边界：真正终止的下一状态价值为零；步数上限截断使用重置前终点的 critic 值；两种回合边界都截断优势递推，避免下一回合的奖励传播回来。普通 rollout 切片使用连续轨迹的下一状态价值。`--seed` 同时设定 PyTorch、NumPy 小批量打乱及每回合环境重置种子，同一种子重复相同的抽样来流序列。这些规则分别由对应入口实现，不代替其他训练路线的算法定义。

### FAST.Farm 实验身份与 GRU

`train_fastfarm.py` 使用完整有效配置和源码／实际输入的内容身份确定 `experiment_id`，每次执行另有独立 `run_id`。配置包含训练预算、预热、回合、奖励及其参数、PPO 参数、观测、风况、种子和循环 critic 设置；源码身份覆盖项目训练代码、WFCRL、求解器／模板／风文件及运行依赖。产物名由可读标签、实验摘要和运行 UUID 组成，已有检查点和历史文件不会被静默覆盖。

正式 CLI 的 sampler 在初始化、预热或观测打包失败后关闭已获取的 driver；换回合须确认严格关闭成功。关闭失败保留 driver／runtime 归属并阻止下一回合，初始化清理失败的异常携带 `driver` 或 `sampler` 引用。PPO 的团队优势标准化对单元素使用零标准差，适用于单机单步和单元素尾批。

自定义 Scene YAML 的 `dt` 使用有限正整数秒，小数步长在创建 case 前拒绝，case、runtime 与 limiter 使用同一步长。非 Scene 的 `--backend floris` 路径保留显式 `--wind-direction`，并在风况调度与实验身份中使用它；未指定方向时，`preset:veer` 等预设可提供 FLORIS 风向。FAST.Farm 的显式 `--wind-direction` 也经配置、调度和采样器传到底层校验：270°及其等价值匹配固定坐标，其他未支持方向拒绝，不执行任意坐标旋转。Bridge Formal 仍只接受 FAST.Farm。

Blender Bridge 停止时等待训练线程、隔离 worker 或正式进程清理完成；资源归属未确认时保留错误状态并阻止重新启动。正式进程的信号、退出码和清理确认分别按[停止合同](../development/BlenderBridge协议.md#正式训练的停止与清理)处理。转矩修正同时满足单步与累计边界；当前值距 VS 包线过远时逐步接近，事件记录实际可提交的 float32 增量，不保证一步进入包线。缺失、布尔、非实数或非有限功率保留为 NaN，不补零。

场景传感器 `NacelleLidar` 为合成示意：轮毂均值、幂律切变和确定性正弦脉动，可读取 BTS 的均值／TI 统计量，尚未实现三维 BTS 插值。输出标识 `source_kind=SYNTHETIC`、`bts_spatial_interpolation=False`；不作为实测或前馈收益证据。

`run_stage3.py` 仅复用配置、来源、完成预算、终态、历史和完整检查点全部对应的独立完成任务；检查点字节须匹配历史中的摘要。空文件、部分训练、缺少身份的既有结果及恢复训练段都不能被视为新的完整实验已完成。改变有效配置会形成不同实验身份，可用 `--dry-run` 检查待运行组合而不启动求解。

`scripts/analysis/compare_stage3.py` 按相同来源身份及完整公共配置配对零偏航基线与学习策略，仅允许 `policy` 和迭代预算 `iters` 不同；种子、场景、观测、奖励参数和 PPO 参数均须匹配。比较要求完整完成的独立新运行、有效实验身份、匹配预算的有限功率历史和检查点摘要，不将恢复段或缺少身份的历史文件纳入统计。缺少配对或存在多个候选时停止，可重复指定 `--pair <zero_run_id>:<learn_run_id>` 明确选择；同一来源、配置和 seed 只计一对。不同来源或训练预算分别统计，图表只使用通过配对校验的运行。`--last N` 取学习策略末 N 轮，基线取其全部轮次；控制台保留来源、公共配置和双方运行标识。

`--recurrent` 只作用于集中式 critic。采样逐步维护隐藏状态，rollout 边界保留并 detach，回合起点清零；训练使用该 rollout 起点的隐藏状态及逐步回合起点标记。截断的 bootstrap 使用重置前观测和同一历史条件，但不提前消耗下一控制步的隐藏状态。数值一致性不代表 GRU 对前馈网络的控制收益已得到物理训练验证。

观测统计在当前动作执行后纳入当前原始观测，再计算下一状态的 bootstrap；连续步的 bootstrap 与下一次决策估值使用同一份统计。截断处仍使用 reset 前观测，GAE 递推在回合边界停止。

## FastFarmDriver 测量与交换预算

### 测量有效性与单位

[FastFarmDriver._read()](../../wfrl/fastfarm_driver.py) 的 `load` 与 `yaw` 缺测时保留 `NaN`，并逐机组返回 `load_valid`、`yaw_valid`、`yaw_command_valid`、`yaw_measured_valid`。`load` 只有在提供的分量非空且全部有限时才是其绝对值均值；真实零值有效，不以零代替缺测。

控制 yaw 时，`yaw` 与 `yaw_command` 表示按度累计的控制指令，`yaw_measured` 单独表示求解器回读。未控制 yaw 时，FAST.Farm 原生弧度观测换算为度，`yaw_command=None` 且其有效掩码全为 false；FLORIS 的角度本来就是度，不重复换算。原生 yaw 回读缺失时 `yaw_measured` 为 `NaN`，不借控制指令补作实测。

### 交换预算与结束

FAST.Farm 的 `start_iter=S`、驱动 `max_steps=M` 对应总共 `S+M` 次 MPI 交换。`MDP.reset()` 消耗 `S+1` 次（含初始观测），之后只剩 `M−1` 次真实控制交换；调用方的 `warmup_steps=W` 也从这部分扣除，可供后续控制的交换为 `M−1−W`（预热须在可用预算内）。`max_steps` 不能直接解释为 reset 后还能训练的步数；CLI sampler 与 Studio 按训练预算另加预热和收尾余量。

驱动在最后一次真实交换返回的测量中即报告 `done=True`，不需要再经过一次 AEC dead-agent 清理轮才结束。`M=1` 时 reset 即已用完预算；结束后再次调用 `step()` 不推进交换或增加控制步数。回合终止／截断标记也参与同一结束判断。依据为 [WFCRL 环境预算](../../wfcrl-env/wfcrl/multiagent_env.py)、[MDP reset](../../wfcrl-env/wfcrl/mdp.py)及[Driver 合同用例](../../tests/test_fastfarm_driver_bn_contract.py)。

### 清理与算例保留

`close()` 只有在剩余交换排空、环境关闭和 MPI 断开都确认后才设置 `cleanup_confirmed=True`；此时 `purge=True` 才可删除算例目录。排空预算耗尽、关闭或断开失败时保留目录、资源归属与 `cleanup_error`，不以清理请求代替完成确认。`strict=True` 抛出错误，非 strict 返回 false；再次关闭不重复排空，未确认时拒绝 reset。异常中断仍重新抛出。对应[失败与幂等关闭用例](../../tests/test_fastfarm_driver_bn_contract.py)。

### 安全监测的范围

[SafetyLimiter.monitor()](../../wfrl/safety.py) 在某机组 `rotor_speed_interval_samples > 0` 且峰值有限时，使用 `rotor_speed_interval_max` 判定超速，并在可用时记录峰值时刻；否则使用最新有限的 `rotor_speed`。超出 `RPM_ALARM` 才产生 `block` 级别事件，缺测不推断、低转矩不撤销超速事件。

[SolverOutputs](../../wfrl/solver_outputs.py) 的峰值来自 `OutputReader.advance()` 本次新接纳的全部完整原生行，包括先前暂存、此刻已到时间的行；只接受不晚于当前求解时刻的行，没有按当前控制窗起点设置时间下界。因此首次读取或积压读取可能包含更早时刻，不能把该峰值解释为严格限定在最近一个控制窗内。

Studio 的 Replay 与 Interactive 在执行控制步后调用 `monitor()`，事件进入快照和事件日志（limiter 的内存历史与计数），由 Bridge 转为 `safety_event`。该监测的 `block` 只是事件严重级别，不停止训练、不改动作或奖励；与执行前 `apply()` 的动作约束分开。正式 CLI `FastFarmSampler` 的采样路径不调用 `monitor()`，不能据正式训练无事件推断没有超速。依据为 [Trainer](../../wfrl/studio/trainer.py)、[正式采样器](../../scripts/train/train_fastfarm.py)、[峰值读取用例](../../tests/test_solver_outputs.py)及[监测用例](../../tests/test_safety_limiter.py)。

## FAST.Farm 风况合同

工作区驱动返回 `wind_conditions`，分别保存调用方请求、实际解析／回读的输入和文件／运行采样统计。`upstream_request.scene` 保留场景显示风向，`requested` 保留实际求解请求；显示方向不自动传作求解方向，显式训练请求则保留并传递。稳态风可指定有限正风速；BTS 不接受标量风速覆盖；显式270°仅记为 `MATCHES_FIXED_COORDINATES`，不执行坐标旋转。未支持的风况到达接口时以 `WindRequestError` 明确拒绝。BTS 超出时长目前记录警告，场内独立入流统计为 `unavailable`。

`--wind-sched preset:turb` 在无场景配置时被拒绝，BTS 训练通过 `--scene` 的 `inflow.turbulence` 指定。原始方向在部分入口中未保留，不能将入口规范化等同于接口拒绝。完整字段、保存位置、入口冲突及待实现限制见[FAST.Farm 风况](FASTFarm风况.md)。

## 2. 依赖与安装

仓库的 `wfcrl-env/requirements-floris-local.txt` 保留 Windows + Python 3.11 的历史兼容组合：FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 GUI 依赖。根 [pyproject.toml](../../pyproject.toml) 将 PyYAML 与 psutil 声明为核心依赖；普通安装、editable 安装和 wheel 均提供内置机型 YAML。`train` / `gui` / `all` 是功能附加组，训练仍需匹配的 WFCRL 环境，FAST.Farm 路径另需外部求解器与控制器，不能用普通 Python 包或 Blender ZIP 代替。[安装范围见本地配置](../setup/setup_local.md#2-macos-完整-python-环境)。

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

### 正式 CLI 的中断与恢复

`train_fastfarm.py` 在训练主线程注册 SIGINT，并在平台提供 SIGBREAK 时注册同一协作式中断处理器（Windows 的 CTRL_BREAK 对应 SIGBREAK）。信号只标记停止请求，采样与优化继续到下一个完整更新边界，随后保存检查点并退出；`save_interval=0` 只关闭定期保存，中断和正常结束仍保存。未完成总目标时，运行记录为 `interrupted`，中断检查点在物理后端关闭之前保存；若完整目标已完成，状态为 `completed`。这不保证求解器阻塞或进程被强制终止时仍能保存。

FAST.Farm MAPPO 的 `--resume-from` 使用明确的 `new_episode_segment`：从已完成训练迭代恢复模型、优化器、归一化、随机状态、奖励整形、累计控制步／agent 步及下一回合风况序号，并开始新的物理回合。恢复段记录父运行、父实验、父检查点摘要及起始计数，不冒充未中断的物理轨迹。未结束回合的物理状态不恢复，丢弃的回合内步数保留在恢复元数据中；这不支持任意控制步的 FAST.Farm 物理状态续接。

恢复前要求完整、兼容的软件检查点和有效配置／来源身份；只有总目标 `iters` 可以增加，其他有效配置须一致。`validate_resume()` 校验完整迭代数、历史及 `recovery`／`run` 中的累计控制步与 agent 步；`resume_counts()` 还将 `training_progress.step`／`agent_step` 与这两份计数交叉核对。缺少 `training_progress` 时，仅从明确的迭代数、历史配置及维度推导计数。类型非法、计数不一致或参数、模型、优化器、统计不兼容时，在创建后端前拒绝，即使本次没有启用进度输出也执行校验。

缺少恢复与来源信息的既有检查点仍可按动作合同用于推理，但不能用于该续训入口。信号、保存和恢复的静态依据为 [CLI](../../scripts/train/train_fastfarm.py)、[恢复校验](../../wfrl/training_integrity.py)、[进度计数](../../wfrl/blender_bridge/training_progress.py)及[检查点恢复用例](../../tests/test_bp_checkpoint_recovery.py)。Windows 实机尚未验证；物理回合初始化及真实求解器运行需匹配环境单独验证。Bridge 的超时终止与结果判定见[正式进程停止](../development/BlenderBridge协议.md#正式训练的停止与清理)。

### 评估、续训与归一化

下列产物均位于 `results/checkpoints/`，原子保存和覆盖范围见[检查点写入与产物选择](#检查点写入与产物选择)。

- SB3 IPPO：`mappo_ablaincourt.manifest.json` 引用的同代 `.zip`／`.pkl`；无清单时才使用固定名称配对。
- 单智能体 PPO：`ppo_ablaincourt.manifest.json` 引用的同代产物；无清单时使用 `ppo_ablaincourt.zip` 与 `vecnormalize.pkl`。
- 多模态 IPPO：`mappo_mm_ablaincourt.manifest.json` 引用的同代产物；无清单时使用 `mappo_mm_ablaincourt.zip` 与 `vecnormalize_mappo_mm.pkl`。
- HornsRev2 PPO：`ppo_hornsrev2.manifest.json` 引用的同代产物；无清单时使用 `ppo_hornsrev2.zip` 与 `vecnormalize_hr2.pkl`。
- 集中式 FLORIS MAPPO：`mappo_central_ablaincourt.pt`；观测统计在 checkpoint 内。
- FAST.Farm MAPPO：环境与可读标签、完整实验摘要及运行 UUID 共同命名的 `.pt`，检查其中 `obs_keys`、维度、机组顺序、控制量、观测统计及配置／来源身份；最终保存和定期检查点均包含完整软件恢复状态。

SB3 IPPO 的 `--eval-only` 自动加载模型与归一化文件，缺少任一文件或校验不兼容时明确失败。评估固定 `training=False`、`norm_reward=False`，冻结观测统计并报告原始奖励。`--resume` 恢复模型、观测及奖励统计和累计步数，设置 `training=True`、`norm_reward=True`；`--timesteps` 表示追加的 agent transitions，实际仍按完整 rollout 取整。

算法对比入口 `scripts/analysis/compare_eval.py` 的 IPPO 分支通过 `load_evaluation_pair()` 加载同一模型／归一化配对，使用冻结统计和原始奖励；缺文件或配对不匹配时停止。该函数返回的环境由调用方关闭，加载失败时函数负责关闭已创建的环境。

SB3 IPPO、多模态 IPPO、单智能体 PPO 与 HornsRev2 PPO 的最终保存均给模型和归一化文件写入相同配对标识，加载时校验标识、观测／动作空间、有限统计量和折扣因子。无配对标识的现有文件可以按兼容性校验加载，但仅凭空间和统计形状不能证明二者来自同次训练，仍须核对来源。

单智能体 PPO 的 `--resume` 和 `--eval-only` 都按清单选择完整配对，无清单时读取 `ppo_ablaincourt.zip` 与 `vecnormalize.pkl`；缺失、损坏或配对不兼容时停止。续训恢复观测与奖励统计及累计步数，`--timesteps` 表示追加的全场控制步数，按完整 rollout 向上取整。评估冻结统计并报告原始奖励；训练前评估使用当前训练环境统计量的独立副本，新训练不会读取磁盘上的旧统计量。定期检查点由 `ppo_wf_<steps>_steps.manifest.json` 原子选择 `ppo_wf_<steps>_steps.<generation>.zip` 和 `ppo_wf_vecnormalize_<steps>_steps.<generation>.pkl`。正常结束、加载、推理、训练或保存失败时均关闭本次创建的环境。

多模态 IPPO 的 `--eval-only` 直接加载保存模型，无需额外指定 `--resume`；评估固定统计并报告原始奖励。`--resume` 恢复观测／奖励统计和累计 agent transitions，`--timesteps` 表示追加量。归一化仅作用于 `yaw`、`wind_speed`、`wind_direction`、`vibration`；`wake_image` 不使用 VecNormalize 的逐维运行统计。训练、续训和评估的图像模式须一致：使用 `--no-image` 训练的模型，后续也需传入该参数；模式、统计通道或空间不兼容时明确报错。两种模式共用默认清单路径，新训练成功保存后将清单切换到新代，旧代文件保留。

HornsRev2 PPO 的 `--resume` 恢复保存的统计量和累计步数，允许调整 `--n-envs`；新建 worker 的回合回报累加器清零，已学习的统计量保留。`--timesteps` 表示所有独立风场 worker 的追加环境 transition 总数，按 `n_steps * n_envs` 的完整 rollout 向上取整，每个 worker 的一步包含整个 91 机组风场。评估只使用一个风场、冻结统计并报告原始奖励。训练前评估使用当前训练统计的独立副本；定期检查点由 `ppo_hr2_<steps>_steps.manifest.json` 原子选择 `ppo_hr2_<steps>_steps.<generation>.zip` 和 `ppo_hr2_vecnormalize_<steps>_steps.<generation>.pkl`。

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

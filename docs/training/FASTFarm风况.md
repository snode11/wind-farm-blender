# FAST.Farm 风况请求、输入与统计

本文描述工作区 [fastfarm_driver.py](../../wfrl/fastfarm_driver.py) 与 [interface.py](../../wfcrl-env/wfcrl/interface.py) 的风况接口。核对请求、读取输入与统计接收数据，不代表完成新的风场生成、风向旋转、数值收敛或实机验证。

## 三层记录与保存

`FastFarmDriver.reset()` 先通过 `FastFarmInterface.set_wind_request_context()` 保留调用方意图，再交给环境重置。返回测量及后续测量中的 `wind_conditions` 是当前 `wind_record` 的独立深拷贝，合同为 `fastfarm-wind-conditions.v1`。

| 层次 | 字段与实际含义 |
| --- | --- |
| 上层上下文与求解请求 | `upstream_request` 保留场景／训练入口的原始配置及用途，不参与求解参数校验；`requested` 保留传给驱动的实际风速、风向、BTS选择及额外参数。`environment_reset` 单独记录环境实际转发值；`application` 逐项记录应用、固定坐标匹配、文件决定或 `not_applied` 原因 |
| 实际输入 | `loaded_inputs` 保存解析后的 FAST.Farm、InflowWind、BTS、各机组主输入及 ElastoDyn 文件路径／SHA-256／字节数；另存风模式、时间步、TMax、机组坐标与初始机舱偏航。稳态风速写入后重新读取核对，显式选择的 BTS 与实际引用文件核对内容哈希 |
| 统计 | `statistics` 分开保存加载 BTS 的文件统计、已声明的背景／阵风叠加文件统计、MPI 接收的机组轮毂风速／风向统计，以及下游机组统计；无法提供的项目明确为 `unavailable` |

驱动指定 [paths.py](../../wfrl/paths.py) 的 `RUNS`，即仓库 `results/runs/`，在其 `wind_conditions` 子目录按记录 ID 保存 JSON；该目录按需创建，生成算例清理不会删除这些记录。直接调用环境接口且未指定记录目录时，保存到 FAST.Farm 主输入同目录的 `wind_conditions` 子目录。完整绝对位置在 `record_path`，拒绝异常也携带同一记录；写入采用 UTF-8 临时文件和原子替换。状态可为 preparing、validated、prepared、running、completed、rejected 或 failed。

输入身份只覆盖上述文件，不是所有求解依赖的完整清单。`loaded_inputs` 的运行说明表示这些解析文件已提交给 MPI 启动调用；没有求解器回传文件哈希确认。文件核对不能替代运行中独立自由入流探针。

### 场景显示方向与求解方向

[SceneRuntime.reset()](../../wfrl/scene/runtime.py) 在 `upstream_request.scene` 保存场景风速、风向和BTS选择。`wind_direction_role=display_only` 表示场景风向只用于显示；稳态场景风速标为 `solver_request`，BTS场景的标量风速标为 `overridden_by_bts`，实际请求风速为 `None`。独立传入 `reset(wind_direction=...)` 才构成求解方向请求。

例如场景显示方向为280°、未给求解方向时，记录保留 `upstream_request.scene.wind_direction=280`，而 `requested.wind_direction=None`；这不是把280°提交给求解器后忽略。显式请求280°则保留在 `requested` 中并由底层拒绝，显式270°只进行固定坐标匹配。驱动将上层上下文复制到本次新记录，成功和 `WindRequestError` 拒绝均保存；重置在新记录建立前失败时，不把上下文写入上一回合。

## 接受与拒绝的请求

接口当前接受 `Mod_AmbWind=2`，且 InflowWind 为稳态 `WindType=1` 或 BTS `WindType=3`，要求 `PropagationDir=0`、`VFlowAng=0`。`WindRequestError` 保存 `record` 和逐项 `not_applied` 原因，在启动求解器之前拒绝以下情况：

- 风速为布尔、字符串、非有限、零或负数；BTS 模式指定任意标量风速，即使等于文件均值；调用方风速与环境转发值不一致。
- 风向不是与270°模360°等价的有限数值，或请求／转发风向任一不匹配；容差为 `1e-8°`。布尔、字符串及非有限风向也拒绝。
- TI、剪切、阵风、随机种子等额外物理参数，以及任何不支持的键，包括值为 `None` 的未知键；接口没有对应的输入生成或修改实现。
- 不支持的环境风模式、风类型或传播／垂向角；无效机组坐标／偏航、机组数量不符、非正或非有限的 `DT_Low`、`DT_High`、`TMax`。
- 输入不可读、BTS 缺失／内容与选择不符、风速写入回读不符、准备时风场文件身份变化；BTS 网格／时间轴或统计高度无效、回合时长非正或非有限。
- 非法 `bts_role`，稳态模式提供 BTS 证据，或在未声明 `gust_superposed` 时提供独立 `background_bts`。

请求预检失败不写入风速；准备和回读失败时恢复 InflowWind 原字节，不启动求解器。可执行文件校验或 MPI 启动错误另存 failed 并抛出相应异常，不能统一称作 `WindRequestError`。

稳态模式允许有限正风速并核对 `HWindSpeed`；未指定时，驱动保留环境的风速抽样，记录中不会把抽样值冒充用户请求。BTS 以实际加载文件为准，不使用模板里未生效的 `HWindSpeed` 覆盖风场。

## 固定坐标与270°

显式270°及其模360°等价值记为 `MATCHES_FIXED_COORDINATES`，仅核对固定坐标约定：全局 +X 为下游、+Y 为横向、+Z 向上，布局和风向量变换均为 identity。求解器传播角为0°，公开风向按 `(degrees(yaw_error_rad + nacelle_yaw_rad) - 90) % 360` 表示，零机舱偏航与 +X 对齐。偏航指令仅转换为弧度，没有方位角偏移。

这不是坐标旋转，也不是将湍流每个时刻的方向指定为270°。任意风向需要布局、风场、机舱姿态、控制和结果坐标的一致转换，当前**待实现**；不能仅改风向标签宣称生效。

## BTS 时长与统计范围

每次重置读取实际 BTS，在轮毂高度对纵向 `u` 逐时刻线性插值，逐横向点计算完整文件时域的均值、总体标准差和 TI，不把空间剪切混入时间方差。BTS 本身不说明背景／阵风分量：`bts_role` 仅为调用方声明，`background_bts` 仅提供对照证据，不改变求解输入。没有独立来源时相应统计为 `unavailable`。

`verification.bts_duration` 比较文件时间轴末值减首值与驱动的 `max_steps * dt`；直接调用接口而没有驱动时长时使用 FAST.Farm `TMax`。范围内记 `within_bts_duration`，超出记 `exceeds_bts_duration` 并在启动前打印、保存警告。**当前超时长不触发拒绝**，超出部分由 InflowWind 处理，可能周期回绕或报错；该检查不保证完整计算域的时空覆盖。

这一时长参数不等于 reset 后可用的控制交换数：`start_iter=S`、`max_steps=M` 的总预算为 `S+M`，reset 使用 `S+1`，后续只剩 `M−1`，调用方预热也从中扣除。最后一次真实交换即返回 `done`；[交换预算与结束](MAPPO_SETUP.md#交换预算与结束)说明驱动与训练预算的区别。

MPI 统计在接口 running 状态下按接收交换累计，保留无效样本数量；风速采用总体标准差，风向采用圆周均值及合向量长度。记录保留初始化／预热／关闭排空范围的说明，首个接收样本为时间原点，绝对求解时刻没有随 MPI 传入。`control_start_sample_index` 在驱动重置返回前记录，调用方之后追加的预热不会自动重新标记。

`statistics.turbine_hub` 是 DISCON 轮毂风速，不是背景纵向风速、转子面积均值或独立自由来流。`downstream_wake_affected` 选择固定坐标中 X 大于最前机组的机组，统计包含湍流、阵风、尾流和瞬态，不能单独分离尾流亏损。`statistics.farm_inflow` 与运行统计核对请求自由入流的结论仍为 **unavailable**；独立场内入流探针**待实现**。驱动的 BTS 参考 `u_inf` 来自完整文件轮毂层均值，供正式 CLI 的 `power_norm` 诊断使用，不是本回合场内入流采样，也不是环境 reward 的分母。

reward 的有效要求是同一步功率与来流归一化；实际 MPI 路径在 `PowerBuffer.get_agg(window=1)` 中对最近两条功率取均值，而来流只取最新一条，未严格同步。为保持与既有训练结果可比，暂不修改该偏差；[归一化要求与实际状态](MAPPO_SETUP.md#奖励归一化要求与实际状态)记录调用链和限制。

## 训练、场景与探针入口

| 入口 | 当前处理 |
| --- | --- |
| [train_fastfarm.py](../../scripts/train/train_fastfarm.py) | `--wind-direction` 经 `experiment_config()`、`training_wind_schedule()` 和 `FastFarmSampler` 保留并传到底层；有场景时经过 `SceneRuntime.reset()`，方向请求独立于场景显示方向。无场景时拒绝 `--wind-sched preset:turb`，BTS由 `--scene` 的 `inflow.turbulence` 指定。非场景采样器仍拒绝BTS与标量风速调度并用 |
| [probe_flex_policy.py](../../scripts/lidar/probe_flex_policy.py) | `--wind` 与 `--turbulence` 是 argparse 互斥参数；选择 BTS 时调用无标量参数的重置，报告的 `wind_mps` 为 null。稳态默认风速14 m/s |

训练在 `upstream_request.training` 保存原始 `wind_speed`、`wind_direction`、`wind_sched` 及用途。风速按入口标为 `overridden_by_scene`、`overridden_by_schedule` 或 `solver_request`；方向标为 `solver_request`。显式CLI方向优先于风速调度所带方向，未提供时保留调度方向；场景模式未显式提供方向则不从场景显示值补入。训练CLI的非场景、稳态场景和BTS场景均沿该求解请求链传递方向，由底层决定接受或拒绝。

**要求与入口限制：**不支持的显式风况应可追溯并明确拒绝。训练和场景入口分别保存上层上下文与实际请求；场景显示方向不能视为求解器已应用方向。任意风向旋转与独立场内入流探针仍待实现。

静态行为依据还包括 [风况合同测试](../../tests/test_fastfarm_wind_conditions.py) 与 [训练完整性测试](../../tests/test_fastfarm_training_integrity.py)。测试夹具核对输入身份、拒绝、记录和统计口径，不代表真实求解、风向旋转或场内探针已通过验收。

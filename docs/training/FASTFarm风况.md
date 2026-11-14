# FAST.Farm 风况请求、输入与统计

本文描述工作区 [fastfarm_driver.py](../../wfrl/fastfarm_driver.py) 与 [interface.py](../../wfcrl-env/wfcrl/interface.py) 的风况接口。支持平地条件下按算例／回合指定稳态风和 BTS 的来流方向；不支持运行中途改变方向。输入核对和短算例对比不代表数值收敛或实机验证。

## 三层记录与保存

`FastFarmDriver.reset()` 先通过 `FastFarmInterface.set_wind_request_context()` 保留调用方意图，再交给环境重置。返回测量及后续测量中的 `wind_conditions` 是当前 `wind_record` 的独立深拷贝，合同为 `fastfarm-wind-conditions.v1`。

| 层次 | 字段与实际含义 |
| --- | --- |
| 上层上下文与求解请求 | `upstream_request` 保留场景／训练入口的原始配置及用途，不参与求解参数校验；`requested` 保留传给驱动的实际风速、风向、BTS选择及额外参数。`environment_reset` 单独记录环境实际转发值；`application` 逐项记录应用、固定坐标匹配、文件决定或 `not_applied` 原因 |
| 实际输入 | `loaded_inputs` 保存解析后的 FAST.Farm、InflowWind、BTS、各机组主输入及 ElastoDyn 文件路径／SHA-256／字节数；另存风模式、时间步、TMax、机组坐标与初始机舱偏航。稳态风速写入后重新读取核对，显式选择的 BTS 与实际引用文件核对内容哈希 |
| 统计 | `statistics` 分开保存加载 BTS 的文件统计、已声明的背景／阵风叠加文件统计、MPI 接收的机组轮毂风速／风向统计，以及下游机组统计；无法提供的项目明确为 `unavailable` |

驱动指定 [paths.py](../../wfrl/paths.py) 的 `RUNS`，即仓库 `results/runs/`，在其 `wind_conditions` 子目录按记录 ID 保存 JSON；该目录按需创建，生成算例清理不会删除这些记录。直接调用环境接口且未指定记录目录时，保存到 FAST.Farm 主输入同目录的 `wind_conditions` 子目录。完整绝对位置在 `record_path`，拒绝异常也携带同一记录；写入采用 UTF-8 临时文件和原子替换。状态可为 preparing、validated、prepared、running、completed、rejected 或 failed。

输入身份只覆盖上述文件，不是所有求解依赖的完整清单。`loaded_inputs` 的运行说明表示这些解析文件已提交给 MPI 启动调用；没有求解器回传文件哈希确认。文件核对不能替代运行中独立自由入流探针。

### 场景、请求与生效方向

风向使用与 FLORIS 相同的气象学约定：从正北顺时针计量，270°为西风、300°为西北偏西来流。世界坐标为 X 向东、Y 向北、Z 向上。

- YAML `inflow.direction` 通过 `Scene.make_case()` 的 `direction` 传入算例，支持稳态风和 `inflow.turbulence` 指定的 BTS。
- `FastFarmDriver.reset(wind_direction=300.)`、`FastFarmInterface.init(wind_direction=300.)` 和训练 CLI `--wind-direction 300` 为显式回合请求，优先于算例默认方向。未指定时使用算例方向；默认算例仍为270°。
- `create_ff_case({... , "direction": 300.}, output_dir=...)` 为直接算例生成接口。省略 `direction` 或给270°及其等价值时，使用原有生成路径，不写方向 sidecar，生成文件字节保持一致。
- 场景 YAML 范围为 `[0, 360)`；接口接受有限实数并模360°规范化，与270°相差不超过 `1e-8°` 时按270°处理。

`application.wind_direction` 保存实际方向与来源（`reset_request` / `case_direction`）；非270°算例还保存 `<主输入名>.direction.json`。该文件包含原始世界机位、求解机位、旋转矩阵、平移、偏航显示偏移、域覆盖和270°参考网格，重新加载或多次 reset 不累计旋转。保留该 sidecar 才能重新加载非270°算例的世界坐标身份。运行记录的 `loaded_inputs.direction_identity` 保存其摘要；`coordinates.transform` 保存相同坐标合同。FAST.Farm 主输入中的机位始终是实际求解坐标。旋转算例与270°算例使用同一套环境风场网格构造（模板 `dY_Low`／`dZ_Low`／`NY_Low`／`NZ_Low`、`DT_High/10` 采样、高分辨率域 `extent_YZ=1.2D`），因此方向改变只改变机位和低分辨率域长度，不改变网格分辨率；横向宽度在需要时按旋转后机位跨度加 2.4D 余量扩展。

**上层标记：**`SceneRuntime.reset()` 把场景方向记为 `upstream_request.scene.wind_direction_role=case_generation`，表示方向在生成算例时以布局旋转应用。应以 `application`、`coordinates.transform` 和回读输入判定生效方向。未显式请求时 `requested.wind_direction=None` 表示使用算例默认值，不表示忽略 YAML。BTS 的标量场景风速仍为 `overridden_by_bts`。

## 接受与拒绝的请求

接口当前接受 `Mod_AmbWind=2`，且 InflowWind 为稳态 `WindType=1` 或 BTS `WindType=3`，要求 `PropagationDir=0`、`VFlowAng=0`。`WindRequestError` 保存 `record` 和逐项 `not_applied` 原因，在启动求解器之前拒绝以下情况：

- 风速为布尔、字符串、非有限、零或负数；BTS 模式指定任意标量风速，即使等于文件均值；调用方风速与环境转发值不一致。
- 风向为布尔、字符串或非有限数值；调用方方向未转发或与环境转发值不一致；旋转后域覆盖不足，或非平地（`WT_Z != 0`）输入。
- TI、剪切、阵风、随机种子等额外物理参数，以及任何不支持的键，包括值为 `None` 的未知键；`reset()` 不执行对应的输入生成或修改。TI/种子组合可在创建算例前通过 [TurbSim 湍流盒库](TurbSim湍流盒.md) 生成；场景可直接指定库名称、组合字符串、旧模板文件名或绝对 BTS 路径，算例生成和启动前身份核对共用 `resolve_bts()`。
- 不支持的环境风模式、风类型或传播／垂向角；无效机组坐标／偏航、机组数量不符、非正或非有限的 `DT_Low`、`DT_High`、`TMax`。
- 输入不可读、BTS 缺失／内容与选择不符、风速写入回读不符、准备时风场文件身份变化；BTS 网格／时间轴或统计高度无效、回合时长非正或非有限。
- 非法 `bts_role`，稳态模式提供 BTS 证据，或在未声明 `gust_superposed` 时提供独立 `background_bts`。

请求预检失败不写入风速；准备和回读失败时恢复 InflowWind、主输入和方向 sidecar 的原字节，不启动求解器。可执行文件校验或 MPI 启动错误另存 failed 并抛出相应异常，不能统一称作 `WindRequestError`。

稳态模式允许有限正风速并核对 `HWindSpeed`；未指定时，驱动保留环境的风速抽样，记录中不会把抽样值冒充用户请求。BTS 以实际加载文件为准，不使用模板里未生效的 `HWindSpeed` 覆盖风场。

## 平地坐标变换与偏航

设气象风向为 `d`，世界来流的流向角为 `270°−d`。先围绕布局包围盒中心把 XY 旋转 `d−270°`，再平移，使求解坐标的最小 X 保持原值、横向 Y 包围盒居中于0；Z 和机组编号不变。`solver_xy = R @ world_xy + translation`，逆变换为 `world_xy = R.T @ (solver_xy − translation)`。平移不改变平地、水平齐次来流下的机组相对几何。

求解器继续使用 `Mod_AmbWind=2`、`PropagationDir=VFlowAng=0`，风沿求解 +X 传播。低／高分辨率网格均由旋转后机位传给 `fastFarmBoxExtent` / `fastFarmTurbSimExtent` 重算；不改变 OpenFAST 结构、控制步长、BTS 样本或默认270°网格。非270°高分辨率横向／垂向目标宽度使用1.3D，并检查取整后的实际网格包含叶轮且处于低分辨率域内。高分辨率时间步保留对应输入的采样时间步。

选择布局旋转是为了让稳态和 TurbSim 共用一条风对齐路径。直接修改 `PropagationDir` 会使湍流盒与 FAST.Farm 网格失去对齐；`Mod_AmbWind=3` 还需要同步各高低分辨率输入，本接口未实现该路径。依据：[FAST.Farm 输入约束](https://openfast.readthedocs.io/en/main/source/user/fast.farm/InputFiles.html)、[OpenFAST 维护者关于布局旋转与湍流网格的说明](https://forums.nlr.gov/t/wind-wave-propagationdir/1440?page=2)。

偏航命令和反馈保留 OpenFAST 求解坐标语义：0°沿当前算例的平均来流，正值绕求解 +Z 逆时针；不是相对正北的机舱方位角，也不是逐时刻局部湍流的误差角。指令仍仅转弧度。Blender 机舱世界姿态为 `yaw_world = yaw_solver + 270°−d`，显示变换不修改训练观测、命令或偏航限幅。局部风向保留已有 DISCON 换算并加 `d−270°`：`(degrees(raw) − 90° + d−270°) mod 360°`；不将湍流瞬时方向强制设为常数。

前端 snapshot 保留世界机位，以实际 `application.wind_direction` 替换场景显示方向；偏航通道的 provenance 携带世界角偏移。实时姿态、来流示意箭头和 DisXY 网格消费同一变换；DisXY 只旋转网格坐标，不改变标量速度值。原始 `.out`、VTK 和载荷／位移向量仍在其求解器声明坐标系，不自动转换为世界分量。前端验证为宿主合同，不包含可见窗口。

显式270°记为 `MATCHES_FIXED_COORDINATES`；未指定且算例为270°时记为 `input_file`。非270°及从旋转态恢复270°完成回读后记为 `applied`。运行中的方向变更由 `init` 拒绝，`update_wind` 明确抛出 `NotImplementedError`；须结束／关闭当前回合再 reset。

## 湍流盒横向覆盖

BTS 在风对齐求解坐标中使用，文件不被旋转、重采样或扩宽。改变风向会改变布局的横向投影；相同 BTS 并不保证覆盖所有方向。接口检查低／高分辨率域及叶轮覆盖，越界时在启动前拒绝，用户须选择更宽的盒子或离线生成。

三机504 m间距的一列，在300°时横向跨度为504 m；`ts_u8_ti8_s42` 的 Y 范围为 `[-340, 340] m`，可容纳该方向。0°时横向跨度变为1008 m，该盒子会被拒绝。空间覆盖通过不等于非周期文件的时间覆盖通过；时间处理限制如下。

## BTS 时长与统计范围

每次重置读取实际 BTS，在轮毂高度对纵向 `u` 逐时刻线性插值，逐横向点计算完整文件时域的均值、总体标准差和 TI，不把空间剪切混入时间方差。BTS 本身不说明背景／阵风分量：`bts_role` 仅为调用方声明，`background_bts` 仅提供对照证据，不改变求解输入。没有独立来源时相应统计为 `unavailable`。

`verification.bts_duration` 比较文件时间轴末值减首值与驱动的 `max_steps * dt`；直接调用接口而没有驱动时长时使用 FAST.Farm `TMax`。范围内记 `within_bts_duration`，超出记 `exceeds_bts_duration` 并在启动前打印、保存警告。**当前超时长不触发拒绝**，超出部分由 InflowWind 处理，可能周期回绕或报错；该检查不保证完整计算域的时空覆盖。

这一时长参数不等于 reset 后可用的控制交换数：`start_iter=S`、`max_steps=M` 的总预算为 `S+M`，reset 使用 `S+1`，后续只剩 `M−1`，调用方预热也从中扣除。最后一次真实交换即返回 `done`；[交换预算与结束](MAPPO_SETUP.md#交换预算与结束)说明驱动与训练预算的区别。

MPI 统计在接口 running 状态下按接收交换累计，保留无效样本数量；风速采用总体标准差，风向采用圆周均值及合向量长度。记录保留初始化／预热／关闭排空范围的说明，首个接收样本为时间原点，绝对求解时刻没有随 MPI 传入。`control_start_sample_index` 在驱动重置返回前记录，调用方之后追加的预热不会自动重新标记。

`statistics.turbine_hub` 是 DISCON 轮毂风速，不是背景纵向风速、转子面积均值或独立自由来流。`downstream_wake_affected` 选择求解坐标中 X 大于最前机组的机组，统计包含湍流、阵风、尾流和瞬态，不能单独分离尾流亏损。`statistics.farm_inflow` 与运行统计核对请求自由入流的结论仍为 **unavailable**；独立场内入流探针**待实现**。驱动的 BTS 参考 `u_inf` 来自完整文件轮毂层均值，供正式 CLI 的 `power_norm` 诊断使用，不是本回合场内入流采样，也不是环境 reward 的分母。

reward 的有效要求是同一步功率与来流归一化；实际 MPI 路径在 `PowerBuffer.get_agg(window=1)` 中对最近两条功率取均值，而来流只取最新一条，未严格同步。为保持与既有训练结果可比，暂不修改该偏差；[归一化要求与实际状态](MAPPO_SETUP.md#奖励归一化要求与实际状态)记录调用链和限制。

## 训练、场景与探针入口

| 入口 | 当前处理 |
| --- | --- |
| [train_fastfarm.py](../../scripts/train/train_fastfarm.py) | `--wind-direction` 经 `experiment_config()`、`training_wind_schedule()` 和 `FastFarmSampler` 保留并传到底层；有场景时经过 `SceneRuntime.reset()`，显式方向覆盖场景算例默认方向。无场景时拒绝 `--wind-sched preset:turb`，BTS由 `--scene` 的 `inflow.turbulence` 指定。非场景采样器仍拒绝BTS与标量风速调度并用 |
| [probe_flex_policy.py](../../scripts/lidar/probe_flex_policy.py) | `--wind` 与 `--turbulence` 是 argparse 互斥参数；选择 BTS 时调用无标量参数的重置，报告的 `wind_mps` 为 null。稳态默认风速14 m/s |

训练在 `upstream_request.training` 保存原始 `wind_speed`、`wind_direction`、`wind_sched` 及用途。风速按入口标为 `overridden_by_scene`、`overridden_by_schedule` 或 `solver_request`；方向标为 `solver_request`。显式CLI方向优先于风速调度所带方向，未提供时保留调度方向；场景模式未显式提供方向则使用算例中的 YAML 方向。训练CLI的非场景、稳态场景和BTS场景均沿该求解请求链传递方向，由底层决定接受或拒绝。

**要求与入口限制：**不支持的显式风况应可追溯并明确拒绝。场景、训练请求与生效输入分别核对；独立场内入流探针、运行中途风向变化和非平地旋转未实现。

静态行为依据还包括 [风况合同测试](../../tests/test_fastfarm_wind_conditions.py) 与 [训练完整性测试](../../tests/test_fastfarm_training_integrity.py)。[方向合同测试](../../tests/test_fastfarm_direction.py) 覆盖逐字节默认兼容、旋转／逆变换、域覆盖、回合请求和回读回滚；[前端宿主合同](../../blender_frontend/tests/test_frontend_wind_direction.py) 覆盖世界机位、实际方向、偏航与 DisXY。独立场内探针尚未提供。

## 使用示例

```yaml
inflow:
  speed: 8.0
  direction: 300.0
  turbulence: null  # 或 ts_u8_ti8_s42；BTS 不接受标量风速覆盖
```

```python
# 已结束／关闭上一回合的驱动；BTS 驱动只传 wind_direction。
measurement = driver.reset(wind_speed=8.0, wind_direction=300.0)
```

原生三算例对比入口要求新输出目录；180秒覆盖两段504 m输运，统计末30秒。脚本确认 FAST.Farm 进程退出后才读取 `.out`，检查完整终时刻、有限值与 INVALID 通道；不把 MPI Finalize 当作文件已关闭。

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/wfrl-mac/bin/python -m scripts.experiments.compare_fastfarm_directions \
  --output /private/tmp/fastfarm-direction-acceptance --seconds 180 --bts ts_u8_ti8_s42
```

## 三机原生求解证据

对象为本机 FAST.Farm / OpenFAST `v3.5.3-dirty`、NREL 5MW 三机一列、间距504 m、显式8 m/s稳态风或 `ts_u8_ti8_s42`、零偏航命令、物理开关关闭。每案180秒，`DT_Low=3 s`，原生输出间隔0.25秒；功率取各机 `.out` 的 `GenPwr`，统计150–180秒（含端点121样本），单位由kW转换为MW。

| 来流 | T1 平均 MW | T2 平均 MW | T3 平均 MW | 全场平均 MW |
| --- | ---: | ---: | ---: | ---: |
| 稳态 8 m/s，270° | 1.707000 | 0.564450 | 0.524172 | 2.795621 |
| 稳态 8 m/s，300° | 1.707000 | 1.707000 | 1.707000 | 5.121000 |
| BTS `ts_u8_ti8_s42`，300° | 1.866587 | 1.615843 | 1.516000 | 4.998430 |

三案均正常退出，读取输出前已确认对应 FAST.Farm 进程结束；输出到达180秒、数值有限、无 INVALID 通道及越界错误。稳态300°相对270°的全场均值高83.18%，下游尾流损失明显减小。此窗口验证方向作用，不证明充分统计收敛；270°的T3在窗口中仍有瞬态。

BTS原始Y范围为 `[-340, 340] m`，实际低分辨率Y范围为 `[-340, 330] m`；三台高分辨率Y范围分别为 `[-340, -180]`、`[-90, 70]`、`[170, 330] m`，均落在低分辨率域内。BTS SHA-256 为 `b27e53a2889e97ab1235fc684c4bf024ce665463db952017685a12bb9992313a`。对应三个 FAST.Farm 主输入 SHA-256 按上表顺序为：

- `1040e7808fbae081a3833e3832bc771928eed1e7d586974cbb8dd2ff578121b1`
- `bad150f8ef20802edbd55add0b494128a41385f78c162468d7f41adb95799651`
- `e703142d733a277d6c0cc3b45998eb2f38f268ba8b63a6460e91c8b22ed75e58`

这些摘要识别测量输入，不是所有依赖的完整清单。原始算例、输出和日志属于临时验收目录，按任务约束清除；表格保留限定对象的测量数值。可复现入口为上面的原生对比命令。宿主坐标合同不等同于可见窗口验收；未运行可见窗口、科学精度或不同平台验证。

## 宿主验证入口

解释器为 `/opt/anaconda3/envs/wfrl-mac/bin/python`。测试使用临时目录并关闭仓库缓存；根目录执行以下命令。方向专测为 `tests/test_fastfarm_direction.py`，可单独运行；其他文件检查请求、场景、训练和协议兼容性。

```bash
DS_PY=/opt/anaconda3/envs/wfrl-mac/bin/python
DS_TEST_TMP=$(mktemp -d /private/tmp/wfrl-direction-tests.XXXXXX)
export PYTHONDONTWRITEBYTECODE=1
"$DS_PY" -m pytest tests/test_{fastfarm_direction,windcond,turbulence_cases,cn_scene_validation,fastfarm_driver_inputs,formal_sampler_contract,fastfarm_driver_bn_contract,cq_inflow_multimodal_contracts}.py \
  tests/blender_bridge/test_{messages,farm_power_validity}.py \
  -q -x -p no:cacheprovider --basetemp="$DS_TEST_TMP/backend"
PYTHONPATH=blender_frontend:. "$DS_PY" -m pytest \
  blender_frontend/tests/test_{fastfarm_direction,animation_math,backend_inflow,scene_geometry_key,wake_payload,ch_scene_landscape_safety,text_io_encoding,text_encoding_guard,static_names}.py \
  --ignore=blender_frontend/tests/blender -q -x -p no:cacheprovider --basetemp="$DS_TEST_TMP/frontend"
"$DS_PY" -m pytest tests/test_fastfarm_wind_conditions.py -q -x \
  -p no:cacheprovider --basetemp="$DS_TEST_TMP/wind"
rm -r "$DS_TEST_TMP"
```


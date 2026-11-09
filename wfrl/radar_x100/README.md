# X100 数据与采集接口

本包提供独立于 Blender 和训练 Bridge 的 `x100-frame.v1` 合同、严格 PLY 读取器、显式索引记录／回放、面积材料采样、几何观测、未标定工程检测模型、同帧分析数据类型／数学基准、有限安装排序、叶片拟合／因果估计和本地采集通道。依赖 Python 标准库和 NumPy。厂家 UDP 解码、SDK、航迹字段和实机标定尚未接入。

本文描述工作区源码接口；面积材料采样、安装搜索与叶片估计由 0.3.42.8 扩展及配套源码提供，日常安装使用 0.3.42.7。公开附件与日常安装的行为及验证范围按[载荷身份](../../docs/blender/发布状态与验证范围.md)核对。

## 帧与来源

`RadarFrame` 的身份是 `(sensor_id, session_id, stream, sequence)`。`source_kind` 为 `GEOMETRY_SIMULATION`、`ENGINEERING_SIMULATION`、`MEASURED_REPLAY`、`MEASURED_LIVE` 或 `TEST`，文件回放保留记录来源。`NEAR`、`FAR`、`COMBINED`、`TRACK`、`GEOMETRY`、`DETECTIONS`、`UNKNOWN` 使用独立序号与新鲜度；`TRACK` 当前仅保留流身份，尚无航迹对象解码与绘制。

点数组固定为不对齐的 `<fffBfffB`，每点 26 字节，最多 4096 点，字段为 `x/y/z/ms/mag/rcs/snr/label`。`ms` 仅支持资料定义的 0／1，原始 label 字节原样保留，不映射成类别或过滤条件。负 RCS／SNR 保留，非有限坐标不参与显示并报告数量；非有限属性不替换成观测零值。几何仿真的 mag、RCS、SNR 用 NaN 表示未知，label 的占位值不具有厂家语义。仿真的 ms 按径向速度绝对值是否大于 `1e-6 m/s` 派生，不代表厂家动静分类算法。

`schema.PROFILE` 是名义尺寸、方位／俯仰半角、总量程和最大点数的配置来源；PLY 和本地通道的点载荷字节预算从 `POINT_DTYPE.itemsize` 派生。`schema.DISPLAY_DEFAULTS` 只定义视场示意长度与画布显示余量，不能作为探测能力。Blender 经 `radar_x100.core` 导入同一规范核心，安装包通过既有构建清单 vendoring，不维护另一份手工规格。近程／远程／2in1／Track 各模式与独立量程、点数和周期的完整映射及 label 完整编码仍未知。

径向速度和材料点身份是独立的可选数组，不放入厂家 PLY 字段。PLY 不含速度、时间戳、帧号和航迹 ID。`FrameTime` 保留时钟域、可缺失的源时刻／采样区间、时间戳参考位置与质量。`ClockMapping` 保存源／目标域、比例／偏移、有效区间、误差及依据；越域、越界或误差超容差时拒绝映射。接收单调时钟只用于当前进程的新鲜度，不写成设备采样时间。

空间字段声明米制、坐标系、运动补偿、对齐状态和可选外参。未确认的实测坐标按 `VENDOR_RAW / UNALIGNED` 展示，不绑定风场动画。内部几何仿真使用右手系 `+X` 前、`+Y` 左、`+Z` 上，外参是同刻 `world_from_sensor`。实测轴映射、原点和运动补偿仍需厂家资料与标定。

## PLY 与显式索引

`ply.read_ply()` 只接受 binary_little_endian 1.0、规定字段类型／顺序和准确的 vertex 载荷长度，拒绝额外 element、缺字段、截断、尾随载荷及超预算文件。合法零顶点 PLY 是有效空帧。没有文件、组帧超时或断流不能生成有效空帧。

`ply.ply_frame()` 必须显式传入 `source_kind`，不根据 PLY 格式推定实测来源。`provenance.source_declaration=EXPLICIT` 表示来源由调用方声明，不代表设备兼容或标定已经验证；单个 PLY 不能恢复记录包中的原始来源、速度和时间元数据。需要保留这些元数据时读取完整记录目录。

按以下 JSON 显式声明序列，不能通过目录文件名排序推断连续性：

```json
{
  "sensor_id": "X100-serial",
  "source_kind": "MEASURED_REPLAY",
  "stream": "UNKNOWN",
  "frames": [
    {"file": "frame_a.ply", "sequence": 0, "replay_time_s": 0.0},
    {"file": "frame_b.ply", "sequence": 1, "replay_time_s": 0.0333333333}
  ]
}
```

`replay_time_s` 是从 0 开始、单调不减的文件呈现时间。已知源时间时，在各项的 `time` 中提供 `FrameTime` 字段；只有用户给出帧率时，可在顶层加 `"fps": 30` 生成 `FPS_DERIVED` 时间，采样区间和硬件同步仍为未知。序号缺口原样保留，不补帧。

```bash
python scripts/radar_x100/import_ply.py /path/to/index.json /path/to/new_recording
```

导入保留同一次读取取得的原始 PLY 字节与摘要。记录包包含 `manifest.json`、`frame_index.jsonl`、`frames/*.ply` 和实际存在时的 `*.velocity.f32`。每项保存完整帧元数据、载荷摘要和回放时间；manifest 保存索引摘要。PLY 逐项只读一次，对同一字节快照校验并解码。`Replay.at(t)` 只选已到达时刻的最后一项原始帧，不插值生成观测；起点前返回无帧。具有相同呈现时间的帧保留独立索引，逐帧操作按索引访问。

Blender 工作区源码使用独立的 Python 播放时钟与索引选择帧，RNA 时间属性只承担显示与显式输入；自动片尾停在最后索引，再次播放从起点开始。目录重录使用实际选中原索引行的 `replay_time_s`，相对记录首行归零，完整保留原始 `FrameTime`。源时刻相等、递减或改变时钟域不改写呈现轴；主动跳回较早索引使当前重录失败并保留 `INCOMPLETE`，包括呈现时间相等的较早索引。UI 重录只包含实际显示并提交的行，不保证覆盖原包全部索引。

`Recorder` 创建 `INCOMPLETE` 标记，至少写入一帧且成功关闭后才删除。关闭期间 `state` 为 `FINALIZING`，不再接受追加；关闭索引、写入状态为 `COMPLETE` 的最终清单并删除标记后，`state` 才变为 `COMPLETE`。零帧关闭进入 `FAILED`，保留错误与不完整标记；一帧零顶点观测仍是可回放的有效空帧。索引预算为 64 MiB，写入与读取使用同一上限；追加超过预算时记录失败，不生成无法被读取器打开的完整包。写盘、校验或记录预算失败进入 `FAILED`；已写文件、最后成功索引行数量和失败原因保留。未正常关闭的包不能作为完整回放打开。 工作区以独占临时文件写入、刷新并原子替换清单；最终清单写入失败时保留 `INCOMPLETE`，失败清单按可用存储尽力写入。

## 几何观测

`simulation.simulate(sample_at, t, ...)` 默认采用 `AREA` 材料采样：在源起点的参考表面按三角面积分配概率，用随机平移的 Halton 序列生成固定三角面与重心坐标。相同种子下，小预算是大预算的前缀；采样坐标随材料运动，面细分不会按三角面数量增加该区域的权重。`CENTROID` 是显式选择的质心对照模型。两种模型均使用名义角域、量程与双面首交几何遮挡，不代表电磁散射或厂家检测概率。

`sampling.MaterialPlan` 记录来源、几何、参考时刻、方法、种子、预算、坐标摘要及 `area_weights_m2`。面积模式每点权重为参考总面积除以预算，权重之和为参考总面积；小预算保留的是材料坐标前缀，积分权重随预算重新分配。`sampling_plan_id` 的摘要包含面积度量与面积权重字节摘要，面积权重改变会改变计划身份；不能把不含面积权重的计划身份当成同一计划。`material_ids` 保留承载点的三角面索引；同一面可承载多个点，唯一材料点须联合 `provenance.sampling_plan_id` 和 `material_point_indices` 识别。`sampling_algorithm` 区分具体算法。传入的计划必须匹配当前源起点、方法、种子和预算。

径向速度来自同一材料坐标和传感器原点的相对世界速度，正号表示远离。面积模式的 `material_at(t, triangle_ids, barycentric=...)` 按给定顺序返回 `MaterialSample`；质心对照不传重心权重。回调只计算已输出材料点的差分位置，未提供时使用完整几何，二者保持相同遮挡、材料坐标及差分定义。

Blender 的 `SceneSampler.sample(t)` 只读保存的同源叶片、塔架和机舱运动；不改场景帧、相机或播放器。当前几何为所选展示叶片表面、塔筒与机舱外壳；未包含其他结构、其他机组、环境及合成缺陷的额外拓扑。固定世界／塔基挂载和完整机舱变换使用同一安装标定，变更来源或标定须重新建立采样器。暂停时也核对源绑定和静态根变换，原生修改安装或源根时撤回点云并保留用户修改；着色只更新显示，不重新生成观测。Blender 采样器绑定静态三角拓扑，在差分时只变形所选材料顶点，按几何身份与时刻复用当前遮挡树；独立 sample/material_sample 调用校验来源绑定；一次同步观测内可用 sampling_transaction 复用校验，finally 结束该作用域，外层 tick 仍校验原生编辑，不跨 tick 缓存校验结论。

Blender 工作区源码核对来源 Scene、材料对象与网格身份、关键父链及 `matrix_parent_inverse`，静态节点保持绑定时的变换，FarmFlex 的合法动态姿态按保存运动更新。塔架、叶片、机舱或布局根从所属 Scene 解除链接，以及同名对象替换，都会使绑定失效。安装对象及其父级不支持约束、动画或非对象父级关系；不匹配时撤回显示并保留编辑。记录器和回放核心仍按其完整性合同工作，显示颜色、窗口字号或采样实现优化不修改原始点属性和已记录的时间。

完整源网格导出使用独立 Blender 进程，默认从源起点导出 1 秒、30 Hz：

```bash
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --python-exit-code 1 --python scripts/radar_x100/export_simulation.py -- \
  --output /path/to/new_geometry_recording --candidates 512
```

`--start/--end` 是绝对源时间，`--fps` 是明确的几何采样假设。网格以参数的十进制值枚举，只包含请求区间内的样本；结束时刻落在网格上才包含端点，不用固定容差纳入区间外样本。源范围检查保持严格，采样或写盘失败保留失败状态与不完整标记。该脚本加载已有运动包，不运行 FAST.Farm 或训练。

## 工程检测模型

[simulation.py](simulation.py) 的 `simulate_detections()` 在同源几何、硬件角域／量程和首交遮挡之后调用 [detection.py](detection.py) 的 `generate_detections()`，输出 `ENGINEERING_SIMULATION / DETECTIONS`。模型身份为 `x100-incoherent-detection.v2`，默认 `calibration_status=UNCALIBRATED`。Blender 工作区的 `wfrl_x100_observation_mode` 提供 `GEOMETRY`（默认）与 `DETECTIONS`，点击“应用配置”才切换生产路径。前者显示视场／遮挡筛选后的材料表面点，后者显示经过功率、噪声、分辨率聚类和门限的工程检测点；操作与显示见[观测模式](../../docs/blender/X100雷达.md#观测模式工作区源码)。两者都不是设备实测。

**交付范围：**公开 0.3.42.8 扩展按 [扩展构建清单](../../scripts/blender/build_extension.py) 的 `RADAR_MODULES` 包含 `detection.py` 与 `clearance.py`，提供 DETECTIONS 工程检测链与净空估计。本机日常安装仍为 0.3.42.7，不包含工程检测链。隔离安装验证工具在安装后的扩展上运行 DETECTIONS 与净空检查；当前发布未运行这些检查，验证范围按[发布记录](../../publication/blender-0.3.42.8/verification.json)识别。

模型假设如下：

- `surface_rcs()` 将参考面积权重乘以有效 RCS 密度和双面入射因子 `abs(normal·LOS)^incidence_exponent`。面积本身不是 RCS；法向取当前几何，参考面积随材料运输，不随表面应变重算。
- `received_power_w()` 使用单站、远场、自由空间点散射体雷达方程，期望功率按距离四次方衰减；收发使用相同形状的可分离方向图，额外传播衰减按往返距离计算。该关系描述功率，不规定点数按距离四次方衰减。
- 分辨率聚类使用距离、`sin(方位角)`、`sin(俯仰角)`、径向速度四维连续距离；每簇各维跨度不超过一个分辨宽度，不按固定箱边界分割近邻，也不让相邻链条无限合并。正轴分辨率为 **0.2 m／1°／2°／0.1 m/s**，角轴宽度用正轴角宽的弧度值表示。离轴采用恒定方向余弦分辨率假设，各角轴局部角宽约为 `正轴角宽 / cos(离轴角)`；这不是厂家给出的离轴性能曲线。位置与速度为功率加权连续质心，不量化到箱中心。实现上每帧先按相邻哈希箱一次性列出各点在四维均不超过一个分辨宽度的近邻，再按字典序种子逐个分簇；有限输入、编码范围和近邻对数（`MAX_NEIGHBOR_PAIRS`）在预算内时使用该路径，否则回退逐种子查 81 个相邻箱的同义实现，两者标签与簇数逐项一致。
- 簇内先相加非相干期望功率。默认 `SWERLING_0` 用确定性合成信号幅值加复高斯噪声，检测概率为 Marcum Q1；可选 `SWERLING_1` 才用信号加噪声均值的指数总功率。门限／处理后噪声比为 `-ln(Pfa)`，门限后可另加独立漏检。
- 虚警试验覆盖距离、两个方向余弦和多普勒四维搜索域；有正响应散射体的搜索箱不参加纯噪声试验。空箱虚警概率为单元 `Pfa`，位置和速度在各自搜索箱内均匀抽样。按抽样总功率截取最强输出，再加独立高斯测量误差；越出量程、角域或速度范围的估计丢弃，不补点。不同帧独立。
- 不包含相干相位干涉、极化、近场电磁求解、多径、实际波形处理、chirp 时序、厂家分类或跟踪。多普勒分辨仅是上述工程聚类近似。

### 参数与预算

下表为 `DetectionConfig` 默认值。代码的 `PARAMETER_SOURCES` 区分手册所列分辨率／精度和额外研究假设；没有实机标定。载频在手册频段内选为 **77 GHz**，波长由 `c/f` 推导。收发峰值增益分别为 `效率 × 4π / ∫方向图 dΩ`，全空间积分不在硬件 FOV 边界截断；默认效率各0.7，峰值线性增益约9.675。

接收机噪声按 `k T0 B × 10^(NF/10)` 计算，处理增益只降低检测器等效噪声，不增加接收目标功率。默认 **43.1221 dB（约43.1 dB）** 是从正轴 **10 m²、396 m、Swerling 0、Pd=0.9、单元 Pfa=1e-12** 的假设锚点反推；所需处理后 SNR 约37.4368（线性）。代码引用的手册仅给出396 m量程，未给出对应参考 RCS、Pd 或 Pfa 标定指标；320 m无人机示例也未提供这些量。锚点不是厂家灵敏度标定，Pd只描述测量边界筛选与输出上限之前。修改其他参数不会自动重拟合处理增益。

| 参数 | 默认值与含义 |
| --- | --- |
| `surface_rcs_density`、`incidence_exponent` | 0.1 m² RCS/m² 参考面积；入射指数 1.0 |
| `transmit_power_w`、`carrier_frequency_hz` | 0.1 W、77e9 Hz；波长为299792458/77e9 m，默认峰值 EIRP 低于代码引用的39 dBm上限 |
| `tx_efficiency`、`rx_efficiency`、`system_loss_linear` | 0.7、0.7、1；收发增益由方向图归一化推导，不能独立指定 |
| `attenuation_db_per_m` | 0 dB/m，单程额外损耗 |
| `antenna_pattern` | `GAUSSIAN`；可选 `ISOTROPIC` |
| `azimuth_half_power_deg`、`elevation_half_power_deg` | 45°、15°，单程偏轴半功率角 |
| `merge_range_m`、`merge_azimuth_deg`、`merge_elevation_deg`、`merge_velocity_m_s` | 0.2 m、1°、2°、0.1 m/s；角宽为正轴值 |
| `velocity_min_m_s`、`velocity_max_m_s` | −400/3.6、200/3.6 m/s；同时用于多普勒搜索和输出筛选 |
| `noise_temperature_k`、`noise_bandwidth_hz`、`noise_figure_db` | 290 K、1 MHz、6 dB；B是接收机等效噪声带宽，不是FMCW扫频带宽或ADC速率 |
| `processing_gain_db`、`false_alarm_probability_per_cell`、`fluctuation_model` | 43.1220604659924 dB、1e-12、`SWERLING_0`；检测器噪声为接收机噪声除以线性处理增益 |
| `miss_probability`、`false_alarms_enabled` | 0；`True`，门限后的附加漏检与空单元虚警开关 |
| `range_std_m`、`azimuth_std_deg`、`elevation_std_deg`、`radial_velocity_std_m_s` | 0.025 m、0.05°、0.1°、0.025 m/s；把手册±0.05 m、±0.1°／0.2°、±0.05 m/s假设为高斯±2σ。手册未说明置信度或分布；显式设0才关闭相应误差 |
| `calibration_status`、`calibration_reference`、`parameter_basis` | `UNCALIBRATED`、`None`、研究假设声明；`USER_CALIBRATED` 必须提供非空引用，但软件只校验声明，不验证标定质量 |

`simulate_detections()` 的 `integration_budget=16384` 是内部面积积分预算，允许 1～1,000,000；`output_point_limit=4096` 是独立的报告点数上限，允许 1～4096。设备最大点数不限制内部积分点数，也不意味着每帧一定输出4096点。几何对照 `simulate()` 的 `candidate_budget` 限制为1～4096。噪声搜索单元总数受1,000,000,000,000的数值预算约束，不分配稠密四维数据立方体；默认为7,257,784,600个试验单元，纯噪声期望虚警约0.00726个／帧（输出筛选前）。

面积采样种子 `sampling_seed` 和噪声种子 `noise_seed` 默认均为 100。检测随机数用 `numpy.random.PCG64`，按噪声种子与帧序号生成独立的功率、漏检、虚警和测量误差流。模型假设、完整参数、种子、输出上限与帧级统计写入 provenance；积分预算、参考总面积、可见参考面积和采样计划身份也保留。增加积分点数是在逼近同一面积响应，不是为每个点赋予相同的独立回波功率。

### 模型诊断与厂家字段

工程帧的 `mag/rcs/snr` 仍为 NaN，`label` 不生成厂家分类；`ms` 仅按合成径向速度绝对值大于 `1e-6 m/s` 派生。未经映射不能把模型值填入这些厂家字段。

| `model_diagnostics` 字段 | 语义 |
| --- | --- |
| `model_power_w` | 合并单元内目标贡献的期望接收功率，W |
| `model_observed_power_w` | 信号归一化的检测器总功率抽样值，W；按所选Swerling模型产生，用于门限与输出排序 |
| `model_snr_linear` | 期望接收目标功率除以处理后等效噪声功率，无量纲线性比；不是厂家 `snr` 或 dB |
| `model_rcs_m2` | 单元内有效散射截面之和，m²；不是厂家 `rcs` |
| `integration_samples` | 该输出单元的正响应积分样本数，不是可辨识目标数 |
| `false_alarm` | 1 表示纯噪声单元，0 表示有目标贡献；0 不保证信号主导或叶片身份已知 |

诊断由 [schema.py](schema.py) 的 `ModelDiagnostics` 保存，合同为 `x100-model-diagnostics.v1`，逐点对应且仅允许工程仿真／测试来源；随元数据进入完整记录与本地通道。纯噪声单元的目标功率、模型 SNR、RCS 和积分样本数均为零。合并和虚警使检测点不再与材料点一一对应，因此工程帧不携带 `material_ids` 或材料点索引。

缺少源样本时返回 `None`，不补造空帧；合法零点帧保留。`detection_statistics()` 与 `summarize_detections()` 把缺失／不完整帧记为 UNKNOWN，已知空帧计零，虚警不计目标支持。ROI 只筛评价点，帧级模型总量仍描述全帧；目标关联仅指含目标贡献的模型单元，不代表识别出某片叶片。

## 同帧侧壁分析与评价边界

`analysis.Context` 以只读 NumPy 数组保存身份、`world_from_analysis`、`world_from_sensor`、侧壁顶点及三角面；不包含叶片真值和材料标签。`validate_surface()` 拒绝非法、非有限或退化塔面；`closest_reference()` 是小样本独立 NumPy 数学基准，`distance_colors()` 提供独立固定单向色标，不改变原始点属性。纯核心不导入 bpy 或 mathutils。

Blender `SceneSampler.analysis_context()` 从中心源时刻样本提取配对上下文，运行时身份包含场景会话运行代、源帧、时钟、来源／几何／标定及适用回放索引。`SideWallQuery` 负责锚点 BVH，结果逐点保留原始 indices、机舱局部 points、distance、世界最近点 nearest、侧壁局部 triangle 和 status；状态为 VALID、OUTSIDE_ROI、NONFINITE 或 QUERY_FAILED。缺失距离／最近点使用 NaN，无面身份使用 -1，不填零。finite 计全部有限坐标点，selected 计 ROI 内有限点，valid 计距离查询成功点；距离分位数只使用有效距离，不受显示缩放或像素裁剪影响。

同帧上下文和结果不属于 `x100-frame.v1` 元数据或记录载荷。历史回放缺上下文时不进行机舱分析；当前 Scene 不能补历史几何。显示参考与在线距离输入分别传递，原始 material_ids 只可用于独立离线评价，不可作为在线目标识别。

[离线覆盖工具](../../scripts/radar_x100/coverage.py) 以相同源和完整 30 Hz 网格评价原位、前部下视、侧面和尾部下视四个基准，分别保存机械约束、几何覆盖与模拟检测支持。每次完整名义下方经过以期望网格数 N 为几何分母；有限参考驻留 E、可见 V 和独立候选实际输出 O 分别计数。UNKNOWN 不当已知缺口，零分母为 null／N/A。软件 ROI 为机舱局部坐标，硬件角域与量程独立保存；操作及来源限制见[X100 说明](../../docs/blender/X100雷达.md#离线安装覆盖研究)。

## 安装搜索与叶片估计

[installation.py](installation.py) 与 [optimize_mount.py](../../scripts/radar_x100/optimize_mount.py) 使用 `x100-installation-search.v2`。报告分开保存硬件、ROI、机械、几何覆盖、所评网格的模拟检测支持及 `detection_profiles[].passage_detection_support`。主指标为 `DISTAL_ROI_PASSAGE_DETECTIONS.v1`：完整叶片经过期间末段ROI的非纯噪声检测支持、最长连续漏检秒数、精确正下方时刻支持与塔距误差。排序依次比较最差经过支持率、最长漏检、正下方支持率、最差叶片支持率、塔距RMSE、平均ROI检测点数，几何覆盖只作平局辅助。各配置取最不利值，不合成设备可靠性分数。

`selected_installation()` 校验v2、来源、几何、面积方法与算法身份并重算排名；机械／运动须为 `PASS_RESEARCH`，检测经过、正下方与适用塔距误差须已知，最差叶片检测支持须为正。导入还要求所评网格的模拟支持已知且为正、几何覆盖字段齐全；几何零覆盖本身不再是排名否决条件。缺证据、排名不符、来源不符或有错误帧时拒绝应用。仅筛选前若干经过的报告不提供可应用 `winner_id`。应用保留预算、观测模式和场景时刻；ROI可能包含塔架返回，支持率不是叶片识别率，详见[安装覆盖搜索](../../docs/blender/X100观测研究.md#安装覆盖搜索)。

`tracking.py` 的 `fit_blades()` 只接收观测世界坐标、同帧塔面距离及三个 `BladePrior`。先验含逻辑叶片编号、`world_from_blade` 和名义叶尖径向坐标 `tip_span_m`；Blender 以轮毂为原点、根部方向为坐标轴，末端局部Z取结构参数 `TipRad`。已知塔面附近点及多叶片走廊重叠点保持未关联；原始观测和原索引不改写。分段中位数与稳健二次拟合提供候选叶片段，支持、跨度、残差和外推条件满足时给出名义径向截面的模型叶尖估计。拟合离散度不是经过标定的物理置信区间。

`BladeTracker` 在叶片局部坐标中做因果平滑，当前根部姿态负责旋转；重复帧不重复更新，后退、长缺口或换来源清除跟踪历史。当前帧不满足拟合条件时不输出沿用的旧叶尖。 `consecutive_frames` 表示最大间隔以内的连续有效估计次数，期间可能有未处理的源帧。估计与塔面距离属于独立派生结果，不进入原始记录，不替代硬件 Track 流。用法、参数与独立评价见[观测研究](../../docs/blender/X100观测研究.md)。

## 外部采集与本地通道

`Assembler` 接收设备适配器已解码的分片，要求一致帧身份、显式分片总数、终止证据和点数预算；只有全部分片到齐才能输出完整帧。`terminal` 必须为布尔值。同身份、元数据和点字节的重复片可补充 `terminal=True`，不重复加点；`False` 不撤销已有终止证据。补充终止证据后立即检查完整性，有效零点帧适用相同规则。冲突、超时、完成或已声明损坏的单元不因重复片恢复。尚未超时的在途前帧可在后帧完成后继续组装，已结束身份的水位不倒退。已声明损坏的采集单元不能升级为完整帧。适配器负责正式线协议校验、片索引和结束标志解释。未知协议不能直接送入组帧器。超时、重复、损坏及容量溢出分别计数；会话重启调用 `begin_session()` 清除该设备旧组帧身份。

`Fanout` 在提交边界复制点数组、时间和元数据，同时交付有界记录队列与每流最新显示槽。显示按 `(sensor_id, session_id, source_kind, stream)` 保留已接受序号水位；取走待发帧、断线重连或淘汰显示槽都不清除水位，迟到或重复帧仍进入记录分支，但不能回退显示。首次出现的传感器可自动认领会话；已知传感器换会话须由可信生产者调用 `begin_session(sensor_id, session_id, source_kind)`，已退休身份不能重新启用。待发载荷最多 8 项；会话身份与流身份各自受 `display_capacity` 限制，默认 64、允许 1–1024，达到上限时拒绝新显示身份而保持记录。水位与退休身份持续到该 Fanout 关闭；新一代采集须在关闭后创建新 Fanout，不能靠清空待发槽重置。

生产者在提交后修改原对象不会改写已接受记录。元数据导出不返回内部字典引用，帧元数据须满足本地通道的容量预算。关闭与提交互斥，关闭开始后拒绝新的记录提交。显示替换有计数，不丢弃记录任务；记录队列溢出或写盘失败停止该记录并保留不完整标记，显示仍可继续。记录完整性只描述实际接收并提交的范围，不承诺恢复设备／UDP 已丢数据。记录 worker 不访问 bpy。

`LocalPublisher` 仅监听 `127.0.0.1`，支持一个显示客户端。每包为 `<4sII` 头（magic `X1F1`、JSON 元数据长度、二进制载荷长度），后接 UTF-8 JSON 和点数组／可选速度数组；元数据最多 256 KiB，数组最多 `4096×30` 字节。HELLO 声明版本、来源、传感器和会话；会话变化必须重连。

工作区发布端在空闲期检测连接可读／关闭状态，新的显式连接接管单一显示客户端。`Fanout.take_latest_with_time()` 消费并返回 `(frame, received_monotonic)`；发布端用 `begin_delivery()` 取得 `(frame, received_monotonic, token)`，成功发送后调用 `finish_delivery()`，两种读取在没有待发帧时均返回 `None`。发送失败仅可用对应的一次性交接凭据退回同一帧；退回时仍须属于当前会话、匹配序号水位且槽位可用，不能覆盖较新观测、恢复已退休会话或重复退回。记录队列独立。FRAME 的可选 `received_monotonic` 是本机 Fanout 首次接收该显示观测的单调时刻，重试使用交接时保留的原值；`PacketReader.pop()` 对此返回 `ReceivedFrame(frame, received_monotonic)`，客户端按该时刻计算年龄，延迟交付不会重置新鲜度。没有此字段的包仍按客户端接收时刻处理。该时刻只在本机显示通道内比较，不进入原始帧元数据或记录，不映射设备物理时钟。

`LocalClient` 非阻塞且有每次字节／帧数／时长预算，独立判断各流等待、有效、有效空帧、过期与断开。当前不提供远程监听、跨主机认证转发或自动重连。

```bash
python scripts/radar_x100/serve.py --adapter trusted_local_adapter:frames --record /path/to/new_recording
python scripts/radar_x100/serve.py --replay /path/to/recording
```

适配器工厂迭代返回规范 `RadarFrame`，换会话前返回 `acquisition.SessionStart(sensor_id, session_id, source_kind)` 控制事件；该事件不写入帧记录。`--replay` 由记录身份声明会话，仅用于软件通道检查，保留文件来源，不构成真实设备接入。实机适配器需补足匹配固件的 UDP／SDK、模式、单位、轴方向、帧号／时间和空帧合同。

`serve.py --record` 在关闭记录线程后核对最终状态；零帧、写盘或记录预算失败以非零退出码结束，保留失败清单和 `INCOMPLETE`。至少记录一帧且完整关闭才成功，有效零点帧满足这一要求。不启用记录时，适配器正常结束可返回成功；采集异常保留原始异常并以非零退出码结束。

# BeamDyn 叶片弹性扭转

`physics.blade_torsion: true` 将 NREL 5MW 算例的叶片结构模型设为 BeamDyn，塔架、机舱及传动链仍由 ElastoDyn 计算。默认关闭；省略选项与显式关闭时，完整生成目录及遥测准备后的文件均与默认字节基准一致，BeamDyn 文件也不进入默认算例。此功能属于工作区源码能力，发布包及本机安装状态另行核对。

## 开启方式

```yaml
physics:
  blade_torsion: true
  tower_flex: true
  dynamic_stall: true
  tower_shadow: true
```

四个选项独立选择。场景、`FastFarmDriver(physics={"blade_torsion": True})`、低层 `create_ff_case()` 使用同一布尔配置。CLI、Bridge 与 Blender 启动器接受 `--blade-torsion` / `--no-blade-torsion`；未传参数保留场景值，运行中不能切换模型。具体入口见 [物理选项](FASTFarm物理选项.md)。

## 模型与来源

叶片输入来自仓库 `results/prebend-20260918/B/5MW_Baseline/` 的两个文件。已与 OpenFAST 官方 r-test v3.5.3 核对：截面质量、刚度及阻尼文件逐字节相同；主文件所有解析参数和 49 个几何点数值相同。主文件的几何数字格式不同，不是截面属性重建。交付输入在嵌套仓库中保持 CRLF。

| 文件 | 官方 URL | 官方原始字节 SHA-256 | 仓库模板 SHA-256 |
| --- | --- | --- | --- |
| `NRELOffshrBsline5MW_BeamDyn.dat` | [r-test v3.5.3 主文件](https://raw.githubusercontent.com/OpenFAST/r-test/v3.5.3/glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat) | `ecc69eecb955afe435f4b993ca40d12335f0b99dcb833516107224660988147b` | `59935bb4b29a8789dad8fb5f1284af98ca14570a19b4c86b20b326ebb6ca847d` |
| `NRELOffshrBsline5MW_BeamDyn_Blade.dat` | [r-test v3.5.3 截面文件](https://raw.githubusercontent.com/OpenFAST/r-test/v3.5.3/glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_BeamDyn_Blade.dat) | `b6031e7e0ebc1b31d905454f0c1aa1af8edd01e2f7882c60047a19bdbdb44138` | 同左 |

只支持三叶片、`TipRad=63 m`、`HubRad=1.5 m`、长度 61.5 m 的此套直叶片模型。主文件及截面模板受摘要约束，生成目录的几何、积分设置和截面摘要也校验；预弯、后掠、修改截面或其他机型须另行建立定义与验证，不会按本选项解释。`nrel5mw.yaml` 和 DISCON 源码不因选项改变。

## 积分与节点输出

开启时设置 `CompElast=2`，三项 `BDBldFile` 指向同一 BeamDyn 输入。保留官方 `QuasiStaticInit=True`、`rhoinf=0`、`quadrature=2`、`refine=DEFAULT`（1）、`order_elem=5`、`DTBeam=DEFAULT` 以及 Newton 迭代设置。结构自由度由一个五阶单元的六个有限元节点描述；输出网格为 49 个梯形积分点，不能把六个输出站位误认为六个等间距有限元节点。

[官方陆上 BeamDyn 算例](https://raw.githubusercontent.com/OpenFAST/r-test/v3.5.3/glue-codes/openfast/5MW_Land_BD_DLL_WTurb/5MW_Land_BD_DLL_WTurb.fst) 使用 `DT=0.0015 s`。本项目选用更小的 `0.00125 s`，使其整除现有 `DT_High=0.1 s` 和控制步 `DT_Low=3 s`；不改变风场网格和耦合步长。不满足整除关系的生成输入拒绝。关闭时保持 `DT=0.00625 s`。时间积分细化属于 BeamDyn 模型对比条件的一部分，短算例不能隔离纯扭转自由度的因果影响，也不构成步长收敛验证。

`NNodeOuts=6`，`OutNd=1,10,19,28,37,49`。每片叶片请求六站位的 `NβRDxr/NβRDyr/NβRDzr` 完整向量；OpenFAST 合并输出名例如 `B1N6RDxr`。`β` 是输出序号，实际节点由 `OutNd` 映射。

| 输出序号 | 积分点号 | 叶根起算距离 / m | 展长比例 |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 0 | 0 |
| 2 | 10 | 8.201025 | 0.13335 |
| 3 | 19 | 18.200925 | 0.29595 |
| 4 | 28 | 36.200130 | 0.58862 |
| 5 | 37 | 54.199950 | 0.88130 |
| 6 | 49 | 61.500000 | 1 |

完整三叶片、六站位的原生向量保留在 `Case.Tn.out`，不改写求解器原始列。对比脚本另外导出 `Case.Tn.torsion.csv`，包括各时刻的 18 个角度列，单位度。输出间隔为 0.25 s，不等于积分步长；需要高频疲劳或振动分析时须另选采样率。

## 弹性扭转角定义

BeamDyn 的 `RDxr/RDyr/RDzr` 是相对未变形截面、在随根部运动的 `r` 系中表达的 Wiener–Milenković 参数，不是三个欧拉角。原生计算去除了根部刚体运动和参考截面方向，因此不把桨距指令、方位角或预设气动扭角当成弹性变化。实现依据为 [BeamDyn v3.5.3 Calc_WriteOutput](https://github.com/OpenFAST/openfast/blob/v3.5.3/modules/beamdyn/src/BeamDyn_IO.f90)；旋转参数定义见 [BeamDyn 理论](https://openfast.readthedocs.io/en/v3.5.3/source/user/beamdyn/theory.html)。

本项目定义为：先用最短旋转将未变形截面的 `+z` 轴转到变形后的截面 `+z` 轴，再取剩余的绕该变形轴的有符号旋转。正号遵循右手规则。这里的轴是截面材料轴（变形后的桨距轴），剪切存在时不必与中心线切向重合。该几何定义也不等同于沿弧长积分的扭转曲率。

对完整参数向量 `c=(cx,cy,cz)`，四元数的标量和向量部分为：

```text
qw = (16 - dot(c,c)) / (16 + dot(c,c))
qv = 8*c / (16 + dot(c,c))
theta = 2*atan2(qv.z, qw) * 180/pi
```

角度归一到 `[-180,180)`。这相当于 `R = R_swing R_twist` 的分解，扭转发生在未弯曲 `z` 轴后再随摆动旋转到当前轴。纯弯曲的最短旋转返回零；弯曲与扭转同时存在时必须使用完整三分量，不能仅对 `RDzr` 乘以 `180/pi`。截面轴反向的 180° 摆动处定义不唯一，按无效值处理。`RootMz` 为扭矩，`TipRD*` 为有限旋转参数，均不直接冒充角度。

## 接口与当前限制

`SolverOutputs` 输出 `tip_torsion_1`、`tip_torsion_2`、`tip_torsion_3`，单位 `deg`。Driver 消费当前算例输出，经原有 Bridge 快照、LIVE TELEMETRY 搜索及 `Elastic tip torsion (blades 1–3)` 组图和导出传递。沿展向其他站位不增加实时消息大小。

此路径沿用塔顶位移的原生文件旁路，并在 MPI 耦合时钟下消费；DISCON 的 12 元测量数组、控制器 ABI、奖励及训练观测维数保持原定义。来源记录包含三原始列、节点 49、展长比例、源时刻、`physics_identity`、`value_kind=DERIVED` 和 `beamdyn-relative-rotation-swing-twist-z.v1`。`DIRECT` 标记来源于真实求解器，几何派生过程在 provenance 中明确保留。缺列、错误单位、非有限向量、奇异姿态及过期输出不会填零。

BeamDyn 下 ElastoDyn 的原生叶片位移及截面载荷列可为 `INVALID`，前端相应通道保留 unsupported，不改标签冒充 BeamDyn 坐标。叶根两向弯矩仍由既有 ServoDyn/MPI 路径传递。对比脚本从 BeamDyn 的根部力矩和叶尖位移向量进行桨距坐标旋转，得到共同的 coned `c` 坐标后比较；转换与 [OpenFAST SrvD_InputSolve](https://github.com/OpenFAST/openfast/blob/v3.5.3/modules/openfast-library/src/FAST_Solver.f90) 的根弯矩公式一致。

全关闭时训练配置身份保持兼容。当前 `scripts/train/train_fastfarm.py` 会给已有非空三项物理配置增加 `blade_torsion: false`，可能使旧 checkpoint 的配置身份不匹配；该脚本的兼容规范化及对应既有测试需由归属任务处理，不能把不匹配直接当作同一训练条件恢复。BeamDyn 开启的训练本来就属于不同物理模型，必须保留独立身份。

## 验证入口

从外层仓库根目录执行；每个输出目录须不存在。单机 8 / 12 m/s 各运行一次 60 s；三机坐标与 `scenes/turb3_row.yaml` 的 0/504/1008 m 一致；全开组合可用 12 s 检查。示例：

```bash
DT_TMP=$(mktemp -d /private/tmp/wfrl-dt.XXXXXX)
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/wfcrl-env:$PWD:$PWD/blender_frontend"
export MPLCONFIGDIR="$DT_TMP/mpl" TMPDIR="$DT_TMP" OMP_NUM_THREADS=1
DT_PY=/opt/anaconda3/envs/wfrl-mac/bin/python
"$DT_PY" -m scripts.experiments.compare_fastfarm_physics \
  --output "$DT_TMP/single8" --seconds 60 --wind-speed 8 \
  --variants off blade_torsion --timeout 900 \
  --fastfarm /opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm
```

12 m/s 使用 `--wind-speed 12`；三机使用 `--turbines 3`；全开使用 `--seconds 12 --variants off all_on`。脚本确认正常结束标记并等待所属 FAST.Farm 原生进程退出后才读取 `.out`，不把 MPI Finalize 或断连等同于写盘完成。报告记录总墙钟、各个 3 s 控制交换的墙钟、末半段的均值／总体标准差／极值、输入身份、原生单位及坐标转换口径。`all` 表示三项塔架／气动组合，`all_on` 表示四项全开。

针对性软件检查：

```bash
"$DT_PY" -m pytest tests/test_dt_blade_torsion.py tests/test_solver_outputs.py \
  tests/test_dq_solver_completion.py -q -x -p no:cacheprovider --basetemp="$DT_TMP/test"
"$DT_PY" -m pytest blender_frontend/tests/test_dt_torsion_telemetry.py \
  blender_frontend/tests/test_dq_tower_telemetry.py \
  blender_frontend/tests/test_solver_telemetry.py \
  blender_frontend/tests/test_live_telemetry_panel.py \
  --ignore=blender_frontend/tests/blender -q -x -p no:cacheprovider \
  --basetemp="$DT_TMP/frontend"
```

测试覆盖完整默认算例字节、开关与来源约束、组合输入、初始化参数、输出节点、幂等性、拒绝时不写入、有限旋转分解、无效值、源时钟、协议、曲线及导出。算例与临时输出放在 `/private/tmp`，提取证据后删除。

## 主机真实求解证据

对象为 Apple M5 Pro、macOS 27.2.1 arm64、FAST.Farm/OpenFAST v3.5.3-dirty（GCC 12.3.0，单精度）、OMP_NUM_THREADS=1。求解器 SHA-256 为 `bed9651e19daf8ee6402100ff22b90867b756d96b72495ef9ee90df677a88d3f`。所有下列算例均到达要求的末时刻、正常终止，统计只在对应原生进程退出后读取。每组一次，墙钟是本机短算例观测，不代表跨平台性能保证。

60 s 算例统计窗口为 30–60 s、间隔 0.25 s、121 个样本；12 s 全开组合窗口为 6–12 s、25 个样本。来流为稳态 8/12 m/s，保留模板剪切及初始转速／控制器，偏航指令为零。比较使用相同 coned 坐标的 B1 叶根弯矩和叶尖 x 向位移。均值 ± 总体标准差如下，ElastoDyn 扭转记为未建模。

| 风速 / (m/s) | 结构 | 功率 / kW | 根部 My / (kN·m) | 根部 Mx / (kN·m) | 叶尖挥舞 / m | 叶尖弹性扭转 / ° |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 8 | ElastoDyn | 1706.756 ± 0.306 | 5565.942 ± 314.170 | 364.159 ± 2517.967 | 3.104 ± 0.258 | 未建模 |
| 8 | BeamDyn | 1709.411 ± 0.271 | 5525.187 ± 334.536 | 377.203 ± 2403.695 | 3.100 ± 0.262 | -0.126867 ± 0.166882 |
| 12 | ElastoDyn | 4999.253 ± 3.679 | 8529.354 ± 607.329 | 1308.501 ± 2582.559 | 4.466 ± 0.427 | 未建模 |
| 12 | BeamDyn | 4999.845 ± 3.677 | 8493.229 ± 673.466 | 1314.505 ± 2459.836 | 4.437 ± 0.450 | -0.501794 ± 0.266453 |

| 算例 | 仿真时长 / s | 总墙钟 / s | 每 3 s 控制步平均 / s | 最慢控制步 / s |
| --- | ---: | ---: | ---: | ---: |
| 1 机 8 m/s off | 60 | 7.339 | 0.341 | 0.361 |
| 1 机 8 m/s blade_torsion | 60 | 134.936 | 6.715 | 7.252 |
| 1 机 12 m/s off | 60 | 8.086 | 0.353 | 0.430 |
| 1 机 12 m/s blade_torsion | 60 | 130.789 | 6.507 | 6.649 |
| 3 机 8 m/s off | 60 | 24.093 | 1.076 | 1.211 |
| 3 机 8 m/s blade_torsion | 60 | 399.442 | 19.820 | 20.404 |
| 1 机 12 m/s off | 12 | 2.281 | 0.296 | 0.311 |
| 1 机 12 m/s all_on | 12 | 26.977 | 6.582 | 6.623 |

三机 `turb3_row` BeamDyn 的控制交换远超过 3 s，因此此主机的实测配置不能实时交互，适合离线运行。三机的 60 s 窗口短于相邻机组按自由来流估算的 63 s 输运时间，不能据此评价充分发展的尾流。单机的两种风速下也不满足实时要求；其他线程数、GPU、其他主机及长时稳定性能尚未验证。

B1 沿展向扭角统计（均值 ± 总体标准差；括号为最小值至最大值，单位 °）：

| 节点 | 8 m/s | 12 m/s |
| ---: | ---: | ---: |
| 1 | 0.000000 ± 0.000000 (0.000000 ～ 0.000000) | 0.000000 ± 0.000000 (0.000000 ～ 0.000000) |
| 10 | 0.000795 ± 0.004393 (-0.006064 ～ 0.006696) | 0.000637 ± 0.006665 (-0.009446 ～ 0.010778) |
| 19 | -0.000155 ± 0.026817 (-0.042018 ～ 0.035879) | -0.004713 ± 0.040135 (-0.062799 ～ 0.056768) |
| 28 | -0.029202 ± 0.111234 (-0.199000 ～ 0.124156) | -0.105917 ± 0.165946 (-0.346728 ～ 0.163549) |
| 37 | -0.097324 ± 0.173495 (-0.347513 ～ 0.152399) | -0.379682 ± 0.266760 (-0.765613 ～ 0.095992) |
| 49 | -0.126867 ± 0.166882 (-0.357925 ～ 0.116490) | -0.501794 ± 0.266453 (-0.885440 ～ -0.010079) |

四项全开、12 m/s、单机 12 s 算例正常完成；末半段平均功率 4857.198 kW，塔顶前后位移 0.380747 m，B1 叶尖弹性扭转 -0.271097°。此组合的短时可运行性已验证，不能替代长时工况和步长／空间离散收敛。

三片叶片所有站位的旋转与派生角度均有限；节点 49 的三个旋转分量与相应 `TipRD*` 原生列逐值相同，根部扭角在数值零附近。`.BD*.sum.yaml` 确认动态准静态初始化、0.00125 s、梯形积分、1 个单元及 6 个有限元节点；输出位置为 quadrature points。

8 m/s 与 12 m/s 完成输出分别经 `SolverOutputs → FastFarmDriver._read → SnapshotAdapter → 协议编解码 → ChartHistory / HistoryWindow` 回放 20 个控制时刻、三叶片各 20 个角度。合计 120 个角度样本的单位、源时刻和物理身份一致，相对离线 CSV 的数值差为 0°。这是求解完成文件的链路核对，没有在运行中读取 `.out`，也不证明在线文件缓冲时延或 Blender 可见窗口效果。

BeamDyn 模型下原有 ElastoDyn 叶片列的 INVALID、模板可选 nodal-output 段提示及 Farm 输出列宽提示不作为有效测量；所报告的原生列均核对了单位和有限值。全开组的非升力圆柱截面存在 UA 关闭提示；不将这些截面解释为产生动态失速响应。短时统计不是认证精度、疲劳寿命或正式训练验收。

算例身份与原始输出摘要用于识别验证对象；输入依赖完整摘要由对比脚本的 physics 记录保存。临时算例不作为仓库资产保留，复现使用上述命令。

| 对象 | physics identity | Case.T1.out SHA-256 |
| --- | --- | --- |
| single8/off | `e34b0c82eaee3628e0f38db8e731288cebbfd5c76df59a3cac8f571067ad44bf` | `ad5396d5b3e29b0b6e5c27dd658f2d6ee034ded4f73ddf9df792435adc27a833` |
| single8/blade_torsion | `f7a64cfe13d3c50fe0da42e2183b9976bac3fadf6fd832a2a41be49dbbf4b145` | `3f8517651560392aa4b7595d92c8347ad4b7781e845883ed631db42087f08143` |
| single12/off | `2b953551956962df6acd28d4bb3b1098d526f6e17252da2c5aef13178cd58915` | `1f2a9c7634531fea2880109d6a1baa77be129f30547c3daf0c013b6b85c75cc3` |
| single12/blade_torsion | `9282cd27b918bfc85b7104ca167964f918c180ae2a2702ff09de202b25d1c7ab` | `cdb224ed29d5c453a490d4dbd5def8f42c13a703c0b1cc7adfc7bb427f63616e` |
| three8/off | `aaa24f8ca1740dc8d6675f7e5f858458431b17361589a481fbfe2d32612f6ea7` | `11e8b6ba7abd3434b687625d1c0174a8a8a1252ee43f28b2c2524ff574ad5f64` |
| three8/blade_torsion | `cf38d1f233778b560b81ee971ba9f47d7bcd221c7f9076b6deb1f42787555dd9` | `c4c79cdc95c00742f6914358d61702a2ff59f897dfb0423dea325f7f4a39eebc` |
| all_on/off | `aa7b7f1dcbff38ae898269564dd46cdc42cce74b4b4367c43f49da31e9a8370c` | `75d4e7ae35c5f8efd6a417e4259ee4717a17bf7ef6d8be0c34545de72b0a9df7` |
| all_on/all_on | `957d0038b48b829da10dba6e3180db6cb01361d18e2ea1380cc768b6b2c7f7e6` | `fe5c41e3845c522112306334fc86b876009f9c75c790624dea8dd942cad8246a` |

软件验收：宿主相关文件 199 项通过，前端及文本／名称守卫 94 项通过。`test_training_identity_preserves_defaults_and_separates_physics` 在相关测试集合中失败，原因是非空旧配置增加关闭字段；为继续检查其余功能，以下宿主命令显式排除该已知失败，不代表全绿。该问题的边界见“接口与当前限制”。

```bash
"$DT_PY" -m pytest tests/test_dt_blade_torsion.py tests/test_dq_physics_options.py \
  tests/test_dq_solver_completion.py tests/test_solver_outputs.py \
  tests/test_fastfarm_driver_inputs.py tests/test_fastfarm_driver_bn_contract.py \
  tests/test_fastfarm_driver_exceptions.py tests/blender_bridge/test_workflow.py \
  -k "not training_identity_preserves_defaults_and_separates_physics" \
  -q -x -p no:cacheprovider --basetemp="$DT_TMP/host"
"$DT_PY" -m pytest blender_frontend/tests/test_dt_torsion_telemetry.py \
  blender_frontend/tests/test_dq_tower_telemetry.py \
  blender_frontend/tests/test_solver_telemetry.py \
  blender_frontend/tests/test_live_telemetry_panel.py \
  blender_frontend/tests/test_text_encoding_guard.py \
  blender_frontend/tests/test_text_io_encoding.py \
  blender_frontend/tests/test_static_names.py \
  --ignore=blender_frontend/tests/blender -q -x -p no:cacheprovider \
  --basetemp="$DT_TMP/frontend"
```

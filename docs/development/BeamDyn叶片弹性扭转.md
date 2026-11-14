# BeamDyn 叶片弹性扭转

`physics.blade_torsion: true` 将 NREL 5MW 算例的叶片结构模型设为 BeamDyn，塔架、机舱及传动链仍由 ElastoDyn 计算。默认关闭；省略选项与显式关闭时，完整生成目录及遥测准备后的文件均与默认字节基准一致，BeamDyn 文件也不进入默认算例。此功能属于工作区源码能力，发布包及本机安装状态另行核对。

## 开启方式

```yaml
physics:
  blade_torsion: true
  blade_torsion_fidelity: fast
  tower_flex: true
  dynamic_stall: true
  tower_shadow: true
```

四个布尔选项独立选择。`blade_torsion_fidelity` 为 `fast|reference`，省略时采用 `fast`；`reference` 要求同时开启 `blade_torsion`。场景、`FastFarmDriver(physics={"blade_torsion": True})`、低层 `create_ff_case()` 使用相同的物理配置。CLI、Bridge 与 Blender 启动器接受 `--blade-torsion` / `--no-blade-torsion`；未传参数保留场景值，`--blade-torsion-fidelity reference` 可显式选择精细配置；运行中不能切换模型。具体入口见 [物理选项](FASTFarm物理选项.md)。

## 模型与来源

叶片输入来自仓库 `results/prebend-20260918/B/5MW_Baseline/` 的两个文件。已与 OpenFAST 官方 r-test v3.5.3 核对：截面质量、刚度及阻尼文件逐字节相同；主文件所有解析参数和 49 个几何点数值相同。主文件的几何数字格式不同，不是截面属性重建。交付输入在嵌套仓库中保持 CRLF。

| 文件 | 官方 URL | 官方原始字节 SHA-256 | 仓库模板 SHA-256 |
| --- | --- | --- | --- |
| `NRELOffshrBsline5MW_BeamDyn.dat` | [r-test v3.5.3 主文件](https://raw.githubusercontent.com/OpenFAST/r-test/v3.5.3/glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat) | `ecc69eecb955afe435f4b993ca40d12335f0b99dcb833516107224660988147b` | `59935bb4b29a8789dad8fb5f1284af98ca14570a19b4c86b20b326ebb6ca847d` |
| `NRELOffshrBsline5MW_BeamDyn_Blade.dat` | [r-test v3.5.3 截面文件](https://raw.githubusercontent.com/OpenFAST/r-test/v3.5.3/glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_BeamDyn_Blade.dat) | `b6031e7e0ebc1b31d905454f0c1aa1af8edd01e2f7882c60047a19bdbdb44138` | 同左 |

只支持三叶片、`TipRad=63 m`、`HubRad=1.5 m`、长度 61.5 m 的此套直叶片模型。主文件及截面模板受摘要约束，生成目录的几何、积分设置和截面摘要也校验；预弯、后掠、修改截面或其他机型须另行建立定义与验证，不会按本选项解释。`nrel5mw.yaml` 和 DISCON 源码不因选项改变。

## 积分与节点输出

开启时设置 `CompElast=2`，三项 `BDBldFile` 指向同一 BeamDyn 输入。保留官方 `QuasiStaticInit=True`、`rhoinf=0`、`quadrature=2`、`refine=DEFAULT`（1）、`order_elem=5`、`DTBeam=DEFAULT` 以及 Newton 迭代设置。结构自由度由一个五阶单元的六个有限元节点描述；输出网格为 49 个梯形积分点，不能把六个输出站位误认为六个等间距有限元节点。

[官方陆上 BeamDyn 算例](https://raw.githubusercontent.com/OpenFAST/r-test/v3.5.3/glue-codes/openfast/5MW_Land_BD_DLL_WTurb/5MW_Land_BD_DLL_WTurb.fst) 使用 `DT=0.0015 s`。项目的数值配置定义在 [BeamDyn_profiles.json](../../wfcrl-env/wfcrl/simulators/fastfarm/inputs/template/BeamDyn_profiles.json)，其中记录官方来源和整除推导。它只选择新生成 OpenFAST 主文件的 `DT`，不改写官方 BeamDyn 主文件、截面或气动输入。

| 配置 | OpenFAST DT / s | 每个 0.1 s 高分辨率耦合步的子步数 | 每个 0.25 s 输出间隔的子步数 |
| --- | ---: | ---: | ---: |
| `reference` | 0.00125 | 80 | 200 |
| `fast`（默认） | 1/460 ≈ 0.002173913043478261 | 46 | 115 |

两者都保留 `InterpOrder=2`、`NumCrctn=0`、`DTBeam=DEFAULT`、五阶单元、梯形积分、`refine=1`、`rhoinf=0`、`NRMax=10`、`stop_tol=1e-5`、`n_fact=5`、`load_retries=20` 和完整站位输出。未启用扭转时保持 `DT=0.00625 s` 与默认生成字节；`blade_torsion_fidelity: fast` 不会隐式开启扭转。两种 BeamDyn 步长都必须整除实际 `DT_High` 和 `DT_Low`，否则生成输入拒绝。风场网格、控制周期、气动模型和结构属性不随数值配置改变。

`fast` 是已测试且同时满足 8/12 m/s 判据的最快候选；其速度与精度证据见下文。默认选择它的依据是保留物理模型和输出合同，同时通过指定的 60 s 对照。`reference` 用于需要精细步长的对照及尚未覆盖的工况；短程通过不证明任意载荷、长时湍流或极端工况的稳定性。

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

训练配置在直接参数和场景两条路径中，都只为非默认 `reference` 序列化 `blade_torsion_fidelity`；显式 `fast` 与省略字段具有相同身份。全关闭及原有三项布尔配置不增加新字段。算例物理记录分别保存实际 DT、fidelity、配置定义摘要及输入摘要。旧 checkpoint 的配置身份保持兼容，不等于数值步长相同：已开启 BeamDyn、按 0.00125 s 生成的 checkpoint 不能仅因训练配置 hash 匹配就视作 `fast` 的同一积分条件；严格续训需核对原始算例身份，并由负责人决定 reference 配置身份的迁移方式。

## 数值配置的精度与性能

本节对象为 Apple M5 Pro（18 核）、macOS 27.2.1 arm64、FAST.Farm 内嵌 OpenFAST v3.5.3-dirty、GCC 12.3.0、单精度。求解器 SHA-256 为 `bed9651e19daf8ee6402100ff22b90867b756d96b72495ef9ee90df677a88d3f`。单机输入为零偏航指令、稳态 8/12 m/s、模板 `PLExp=0.2`、其余三项物理开关关闭，保留模板初始转速及控制器。全部 `.out` 均在正常结束标记出现且本算例原生进程退出后读取。

以下门槛由本任务明确指定，只用于 fast 对 reference 的数值配置比较；[FAST.Farm 物理选项](FASTFarm物理选项.md) 中的其他精度标准仍为“建议，待确认”，不在这里改成正式要求。功率，以及三片叶片各自的 coned 坐标叶根挥舞/摆振弯矩、叶尖挥舞位移，分别要求均值相对差 ≤1%、总体标准差相对差 ≤5%；三片叶尖弹性扭角分别要求均值绝对差 ≤0.05°、总体标准差相对差 ≤10%。均值分母取 reference 均值的绝对值；零标准差不使用任意容差豁免。所有通道必须同时满足。

单机各运行 60 s，丢弃前 30 s；统计包含 30 s 和 60 s，间隔 0.25 s，共 121 个样本。下表展示 B1，单位依列名；后面的判据表覆盖全部三片叶片。数值为均值 ± 总体标准差。

| 风速 / (m/s) | 配置 | 功率 / kW | 根部 My / (kN·m) | 根部 Mx / (kN·m) | 叶尖挥舞 / m | 叶尖弹性扭转 / ° |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 8 | reference | 1709.411 ± 0.271 | 5525.187 ± 334.536 | 377.203 ± 2403.695 | 3.100491 ± 0.261786 | -0.126867 ± 0.166882 |
| 8 | fast | 1710.265 ± 0.276 | 5528.021 ± 334.782 | 377.765 ± 2403.755 | 3.102585 ± 0.262028 | -0.126589 ± 0.166883 |
| 12 | reference | 4999.845 ± 3.677 | 8493.229 ± 673.466 | 1314.505 ± 2459.836 | 4.437425 ± 0.450127 | -0.501794 ± 0.266453 |
| 12 | fast | 4999.867 ± 3.655 | 8486.897 ± 673.576 | 1314.663 ± 2460.102 | 4.434203 ± 0.450393 | -0.503145 ± 0.266110 |

| 风速 / (m/s) | 主要指标最大均值差 / % | 主要指标最大标准差差 / % | 扭角最大均值差 / ° | 扭角最大标准差差 / % | 判读 |
| ---: | ---: | ---: | ---: | ---: | --- |
| 8 | 0.160337 | 1.831673 | 0.000336 | 0.021375 | 全部通过 |
| 12 | 0.075260 | 0.603666 | 0.001351 | 0.130783 | 全部通过 |

### 候选配置的筛选结果

下列单机 12 m/s 结果使用完整 60 s / 30–60 s 窗口。各行只改变列明参数；Gauss 行同时采用必要的根部/叶尖通道，避免沿用梯形积分点号。`fast` 保留完整六站位输出。表内候选墙钟为各组一次测量，不把不足 1% 的时间波动当作确定收益。

| 候选（其余为 reference） | 60 s 墙钟 / s | 判读 |
| --- | ---: | --- |
| reference | 129.449 | 参考 |
| DT=0.0015625 | 107.084 | 通过 |
| DT=1/600 | 97.086 | 通过 |
| DT=1/560 | 91.112 | 通过 |
| DT=0.002 | 84.603 | 通过 |
| DT=1/480 | 78.401 | 8/12 m/s 均通过 |
| DT=1/460 | 76.023 | 8/12 m/s 均通过；采用为 fast |
| 三阶单元 | 82.811 | 不通过；B1 扭角均值差 0.418°、标准差差 140.7% |
| 四阶单元 | 106.500 | 不通过；B1 扭角标准差差 36.6% |
| 三阶单元、DT=0.0025 | 44.304 | 不通过；空间离散偏差仍超标 |
| Gauss、五阶单元 | 34.806 | 不通过；B1 挥舞位移均值差 1.76%、功率标准差差 40.83% |
| Gauss、七阶单元 | 47.899 | 不通过；B1 挥舞位移均值差 2.05%、功率标准差差 16.14% |

DT=1/440≈0.00227273、0.0025、0.005、0.01 s 在 8/12 m/s 均出现 BeamDyn Newton 不收敛并中止；12 m/s 下的 0.003125、0.00625 s 也中止。四阶单元配合 0.0025/0.005 s、三阶单元配合 0.005 s、Gauss 五/七阶配合 0.0025 s 均未得到可采用结果。0.0025 s 增大 `NRMax` 到 20、设置 `n_fact=1`，或改用线性 `InterpOrder=1` 仍不收敛；线性插值的 0.005/0.01 s 也中止。`NumCrctn=1` 配合 0.0025/0.005/0.00625/0.01/0.0125 s，以及 `NumCrctn=2, DT=0.01 s` 均中止。失败输出不用于统计，也不通过修改气动参数来接受这些候选。

以下是独立的 12 m/s、12 s 短程筛选，统计窗口 6–12 s（25 个样本）。它用于检查运行性和明显的性能变化，不替代上面的 60 s 精度判据；没有依据此短程统计采用新的容差、阻尼、输出方式或积分点细化。

| 设置 | 12 s 墙钟 / s | 结果与选择 |
| --- | ---: | --- |
| `reference`：`{}` | 26.179 | 短程参考 |
| `refine2`：`{"refine": 2}` | 51.524 | 可运行，约慢一倍；保留 refine=1 |
| `tol1e4`：`{"stop_tol": 0.0001}` | 26.139 | 可运行，无明显提速；保留 1e-5 |
| `tol1e3`：`{"stop_tol": 0.001}` | 26.117 | 可运行，无明显提速；保留 1e-5 |
| `nr5`：`{"NRMax": 5}` | 26.085 | 可运行，限制上限未显示加速；保留 10 |
| `nr20`：`{"NRMax": 20}` | 26.599 | 可运行，无提速；保留 10 |
| `rho05`：`{"rhoinf": 0.5}` | 26.080 | 可运行，无明显提速；保留 0 |
| `rho1`：`{"rhoinf": 1.0}` | 26.639 | 可运行，无提速；保留 0 |
| `retry1`：`{"load_retries": 1}` | 26.200 | 可运行，无明显提速；保留 20 |
| `nfact1`：`{"n_fact": 1}` | 43.425 | 可运行，明显更慢；保留 5 |
| `nfact10`：`{"n_fact": 10}` | 26.170 | 可运行，无明显提速；保留 5 |
| `interp1`：`{"InterpOrder": 1}` | 25.990 | 可运行，时间差不足 1%；保留二阶 |
| `omp2`：`{"threads": 2}` | 26.099 | 单机无明显提速 |
| `omp3`：`{"threads": 3}` | 26.111 | 单机无明显提速 |
| `omp6`：`{"threads": 6}` | 26.109 | 单机无明显提速 |
| `minimal`：`{"minimal": true}` | 26.325 | 只保留必要根部/叶尖通道无明显提速；保留完整输出 |

`DTBeam=0.00125` 配合主步长 0.0025/0.005 s，以及 `DTBeam=0.0025` 配合主步长 0.00125 s，实际运行均不收敛。根据 [BeamDyn v3.5.3 输入说明](https://openfast.readthedocs.io/en/v3.5.3/source/user/beamdyn/input_files.html)，`load_retries` 用于静态求解；改变它不能直接减少正常动态步的工作量。`NRMax` 是上限，不是每步固定迭代次数。`n_fact` 控制切线矩阵分解频率。这里保留官方参考值，不用更松容差换取未经证明的收益。

### 生产入口计时与实时性

总墙钟从 MPI 求解启动计至所属原生进程退出；不含算例生成、Python 导入及结果解析。控制步列是逐次 `update_command` 的墙钟，初始化及最终退出另计。单机每组 60 s、20 个控制步。三机采用 `turb3_row` 的 0/504/1008 m 布局、8 m/s，每组 15 s、5 个控制步；这一短程测量只评价吞吐，不代表充分发展的尾流。各组一次，CPU 调度及并行任务会带来墙钟波动。

| 对象 | OMP 线程 | 仿真时长 / s | 总墙钟 / s | 每 3 s 控制步平均 / s | 最慢控制步 / s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 单机 8 m/s reference | 1 | 60 | 128.550 | 6.364 | 6.539 |
| 单机 8 m/s fast | 1 | 60 | 76.439 | 3.781 | 3.863 |
| 单机 12 m/s reference | 1 | 60 | 129.449 | 6.436 | 6.561 |
| 单机 12 m/s fast | 1 | 60 | 75.982 | 3.763 | 3.865 |
| 三机 reference | 1 | 15 | 97.395 | 19.151 | 19.255 |
| 三机 fast | 1 | 15 | 56.937 | 11.104 | 11.232 |
| 三机 reference | 3 | 15 | 35.535 | 6.887 | 6.935 |
| 三机 fast | 3 | 15 | 21.495 | 4.047 | 4.092 |
| 三机 fast | 6 | 15 | 21.435 | 4.037 | 4.069 |

相同单线程下，单机 8/12 m/s 分别加速 1.68/1.70 倍，三机控制步平均加速 1.72 倍。三机已测最快为 OMP=6，平均每步 4.037 s、最慢 4.069 s。3 与 6 线程均约 4.04 s/步，单次测量差约 0.01 s，不支持确定性排序；3 线程已接近已测最高吞吐。**不能实时**：最佳三机和单机 fast 的平均控制步墙钟仍超过 3 s。配置选择不会修改进程的 OpenMP 环境；线程数由启动环境显式指定，不影响关闭扭转时的行为。不同 CPU、长时湍流、偏航变化、极端载荷以及充分发展尾流的精度与稳定性没有由这些结果证明；这些工况需另行验收。

### 测量对象摘要

以下摘要对应上面的单机 reference 与生产入口 fast；SHA-256 识别原始对象，不表示跨来源数值相同。

| 对象 | 输入 identity | Case.T1.out SHA-256 |
| --- | --- | --- |
| 单机 8 m/s reference | `499415a740ce41db05b3d916fc739e3a26ac88c237459ad25490e68e9c328ab6` | `cd5ead1d794c67a52b8dddefcf511f99a3ff9f31226826ae290a182c3d784809` |
| 单机 8 m/s fast | `d5c1d3c89da249cdca755fa89c2baf957fec71ffe40fad27493b30d67a19e53e` | `301085121aff7f6a5db6d374138c38c2247b1370d364ba2f896db320f5cf39b2` |
| 单机 12 m/s reference | `71499f5804f7dec8bb436da04bfddba06127c00d58a57b4e83667cca9bfba919` | `f9ca9f9fb1e0790d104b12ab78a27db947a9a914c48bb89ec9fa837c27ca7c00` |
| 单机 12 m/s fast | `4d6ed90ca81849ac83cfcd3dae92d79ec8e11d99f0079369fb137c7443d88c2e` | `1a8913b3b3a8a38665f198e79d421253780d30618b7d347804b316c6d690e873` |

## 验证入口与适用范围

[benchmark_beamdyn.py](../../scripts/experiments/benchmark_beamdyn.py) 为每个候选建立独立算例，记录参数、完整输入摘要、输出摘要、逐控制步墙钟和各叶片统计。实验参数写入新生成的 `DW_BeamDyn.dat`；官方模板不就地修改。生产入口候选直接调用配置选项，不手工补写 DT。实验参数修改后不用修改前的 `.physics.json` 声明身份，改用实际输入集合摘要；生产算例保留正式物理记录。

脚本会等待原生求解器退出再读 `.out`。超时或异常只终止该 worker 的进程组和已捕获子进程，不终止其他算例。正常完成后也检查所属子进程的清理结果。输出路径必须是 `/private/tmp` 下的新目录。候选失败会记为 `failed` 并继续扫描，命令正常返回不表示所有候选通过；采用设置须同时检查 `status=completed` 和对应 60 s 的 `accuracy.passed=true`。参数、线程数、统计窗口和对象摘要都应与结论一起保存。

从外层仓库根目录执行，使用指定 Python。两档风速的生产配置对照：

```bash
DW_TMP=$(mktemp -d /private/tmp/wfrl-beamdyn.XXXXXX)
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/blender_frontend:$PWD:$PWD/wfcrl-env"
export MPLCONFIGDIR="$DW_TMP/mpl" TMPDIR="$DW_TMP"
DW_PY=/opt/anaconda3/envs/wfrl-mac/bin/python
"$DW_PY" -m scripts.experiments.benchmark_beamdyn \
  --output "$DW_TMP/single" --seconds 60 --discard 30 --winds 8 12 \
  --variants production_reference production_fast
"$DW_PY" -m scripts.experiments.benchmark_beamdyn \
  --output "$DW_TMP/farm" --seconds 15 --discard 6 --winds 8 --turbines 3 \
  --variants production_reference production_fast reference_omp3 fast_omp3 fast_omp6
```

稳定性、空间离散及迭代参数候选名称见脚本 `CANDIDATES`。例如 `--variants reference dt0025 dt005 gauss5 gauss7 order3 order4 dt0021739` 使用完整 60 s 对照；`--seconds 12 --discard 6 --variants reference refine2 tol1e4 tol1e3 nr5 nr20 rho05 rho1 retry1 nfact1 nfact10 substep substep005 coarse_beam interp1 omp2 omp3 omp6 minimal` 对应短程筛选。`--settings` 可读取命名设置的 JSON 对象，`--reference-report` 可复用相同求解器、时长、统计窗口及机组数量的参考报告；复用时仍须核对输入来源和来流条件，不能仅按相同风速认定模型相同。

软件检查按 `PYTHONPATH=blender_frontend:.` 执行，143 项通过。范围包括默认算例完整字节、fidelity 严格校验、场景/CLI/训练身份、reference 步长保留、inspect 不写入、所有三片叶片的判据、完整旋转向量、原生进程完成约束、Driver 输入及已有遥测通道。

```bash
PYTHONPATH=blender_frontend:. "$DW_PY" -m pytest \
  tests/test_dw_beamdyn_performance.py tests/test_dt_blade_torsion.py \
  tests/test_dq_physics_options.py tests/test_solver_outputs.py \
  tests/test_dq_solver_completion.py tests/test_fastfarm_driver_inputs.py \
  -q -x -p no:cacheprovider --basetemp="$DW_TMP/tests"
```

四项布尔物理选项全开、使用 `fast`、12 m/s 单机的 12 s 组合算例正常完成，末半段平均功率 4857.025625 kW、塔顶前后位移 0.380813 m、B1 叶尖扭角 -0.271527°。这只证明该组合短程可运行，不替代两种配置在全开工况下的 60 s 精度或长时稳定性对照。其 physics identity 为 `e814e9512a55ae892ad5460cbe1ef68be119165e7117ecf7b890190ff7114f28`。

本功能未达到三机实时目标。长时湍流、变化偏航、充分发展尾流及其他机型/主机仍需独立验收；不为达到实时目标放宽本节门槛。严格恢复既有 BeamDyn checkpoint 的数值身份迁移需要单独决定，默认配置身份兼容不授权忽略积分条件差异。

## Reference 与 ElastoDyn 的固定输入对比


对象为 Apple M5 Pro、macOS 27.2.1 arm64、FAST.Farm/OpenFAST v3.5.3-dirty（GCC 12.3.0，单精度）、OMP_NUM_THREADS=1。求解器 SHA-256 为 `bed9651e19daf8ee6402100ff22b90867b756d96b72495ef9ee90df677a88d3f`。本节所有 BeamDyn 对象均为 `reference`（DT=0.00125 s，包含全开组合）；输入身份和原始数值独立保留，不与 fast 的计时合并。所有下列算例均到达要求的末时刻、正常终止，统计只在对应原生进程退出后读取。每组一次，墙钟是本机短算例观测，不代表跨平台性能保证。

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

三机 `turb3_row` BeamDyn 的控制交换远超过 3 s，因此此主机的实测配置不能实时交互，适合离线运行。三机的 60 s 窗口短于相邻机组按自由来流估算的 63 s 输运时间，不能据此评价充分发展的尾流。单机的两种风速下也不满足实时要求；本节计时只对应单线程 reference；fast 与线程对照见上文。GPU、其他主机及长时稳定性能尚未验证。

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

# OC3 海上单桩与波浪

## 配置与模型边界

海上求解通过独立的场景 `substructure` 字段显式开启。该字段包含带单位的海况及完整基础模型，不属于布尔型 `physics` 开关。省略或设为 `null` 使用原有陆上模板；关闭时生成目录与原生输出准备的逐字节合同由 `tests/test_du_offshore.py` 和既有 DQ 固定摘要共同检查。观测维数、控制器、奖励计算和既有训练结果不随此字段自动改写。

```yaml
backend: fastfarm
layout:
  - {id: T1, x: 0, y: 0}
  - {id: T2, x: 504, y: 0}
  - {id: T3, x: 1008, y: 0}
inflow: {speed: 8, direction: 270, turbulence: null}
physics:
  tower_flex: true
substructure:
  model: oc3_monopile
  hs_m: 2.0
  tp_s: 8.0
  direction_deg: 0.0
  seed: 123456789
  water_depth_m: 20.0
```

| 字段 | 合同 |
| --- | --- |
| `model` | 仅 `oc3_monopile`，不是漂浮式平台 |
| `hs_m` | 有效波高，有限数，0–6 m |
| `tp_s` | 谱峰周期，有限数，4–20 s |
| `direction_deg` | 波传播方向，有限数，(-180, 180]°；0°沿求解坐标 +X，90°沿 +Y，区别于风的气象来向 |
| `seed` | 有符号 32 位整数；拒绝布尔值和小数；所有机组共用此种子及 `RANLUX` |
| `water_depth_m` | 可省略，固定 20 m，其他值拒绝 |

数值范围是软件接口限制，不代表整个范围已通过波浪适用性或精度验收。未知字段、字符串数值、NaN/Inf、错误类型和不支持的组合均拒绝。波向定义依据 [HydroDyn v3.5.3 输入说明](https://openfast.readthedocs.io/en/v3.5.3/source/user/hydrodyn/input_files.html)。

`tower_flex=true` 是海上模式的必要条件：OC3 塔架从海平面上方 10 m 的过渡段接到塔顶，使用海上塔架模态，并开放六个平台自由度。显式关闭或省略该开关会在生成前报错。`dynamic_stall` 和 `tower_shadow` 可独立组合，采用现有开关含义；`TwrAero=False`，不会额外打开塔架气动阻力。`blade_torsion=true` 的海上组合尚未验证，生成前拒绝。海上场景目前只支持 `inflow.direction=270`；Driver 也拒绝其他风向的 reset，避免风场坐标旋转与波向定义混用。直接绕过场景和 Driver 修改低层接口不在此合同内。

通过现有训练或 Bridge 的 `--scene <场景.yaml>` 使用；Bridge `scene_overrides.substructure` 接受整组替换或 `null`。无场景的 Python 入口为 `FastFarmDriver(physics={"tower_flex": True}, substructure={...})`。低层 `create_ff_case()` 的参数字典接受同名字段；`configure_physics()`、`prepare_case()` 和 `SolverOutputs()` 接受关键字 `substructure`。对已生成海上算例调用这些接口时须提供其有效配置。改变模型时重新生成算例，不在求解中切换。

## 官方输入与结构切换

模型唯一来源是 [OpenFAST/r-test v3.5.3](https://github.com/OpenFAST/r-test/tree/v3.5.3/glue-codes/openfast/5MW_OC3Mnpl_DLL_WTurb_WavesIrr)，固定提交 `548bf2143633a7066f4582167fdf4180951fd7f6`。新模型目录为 [`wfcrl-env/wfcrl/simulators/fastfarm/inputs/oc3_monopile/`](../../wfcrl-env/wfcrl/simulators/fastfarm/inputs/oc3_monopile/)，保留 24 个官方输入文件和上游 LICENSE。逐文件 URL、下载字节 SHA-256、CRLF 存储字节 SHA-256 见 [provenance.json](../../wfcrl-env/wfcrl/simulators/fastfarm/inputs/oc3_monopile/provenance.json)。存储只归一化行尾，不重建或推测模型系数。

生成的每台机组有独立 `OC3/WTn/`，共用 `OC3/5MW_Baseline/` 中的官方叶片、翼型和气动几何。主 `.fst` 成套引用海上 ElastoDyn、塔架、HydroDyn、SubDyn 和 AeroDyn；开启 `CompHydro=1`、`CompSub=1`，保持 `CompMooring=0`。水深 20 m、水密度 1027 kg/m³、`MSL2SWL=0`、积分步长 0.005 s 均来自官方主输入。步长须整除 FAST.Farm 高低分辨率耦合间隔。

模型目录供生成器使用，其中官方主 `.fst` 用作参数来源，并不是可直接运行的完整 r-test 交付包。独立参考复现还需要上游的来流、ServoDyn 和控制器源文件。

ElastoDyn 的 `TowerBsHt=PtfmRefzt=10 m`，塔顶高 87.6 m；六个平台自由度用于描述柔性单桩顶部的过渡段运动，不代表泥线可自由漂移。SubDyn 按参考模型采用 Timoshenko 梁、`NDiv=3`、`CBMod=True`、`Nmodes=0` 的 Guyan 缩减；反力节点在 -20.0001 m，六自由度固定。其截面、材料、质量、阻尼及塔架模态均取自官方文件。没有土体弹塑性、冲刷、桩土接触或海床失效模型。

HydroDyn 保留参考模型的 `WaveMod=2`、默认 JONSWAP 峰形、`WaveDT=0.25 s`、`WaveTMax=3630 s`、长峰单向波、`WvDiffQTF=True`、`WvSumQTF=True`、`WaveStMod=0`、`CurrMod=0` 和 `PotMod=0`。仅替换四个海况标量；水动力表格和系数保留原始字节。总时长超过 3630 s 会拒绝，避免把重复波列当作新样本。

FAST.Farm 生成算例保留项目 ServoDyn/DISCON，校验实际 DLL 与项目库摘要一致，不修改任何 DISCON 源码或 `nrel5mw.yaml`。海上 AeroDyn 的几何来自官方模型，`AFAeroMod`、`TwrPotent`、`TwrShadow` 按场景 physics 开关设置，`TwrAero` 按项目合同关闭；因此该运行配置不等同于未改动的 r-test 气动/控制配置。

`Case.physics.json` 记录海况、每机实际模块与自由度、参考来源、相关输入及控制 DLL 的摘要和内容身份。场景序列化、训练有效配置、回合元数据和原生遥测 `physics_identity` 保留此身份。身份是输入配置证据，不单独证明求解已经执行。

## FAST.Farm 波浪场合同

[FAST.Farm v3.5.3 输入说明](https://openfast.readthedocs.io/en/v3.5.3/source/user/fast.farm/InputFiles.html) 定义每台机组独立的 OpenFAST 主输入，并提供两种波场处理：`Mod_WaveField=1` 直接使用各机 HydroDyn 输入，`Mod_WaveField=2` 按机位相对风场原点的偏移修正相位。

本配置固定使用 `Mod_WaveField=2`，各机共用海况、种子及频率离散，描述同一入射波场在不同位置的采样。各机局部波面一般不同；不能把它们称为互不相关的独立随机海况，也不能声称各机同相。若改成模式 1，相同种子和输入会在各机局部原点产生同相序列；本接口不提供该切换。

各机 HydroDyn/SubDyn 独立计算基础响应，当前条带理论 `PotMod=0` 配置不包含机组之间的水动力散射、辐射或共享基础耦合。三机 120 s 算例中 T1–T2、T1–T3、T2–T3 的波面零时差相关系数分别为 0.09824、0.04920、-0.01261，确认输出序列并非同一波面复制；这不是空间相位精度的专项验收。

## 输出与 Blender 主机合同

`.out` 保留官方全部六分量泥线反力、桩身指定截面的弯矩、HydroDyn 合力/力矩、波面，以及六个平台位移/转角；既有塔底六分量载荷输出仍保留。SubDyn 官方通道带负号，表示反力取负后的基础载荷；不得再次翻转符号。`M1N1...`、`M2N1...` 的编号是 SubDyn 输出列表索引，对应原始列表中的成员 2、3，不直接等同于结构 MemberID。

| 遥测字段 | 原生通道 | 协议单位 |
| --- | --- | --- |
| `mudline_shear_x/y` | `-ReactFXss`、`-ReactFYss` | N |
| `mudline_moment_x/y` | `-ReactMXss`、`-ReactMYss` | N m；原文件为 `N*m`，数值不乘 1000 |
| `platform_surge` | `PtfmSurge` | m，海平面上方 10 m 的平台参考点 |
| `platform_pitch` | `PtfmPitch` | deg，同一参考点 |
| `wave_elevation` | `Wave1Elev` | m，当前风机局部 (0,0) 波面采样点 |

这七个关键通道复用 `TTDspFA` 的原生输出遥测链路：在 MPI 耦合时钟边界消费完整文本行，经 Driver、Bridge、曲线和导出传递，不改变 DISCON 的 MPI 数组、训练观测或奖励定义。源时间、单位、坐标、符号和物理身份保留；缺失、旧样本、单位不符和非有限值保留无效状态，不补零。其他海上通道留在原生输出文件。

求解器的文件缓冲可能使部分耦合步暂时没有新读数。真实输出回传核对采用求解结束后的末帧；在线遥测时延尚未测量。

快照保留 `scene.substructure` 请求，并另带 `hydrodynamics` 元数据：未配置为 `not_computed`；只有场景请求为 `requested`；已核对求解输入但无完整有效海上读数为 `configured`；真实模式下各机七个通道均有效且身份匹配才为 `computed`。演示模式、过期样本或身份不匹配不宣称已计算。状态随当前读数变化，完整历史完成状态须另看运行终态。

Blender 可查询泥线两轴组图和单通道，保留同源曲线/导出。这里只验证主机合同，未做 Blender 原生窗口验收，未把平台位移驱动为三维动画，也未把视觉单桩几何当成 OC3 结构网格证明。

## 真实求解数值证据

### 官方单机参考

环境为 macOS arm64、OpenFAST-v3.5.3-dirty、求解器编译器 GCC 12.3.0、单精度、`OMP_NUM_THREADS=1`。单机复现采用官方 12 m/s TurbSim 来流和原海况 Hs=6 m、Tp=10 s，0–60 s、0.005 s 积分、0.05 s 输出。为复现官方参考，临时目录内使用 Homebrew GNU Fortran 16.2.0 编译了上游原样 DISCON；此库只用于参考复现，FAST.Farm 的海上配置使用项目 DISCON。主输入只改输出格式为文本与二进制并存及文本精度，模型参数不变。

执行命令中的 `RTEST_OPENFAST` 指向官方版本的 `glue-codes/openfast`，复现前复制到新的临时目录：

```bash
gfortran -cpp -DIMPLICIT_DLLEXPORT -ffree-line-length-none -shared -fPIC \
  "$RTEST_OPENFAST/5MW_Baseline/ServoData/DISCON/DISCON.F90" \
  -o "$RTEST_OPENFAST/5MW_Baseline/ServoData/DISCON.dll"
OMP_NUM_THREADS=1 /opt/anaconda3/envs/wfrl-mac/bin/openfast \
  "$RTEST_OPENFAST/5MW_OC3Mnpl_DLL_WTurb_WavesIrr/5MW_OC3Mnpl_DLL_WTurb_WavesIrr.fst"
```

进程退出码 0，并出现 `OpenFAST terminated normally.`；程序报告墙钟 34.48 s。使用 `openfast_toolbox.io.fast_output_file.FASTOutputFile(...).toDataFrame()` 读取两份 `.outb`；时间网格一致，共 1201 样本。均值和总体标准差采用全部 0–60 s 样本，相对差为 `(本机−参考)/abs(参考) × 100%`。

| 通道（原生单位） | 参考均值 ± 标准差 | 本机均值 ± 标准差 | 均值相对差 | 标准差相对差 |
| --- | --- | --- | --- | --- |
| `GenPwr_[kW]` | 4863.0756 ± 337.35245 | 4862.9657 ± 337.56291 | -0.002260% | +0.062386% |
| `-ReactMYss_[N*m]` | 67275474 ± 22309762 | 67273940 ± 22297777 | -0.002281% | -0.053720% |
| `-ReactFXss_[N]` | 638185.82 ± 780897.85 | 638189.59 ± 780834.91 | +0.000590% | -0.008060% |
| `PtfmSurge_[m]` | 0.026879484 ± 0.0081922585 | 0.02687888 ± 0.0081874025 | -0.002250% | -0.059275% |
| `PtfmPitch_[deg]` | 0.096015918 ± 0.028509448 | 0.096013705 ± 0.028492205 | -0.002305% | -0.060481% |
| `Wave1Elev_[m]` | 0.012957434 ± 1.5266197 | 0.013161433 ± 1.5266179 | +1.574375% | -0.000114% |
| `RootMyc1_[kN-m]` | 8068.3592 ± 1588.4313 | 8068.169 ± 1588.1205 | -0.002358% | -0.019562% |

波面均值的相对差为约 1.57%，绝对差约 0.204 mm；其均值接近零，不能仅凭该相对数判断波动误差。其标准差相对差约 -0.000114%。这些是参考复现的数值差异，没有把短算例复现升级为土壤、疲劳寿命或完整海况精度认证。

参考二进制来源及摘要：

- [官方 `.outb`](https://github.com/OpenFAST/r-test/blob/v3.5.3/glue-codes/openfast/5MW_OC3Mnpl_DLL_WTurb_WavesIrr/5MW_OC3Mnpl_DLL_WTurb_WavesIrr.outb)：`a06e2e539f9560b33c725c4b073ba33433b92d32515c227949e096caa862fd15`。
- 本机 `.outb`：`bc46ff8322cff37bc7e89f4fb0917e4626767ab3d769a3f4af82902998d1c884`。
- OpenFAST 可执行文件：`95a286bcff9326973da53523268bd4f90624d4489c0f852567197259e6fe753a`。

### FAST.Farm 单机与三机

六组运行均为 120 s，8 m/s 稳态风、模板剪切、控制步长 3 s、零偏航指令、单线程；三机位于 x=0、504、1008 m，y=0，即 `turb3_row`。海上组 Hs=2 m、Tp=8 s、波向 0°、种子 123456789。每组都确认正常结束标记及本次 FAST.Farm PID 退出后，才读取 `.out` 做统计；MPI 断连本身不作为写盘完成证据。输出末行均为 120.0000 s。

下表统计 60–120 s，0.25 s 输出，共 241 个样本，使用总体标准差；`offshore_aero` 同时开启动态失速和塔影。

| 布局/配置/机组 | 功率 MW（均值 ± 标准差） | 泥线 My MN m（均值 ± 标准差） | Surge m（均值 ± 标准差） | Pitch deg（均值 ± 标准差） |
| --- | --- | --- | --- | --- |
| 1 机 / offshore / T1 | 1.704506 ± 0.00249041 | 41.28257 ± 3.7934 | 0.01654808 ± 0.00083702 | 0.05919807 ± 0.00205117 |
| 1 机 / offshore_aero / T1 | 1.6134 ± 0.00422485 | 39.06916 ± 3.82985 | 0.01565894 ± 0.000864056 | 0.05601205 ± 0.00219097 |
| 3 机 / offshore / T1 | 1.704506 ± 0.00249041 | 41.28257 ± 3.7934 | 0.01654808 ± 0.00083702 | 0.05919807 ± 0.00205117 |
| 3 机 / offshore / T2 | 0.9443981 ± 0.544603 | 26.25337 ± 11.2977 | 0.01051612 ± 0.00436972 | 0.03756811 ± 0.0155302 |
| 3 机 / offshore / T3 | 0.9435722 ± 0.545015 | 26.39923 ± 11.5976 | 0.01054401 ± 0.0044779 | 0.03761887 ± 0.0158828 |
| 3 机 / offshore_aero / T1 | 1.6134 ± 0.00422485 | 39.06916 ± 3.82985 | 0.01565894 ± 0.000864056 | 0.05601205 ± 0.00219097 |
| 3 机 / offshore_aero / T2 | 0.9061398 ± 0.50705 | 25.37358 ± 10.5845 | 0.01016593 ± 0.00406403 | 0.03632355 ± 0.0144095 |
| 3 机 / offshore_aero / T3 | 0.9062657 ± 0.508515 | 25.52415 ± 11.002 | 0.01019558 ± 0.00421938 | 0.03638017 ± 0.0149244 |

| 布局/配置/机组 | 泥线 Fx kN（均值 ± 标准差） | 塔底 My kN m（均值 ± 标准差） | 波面 m（均值 ± 标准差） |
| --- | --- | --- | --- |
| 1 机 / offshore / T1 | 371.214 ± 284.067 | 30162.46 ± 672.141 | -0.008332693 ± 0.520283 |
| 3 机 / offshore / T1 | 371.214 ± 284.067 | 30162.46 ± 672.141 | -0.008332693 ± 0.520283 |
| 3 机 / offshore / T2 | 237.666 ± 289.001 | 19041.33 ± 7941.26 | -0.02446516 ± 0.517831 |
| 3 机 / offshore / T3 | 248.9614 ± 288.215 | 19031.97 ± 8092.05 | -0.002653673 ± 0.493944 |

| 布局 | 陆上 off 墙钟 | 海上墙钟 / off 比值 | 海上加动态失速与塔影墙钟 / off 比值 | 陆上功率均值（T1/T2/T3，MW） |
| --- | --- | --- | --- | --- |
| 1 | 16.056 s | 52.152 s / 3.248× | 52.850 s / 3.292× | 1.707002 |
| 3 | 46.734 s | 142.873 s / 3.057× | 149.405 s / 3.197× | 1.707002 / 0.929910 / 0.929615 |

墙钟从 MPI 启动计到原生求解进程退出，排除建例；同一主机同期有 DV 求解工作，以上是一次实际观测，不是隔离性能基准。海上组使用完整官方结构、叶片和初始状态；海上/陆上积分步长分别为 0.005/0.00625 s，初始转速分别为 12.1/9 rpm。不能把耗时比解释为仅开启波浪的成本。120 s 及其末半段仍含尾流建立和结构瞬态，不代表稳态功率或疲劳寿命。

组合组中求解器对常系数圆柱叶根节点提示关闭非定常气动，保留该原生行为，不声称这些节点发生动态失速。其余 lifting sections 使用请求的非定常模型。六组均无 fatal/severe 退出。

四个海上运行对象的真实末帧也通过原文件 → `SolverOutputs` → Bridge → Blender 主机协议解码的逐值核对；各机 7 个通道的单位、符号、120 s 时间戳和物理身份匹配，元数据为 `computed`。核对前后物理输入字节不变。

可复现的 FAST.Farm 命令（输出目录必须新建）为：

```bash
DU_RUN=$(mktemp -d /private/tmp/wfrl-du-XXXXXX)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/blender_frontend:$PWD/wfcrl-env:$PWD" \
MPLCONFIGDIR="$DU_RUN/mpl" TMPDIR="$DU_RUN" OMP_NUM_THREADS=1 \
/opt/anaconda3/envs/wfrl-mac/bin/python -m scripts.experiments.compare_fastfarm_offshore \
  --seconds 120 --turbines 1 3 --variants off offshore offshore_aero \
  --fastfarm /opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm --output "$DU_RUN/results"
```

临时目录保存输入、日志、完整原生输出和 `comparison.json`。当前文档保留数值、输入身份和输出摘要；执行产生的完整临时数据不作为仓库资产保留。

FAST.Farm 可执行文件 SHA-256：`bed9651e19daf8ee6402100ff22b90867b756d96b72495ef9ee90df677a88d3f`。

| 运行对象 | 物理输入身份 |
| --- | --- |
| 1 机 / off | `0d4e5302eed2aefc7a76cdd2e401efa3dd91b660691314993eb867915882480a` |
| 1 机 / offshore | `33b0e77c1cdce50716db4529038cb59c88d0251fff6414592cfc35dc54365b79` |
| 1 机 / offshore_aero | `c8aaead4418daf8f85286c96b8879f27cac30798b85672f909c87eddc5e69297` |
| 3 机 / off | `23f3f59dd8be235a52773ff25b321ae4a06ee6a367865c4d54d96dea37fc27b2` |
| 3 机 / offshore | `64efc060dd6c79a94aadcadb92d07dffe3cd6edae1a2b7a3e5d1f835fd9aa875` |
| 3 机 / offshore_aero | `83afe71467050e3e510b312878bec481ea6821e64dfa71b01c12c3b2c25ccc1b` |

| 运行对象/机组 | 完整原生 `.out` SHA-256 |
| --- | --- |
| 1 机 / off / T1 | `a0bd1472b03c96db68d1e6f76c2c41d2b08f785ad02b988b2a9bdd80a4b9c98f` |
| 1 机 / offshore / T1 | `057f453a99d7481a133db3dd65c34030ba926f9d0a8a7838fea754c5ae044ba3` |
| 1 机 / offshore_aero / T1 | `7155c5dcf8bfaae5d771ea112fb7afd87dc529767e56ace069147c5ad790593a` |
| 3 机 / off / T1 | `52bdba331bdf4962df3fb908a3dfe4ce9431f28ddd116038bd0366db2c64753c` |
| 3 机 / off / T2 | `1516ab8557dd369044f14bbee0fd7ed6e01044dd8e5ecf6ee0a08b53236224c9` |
| 3 机 / off / T3 | `24900999e9124046552c801ef548c93a135cfca8192e5b04bdac177a0cdc716e` |
| 3 机 / offshore / T1 | `54c81dcb866120e8441b7be1f6803de3cff15bb1291b4f8b335e1892efd4b911` |
| 3 机 / offshore / T2 | `71902b76666fdf61a702e921af85c929229e43549bdf0e4ce073d744c108e2f4` |
| 3 机 / offshore / T3 | `9165db5831a74385f052e9139cdc4dbd093fa8e1b83ac2be7ea0762e20ad76ef` |
| 3 机 / offshore_aero / T1 | `91b6ef0c17ea7f13369228ea93e3b29fe6042e5bb6c0b2e4b2e3deca610b02cf` |
| 3 机 / offshore_aero / T2 | `667ade95408b964c7651a2dd36d70cd01fd0737b9ed396a8badbfdc2782bbca6` |
| 3 机 / offshore_aero / T3 | `c9868af956e1eaa7d32477ac49e7150236d52b6e107f17b2b0ff17975ec4827a` |

## 软件验证入口

主机测试覆盖默认字节合同、参数范围与拒绝路径、官方来源、三机独立输入、海上完整结构、海况/训练身份、控制 DLL 保持、无副作用检查、协议状态、时间/单位/符号以及曲线/导出。与代码直接相关的完整命令为：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/blender_frontend:$PWD/wfcrl-env:$PWD" \
MPLCONFIGDIR="$DU_RUN/mpl" TMPDIR="$DU_RUN" \
/opt/anaconda3/envs/wfrl-mac/bin/python -m pytest \
  tests/test_du_offshore.py tests/test_dq_physics_options.py tests/test_dt_blade_torsion.py \
  tests/test_solver_outputs.py tests/test_fastfarm_driver_inputs.py \
  tests/test_fastfarm_driver_exceptions.py tests/test_fastfarm_driver_bn_contract.py \
  tests/test_cn_scene_validation.py tests/test_cn_scene_runtime_lifecycle.py \
  blender_frontend/tests/test_du_offshore_telemetry.py \
  blender_frontend/tests/test_dq_tower_telemetry.py blender_frontend/tests/test_dt_torsion_telemetry.py \
  blender_frontend/tests/test_solver_telemetry.py blender_frontend/tests/test_live_telemetry_panel.py \
  blender_frontend/tests/test_workflow_options.py blender_frontend/tests/test_chart_snapshot_contract.py \
  blender_frontend/tests/test_farm_telemetry_arrays.py \
  -q -x -p no:cacheprovider --ignore=blender_frontend/tests/blender --basetemp="$DU_RUN/pytest"
```

该命令在本机通过 336 项测试。验证只覆盖所列源码与主机合同、明确列出的本机求解对象。未验证 Windows/Linux 原生求解、全海况空间、长时疲劳、桩土相互作用、BeamDyn 海上组合、转向来流组合或 Blender 原生窗口。

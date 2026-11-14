# TurbSim 湍流盒生成与选择

[`generate_turbsim.py`](../../scripts/inflow/generate_turbsim.py) 使用安装的 TurbSim 和包内 `FarmInputs/90m_08mps.inp` 模板，按风速、TI 或 IEC 类别、随机种子的笛卡尔积生成 BTS。生成不会修改模板、控制器、训练调度或机型配置；只有显式选择文件时才改变来流。

## 生成与存储

从仓库根目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/wfrl-mac/bin/python \
  scripts/inflow/generate_turbsim.py \
  --wind-speeds 8 12 --ti 8 16 --seeds 42 \
  --duration 300 --simulation-seconds 60 --scale-iec 2 --threads 2
```

`--ti` 的单位是百分数：`8` 表示 8%，不是 `0.08`。用 `--iec-class A B C` 替代 `--ti` 可选择 IEC 61400-1 Ed.3 NTM 类别；两者互斥。`--seeds` 设置有符号 32 位 `RandSeed1`；`RandSeed2` 固定为模板的 `-75384093`。同一组种子可用于不同强度的配对对照。

默认库目录为仓库 `data/turbulence/`，在外层 `.gitignore` 中忽略。`WFRL_TURBULENCE_DIR` 可指定另一目录，`--output-dir` 优先于该环境变量。BTS、对应 `.inp`、TurbSim 原始 `.sum` 和 `manifest.json` 是保留的运行输入与来源资料，不提交 Git。分发到服务器时应将它们一起复制，并设置相同的库目录；不依赖机器上其他环境的风文件。

临时计算使用 `/private/tmp` 下的独立目录，可由 `--scratch-root` 指定；正常完成、生成错误及超时会清理临时目录。每个成功盒子发布后原子替换清单；后续组合失败时，已发布的盒子仍有效。生成器拒绝覆盖已有组合或同名文件；改变时长、网格或缩放方法时使用独立库目录。`.generation.lock` 防止两个生成器同时写同一库；进程被强制终止后，须先确认该库没有活动生成任务再处理遗留锁。

文件名例：`ts_u8_ti16_s42.bts`、`ts_u12_iecA_s42.bts`。小数点编码为 `p`，如 `ts_u8p5_ti8_s42.bts`。种子可以为负数。名称中的 TI/类别表示请求条件，实际统计以清单为准。

## 网格、时长与物理口径

脚本面向 NREL 5MW 和 [`turb3_row`](../../scenes/turb3_row.yaml)：X 为 0、504、1008 m，Y 均为 0。默认网格为 69×35，横向范围 −340～340 m，竖向范围 10～350 m，间距 10 m，时间步 0.1 s。物理轮毂高度为 90 m，恰好位于网格层上；`RefHt=90`，幂律指数为 0.2。

TurbSim 的 `HubHt=180` 用于扩展网格，不表示风机轮毂升高。其 IEC 标准差和传播速度使用该高度的平均风速。对于数值 TI，脚本用 `IECturbc = TI90 × U90/U180` 保持目标纵向标准差对应 90 m。IEC 类别先按 `σu=Iref×(0.75×U90+5.6)` 换算成 90 m 目标 TI，再使用同样的修正；`Iref` 对 A/B/C 分别为 0.16/0.14/0.12。实际传入 TurbSim 的 `IECturbc` 是数值，类别和目标值另外保存在清单中。这是类别导出的 NTM 目标，不是完整 IEC 载荷认证。

`--scale-iec` 默认 1，与基础模板一致：仅在人工中心高度统一缩放，90 m 的单点及横向平均 TI 会随种子和时长变化。显式设为 2 时，各网格点独立缩放标准差，适合要求 90 m 目标 TI 接近输入值的集合；该方式会改变空间相关性。0 不附加缩放。脚本不事后修改 BTS 的风速样本，不把目标值写成实测值。人工中心高度也影响相干模型和传播速度，标准差修正不能消除这些差异。[TurbSim 手册](https://openfast.readthedocs.io/en/main/_downloads/cb14d3e2d3533d76e405d730fea19846/TurbSim_v2.00.pdf) 与 [FAST.Farm 建模指导](https://openfast.readthedocs.io/en/main/source/user/fast.farm/ModelGuidance.html) 说明这些参数的含义与限制。

`UsableTime="ALL"` 生成周期文件，默认分析时长 300 s。生成前按 `fastFarmBoxExtent` 预检，生成后按 `create_ff_case` 同参数的 `fastFarmTurbSimExtent` 核对实际网格：`D=126`、ElastoDyn `TowerHt=87.6`、`chord_max=5`、高分辨率范围系数 1.2、`Cmeander=1.9`。检查高分辨率域落在低分辨率域内，且低分辨率横向和竖向域未越出 BTS。时长保守要求为 `--simulation-seconds` 加完整低分辨率 X 域的平流时间，平流速度取 BTS 头中的人工中心速度。300 s 的集合按默认 60 s 预算检查，不自动保证长训练回合无周期重复。

`--grid-spacing` 必须整除 680、340、80 m，确保完整网格和 90 m 层；风速较低时可能需要更细网格。过粗空间/时间离散、时长不足或网格超出脚本资源上限会在调用 TurbSim 前拒绝。覆盖检查不证明载荷收敛，也不改变算例的控制步长；其他布局、海上位移或机型需要单独核对。

## Manifest 与统计

`schema` 为 `wfrl-turbulence-library.v1`，`boxes` 中每项包括：

| 字段 | 含义 |
| --- | --- |
| `name`、`file` | 稳定选择名称与相对 BTS 文件名 |
| `wind_speed_mps`、`ti`、`seed` | 请求风速、TI 百分数或类别、RandSeed1 |
| `target_hub_ti_percent` | 90 m 的目标 TI；与实测值分开 |
| `input_parameters` | 显式写入模板的生成参数，包括两种高度、随机种子、IEC 缩放、网格、时长与相干参数 |
| `input_file`、`summary_file`、`template_file` 及其 SHA-256 | 完整生成输入、原始摘要与基础模板身份 |
| `sha256`、`size_bytes` | BTS 内容身份 |
| `turbsim_version`、`turbsim_executable_sha256` | 实际可执行文件的版本与内容身份；带 `dirty` 的版本字符串原样保留 |
| `statistics` | 90 m 完整文件时间窗内的纵向风速统计 |
| `coverage` | 实际 FAST.Farm 域、布局、传播速度和时长预算 |

统计先在每个时刻沿高度插值，再逐横向点计算 `mean(u)`、总体标准差 `std(u, ddof=0)` 和 `100×std/mean`。`mean_speed_mps` 为各点均值的平均；`mean_ti_percent` 为各点 TI 的平均；还保存中心线值、逐点值、TI 范围、网格范围、时间跨度和周期标志。不能用混合空间差异的方差代替时间 TI。

[`bts_info()`](../../wfrl/inflow.py) 保留既有训练诊断口径：均值按轮毂高度插值，TI 使用最近高度层的时空标准差。批量生成清单使用独立的 `bts_statistics()`，不改变已有训练诊断或奖励归一化。

## 选择与场景

```python
from wfrl.inflow import resolve_bts
from wfrl.windcond import resolve_turbulence

path = resolve_bts("ts_u8_ti8_s42")
assert path == resolve_bts("ts_u8_ti8_s42.bts")
assert path == resolve_bts((8, 8, 42))
assert path == resolve_bts("bts:8,8,42")
assert path == resolve_turbulence(wind_speed=8, ti=8, seed=42)
```

`library_dir=` 可显式指定库目录。未知名称或组合报错并列出可选项；库选择校验文件大小和 SHA-256，损坏清单不会当作空库。既有模板文件名和显式绝对路径保持可用，不要求它们属于库。

场景字段仍为字符串 `inflow.turbulence`。`create_ff_case` 可以解析库名称和 `bts:风速,TI,种子`，只将选中的 BTS 复制到算例 `FarmInputs/`，并写入相对文件名，使算例可以整体移动。`turbulence: null` 保持模板 BTS 复制行为和算例字节；原模板文件名 `90m_08mps.bts` 仍可直接使用。

`wfcrl/interface.py` 的启动前选择文件哈希核对调用同一个 `resolve_bts()`。YAML 可直接写库名称、`bts:风速,TI,种子`、既有模板文件名或绝对路径，经 `SceneRuntime → FastFarmDriver → MPI` 启动。`requested.wind_time_series` 保留原始选择字符串，`selected_wind_file` 记录解析后的实际路径与身份，并与算例复制件的 SHA-256 比较。文件缺失、清单损坏或内容变化时在 MPI 启动前拒绝；稳态模式不调用 BTS 解析器，存在的旧模板文件不依赖库清单。

```yaml
inflow:
  speed: 8.0
  direction: 270.0
  turbulence: ts_u8_ti8_s42
```

Python 调用方也可显式取得绝对路径：

```python
from wfrl.scene import load_scene, dump_scene
from wfrl.windcond import resolve_turbulence

scene = load_scene("scenes/turb3_row.yaml")
scene.turbulence = resolve_turbulence(wind_speed=8, ti=8, seed=42)
dump_scene(scene, "/private/tmp/turb3-library.yaml")
```

TI 和种子只用于选择已生成的盒子，不作为 `reset()` 的新物理参数；BTS 场景的标量风速由文件决定。网页的库列表选择控件不在这些后端接口中实现。

## 验证入口

软件合同测试在 `tests/test_turbulence_library.py`、`tests/test_turbsim_generation.py`、`tests/test_turbulence_cases.py` 和 `tests/test_turbulence_smoke.py`。算例测试用实际模板分别执行模板选择/复制合同与当前生成器，比较整个目录中文件路径及 SHA-256；覆盖稳态默认、原 BTS 默认和稳态多模板文件的字节一致性。启动检查覆盖原始选择保留、坏清单下旧模板兼容、稳态不查库、文件替换拒绝以及检查前后算例字节不变。

单机真实读取检查使用 [`smoke_turbulence.py`](../../scripts/inflow/smoke_turbulence.py)：

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/wfrl-mac/bin/python \
  scripts/inflow/smoke_turbulence.py --box ts_u8_ti8_s42 --seconds 30

PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/wfrl-mac/bin/python \
  scripts/inflow/smoke_turbulence.py --box ts_u12_ti16_s42 \
  --layout turb3_row --seconds 60 --timeout 180
```

脚本自动查找 Python 环境内或 PATH 中的 `mpiexec`，也可用 `--mpiexec` 指定匹配的启动器。工作进程由 `mpiexec -n 1` 启动，保留 YAML 中的原始选择字符串，调用真实 `SceneRuntime` 和 `FastFarmDriver`。只有烟测场景将预热设为零，使总求解时长等于 `--seconds`；普通场景和训练的预热设置保持不变。

输出包含请求字符串、实际加载哈希、MPI 交换数、各机组 Driver/MPI 功率与轮毂风速统计，以及原生输出完整末时刻。Driver 重置帧可能没有功率值，统计显式报告缺失数量；MPI 原始功率单独统计，不将缺失值填零。完整文件的背景纵向 TI 与短窗机组轮毂读数的测点、分量和时间窗不同，不能要求二者数值相等。

最终 `.out` 汇总前，按本算例唯一输入路径识别 FAST.Farm 进程，等待其退出并核对正常结束标记；单独收到 MPI 断开或日志中的 Finalize 不代表文件已写完。还要求 `.out` 没有未终止行且末时刻等于 TMax。默认 `--timeout 180` 限制整个 MPI 作业时长；父进程仅跟踪和清理自己启动的进程树，不按全局进程名终止其他任务。算例、记录和 Matplotlib 缓存均位于父进程管理的临时目录中。真实结果须以成功返回的 `status=completed` 为准，短算例含启动瞬态，不作为稳态功率、疲劳载荷或训练策略验收。

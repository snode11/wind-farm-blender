# FAST.Farm 塔架与气动物理选项

场景、`FastFarmDriver`、训练命令和 Blender 启动器支持三个独立、默认关闭的布尔选项。选项在生成算例、启动求解器之前应用；运行中不切换物理模型。只修改生成目录中的输入，不修改模板、控制器或已有训练结果。

| `physics` 字段 | 开启后的输入 | 保持的限制 |
| --- | --- | --- |
| `tower_flex` | ElastoDyn 的 `TwFADOF1`、`TwFADOF2`、`TwSSDOF1`、`TwSSDOF2` 均为 `True` | 要求 `CompElast=1`，保留模板塔架结构与模态系数 |
| `dynamic_stall` | AeroDyn `AFAeroMod=2` | 要求模板 `UAMod=3`、`FLookup=True`、`CavitCheck=False`；不生成或修改 UA 系数 |
| `tower_shadow` | `TwrPotent=1`、`TwrShadow=1` | 标准势流与 Powles 塔影一起开启；`TwrAero` 保持模板值 `False`，不额外开启塔架气动阻力载荷 |

三项可以分别开启，也可以组合。这里只支持当前 NREL 5MW 的 ElastoDyn / AeroDyn v15 输入。`UAMod=3` 对应 Beddoes–Leishman 的 Minnema/Pierce 扩展；势流扰动与下游塔影可叠加。[OpenFAST AeroDyn 输入说明](https://openfast.readthedocs.io/en/v3.5.3/source/user/aerodyn/input.html)

## 场景与启动

在有效场景 YAML 中加入：

```yaml
physics:
  tower_flex: true
  dynamic_stall: false
  tower_shadow: true
```

只接受上述键和 YAML 布尔值。未知键、`null`、字符串 `"true"`、数字 `0/1` 都拒绝；FLORIS 场景拒绝开启这些选项。省略字段代表关闭。全关闭的场景序列化省略 `physics`，保留默认训练配置身份的兼容性。

以下入口接受 `--tower-flex`、`--dynamic-stall`、`--tower-shadow`，以及相应的 `--no-*`。未提供的命令行选项保留场景值；显式选项覆盖对应字段，不影响其余字段。

- `python -m scripts.train.train_fastfarm --scene <场景.yaml> --tower-flex --dynamic-stall`
- `python -m wfrl.blender_bridge --scene <场景.yaml> --tower-flex --tower-shadow`
- `python scripts/blender/wfrl_launcher.py launch --scene <场景.yaml> --tower-flex --dynamic-stall`，其 Python、MPI、FAST.Farm 与 Blender 路径仍按启动器要求提供。

Bridge 的物理覆盖参数要求真实后端和显式 `--scene`。训练无场景时可通过相同参数配置预注册 FAST.Farm 布局。Python 接口：

```python
driver = FastFarmDriver(physics={"tower_flex": True, "dynamic_stall": True})
```

Bridge 的场景重载保留启动参数；正式训练使用带有效选项的临时场景。`scene.load`／运行请求也可通过 `scene_overrides.physics` 按字段覆盖，走同一校验；请求内的显式覆盖优先于启动时配置。

低层 `create_ff_case(case, output_dir=...)` 接受 `case["physics"]`。`Scene.make_case()` 会把有效选项传给该接口；`SceneRuntime` 和 `FastFarmDriver` 在启动前准备原生遥测输出。单独调用低层建例接口不启动遥测读数。

## 输入检查与追溯

`wfcrl.fastfarm_physics.configure_physics()` 在写入任何物理参数前检查所有机组。开启动态失速时检查引用翼型的单表结构、`InclUAdata`、32 项 UA 参数及有限且按攻角递增的系数表；缺失或无效数据直接报错。保留模板声明的合法 `DEFAULT`，不补系数。当前模板的 8 个翼型文件均带 UA 参数；其中两个圆柱叶根截面的 `C_nalpha=0` 且升力系数为零，应作为非升力截面解释，不声称这些截面产生动态失速响应。

塔影检查节点数量、有限值、高度严格递增、正直径／阻力系数以及塔基至塔顶的覆盖。当前模板表为 12 个节点，范围 0–87.6 m。塔架柔性检查四组模态形状系数存在、有限且非全零。这些输入检查不能代替求解器稳定性或物理精度验证。

启用任意选项的生成算例带 `FarmInputs/Case.physics.json`，包含：

- `requested`：三个归一化布尔请求；`turbines[].actual`：每台机组实际 DOF、AeroDyn 开关及 `TwrAero`。
- `turbines[].checks`：所启用功能检查过的翼型或塔架节点信息。
- `inputs_sha256`：实际读取的主输入、ElastoDyn、AeroDyn 及所启用选项的依赖输入摘要。范围按清单列出，不代表完整交付包摘要。
- `identity`：上述相对路径、请求、实际设置和摘要的内容身份，不包含时间或输出目录绝对路径。

Driver 的 `physics_record` 和测量 `physics` 保留此记录；每回合的持久 `wind_conditions` 也含 `physics`，包括全关闭时的实际参数。原生遥测 `provenance.physics_identity` 关联该身份。训练有效配置和 checkpoint 身份包含已启用选项，配置不匹配的恢复不能当作同一训练条件。低层算例附属记录应在任何手工修改输入之后重新生成，不能用旧摘要声明修改后输入的身份。

全关闭时不改物理输入、不增加算例附属文件，也不增加塔顶输出列。默认算例和默认遥测准备的字节合同由 `tests/test_dq_physics_options.py` 及 `tests/fixtures/dq/default_case_sha256.json` 检查；它同时比较省略选项与显式全关闭时的完整生成目录。模板、控制律、观测维数和奖励公式不随选项自动改变。

## 塔顶位移遥测

`tower_flex` 开启时，在既有塔底载荷输出之外追加：

| 遥测字段 | 原生通道 | 单位 |
| --- | --- | --- |
| `tower_top_fa` | `TTDspFA` | m |
| `tower_top_ss` | `TTDspSS` | m |

读数由 `SolverOutputs` 读取当前 OpenFAST 原生文本输出，在 MPI 耦合时钟边界消费，再经 Driver、Bridge、曲线与导出传递；没有改变 DISCON 的 MPI 数组。保留源时刻、坐标系、有效性和物理身份。缺测与超时不补零；有效零位移与缺测不同。LIVE TELEMETRY 可搜索单通道或选择 `Tower top displacement (fore-aft / side-to-side)` 两轴组图。

## 短算例对比

`scripts/experiments/compare_fastfarm_physics.py` 创建关闭、仅塔架柔性、仅动态失速、仅塔影、三项全开共五组；使用相同的 8 m/s 稳态来流、模板剪切、初始状态、零偏航指令和单线程设置。对比脚本显式为所有组请求塔顶输出，便于比较关闭时的位移；这不改变日常默认输出列表。

在允许 MPI 本地端口与进程生成的 POSIX 环境运行：

```bash
cd '/Users/eason/Desktop/wfcrl/wind farm RL'
DQ_RUN=$(mktemp -d /private/tmp/wfrl-dq-compare-XXXXXX)
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/wfcrl-env:$PWD" \
MPLCONFIGDIR="$DQ_RUN/mpl" TMPDIR="$DQ_RUN" OMP_NUM_THREADS=1 \
/opt/anaconda3/envs/wfrl-mac/bin/python -m scripts.experiments.compare_fastfarm_physics \
  --seconds 45 --turbines 1 --repeats 1 --timeout 600 \
  --fastfarm /opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm \
  --output "$DQ_RUN/results"
```

`--seconds` 支持 30、45、60；`--turbines` 支持 1、3；`--variants off all` 可只比较关闭与全开，所选组必须含 `off` 且不能重复；`--prepare-only` 只生成并检查输入，不初始化 MPI。每组子进程有超时和进程组清理。每组原始输出、日志和元数据都在指定目录；提取所需证据后清理该临时目录。

`comparison.json` 记录求解器摘要、MPI spawn 至确认原生正常终止的墙钟时间（`wall_seconds`，不含建例）、MPI 交换结束时间（`mpi_wall_seconds`）、等待原生收尾的时间（`completion_wait_seconds`）及相对关闭组的耗时变化。MPI 断连不代表输出已经写完；只有日志出现 `FAST.Farm terminated normally.` 后才读取输出。当前 45 s 算例的输出间隔为 0.25 s，末行应为 45.0000 s；末行 44.5 或 44.75 s 均拒绝，终止时刻误差只容许时间列四位小数的打印精度。未换行的残缺行和超出终止时刻的数据也拒绝。

统计包含每台机组末半段仿真窗口的功率、B1 叶根两向弯矩、塔底两向弯矩、塔顶两向位移的均值、总体标准差、极值、绝对峰值和相对关闭组的差值。功率单位 kW，弯矩 kN·m，位移 m。短窗口包含启动瞬态，不能据此认定稳态性能、疲劳寿命或物理精度；单次墙钟结果也不能当作稳定性能基准。缺失通道、无效单位、非有限数或未到达终止时刻的输出会拒绝汇总。

## 在线 MPI 遥测验证

`scripts/experiments/smoke_physics_telemetry.py` 使用真实 `FastFarmDriver`，单机 8 m/s、开启 `tower_flex`，将每个在线快照经 `SnapshotAdapter` 和协议编解码传递。检查两轴都有有效非零值、单位为 m、物理身份匹配，并在求解器正常结束后逐值核对原生输出；不启动 Blender 窗口或网络 Bridge。Driver 保留默认预热时间，`--steps 12` 对应 138 s 原生算例和 12 个遥测快照。

```bash
cd '/Users/eason/Desktop/wfcrl/wind farm RL'
DQ_SMOKE=$(mktemp -d /private/tmp/wfrl-dq-telemetry-XXXXXX)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/wfcrl-env:$PWD" \
MPLCONFIGDIR="$DQ_SMOKE/mpl" TMPDIR="$DQ_SMOKE" OMP_NUM_THREADS=1 \
OMPI_MCA_btl=self,tcp OMPI_MCA_btl_tcp_if_include=lo0 \
WFCRL_FASTFARM_EXECUTABLE=/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm \
/opt/homebrew/bin/mpiexec -n 1 /opt/anaconda3/envs/wfrl-mac/bin/python \
  -u -m scripts.experiments.smoke_physics_telemetry --steps 12 \
  --output "$DQ_SMOKE/results" --solver-log "$DQ_SMOKE/solver.log" \
  > "$DQ_SMOKE/solver.log" 2>&1
```

`--solver-log` 必须与该 MPI 作业的标准输出／错误重定向文件相同。`telemetry.json` 保存在线快照、核对统计与 Driver 清理状态；日志中的 `DQ_PHYSICS_TELEMETRY_PASS` 表示所有断言通过。核验后删除 `$DQ_SMOKE`。上述 Open MPI 的 `lo0` 设置适用于本机 macOS；其他平台按其网络接口和 MPI 安装配置。

## 主机短算例证据

验证对象为 macOS arm64 上的 FAST.Farm v3.5.3-dirty、GCC 12.3.0、单精度、OpenMP 单线程。求解器 SHA-256 为 `bed9651e19daf8ee6402100ff22b90867b756d96b72495ef9ee90df677a88d3f`。以下七组均正常结束，所有机组输出末行均为 45.0000 s。输入为 8 m/s、零偏航、45 s；三机坐标为 x=0/504/1008 m、y=0。统计窗口 22.5–45 s，间隔 0.25 s，每通道 91 点。每组仅运行一次；均值±标准差中的标准差为总体标准差。

| 机组数 | 选项 | 墙钟 / s | 相对 off | 原生收尾等待 / s |
| --- | --- | ---: | ---: | ---: |
| 1 | off | 6.905 | +0.00% | 0.060 |
| 1 | tower_flex | 6.275 | -9.12% | 0.060 |
| 1 | dynamic_stall | 6.580 | -4.70% | 0.060 |
| 1 | tower_shadow | 6.457 | -6.49% | 0.060 |
| 1 | all | 7.039 | +1.94% | 0.057 |
| 3 | off | 16.697 | +0.00% | 0.150 |
| 3 | all | 17.768 | +6.42% | 0.179 |

单机各通道均值±标准差：

| 通道 | off | tower_flex | dynamic_stall | tower_shadow | all |
| --- | ---: | ---: | ---: | ---: | ---: |
| 功率 / kW | 1706.054 ± 0.841 | 1704.268 ± 1.160 | 1617.439 ± 0.211 | 1703.871 ± 0.978 | 1614.364 ± 1.915 |
| B1 叶根 My / kN·m | 5532.366 ± 315.828 | 5542.891 ± 311.810 | 5318.700 ± 310.540 | 5526.347 ± 335.336 | 5325.003 ± 327.622 |
| B1 叶根 Mx / kN·m | 458.729 ± 2526.174 | 465.370 ± 2522.465 | 539.348 ± 2520.198 | 461.161 ± 2525.666 | 544.784 ± 2527.364 |
| 塔底 My / kN·m | 32717.509 ± 55.306 | 33626.774 ± 625.247 | 30961.317 ± 128.825 | 32694.445 ± 142.855 | 31801.542 ± 1096.788 |
| 塔底 Mx / kN·m | 2259.176 ± 209.350 | 2398.470 ± 634.956 | 2185.949 ± 745.747 | 2273.993 ± 247.408 | 2359.403 ± 810.560 |
| 塔顶前后 / m | 0.000000 ± 0.000000 | 0.203219 ± 0.003643 | 0.000000 ± 0.000000 | 0.000000 ± 0.000000 | 0.192028 ± 0.006407 |
| 塔顶侧向 / m | 0.000000 ± 0.000000 | -0.024775 ± 0.003906 | 0.000000 ± 0.000000 | 0.000000 ± 0.000000 | -0.024183 ± 0.004964 |

单机均值相对 off 的差值（单位与上表相同）：

| 通道 | tower_flex | dynamic_stall | tower_shadow | all |
| --- | ---: | ---: | ---: | ---: |
| 功率 / kW | -1.786 | -88.615 | -2.183 | -91.690 |
| B1 叶根 My / kN·m | +10.524 | -213.667 | -6.020 | -207.363 |
| B1 叶根 Mx / kN·m | +6.641 | +80.619 | +2.432 | +86.055 |
| 塔底 My / kN·m | +909.264 | -1756.193 | -23.065 | -915.968 |
| 塔底 Mx / kN·m | +139.295 | -73.226 | +14.818 | +100.228 |
| 塔顶前后 / m | +0.203219 | +0.000000 | +0.000000 | +0.192028 |
| 塔顶侧向 / m | -0.024775 | +0.000000 | +0.000000 | -0.024183 |

三机每台机组均值±标准差：

| 通道 | off T1 | off T2 | off T3 | all T1 | all T2 | all T3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 功率 / kW | 1706.054 ± 0.841 | 1706.054 ± 0.841 | 1706.054 ± 0.841 | 1614.364 ± 1.915 | 1614.365 ± 1.915 | 1614.365 ± 1.915 |
| B1 叶根 My / kN·m | 5532.366 ± 315.828 | 5532.369 ± 315.830 | 5532.367 ± 315.831 | 5325.003 ± 327.622 | 5325.009 ± 327.626 | 5325.008 ± 327.625 |
| B1 叶根 Mx / kN·m | 458.729 ± 2526.174 | 458.730 ± 2526.172 | 458.729 ± 2526.172 | 544.784 ± 2527.364 | 544.785 ± 2527.362 | 544.784 ± 2527.363 |
| 塔底 My / kN·m | 32717.509 ± 55.306 | 32717.484 ± 55.375 | 32717.563 ± 55.297 | 31801.542 ± 1096.788 | 31801.555 ± 1096.682 | 31801.553 ± 1096.693 |
| 塔底 Mx / kN·m | 2259.176 ± 209.350 | 2259.168 ± 209.362 | 2259.166 ± 209.355 | 2359.403 ± 810.560 | 2359.410 ± 810.579 | 2359.407 ± 810.588 |
| 塔顶前后 / m | 0.000000 ± 0.000000 | 0.000000 ± 0.000000 | 0.000000 ± 0.000000 | 0.192028 ± 0.006407 | 0.192028 ± 0.006407 | 0.192028 ± 0.006407 |
| 塔顶侧向 / m | 0.000000 ± 0.000000 | 0.000000 ± 0.000000 | 0.000000 ± 0.000000 | -0.024183 ± 0.004964 | -0.024183 ± 0.004964 | -0.024183 ± 0.004964 |

三机 all 各机组均值相对本机 off 的差值（单位同上）：

| 通道 | T1 | T2 | T3 |
| --- | ---: | ---: | ---: |
| 功率 / kW | -91.690 | -91.689 | -91.689 |
| B1 叶根 My / kN·m | -207.363 | -207.360 | -207.358 |
| B1 叶根 Mx / kN·m | +86.055 | +86.055 | +86.056 |
| 塔底 My / kN·m | -915.968 | -915.929 | -916.011 |
| 塔底 Mx / kN·m | +100.228 | +100.242 | +100.241 |
| 塔顶前后 / m | +0.192028 | +0.192028 | +0.192028 |
| 塔顶侧向 / m | -0.024183 | -0.024183 | -0.024183 |

三机总平均功率为 off 5118.162 kW、all 4843.094 kW，差值 −275.069 kW（−5.374%）。按自由来流估算相邻机组的输运时间为 504/8=63 s，长于 45 s 算例；结合三机近乎相同的输出，这组证据用于确认多机运行与物理选项传递，不评价充分发展尾流。

物理一致性检查：关闭塔架自由度时，两轴位移在全部输出中均为零；单机 tower_flex 的前后绝对峰值 0.214756 m、侧向绝对峰值 0.030914 m，约为塔高的 0.25% 与 0.04%，开启后的有限非零位移和塔底弯矩波动增加符合柔性响应的基本预期。tower_shadow 的平均功率差为 −0.128%；dynamic_stall 为 −5.194%，all 为 −5.374%。功率未出现数量级偏离或发散，但约 5% 的 UA 模型差异不是数值噪声，短算例不能证明其预测精度或稳态偏差满足训练研究的验收阈值。

动态失速组的 UA 关闭警告仅对应每片叶片的节点 1–4；它们引用 Cylinder1/Cylinder2 常值非升力极曲线。其余升力翼型未出现缺参数或关闭 UA 的警告。各组还包含模板可选 ElastoDyn nodal outputs 段提示和 Farm OutFmt 列宽提示；这里的统计只使用已核对名称、单位、有限值和终止时间的普通 OutList 通道。

在线 tower_flex 验证经过默认预热后收到 12 个快照，两轴源时刻均为 104.5–137.5 s；求解器到达 138 s 正常结束，原生报告 Real Time 为 18.114 s。Driver 清理状态为 confirmed。每个有效值与对应原生输出的打印值完全一致（最大误差 0 m），单位和 physics_identity 一致。

| 在线通道 | 有效样本 | 均值 / m | 最小值 / m | 最大值 / m |
| --- | ---: | ---: | ---: | ---: |
| tower_top_fa | 12 | 0.203072148 | 0.202256456 | 0.203920320 |
| tower_top_ss | 12 | -0.026221203 | -0.027200973 | -0.025038952 |

算例物理身份（绑定上述输入与选项，不代表整个交付包摘要）：

| 对象 | physics identity |
| --- | --- |
| 1 机 off | `0437c776ab2c903495433e1cc63f4ce50d007497329c4869f4798df6cd2bc0bf` |
| 1 机 tower_flex | `5bd47133bf31a6738b928a6d1e1acf82a66b46ac788ed28e1b027dad5b6e116f` |
| 1 机 dynamic_stall | `879b06f65aad4b6628f5abbe8c4c56c4a52e57ccea0a6b8750800fe8033281bd` |
| 1 机 tower_shadow | `e8c1f36122f49c71d8c765fd3d60705cddc9153f89d2bcecb9692bf4f2031e70` |
| 1 机 all | `8d9c9533c95f640e4762722b3026cc0e1edefe217705451c90dcb8fd1fcbccec` |
| 3 机 off | `30a87f4670e94559750b87d19b56fa3911fe1e9b21df0527d0a0e7b5eb003bc2` |
| 3 机 all | `f3455d91f484b61b5223971af02d28331a986da02bfe0c9a07044eef85478dc6` |
| 在线 tower_flex | `b2fe0c030b08070e2df45fe3688f198b554ecffe24e2017621acc17ef6956f05` |

## 软件验证入口

以下命令从仓库根目录执行，临时目录和缓存均放在独立的 `/private/tmp` 子目录。宿主测试覆盖配置、拒绝行为、默认字节、算例身份、传递与曲线导出；它们不代替真实求解或 Blender 可见窗口验证。

```bash
DQ_TEST_TMP=$(mktemp -d /private/tmp/wfrl-dq-tests-XXXXXX)
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/wfcrl-env:$PWD/blender_frontend:$PWD"
export MPLCONFIGDIR="$DQ_TEST_TMP/mpl"
export TMPDIR="$DQ_TEST_TMP"
DQ_PYTHON=/opt/anaconda3/envs/wfrl-mac/bin/python

"$DQ_PYTHON" -m pytest \
  tests/test_dq_physics_options.py tests/test_dq_solver_completion.py tests/test_solver_outputs.py \
  tests/test_cn_scene_validation.py tests/test_cn_scene_runtime_lifecycle.py \
  tests/test_fastfarm_driver_inputs.py tests/test_fastfarm_training_integrity.py \
  tests/test_fastfarm_wind_conditions.py tests/test_bx_training_checkpoint_atomicity.py \
  tests/blender_bridge/test_workflow.py \
  -q -x -p no:cacheprovider --basetemp="$DQ_TEST_TMP/root"

"$DQ_PYTHON" -m pytest \
  blender_frontend/tests/test_dq_tower_telemetry.py \
  blender_frontend/tests/test_solver_telemetry.py \
  blender_frontend/tests/test_live_telemetry_panel.py \
  --ignore=blender_frontend/tests/blender \
  -q -x -p no:cacheprovider --basetemp="$DQ_TEST_TMP/frontend"

"$DQ_PYTHON" -m pytest blender_frontend/tests/test_launcher.py \
  -k 'verify or python_executable or selected_python or fastfarm_yaml_forms or preflight or protocol_stop' \
  --ignore=blender_frontend/tests/blender \
  -q -x -p no:cacheprovider --basetemp="$DQ_TEST_TMP/launcher"

"$DQ_PYTHON" -m pytest \
  blender_frontend/tests/test_text_encoding_guard.py \
  blender_frontend/tests/test_text_io_encoding.py \
  blender_frontend/tests/test_static_names.py \
  --ignore=blender_frontend/tests/blender \
  -q -x -p no:cacheprovider --basetemp="$DQ_TEST_TMP/guards"
```

启动器的完整 `test_launcher.py` 还包含真实本地端口及进程监管测试，需要允许相应系统调用的环境；上述过滤只选择可独立验证的启动参数、预检和协议合同。完成检查后删除 `$DQ_TEST_TMP`。

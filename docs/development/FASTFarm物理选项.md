# FAST.Farm 结构与气动物理选项

场景、`FastFarmDriver`、训练命令和 Blender 启动器支持四个独立、默认关闭的布尔选项。选项在生成算例、启动求解器之前应用；运行中不切换物理模型。只修改生成目录中的输入，不修改模板、控制器或已有训练结果。

| `physics` 字段 | 开启后的输入 | 保持的限制 |
| --- | --- | --- |
| `tower_flex` | ElastoDyn 的 `TwFADOF1`、`TwFADOF2`、`TwSSDOF1`、`TwSSDOF2` 均为 `True` | 使用选定结构模型的 ElastoDyn 塔架；陆上模型可与 `blade_torsion` 组合，海上 OC3 必须显式开启 |
| `dynamic_stall` | AeroDyn `AFAeroMod=2` | 要求模板 `UAMod=3`、`FLookup=True`、`CavitCheck=False`；不生成或修改 UA 系数 |
| `tower_shadow` | `TwrPotent=1`、`TwrShadow=1` | 标准势流与 Powles 塔影一起开启；`TwrAero` 保持模板值 `False`，不额外开启塔架气动阻力载荷 |
| `blade_torsion` | `CompElast=2`、三叶片 BeamDyn、`DT=0.00125 s` | 只支持已核对的 NREL 5MW 直叶片输入；[来源、扭角定义与性能](BeamDyn叶片弹性扭转.md) |

陆上模型的四项可以分别开启，也可以组合。这里只支持当前 NREL 5MW 的 ElastoDyn / BeamDyn / AeroDyn v15 输入。独立 `substructure` 字段可选择 [OC3 海上单桩与波浪](OC3海上单桩与波浪.md)；其完整结构、必需的塔架柔性以及拒绝的 BeamDyn 组合按该模型合同执行。`UAMod=3` 对应 Beddoes–Leishman 的 Minnema/Pierce 扩展；势流扰动与下游塔影可叠加。[OpenFAST AeroDyn 输入说明](https://openfast.readthedocs.io/en/v3.5.3/source/user/aerodyn/input.html)

## 场景与启动

在有效场景 YAML 中加入：

```yaml
physics:
  tower_flex: true
  dynamic_stall: false
  tower_shadow: true
  blade_torsion: false
```

只接受上述键和 YAML 布尔值。未知键、`null`、字符串 `"true"`、数字 `0/1` 都拒绝；FLORIS 场景拒绝开启这些选项。省略字段代表关闭。全关闭的场景序列化省略 `physics`，保留默认训练配置身份的兼容性。

以下入口接受 `--tower-flex`、`--dynamic-stall`、`--tower-shadow`、`--blade-torsion`，以及相应的 `--no-*`。未提供的命令行选项保留场景值；显式选项覆盖对应字段，不影响其余字段。

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

- `requested`：四个归一化布尔请求；`turbines[].actual`：每台机组实际 DOF、AeroDyn 开关及 `TwrAero`。
- `turbines[].checks`：所启用功能检查过的翼型或塔架节点信息。
- `inputs_sha256`：实际读取的主输入、ElastoDyn、AeroDyn 及所启用选项的依赖输入摘要。范围按清单列出，不代表完整交付包摘要。
- `identity`：上述相对路径、请求、实际设置和摘要的内容身份，不包含时间或输出目录绝对路径。

Driver 的 `physics_record` 和测量 `physics` 保留此记录；每回合的持久 `wind_conditions` 也含 `physics`，包括全关闭时的实际参数。原生遥测 `provenance.physics_identity` 关联该身份。训练有效配置和 checkpoint 身份包含已启用选项，配置不匹配的恢复不能当作同一训练条件。`blade_torsion` 的关闭字段会进入非空物理配置的训练身份；已有三项物理选项的 checkpoint 兼容处理待实现，见 [BeamDyn 限制](BeamDyn叶片弹性扭转.md#接口与当前限制)。低层算例附属记录应在任何手工修改输入之后重新生成，不能用旧摘要声明修改后输入的身份。

未配置 `substructure` 且 physics 全关闭时不改物理输入、不增加算例附属文件，也不增加塔顶输出列。默认算例和默认遥测准备的字节合同由 `tests/test_dq_physics_options.py` 及 `tests/fixtures/dq/default_case_sha256.json` 检查；它同时比较省略选项与显式全关闭时的完整生成目录。模板、控制律、观测维数和奖励公式不随选项自动改变。

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

`--seconds` 支持 6、12、30、45、60；`--turbines` 支持 1、3；`--wind-speed` 支持 8、12 m/s，默认 8；`blade_torsion` 组只开叶片弹性，`all_on` 组开四项，`all` 组保留三项塔架／气动组合；`--variants off all` 可只比较关闭与全开，所选组必须含 `off` 且不能重复；`--prepare-only` 只生成并检查输入，不初始化 MPI。每组子进程有超时和进程组清理。每组原始输出、日志和元数据都在指定目录；提取所需证据后清理该临时目录。

`comparison.json` 记录求解器摘要、MPI spawn 至确认原生进程退出的墙钟时间（`wall_seconds`，不含建例）、MPI 交换结束时间（`mpi_wall_seconds`）、等待原生收尾的时间（`completion_wait_seconds`）及相对关闭组的耗时变化。MPI 断连不代表输出已经写完；只有日志出现 `FAST.Farm terminated normally.` 且已识别的本次 FAST.Farm 原生进程退出后才读取输出。当前 45 s 算例的输出间隔为 0.25 s，末行应为 45.0000 s；末行 44.5 或 44.75 s 均拒绝，终止时刻误差只容许时间列四位小数的打印精度。未换行的残缺行和超出终止时刻的数据也拒绝。

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

## 精度验收标准（建议，待确认）

以下标准是供确认的建议，尚未被项目负责人采纳；采纳或修改后再改为正式要求。各项的当前测量、判读与限制见下方“当前已知结果”，通过建议门槛不等于门槛已被采纳。

**适用对象**：`dynamic_stall`、`tower_flex`、`tower_shadow` 及其组合，NREL 5MW 陆上模板，FAST.Farm v3.5.3。

| 编号 | 检查 | 建议门槛 | 说明 |
| --- | --- | --- | --- |
| A1 | 与官方算例一致 | 用同一输入复现 OpenFAST r-test v3.5.3 中开启 `AFAeroMod=2` 的 5MW 算例，功率、叶根弯矩、塔底弯矩的均值相对差 ≤1%，标准差相对差 ≤5% | 证明本机求解器与模板接入正确，是其余各条的前提 |
| A2 | 稳态附着流下动态失速的均值影响 | 均匀稳态风 8 / 12 m/s、零偏航，丢弃前 ≥60 s 启动段，统计窗口 ≥10 个转子转动周期：`dynamic_stall` 相对 `off` 的平均功率差 ≤2%，平均叶根挥舞弯矩差 ≤3% | 附着流中 B-L 模型主要引入相位滞后，均值不应大幅偏离稳态极曲线；超过门槛须找出原因再使用 |
| A3 | 时间步收敛 | 结构／气动求解步长减半，功率、叶根和塔底弯矩均值变化 ≤1%（以参考 RMS 归一化），RMS、标准差、峰值、DEL 相对变化 ≤5% | 与已有离线筛查门槛一致 |
| A4 | 湍流来流 | 同一湍流盒（如 `ts_u8_ti8_s42`），≥300 s：平均功率差 ≤3%；DEL 与标准差差异只报告、不设门槛 | 动态失速主要影响载荷波动，载荷精度须对照外部基准，不由本条判定 |
| A5 | 塔架弯曲 | `tower_flex` 关闭时塔顶位移严格为 0；开启后塔顶前后位移均值与静力估算（推力 × 塔架柔度）相对差 ≤10%，一阶塔架频率与 5MW 定义报告值（约 0.32 Hz）相对差 ≤5% | 检查模态与刚度接入正确 |
| A6 | 塔影 | 叶片经过塔架时叶根弯矩出现每转一次的周期性下降；平均功率变化 ≤1% | 塔影只应带来局部周期扰动 |
| A7 | 运行状态 | 全部算例正常结束；无 UA cutout 或翼型表越界警告；输出无 INVALID、NaN | 任一项失败即不通过 |

**当前已知结果**：下列结论只按上述建议门槛判读，门槛仍为**建议，待确认**。A1、A5、A6、A7 在列明对象与范围内通过；A2、A3、A4 不通过，不能据此宣称这组物理选项已通过整体精度验收。未调整 UA 参数或物理开关以满足门槛。

### 验证对象与统计口径

单机 NREL 5MW 陆上模型、零偏航指令、macOS arm64、单精度、OpenMP 单线程。FAST.Farm v3.5.3-dirty 的 SHA-256 为 `bed9651e19daf8ee6402100ff22b90867b756d96b72495ef9ee90df677a88d3f`；OpenFAST v3.5.3-dirty 为 `95a286bcff9326973da53523268bd4f90624d4489c0f852567197259e6fe753a`。这里不覆盖 BeamDyn、海上单桩、多机充分发展尾流或实测验证。

除 A1 与无气动自由衰减外，结构步长为 0.00625 s，AeroDyn 使用同一步长，输出间隔为 0.025 s；FAST.Farm 高／低分辨率耦合步长保持 0.1 / 3 s。A3 只把结构与气动步长减半为 0.003125 s，输出与耦合步长不变。稳态比较运行 180 s，统计 60–180 s，共 4801 点；湍流比较运行 360 s，统计 60–360 s，共 12001 点。先确认原生进程退出，再读取 `.out`；均检查末行时刻、完整行和全部通道有限值。

均值与总体标准差按窗口内等间隔样本计算；RMS 含均值，峰值为绝对峰值。DEL 使用未分箱 ASTM rainflow、半循环权重 0.5、1 Hz 等效循环数、无均值修正；叶根弯矩用 Wöhler 指数 10，塔底弯矩用 4。功率的同形式指标只用于数值比较，不是结构疲劳寿命。近零分母不添加人为下限，无法定义的相对差保留为空。

| 编号 | 数值证据 | 按建议门槛判读 |
| --- | --- | --- |
| A1 | 官方 60 s 算例，9601 点；7 个功率／叶根／塔底通道的均值最大绝对相对差 0.01774%，标准差最大差 0.10993% | 通过；只证明所复现官方算例的数值一致性 |
| A2 | 8 m/s：功率 −5.36474%，叶根挥舞弯矩 −4.21053%；12 m/s：功率 +0.00108%，叶根挥舞弯矩 +4.24316% | 不通过；两种风速均有越限项 |
| A3 | 五组的均值差／参考 RMS 最大 0.02374%，RMS 最大变化 0.02637%，峰值最大变化 0.48084%；标准差最大变化 19.77161%，DEL 最大变化 23.96668% | 不通过；部分小波动的相对统计量超过 5% |
| A4 | 相同湍流盒：`dynamic_stall` 平均功率 −5.00702%，三项 `all` −5.17707%；仅塔架柔性 −0.12383%，仅塔影 −0.14293% | 动态失速与组合不通过；两个单独塔架选项通过功率门槛 |
| A5 | 关闭塔架自由度两轴全时序严格为 0；开启后前后均值 0.197373 m，简化静力估算 0.209537 m，差 −5.80550%；一阶前后／侧向频率 0.324356 / 0.315328 Hz | 通过该简化筛查；不是完整静力或模态认证 |
| A6 | 5° 方位角分箱的 B1 叶根挥舞差值在 192.5° 达到 −217.513 kN·m，每转一次；平均功率 −0.21065% | 通过 |
| A7 | 21 组物理对比均正常结束；UA cutout、翼型表越界、INVALID／NaN 均为 0；官方复现与独立自由衰减也正常结束 | 通过列明运行矩阵；非升力圆柱段的 UA 关闭提示单列，不等于 cutout |

### 动态失速功率差的来源

保留模板 `PLExp=0.2` 时，22.5–45 s 的平均功率为 off 1706.062 kW、UA 1617.441 kW（−5.19446%）；60–120 s 为 1707.002 / 1617.223 kW（−5.25945%），120–180 s 为 1707.005 / 1617.222 kW（−5.25969%）。丢弃启动段后差异没有消失，两个后续窗口仅差 0.00024 个百分点，不能归因于 UA 初始化瞬态。

A2 使用 `PLExp=0` 的均匀风，与保留模板剪切的诊断分开：

| 风速 | off / UA 平均功率（kW） | off / UA 平均 RootMyc1（kN·m） | 统计窗口内 off / UA 转数 |
| --- | ---: | ---: | ---: |
| 8 m/s | 1755.747 / 1661.556 | 5682.059 / 5442.814 | 18.259 / 17.940 |
| 12 m/s | 4999.974 / 5000.028 | 8352.724 / 8707.144 | 24.200 / 24.197 |

两个窗口均超过 10 转。12 m/s 已进入额定功率控制区，约 5 MW 的均值一致不代表气动或载荷一致。8 m/s 的 DU40 内侧升力截面平均攻角约 13.7°，整片叶片并非全部处于附着区；但造成主要差异的中段 DU25／DU21 攻角只有约 3.7–4.4°，因此不能用叶根失速解释主要功率损失。

官方 [r-test v3.5.3 的 5MW_Land_DLL_WTurb AeroDyn 输入](https://github.com/OpenFAST/r-test/blob/548bf2143633a7066f4582167fdf4180951fd7f6/glue-codes/openfast/5MW_Land_DLL_WTurb/NRELOffshrBsline5MW_Onshore_AeroDyn15.dat) 同样采用 `AFAeroMod=2`、`UAMod=3`、`FLookup=True`。项目 8 个翼型文件与该版本的 UA 参数及极曲线相同；5 个文件逐字节一致，Cylinder1、Cylinder2、DU30 只相差末尾空行。未发现项目开关写入错误或翼型数值版本不匹配。

以下将 `Cn = Cl cos(alpha) + (Cd-Cd0) sin(alpha)` 在 −2° 到 5° 内按 0.1° 等距采样拟合，作为附着区一致性诊断；拟合值没有写回输入。零升力角由极曲线零交叉插值，不能与区间线性拟合的截距混用。

| 翼型 | alpha0 / 极曲线零升力角（°） | C_nalpha / 拟合斜率（rad⁻¹） | alpha1 / alpha2（°） | Cn1 / Cn2 | 极曲线 Cn(alpha1) / Cn(alpha2) | Cd0 / Cd(alpha0) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DU40 | −3.200 / −3.075 | 7.4888 / 7.5198 | 9 / −9 | 1.3519 / −0.3226 | 1.2708 / −0.2660 | .03000 / .03190 |
| DU35 | −1.200 / −1.366 | 7.1838 / 7.8813 | 11.5 / −11.5 | 1.6717 / −0.3075 | 1.5788 / −0.5101 | .01200 / .01256 |
| DU30 | −2.200 / −2.125 | 7.3326 / 7.5658 | 9 / −9 | 1.4490 / −0.6138 | 1.3741 / −0.7646 | .00800 / .00880 |
| DU25 | −3.200 / −3.366 | 6.4462 / 7.2742 | 8.5 / −8.5 | 1.4336 / −0.6873 | 1.3555 / −0.6900 | .00600 / .00684 |
| DU21 | −4.200 / −4.125 | 6.2047 / 6.8345 | 8 / −8 | 1.4144 / −0.5324 | 1.3460 / −0.5152 | .00600 / .00638 |
| NACA64 | −4.432 / −3.838 | 6.0031 / 6.5045 | 9 / −9 | 1.4073 / −0.7945 | 1.3108 / −0.5882 | .00650 / .00750 |

`alpha1/alpha2`、`Cn1/Cn2` 和 `Cd0` 并不处处与当前静态曲线直接插值相等；这些是官方输入继承的参数关系，不能自动视为项目缺陷。`FLookup=True` 使用极曲线求分离函数，`Cn1/Cn2` 仍参与失速触发等计算。[5MW 定义报告第 3 节](https://docs.nlr.gov/docs/fy09osti/38060.pdf) 说明极曲线经过旋转失速延迟等修正并估计了动态失速参数；这不保证某一 UA 参数组精确重现修正后的整个附着区。

[OpenFAST v3.5.3 的 Get_f_from_Lookup 与 ComputeKelvinChain](https://github.com/OpenFAST/openfast/blob/v3.5.3/modules/aerodyn/src/UnsteadyAero.f90) 给出了这里的直接机制：设 `L = C_nalpha / sqrt(1-M²) × (alpha-alpha0)`，正升力分支计算 `f = (2 sqrt(Cn_static/L)-1)²` 并将上限裁为 1。当极曲线的 `Cn_static > L`，`f` 饱和，`cn_fs=0`；稳态的 UA 法向系数趋向这条较低的线，而非原静态极曲线。这里只用该稳态极限解释观测，完整 UA 仍含非环量、状态滞后和涡项。

19 个 AeroDyn B1 节点输出 `AB1NxxxAlpha/Cl/Cd/Cn/Ct/Fx/Fy/Vrel/Phi`。下表用实际 `Alpha/Cl/Cd` 重建扣除 `Cd0` 的翼型法向系数，**不是**直接把 rotor-normal 输出 `AB1NxxxCn` 当作翼型法向系数。数值取模板剪切、60–180 s：

| 节点 / 半径 | 翼型 | UA 平均攻角（°） | 同攻角极曲线 Cn | 实际 UA Cn | 压缩性修正直线 L | 同攻角 Cl 差 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 9 / 28.15 m | DU25 | 4.428 | .9962 | .8609 | .8611 | −13.59% |
| 10 / 32.25 m | DU25 | 4.065 | .9558 | .8209 | .8209 | −14.12% |
| 11 / 36.35 m | DU21 | 3.713 | .9633 | .8615 | .8616 | −10.59% |
| 12 / 40.45 m | DU21 | 3.723 | .9642 | .8637 | .8638 | −10.45% |

上述四段的正分支查表在窗口内均处于 `f=1` 裁剪条件。节点 6–7 的 DU35 也存在约 5.2–8.4% 的同攻角升力差。按未变形半径对 B1 平均切向分布力差作 `∫r ΔFy dr` 梯形积分，节点 9–12 占总负扭矩差代理量的 84.88%；它仅用于定位截面，不是含叶片变形、转速反馈和发电效率的精确功率分解。节点 1–4 为圆柱常值极曲线，UA 自动关闭且 Cl 始终为 0，其对应扭矩差代理量很小且为正，不是主要损失来源。

**结论边界**：这是一组可由官方模型／参数组合解释并复现的稳态差异，不是已经发现的项目接入错误；也不能把“官方输入同样如此”当作实测精度证明。需要负责人决定是否接受该模型差异、开展独立 UA 标定或修订建议门槛；本结果不授权重写翼型参数、改变默认训练条件或放宽门槛。

### 各项检查的证据范围

**A1 官方复现。** 使用 r-test v3.5.3 提交 `548bf2143633a7066f4582167fdf4180951fd7f6` 的 `5MW_Land_DLL_WTurb`，保留官方 12 m/s 湍流输入、全部模型参数与 60 s 时长。控制器从该提交的官方 `5MW_Baseline/ServoData/DISCON/DISCON.F90` 在临时目录编译；没有使用或修改项目 MPI 控制器源文件。参考二进制输出 SHA-256 为 `4efa7e987502038a4e1b846e3f3ddf9298e7a19bb3c28ac06192658a8fa18c73`。全时序同采样点比较，不插值：

| 通道 | 参考 / 本机均值 | 均值相对差 | 标准差相对差 |
| --- | ---: | ---: | ---: |
| GenPwr（kW） | 4866.268822 / 4866.151672 | −0.00241% | +0.08521% |
| RootMxb1（kN·m） | 859.525561 / 859.493546 | −0.00372% | +0.00045% |
| RootMyb1（kN·m） | 8127.171343 / 8126.969586 | −0.00248% | −0.02875% |
| RootMzb1（kN·m） | −4.779563 / −4.778715 | +0.01774% | +0.00368% |
| TwrBsMxt（kN·m） | 5068.697680 / 5068.530393 | −0.00330% | +0.10993% |
| TwrBsMyt（kN·m） | 54441.839280 / 54440.592278 | −0.00229% | −0.10284% |
| TwrBsMzt（kN·m） | −330.078532 / −330.043534 | +0.01060% | +0.00532% |

A1 叶根通道按官方输出的 `b` 坐标系逐列比较；A2–A4 使用项目 `c` 坐标系通道，不混用二者。A1 的通过不意味着项目默认塔架 DOF、控制器和 FAST.Farm 耦合与官方单机输入相同，也不证明 5% 均值差具有实测依据。

**A3 时间步。** 下表为每组在功率、两向 B1 叶根弯矩、两向塔底弯矩中的最大绝对百分比差；均值以参考 RMS 归一化，其余以自身参考统计量归一化。

| 组 | 均值 / RMS | RMS | 标准差 | 绝对峰值 | DEL | 判读 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| off | .00283% | .00283% | 19.77161% | .00647% | 21.05414% | 不通过 |
| tower_flex | .00307% | .00590% | .39341% | .02238% | .28085% | 通过 |
| dynamic_stall | .02373% | .02636% | 7.44050% | .48084% | 23.96668% | 不通过 |
| tower_shadow | .00173% | .00164% | .22772% | .00240% | .30661% | 通过 |
| all | .02167% | .00873% | .28885% | .04825% | .26506% | 通过 |

例如 off 功率标准差由 .006657 变为 .005341 kW，UA 功率 DEL 由 .013551 变为 .016798 kW；绝对值很小但仍违反字面的相对门槛。即使不把功率 DEL 当作验收项，off 的 `TwrBsMyt` DEL 也由 17.6165 变为 16.2240 kN·m（−7.90471%），所以 A3 仍不通过。是否为近零波动设置独立绝对容差须确认，不能由报告自行豁免。

**A4 湍流。** `ts_u8_ti8_s42` 使用同一 660 s、0.1 s、10 m 网格的 TurbSim 盒，统计库轮毂层均值 8.0000 m/s、TI 8.051%；种子 42，`ScaleIEC=1`。BTS SHA-256 为 `19e1c0e32f5fa353fe9c193e409ff93b1c12275c91f608f6b29e684738947590`。每组算例完整覆盖 360 s，统计 300 s；命令中的名义风速不会覆盖 BTS 样本。

| 组 | 平均功率（kW） | 相对 off |
| --- | ---: | ---: |
| off | 1818.899794 | 0% |
| tower_flex | 1816.647373 | −0.12383% |
| dynamic_stall | 1727.827118 | −5.00702% |
| tower_shadow | 1816.300039 | −0.14293% |
| all | 1724.733998 | −5.17707% |

波动与 DEL 只报告，不据此判定物理精度。下面每格按“标准差 / DEL”排列；off 给出原单位（功率 kW、弯矩 kN·m），其余给出相对 off 的变化。

| 通道 | off | tower_flex | dynamic_stall | tower_shadow | all |
| --- | ---: | ---: | ---: | ---: | ---: |
| GenPwr | 212.325 / 199.280 | −0.346% / +0.123% | −2.738% / −3.769% | −0.230% / −0.103% | −2.963% / −3.914% |
| RootMyc1 | 567.441 / 1573.247 | −0.795% / −0.258% | −3.603% / +1.290% | +1.701% / +3.370% | −2.122% / +4.028% |
| RootMxc1 | 2543.687 / 5996.936 | −0.035% / −0.071% | +0.735% / +0.884% | +0.025% / −0.323% | +0.673% / +0.438% |
| TwrBsMyt | 2967.930 / 3680.102 | +7.266% / +26.607% | −4.526% / −5.777% | +0.035% / +0.792% | +3.430% / +25.428% |
| TwrBsMxt | 345.045 / 739.062 | +59.251% / +69.804% | +13.250% / +29.267% | +10.625% / +24.094% | +138.241% / +151.652% |

**A5 塔架。** 采用 AeroDyn `RtAeroFxh` 的气动推力均值 379279.444 N，不将含结构作用的轴向总载荷 `RotThrust`（476617.911 N）冒充气动推力。由模板塔架前后刚度表线性插值，在 87.6 m 长度上积分 `C = ∫(L-z)²/EI(z) dz = 5.5246155e-7 m/N`，得到顶端集中力近似 `F C = 0.209537 m`；与位移 0.197373 m 差 −5.80550%。该近似忽略轴倾角投影、轮毂高度偏置、重力弯矩和几何刚度，不作为精确静力解。

一阶频率采用独立 OpenFAST 无气动、无 ServoDyn、锁定转速／传动链的自由衰减：保留塔架与叶片弹性自由度，初始塔顶前后／侧向位移各 0.1 m，运行 630 s。10–100 s 的前后 29 个正峰和侧向 28 个正峰分别得到 0.324356 / 0.315328 Hz。相对定义报告表 9-1 的 0.3240 / 0.3120 Hz，差 +0.110% / +1.067%；相对建议中的约 0.32 Hz 也在 5% 内。FAST.Farm 要求 AeroDyn 开启，不能用 `CompAero=0` 直接进行同一无气动自由衰减；因此该频率项使用单机 OpenFAST，不称作 FAST.Farm 无气动求解。

**A6 塔影。** 对 60–180 s 按实际方位角分箱后相减，避免两组略有不同的转速造成时间对齐偏差。差值在叶片经过塔架附近（180°）开始明显下降，最低在 192.5°（−217.513 kN·m），结构响应存在相位滞后。平均功率由 1755.747 降至 1752.049 kW。该开关同时启用势流和 Powles 塔影，结论针对组合开关，不把整个差异单独归给下游塔影公式。

**A7 运行范围。** 21 组包括模板剪切 8 m/s 两组、均匀 8 m/s 五组、均匀 12 m/s 两组、半步长五组、湍流五组和塔架气动推力复核两组。所有 `.out` 到达规定末时刻；所有列完整且有限。启用 UA 的每组有 12 条“constant data”关闭提示，仅对应三片叶片的节点 1–4；没有升力截面 UA cutout 或翼型表越界。长算例有尾流面离开低分辨率域的提示，不能将这里的单机证据扩展为下游尾流域收敛证明。

### 诊断脚本用法

对比脚本的显式诊断选项如下；全部省略时保持原有生成输入字节与半程统计窗口。默认字节合同仍由固定输入摘要测试覆盖，诊断默认值另由 `tests/test_dv_physics_accuracy.py` 对完整生成目录逐字节检查。

| 参数 | 行为 |
| --- | --- |
| `--seconds N` | 接受正的 3 s 整数倍，支持 180 / 360 s 等长算例；默认 45 |
| `--discard S` | 统计窗口起点，要求 `0 <= S < seconds`；默认仍取后半程 |
| `--uniform-wind` | 显式设置稳态风 `PLExp=0`，不能与 BTS 同用 |
| `--step-scale 0.5` | 结构 DT 与 DTAero 减半，不改 FAST.Farm 耦合或输出间隔 |
| `--output-dt 0.025` | 指定输出间隔，必须是结构步长的整数倍 |
| `--aero-nodes` | 第一片叶片全部 19 节点的气动诊断输出 |
| `--wind-box /absolute/path/file.bts` | 显式使用同一湍流盒，不请求用 HWindSpeed 改写 BTS 风速 |

启用诊断配置时还请求 `Azimuth/RotSpeed/RotThrust/RtAeroFxh/RtAeroPwr` 等分析通道；`comparison.json` 的 `diagnostics.inputs_sha256` 与 `wind_box.sha256` 标识其修改范围。统计程序支持超过 16 MiB 的完整输出，不把一次在线读取预算误当作文件结束。

```bash
DV_RUN=$(mktemp -d /private/tmp/wfrl-dv-XXXXXX)
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/wfcrl-env:$PWD"
export MPLCONFIGDIR="$DV_RUN/mpl" TMPDIR="$DV_RUN" OMP_NUM_THREADS=1
DV_PY=/opt/anaconda3/envs/wfrl-mac/bin/python
DV_FF=/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm

"$DV_PY" -m scripts.experiments.compare_fastfarm_physics \
  --seconds 180 --discard 60 --uniform-wind --output-dt .025 --aero-nodes \
  --fastfarm "$DV_FF" --output "$DV_RUN/uniform8"
"$DV_PY" -m scripts.experiments.audit_fastfarm_physics \
  "$DV_RUN/uniform8/comparison.json" --discard 60 --output "$DV_RUN/uniform8-audit.json"
```

`--wind-speed 12 --variants off dynamic_stall` 对应 A2 的 12 m/s 组；省略 `--uniform-wind`、选择同样两组对应模板剪切诊断；`--step-scale .5` 对应 A3，均使用各自的新输出目录。A3 可用 `audit_fastfarm_physics.compare_metrics()` 比较相同通道的统计，A6 使用 `phase_binned_difference()` 按方位角比较；`airfoil_audit()` 输出 UA／极曲线参数关系，审计 JSON 中的 `nodes` 输出逐截面的同攻角比较。

```bash
"$DV_PY" -m scripts.inflow.generate_turbsim \
  --wind-speeds 8 --ti 8 --seeds 42 --duration 660 --simulation-seconds 360 \
  --threads 1 --scratch-root "$DV_RUN" --output-dir "$DV_RUN/turbulence"
"$DV_PY" -m scripts.experiments.compare_fastfarm_physics \
  --seconds 360 --discard 60 --output-dt .025 --aero-nodes \
  --wind-box "$DV_RUN/turbulence/ts_u8_ti8_s42.bts" \
  --fastfarm "$DV_FF" --output "$DV_RUN/turb8"
"$DV_PY" -m scripts.experiments.check_tower_decay \
  --openfast /opt/anaconda3/envs/wfrl-mac/bin/openfast --seconds 630 \
  --output "$DV_RUN/decay"
```

A1 从上方固定 r-test 提交取得 `5MW_Land_DLL_WTurb` 及其引用的 `5MW_Baseline` 输入、风文件和官方控制器源码。运行前把官方 `.outb` 另存为不可被求解器覆盖的参考文件；原始 `.fst` 不变。官方控制器在临时目录用 `gfortran -shared -fPIC -ffree-line-length-none -fdefault-real-8 -DIMPLICIT_DLLEXPORT` 编译至输入引用的 `5MW_Baseline/ServoData/DISCON.dll`，再用本机 `openfast` 运行该 `.fst`。进程退出后：

```bash
"$DV_PY" -m scripts.experiments.check_openfast_reference \
  --reference "$DV_RUN/reference.outb" \
  --actual "$DV_RUN/r-test/glue-codes/openfast/5MW_Land_DLL_WTurb/5MW_Land_DLL_WTurb.outb" \
  --output "$DV_RUN/A1.json"
```

所有脚本的输出目录应位于独立临时目录；提取数值证据、确认本次求解器与 MPI 进程退出后删除 `$DV_RUN`。不将原始求解输出、临时参考仓库、编译模块或日志存入项目。

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

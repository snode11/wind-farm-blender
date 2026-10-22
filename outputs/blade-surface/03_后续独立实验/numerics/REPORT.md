# 共同数值链路实际执行报告 · 2026-10-06

判定：**NUMERICAL_GATE_PASSED_REVIEW_ONLY**。已完成一项主要修复和预声明 8 次优化，达到原 1.6667 mm 的目标 blade0 平均组间几何稳定性门槛。旧 P2b-0、原模块、共享 SciPy 与其他 run 均保留。此结论仅是旧开发 clip 的数值前置，不是新 P2b 几何增益、表面精度或现场能力。

## 修复及其真实效果

中心 z 起点经 SciPy 严格可行化成为极小非零，bounded TRF 原初始半径约 1.4278e-11，绕过 exact-zero fallback。独立 vendor 唯一数学修改是 `Delta=max(1.0, Delta_raw)`；后续信赖域正常按下降比更新，物理目标、A0/q0、边界、健康模板、support、权重、尺度和 FD 不变。

全部 8 组初始 Delta 被设为 1，均有真实接受物理步骤；相对 A0 的目标 blade0 平均更新为 12.41–12.84 mm。首 sparse A1 第一步成本实际下降 34.4775，预测下降 33.2083，ratio=1.0382；不是旧 correction01 几乎不动的退化一致。固定有界二次模型自检 2 方法恢复目标解，误差约 5.37e-12，证据 `adapter_selfcheck.json`。

| run | nfev/njev | 真实 residual 调用 | 优化秒 | cost | optimality_z | active维数 | 对A0目标平均更新mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sparse_lsmr_A1 | 9/9 | 574 | 27.77 | 6851.398409 | 1.22 | 1 | 12.8370 |
| sparse_lsmr_A3 | 9/9 | 718 | 36.06 | 6861.482025 | 0.519 | 1 | 12.4867 |
| dense_exact_A1 | 9/9 | 565 | 27.13 | 6851.396547 | 1.97 | 4 | 12.7719 |
| dense_exact_A3 | 9/9 | 705 | 35.04 | 6861.480453 | 1.32 | 4 | 12.4130 |
| sparse_lsmr_A3_shift360_weakprior | 8/8 | 638 | 34.64 | 6861.484262 | 4.11 | 1 | 12.4883 |
| dense_exact_A3_shift360_weakprior | 9/9 | 705 | 36.00 | 6861.480453 | 1.32 | 4 | 12.4130 |
| sparse_lsmr_A3_shift360_strict | 9/9 | 718 | 36.10 | 6861.482196 | 0.521 | 1 | 12.4904 |
| dense_exact_A3_shift360_strict | 9/9 | 705 | 36.01 | 6861.480453 | 1.32 | 4 | 12.4130 |

总 least_squares 墙钟 268.76 秒，真实目标/差分 residual 调用 5328 次。终点独立 Jacobian probe 596 次与 branch 原点重放 24 次单列，不合入优化计数。周期 ps RSS 采样最大约 170.6 MiB；采样在首组完成后开始，只是 peak 的下界。

## 门槛、严格同目标与先验差

| 对照 | 目标mean mm | 目标P95 mm | 目标max mm | 目标关系 |
| --- | ---: | ---: | ---: | --- |
| methods_A1 | 0.10340788 | 0.57824072 | 1.5171988 | 严格同目标 |
| methods_A3 | 0.11164177 | 0.69476596 | 1.8393079 | 严格同目标 |
| sparse_lsmr_shift_strict | 0.006753952 | 0.033545835 | 0.089078499 | 严格同目标 |
| sparse_lsmr_shift_weakprior | 0.025724286 | 0.090369389 | 0.16311867 | 保留绝对psi先验差 |
| dense_exact_shift_strict | 6.3188985e-08 | 3.1939273e-07 | 7.391377e-07 | 严格同目标 |
| dense_exact_shift_weakprior | 6.3188985e-08 | 3.1939273e-07 | 7.391377e-07 | 保留绝对psi先验差 |

门槛明确针对目标 mean，A3方法对照的 max=1.8393 mm 不能隐藏；它不违反已冻结的mean规则，也不构成max稳定性通过。网格为 81×64 固定材料 q 中点，8 时刻；它用于同一健康模板的组间对应位移，不是面积加权真值误差。新 P2b 的主表面指标应由 root 独立评价。

+360° 保留绝对 psi prior 时，white physical prior 各帧差约 7.2e-07，故不称严格同目标。同步 prior_mean+360° 后，该初始 prior 差为 7.94e-23，其他分块仅浮点舍入差；几何初始等价已独立核查。strict sparse 的 0.006754 mm 与 dense 的约 6.32e-8 mm 都低于原门槛。保留prior版本仅提供旁证。

## Jacobian、branch、边界和终止审计

同终点独立 physical FD 与 solver J_z 的最大元素差 ≤1.52e-08。104 项保存证据/branch原坐标重放检查 PASS，记录在 `delivery_checks.json`。初始增加 branch 记录的残差与未增加记录的 recon 数学实现逐字节相同。

实际每个 solver/FD call 保存 DT cell、零平台、silhouette候选/有效性、nearest segment、track triangle与domain选择的摘要；逐组 `branch_replay_detail.json` 解码首点、终点及其首个已执行FD probe 的变化位置/数量。该记录证明实际分支是否改变，不把候选/格线变化单独等同于值不连续，也不声称已定位唯一根因。

8 组都在 ftol 相对成本规则退出，optimality 约 0.519–4.11，而非 gtol 收敛。最终实际下降、预测下降与 ratio、真实步逐项在 `numerical_runs.csv` / `trf_trace.json`；保留非零梯度和active_mask，不以success替代门槛。方法与严格等价初始化的低mean差支持有限数值稳定性；新窗口仍需开发阶段对照与冻结，不能声称全局唯一或任意观测下稳定。

## 隔离与可复用交付

实际优化与正式最终comparison全程使用 `sandbox-exec`，仅读旧clean RGB/mask/calibration/二维审核点、fresh A0、RGB-on-A0 q0和配置。6 项现存禁止路径实际读探针 PASS，包括旧评价、源blend、MAPPO资产及未白名单旧解/规则文件。Python read_guard 加严格 input_bundle loader保存实际读取；未读取源state/mesh/绑定/ID/WideEval。输入schema中的历史 `human_2D_annotations` role 名称不改变作者事实：旧点由Codex逐图审核，不能称用户人工标注或自动跟踪。

一项仅用于早期进度的只读对应几何比较没有使用OS沙箱，读取同许可bundle与本目录4个结果、无源真值，详见 `operator_postprocess_audit.json`；它未用于修订矩阵/参数/门槛，最终数字均来自沙箱driver。首两次启动因getcwd与不存在probe路径停止在任何真实优化前；操作amendment保留，不计入8次矩阵。

`solver_api.py::solve_window(prepared_fitter, method='dense_exact')` 复用同一冻结Instrumented与vendor。API检查本gate及源码hash，调用端负责独立进程、严格沙箱和相同组配置/初值/归一化；API本身没有追加旧优化。新P2b可使用dense exact，采用同physical尺度与预算，只统一变更经开发冻结的RGB定位sigma与材料块总归一化。可读协议见 `finite_repair.md`，复核命令见 `REPRODUCE.md`。

完成状态：数值修复与门槛 **COMPLETED / REVIEW_ONLY**；无容差/步长/半径扫描；本目录不含新P2b采集/测试，也不据此标记自动跟踪/局部形状已通过。

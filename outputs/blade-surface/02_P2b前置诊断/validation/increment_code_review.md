# correction01 增量适配器独立静态复核

2026-10-06；reviewer：capture_contract。范围仅为 `scripts/diagnose_numerics_increment.py` 的参数/边界转换、固定可行 Jacobian、真实调用计数及 uniform360 复用。未运行任何优化、未读取独立评价或新测试成绩、未修改驱动；本文件不授予 P2b 主采集门槛通过。

结论：**PASS_STATIC_WITH_LIMITATIONS**。所要求三项计算路径未发现阻止预声明 correction01 六次验证的实现错误；一次实际扰动日志问题已在优化前由数值代理修正，并保留修订前 driver/amendment。此为静态代码复核，不是 correction01 运行通过。

## 复核身份与日志修订

- 当前 driver SHA-256：`95b8eaf54abd64579ff4b8552b5f1862176ca5e3abf0d6435662292dc991d470`。
- 当前 `diagnostics_old_attempt02/numerics/preregistration_amendment01.json` SHA-256：`f7744c75635553a1acabb2da00578ec67a9bd07f73b2b730ab11502e072674ab`；当前 driver 哈希与 amendment 匹配。
- 修订前 driver 位于 `diagnostics_old_attempt02/numerics/correction01/driver_preflight_snapshot.py`，实际 SHA-256 `e70d2ca48574748ba02bbc0ee69e0cec6c70733e3246f3676d904c3f51d372a0`。
- 修订前 amendment 位于 `diagnostics_old_attempt02/numerics/preregistration_amendment01_preflight_snapshot.json`，实际 SHA-256 `c6da8aa653602e4757d4336acab56c9480a9475f4b55be8855fba8a79b41633c`。当前 amendment 的 `preflight_log_repair` 明确引用该身份；六次 comparison matrix 与所有 read_files 身份逐值保持相同。
- 实际新旧 driver diff 只把单边 FD 的未执行一侧日志记为 0：forward 为 `[actual_plus,0,+1]`，backward 为 `[0,actual_minus,-1]`，central 保留双侧。原代码虽使用正确单边 Jacobian，却把未执行甚至可能越界的另一侧也记为实际扰动。本次未改目标、Jacobian 或优化参数。AST 解析通过（只解析，没有 import/执行驱动）。

## 1. 物理目标与边界保持

`InstrumentedIncrement.__init__` 第 65–67 行固定 `p0` 与正的 physical column scales，转换为 `z_lower=(lower-p0)/scale`、`z_upper=(upper-p0)/scale`。`evaluate` 第 81 行唯一目标调用为原冻结 `fit.residuals(p0+scale*z)`，保留原图像 robustification、domain penalty、physical prior 和 actual-dt acceleration，没有裁剪/重新加权残差。第 176 行附近的 `least_squares` 初值为零增量、`x_scale=ones`；求解后重建 `p=p0+scale*result.x` 并沿既有 `_unpack` 恢复活动状态/q。因此物理可行域与目标函数保持，优化坐标、有限差分和终止的数值表示改变。

无界 psi 的原物理上下界 ±inf 仍映射为 ±inf；有限 pitch/flap/edge 和各 track q bounds 使用原 `packed_setup` 规则。所有 scales 来自同一冻结配置，且已由冻结 fitter 验证正值。A1 无 q；A3 的 q bounds 仍围绕同一 frozen RGB-on-A0 q0，非 source binding。

限制：`x_scale_z=ones` 与零中心增量会改变 TRF 的数值路径/xtol表示；本对照同时改变 centering、坐标尺度和 FD 策略，不能单独归因于有限差分。amendment 已明示此 causal scope。

## 2. 固定可行 Jacobian 与真实计数

第 89–116 行对每列使用统一 `h_z=1e-4`，因此物理请求扰动为 `h_p=scale[k]*1e-4`。两侧可行时采用 `(r(z+h)-r(z-h))/(2e-4)`；靠近 lower 时仅向内 forward，靠近 upper 时仅向内 backward；必要时在边界上截短实际扰动并使用实际 z divisor。该导数就是 `J_z=J_p*scale`。dense/exact 与 sparse/LSMR 共用同一全列计算函数，只有返回值 ndarray/CSR 与 tr_solver 选择不同，不在 sparse 分支使用另一 FD 分组或不同尺度。

日志修正后 `jac_actual_physical_steps` 用浮点实际 `pplus-pbase`/`pbase-pminus`，单边未执行侧为 0，并保留 scheme。它能检查 uniform360 下物理扰动的真实浮点幅度，不能只根据请求 step 推定等价。

第 73–84 行在每一次真实 `fit.residuals` 调用前统一递增 `residual_calls`，role0 计入 solver_fun_calls，role1（缺缓存时的Jac base）、role2/3（正/负Jac探测）计入 jac_probe_calls。故在正常完成路径应有：

- `actual_residual_calls == solver_fun_calls + jacobian_probe_calls == len(call_z) == len(call_role)`。
- `jacobian_calls == len(jac_actual_physical_steps)`；cached base 被复用时不伪造一次调用。
- role0/1/2/3 能区分目标、额外base和正/负探测。SciPy nfev/njev 与真实调用数分列；max_nfev60不等于总 residual 调用60。

计数范围是 optimizer + 该适配器 Jacobian 实际调用 `fit.residuals`，不包括之后 `save_block_diagnostic` 对分块残差函数的离线诊断开销。墙钟字段覆盖 least_squares，同样不包括离线分块日志。报告应保留这一口径，不能称整个驱动总工作量。

静态检查没有测试数值 Jacobian 的实际精度；运行后仍要审查 projected optimality、活动边界、domain rows与实际FD日志，不能单凭 mean几何对照低于1.6667mm宣布整个门槛通过。

## 3. uniform360 的 A0/q0/support 与 actual dt

`execute` 一次读取 frozen A0、q0、配置和同一 input bundle，构建 unshifted A0 base 并 deep-copy `_model_support`。六次循环只复制 A0 后统一对全部 psi 加指定 360°；所有方法的 `build_fitter(...,q0,...,support)` 使用同一 q0、同一 contours/cameras/observations 和冻结 config。固定 support 明确从未移角 A0复用，避免浮点投影改变支持点集合。

`build_fitter` 传入相同 `bundle.times`，fitter acceleration 使用这些 actual dt；没有逐帧取模、改时间、把实际acceleration替换为差分proxy或额外读源。结果统一减去对应shift后只做相同材料网格的几何比较。

已知限制保留：绝对 psi prior 的 `prior_mean` 未平移，因此 shift360 的白化 psi prior 每帧有已知 `2*360/1e9=7.2e-7` 差。几何和图像数据应周期等价，但总代价不是严格同一函数表示；代码已把比较字段命名为 `objective_cost_difference_with_known_shift_prior_caveat`，并保存各残差块初值等价差。移角差不能全归因于FD。

## 运行前后状态边界

本复核供统筹代理判断是否开始**已授权且预声明**的唯一 correction01 六次开发验证；不提供新扫描或主采集授权。旧数值六次矩阵、旧结果、原场景与输入保持原样。即便 correction01 的几何 mean稳定，仍须 root独立审查剩余 boundary/gradient/domain/observation证据；任何未解释问题继续 NUMERICAL_INCONCLUSIVE。需要进一步改变估计的修复不得以本静态复核为理由无限追加。

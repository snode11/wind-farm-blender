# P2b-0 数值诊断与唯一统一修正验证

**最终建议：NUMERICAL_INCONCLUSIVE，停止进入主P2b采集。** 原始有限矩阵存在统一+360°表示相关几何差异。唯一统一修正虽然组间几何一致，但六次都近乎停在fresh A0，不能算解决了原来的数值问题。不能把停止结论改称材料点信息无用。

原矩阵及信息profile见 `REPORT_original_matrix.md`。本页记录另行冻结的 correction01；没有覆盖原矩阵或旧正式结果，没有运行第二轮修正或容差/步长扫描。

修正使用z=(p-p0)/固定physical scale；活动量、原物理边界、先验、q0、支持集合、实际dt、权重均复用，z的x_scale=1。固定可行FD为1e-4×physical scale，内部central、近界单侧，稀疏CSR/LSMR和dense/exact用同一个full-column Jacobian。每组max_nfev60、三容差1e-7。这个组合同时改了坐标中心、尺度坐标和差分策略，不隔离FD单因果。

独立运行前审查发现单侧FD日志曾错误记录未执行那侧；优化前已只修日志为0，保留原driver/amendment快照和新SHA，未改变算法。解析Jac、边界单侧和实际call计数自检通过。

| 条件 | nfev/njev | residual总调用 | Jac调用 | 秒 | cost | optimality_z |
|---|---:|---:|---:|---:|---:|---:|
|sparse_lsmr_A1|2/2|124|2|5.634|6886.675164371|2478.225326|
|sparse_lsmr_A3|2/2|156|2|7.228|6903.238830371|2464.003038|
|dense_exact_A1|2/2|124|2|5.511|6886.675164371|2478.225326|
|dense_exact_A3|2/2|156|2|7.305|6903.238830371|2464.003038|
|sparse_lsmr_A3_shift360|2/2|156|2|7.340|6903.238830371|2464.003038|
|dense_exact_A3_shift360|2/2|156|2|7.399|6903.238830371|2464.003038|

真实residual总调用包含优化器fun与Jac probes，不包含后续分块/元数据诊断。角色向量、实际执行FD侧数、Jac次数和优化成本分块均逐值核对通过。每组固定五类残差块保存raw/white/robust、有效行、目标与梯度；触界值与active_mask_z保存。

| 同q网格比较 | blade0均值 mm | P95 mm | 最大 mm |
|---|---:|---:|---:|
|sparse_vs_dense_A1|0|0|0|
|sparse_vs_dense_A3|0|0|0|
|sparse_shift_equivalence_A3|1.881786e-11|4.81089176e-11|7.88430335e-11|
|dense_shift_equivalence_A3|1.881786e-11|4.81089176e-11|7.88430335e-11|

自动 `CORRECTION01_GEOMETRY_MEAN_STABLE` 仅是这一几何均值子判据，不是数值门槛或主实验通过。六次统一状态最大更新只有约1.15–1.17e-10（角度列deg、形变列m、q为canonical单位）；所有nfev/njev都2/2并ftol停止，optimality_z约2464–2478。一致来自几乎没有更新。

本地SciPy TRF先把近边界的零中心初值严格可行化，再由 Delta=norm(z0/sqrt(CL_v)) 初始化信赖域，仅Delta恰好0时才用1。实际严格可行z0范数1.39165178168e-10；固定差分策略重建初始Jac之后计算的实际初始Delta=1.42780460165e-11，绕过零值fallback。A1目标只下降1.40790e-8，A3只下降1.62045e-8，足以触发相对ftol而梯度仍大。原驱动的表示依赖和本适配器的初域退化是两个已区分的问题。

初始Jac重建仅用于元数据，两次分别62/78次残差调用，单列在trust_region_degeneracy.json；没有重新优化，也没有试新步长。SciPy实际源码路径和SHA一并保留。

四个A3重算/shift条件各32个已由RGB确认的点观测，预测域惩罚目标0、估计可见性矛盾0，固定行没有静默删失。该检查只针对声明健康模板的估计几何，不是源叶片身份或精度验证。

全部估计使用系统sandbox-exec，旧evaluation、旧capture/source blend、工作区/安装mappo资产和新run capture/evaluation被policy拒读；本职责有实际拒读探针及declared input读取审计。源模块路径为本轮frozen-source/blade_recon。原旧solver没有保存真实residual调用数和完整active_mask，本轮不会反填这些历史字段。

最小下一步是开发侧明确并验证初始信赖域合同，覆盖fresh A0近界初值与统一相位表示；同时检验有限预算内的实际状态更新、可行最优性和目标下降，不能只要求表示等价。通过后再冻结数值实现并进入RGB材料点采集。当前不继续扫描参数，也不打开盲测数据。

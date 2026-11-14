# P2b-0 原始有限数值矩阵（已完成，REVIEW_ONLY）

冻结后实际执行 6 次；旧 attempt02 未改写。结果是 **NUMERICAL_INCONCLUSIVE**：同物理几何的统一 +360° 表示产生超过 1.667 mm 的几何差异。不能将此结论称为表面材料点无用。

同 A0/q0、固定轮廓支撑、权重/边界/参数尺度、60 nfev 和 ftol/xtol/gtol=1e-7。sparse/LSMR 与 dense/exact 使用同 2-point relative FD=1e-3。实际轨迹/轮廓求解全部在系统沙箱内，读取 frozen-source。

| 条件 | nfev / njev | 真实 residual 调用 | 墙钟秒 | cost | optimality |停止|
|---|---:|---:|---:|---:|---:|---|
|sparse_lsmr_A1|11 / 6|101|4.530|6852.095236|6.493891|`xtol` termination condition is satisfied.|
|sparse_lsmr_A3|14 / 6|110|5.147|6862.246035|14.189089|`xtol` termination condition is satisfied.|
|dense_exact_A1|12 / 6|204|9.205|6852.091048|7.042873|`xtol` termination condition is satisfied.|
|dense_exact_A3|12 / 8|332|15.665|6862.228132|12.115620|`xtol` termination condition is satisfied.|
|sparse_lsmr_A3_shift360|8 / 2|40|1.889|6893.072196|1516.333310|`xtol` termination condition is satisfied.|
|dense_exact_A3_shift360|8 / 2|88|4.178|6893.046570|1563.798670|`xtol` termination condition is satisfied.|

| 同 q 网格比较 | blade0均值 mm | P95 mm | 最大 mm |
|---|---:|---:|---:|
|sparse_vs_dense_A1|0.067058|0.240112|0.643204|
|sparse_vs_dense_A3|0.583339|3.421000|9.110928|
|sparse_shift_equivalence_A3|11.185245|25.550592|62.281320|
|dense_shift_equivalence_A3|10.812765|24.430210|53.405620|

网格为每片81×64材料bin中点、同8时刻，等q权重。这些数值是估计组间位移，**不是表面精度**。原始判据按blade0均值使用5mm建议收益的1/3；完整P95/最大值保留，避免尾部被均值隐藏。

统一+360°保持源模板几何与初始输入残差等价至浮点舍入，但原始psi绝对弱先验未改，白化差为每帧7.2e-7。shift对照中的 `same_objective_cost_difference` 是历史驱动字段名；应解释为包含该已知非周期弱先验差异的成本差。算法同表示比较才严格具有同一物理目标。

实际psi FD从原A0的0.053782..0.099149°变为统一+360°后的0.413782..0.459149°。求解结果同时受FD和坐标范数相关终止影响，尚不归因单一原因。6次均由xtol退出，预算未用满；不能简单称预算不足。

信息诊断使用同一个旧x3/q3参考、同白化和固定列尺度，排除先验/域惩罚/鲁棒化。32状态列中frame0 pitch0近下界，仅free-state可行子空间31维；q8维均内部。最弱輪廓方向的轮廓能量1.096064891、消q轨迹增量0.310589803（28.3368%）；最小特征值由1.096064891变为1.374487884。这个局部信息增加不证明精度改善或全局唯一。保存了±弱方向和pitch0向内方向的受限profile；没有忽略活动约束做全空间无约束结论。

| 旧x3/q3残差块 | 有效行 | white目标 | robust目标 |
|---|---:|---:|---:|
|silhouette|7865|23664.9734|6845.86543|
|track|64|10.5769148|10.0512085|
|physical_prior|32|1.43462824|1.43462824|
|acceleration|24|4.89476726|4.89476726|
|domain_penalty|96|0|0|

`residual_blocks.npz` 保存 raw/white/robust 各块残差、有效行、完整Jacobian。JSON保存各块梯度、缩放梯度、实际dt加速度、近界变量及新rerun真实active_mask。raw轮廓距离位于1/4拟合图；乘4是原图px等效距离；track raw为原4K图px。raw物理先验/加速度混合角度和米制，不应直接用其未白化总和比较数据块。域惩罚是深度/出画/背面像素等效二次惩罚，维度固定；旧x3/q3域惩罚全部0。

原历史求解的真实residual调用数与完整active_mask没有保存；本轮记录只属于新重算，不伪造历史。唯一修正验证由独立 preregistration_amendment01 冻结，保存本原判；不覆盖本矩阵。

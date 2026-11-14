# 数值修复预声明的可读说明

2026-10-06。既有 P2b-0 已完成而未通过；本轮是新的实际执行授权下的独立修复，历史结果不变。本目录仅使用旧 `solver_bundle_clean`、fresh A0、原始 RGB-on-A0 q0 与冻结配置。

唯一主要修复：冻结本机 SciPy 1.18.1 的 `trf.py` 副本，将 bounded TRF 的 `Delta==0 → 1` 改为 `Delta=max(1, Delta_raw)`，坐标仍为 `z=(p-p0)/scale`。原始半径约 1.43e-11，来自严格可行化的极小非零 z；修改使该原点能够执行具有实际物理幅度的步骤。1 是固定尺度坐标的单位初始半径，仅在开始设定；后续 TRF 仍按预测/真实下降比正常更新半径。

不改物理目标、健康模板、物理初值（保留 SciPy 自身严格可行化）、q0、边界、support、权重、状态尺度或预算。FD 复用 correction01 的固定 `1e-4×physical_scale`；双侧可行时用 central，触界时用 inward single side。只在独立冻结副本增加分支记录，初始残差与未加记录的实现逐字节一致。

有限矩阵共 8 次：A1/A3 × sparse LSMR/dense exact；A3 的两种方法分别做 +360° 保留弱绝对 psi prior 与 +360° 同步 prior_mean 的严格同目标对照。前者专门保留物理先验差异的证据，后者才用于严格目标等价判断。每次 max_nfev=60、ftol=xtol=gtol=1e-7；不再扫描半径、步长、预算或容差。

正式数值门槛仍为目标 blade0 同材料 q 网格平均几何差 ≤1.6667 mm。其为组间位移稳定性，不是源表面精度。不得把所有组不动、低目标或 SciPy success 当门槛通过。

源状态、源 mesh、绑定、ID、WideEval 及安装资产被系统沙箱和 Python 白名单共同隔离；6 项实际现存禁止路径读探针用于核实。启动时工作目录须为 /tmp，以避免严格沙箱拒绝 repository getcwd；两项启动操作修正发生在真实数据优化之前，机器 amendment 保留详情。

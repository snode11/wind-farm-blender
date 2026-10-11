# 数值诊断复现

从真实Git根执行，Python为已有blade_recon/.venv-trial/bin/python。读取冻结源码与旧input/solver_bundle_clean，原已残缺solver_bundle不使用。重算输出必须用新目录/复制驱动路径和OUT常量，禁止覆盖本次结果。

原有限矩阵：

```sh
PYTHONDONTWRITEBYTECODE=1 blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/scripts/diagnose_numerics.py --predeclare
/usr/bin/sandbox-exec -f outputs/blade-surface-20261006-p2b-a01/diagnostics_old_attempt02/numerics/solver_policy.sb env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/scripts/diagnose_numerics.py --execute
```

唯一修正矩阵（本次已预声明、已运行，勿覆盖；运行时使用修订amendment f7744c75635553a1acabb2da00578ec67a9bd07f73b2b730ab11502e072674ab）：

```sh
/usr/bin/sandbox-exec -f outputs/blade-surface-20261006-p2b-a01/diagnostics_old_attempt02/numerics/correction01/solver_policy.sb env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/scripts/diagnose_numerics_increment.py --execute
```

原driver SHA90a1167a8dbd894f356ca56db58131416b70eaa3975568cc3aa591bcf3eb5058；修订increment driver SHA95b8eaf54abd64579ff4b8552b5f1862176ca5e3abf0d6435662292dc991d470。修正最初预声明与driver快照保存，FD日志修复发生在任何修正优化之前。

每组result.json记录真实计数/墙钟/停止；instrumented_solver.npz保存实际调用输入、残差、Jac、gradient和尺度；result.json保存实际active_mask。residual_blocks.npz/summary保存固定尺度raw/white/robust块。information_matrices.npz/summary保存Js、Jtx、Jtq、可行profile及边界限制。postprocess_checks.json核对成本分块/执行FD侧与probe计数。

trust_region_degeneracy.json记录从保存z0、同固定Jac策略和本地SciPy CL_scaling_vector重建初始Delta；metadata不是另一轮优化。独立副本重新计算时，应同时保存这个检查，不能仅报告correction01几何均值稳定。

固定初始信赖域元数据的独立复现（没有优化）：

```sh
/usr/bin/sandbox-exec -f outputs/blade-surface-20261006-p2b-a01/diagnostics_old_attempt02/numerics/correction01/solver_policy.sb env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/diagnostics_old_attempt02/numerics/correction01/reconstruct_initial_trust.py
```

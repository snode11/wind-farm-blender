# 数值前置复现

运行目录为 `outputs/blade-surface-20261006-exec-a01/numerics`。机器协议冻结于 `preregistration.json`，8 次优化不再追加。物理算法副本、原 SciPy 与修改 SciPy 字节见 `source_manifest.json`；新 P2b 或新的输入必须另有输入冻结，不继承旧 clip 的收益结论。

已有结果的独立复核（不运行优化）：

```sh
cd /tmp
/usr/bin/sandbox-exec -f 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/numerics/solver_policy.sb' 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/numerics/check_delivery.py'
```

原本 8 次优化使用以下命令；不在原输出目录重复运行以免覆盖证据。复现应先整体复制到新 run 并显式替换 `ROOT/OUT` 及机器 manifest 的路径、SHA、沙箱权限，再冻结和运行。修改数值、输入或代码时不能沿用原冻结。

```sh
cd /tmp
/usr/bin/sandbox-exec -f 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/numerics/solver_policy.sb' 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/numerics/run_numerics.py' --execute
```

共享 `.venv-trial` 不安装、不改写。实际 SciPy 1.18.1；运行时使用 process-local 独立 vendor，而不是改写共享 site-packages。z 初始为全零，SciPy 自身严格可行化保留，初始 Delta 原公式值和修复值均保留；之后的 Delta、prediction/actual reduction、ratio、accepted 与真实物理步见逐组 `trf_trace.json`。

`nfev` 仅 optimizer fun 次数；真实调用含 Jacobian probes，见每组 result 和 instrumented_solver。终点同点独立 FD 与已有 3 个 call 的 branch 重放分别计数，不合入 optimizer calls。低残差、success、byte equality、系统隔离 PASS 均不等于新几何收益或现场精度。

`solver_api.solve_window(prepared_fitter, method='dense_exact')` 只在 `NUMERICAL_GATE_PASSED_REVIEW_ONLY` 时可用。它直接复用本矩阵的 frozen Instrumented、packed_setup 和 vendor；调用端仍须部署自己的白名单与系统沙箱，冻结同 RGB/A0/q0/support/config，并按实验公平合同统一方法。API 是 process-local SciPy patch，使用独立进程，不在多个并行线程内混用。API 未运行额外旧 clip 优化。

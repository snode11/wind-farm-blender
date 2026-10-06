# 本轮诊断复现 · 2026-10-06

最终判定为 `NUMERICAL_INCONCLUSIVE / REVIEW_ONLY`。P2b-0 实际执行原矩阵6次、唯一修正矩阵6次；没有新RGB、P2b组或盲测指标。先读 `REPORT.md`、`decision.json` 和机器协议。

## 输入、来源与环境

所有命令从 `/Users/eason/Desktop/wfcrl/wind farm RL` 执行。使用已有 `blade_recon/.venv-trial/bin/python`。NumPy2.5.3、SciPy1.18.1、OpenCV5.0.0；不安装共享依赖、不恢复全部废纸篓。

旧正式输入仅 `outputs/blade-surface-20261005-manual-a01/input/solver_bundle_clean`。旧 `input/solver_bundle` 不完整。x1/q2、x3/q3路径及SHA在 `diagnostics_old_attempt02/geometry/canonical_identity.json`；q2来自 `A2_material/result.json`。110项正式哈希校核在 `provenance/old_evidence_audit.json`。

实际算法从本run `provenance/frozen-source/blade_recon` 导入，15个模块SHA在 `frozen_source_manifest.json`。不能用将来工作区源码代替这些字节而称同一复现。数值原冻结和修订冻结在 `diagnostics_old_attempt02/numerics/preregistration*.json`，包含每个输入、源、驱动和沙箱SHA。

## 只读核验现存产物

下列仅重查保存数组，不运行优化或源评价。检查输出会覆盖本run对应核验日志/JSON；需要保留原核验时先使用新run。

```sh
/usr/bin/sandbox-exec -f outputs/blade-surface-20261006-p2b-a01/diagnostic_read_policy.sb blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/validation/check_numerical_delivery.py
/usr/bin/sandbox-exec -f outputs/blade-surface-20261006-p2b-a01/diagnostic_read_policy.sb blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/validation/check_increment_delivery.py
```

本轮实际结果：44项原证据检查PASS；60项修正trace检查PASS，同时确认六组退化在A0附近。PASS不是收敛通过。8项实际系统读探针在 `validation/isolation_probe_result.json`，17项理想测试在 `validation/window_fit_tests.log`。

## 在新目录重跑，保留本轮冻结

`prepare_reproduction.py` 只复制同一诊断驱动/冻结源码并按新目录生成沙箱；不复制源真值、不渲染、不运行优化。它拒绝已存在目的目录。本轮只对该辅助脚本做语法检查，没有实际创建复现目录或额外优化。

```sh
blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-p2b-a01/scripts/prepare_reproduction.py --name blade-surface-20261006-p2b-repro-a01
P2B_REPRO_RUN='outputs/blade-surface-20261006-p2b-repro-a01'
blade_recon/.venv-trial/bin/python "$P2B_REPRO_RUN/scripts/diagnose_old_geometry.py" > "$P2B_REPRO_RUN/diagnostics_old_attempt02/geometry_run.log" 2>&1
blade_recon/.venv-trial/bin/python "$P2B_REPRO_RUN/scripts/diagnose_numerics.py" --predeclare > "$P2B_REPRO_RUN/provenance/numerics_predeclare.log" 2>&1
/usr/bin/sandbox-exec -f "$P2B_REPRO_RUN/diagnostics_old_attempt02/numerics/solver_policy.sb" blade_recon/.venv-trial/bin/python "$P2B_REPRO_RUN/scripts/diagnose_numerics.py" --execute > "$P2B_REPRO_RUN/diagnostics_old_attempt02/numerics/run.log" 2>&1
blade_recon/.venv-trial/bin/python "$P2B_REPRO_RUN/scripts/diagnose_numerics_increment.py" --predeclare > "$P2B_REPRO_RUN/provenance/increment_predeclare.log" 2>&1
/usr/bin/sandbox-exec -f "$P2B_REPRO_RUN/diagnostics_old_attempt02/numerics/correction01/solver_policy.sb" blade_recon/.venv-trial/bin/python "$P2B_REPRO_RUN/scripts/diagnose_numerics_increment.py" --execute > "$P2B_REPRO_RUN/diagnostics_old_attempt02/numerics/correction01/run.log" 2>&1
```

修正预声明要求原矩阵仍为NUMERICAL_INCONCLUSIVE；如环境/结果已改变而不满足，脚本拒绝继续，保留新日志核对。不要原地执行本轮 `--predeclare/--execute` 覆盖冻结。现有模型/输入byte变化需另立身份，不能沿用本次结论。

原矩阵使用同2-point相对FD=1e-3、同x_scale，sparse/LSMR与dense/exact；统一psi+360不逐帧模。修正矩阵使用z=(p-p0)/scale、固定物理FD=1e-4×scale、可行单侧/中心差分，物理残差原样复用。两矩阵max_nfev60、ftol/xtol/gtol1e-7，固定同A0/q0/轮廓support/边界/先验。全部优化先冻结再跑；没有容差或h扫描。

## 测试依赖与实际口径

本轮pytest源码及必要依赖按 `outputs/cleanup-blade-surface-20261006/清理记录.json` 的精确runtime_pytest映射逐SHA复制至本run；源废纸篓和共享venv保留。复制证据在 `provenance/dependency_restore_audit.json`。

实际执行的理想测试命令：

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=outputs/blade-surface-20261006-p2b-a01/runtime_pytest blade_recon/.venv-trial/bin/python -m pytest outputs/blade-surface-20261006-p2b-a01/provenance/frozen-source/blade_recon/tests/test_window_surface_fit.py -q -p no:cacheprovider
```

结果17 passed。完整本轮测试清单为 `validation/test_manifest.json`。

真实residual调用包含优化目标和FD/Jac probes，nfev不是总调用数；墙钟仅least_squares。分块残差/J计算、独立数组复核和140次初始域metadata调用单列。`correction01/reconstruct_initial_trust.py` 是复现该metadata的同逻辑保存脚本，本轮只语法检查；实际metadata已由沙箱postprocess执行，没有再运行这个文件。

P2b区域面积指标、q_eval、20配对扰动、负例、三窗盲测和新Blender显示均NOT_RUN。需要先在另一有限开发run解决数值门槛；本文命令不启动P2b主实验。

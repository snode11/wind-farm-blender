# 求解侧复现入口

原冻结产品模块与本run旧输入/结果不可覆盖。实际复现使用新输出叶子；原脚本均拒绝覆盖已有 result/config。解释器为 `REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python`，当前run根为 `REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01`，所有solver cwd为 `/tmp`。

复现链：仅root审批 `public_input` leaf的WideInput RGB、known calibration、二维annotations及mask → 原 `p2b/scripts/prepare_input.py` → 原 `p2b/scripts/solve_a0.py`（fresh causal legacy+公开RGB唯一blade统一身份）→ `freeze_window_configuration.py`（本窗freshq0，完全继承dev设置）→ 原 `solve_groups.py`。每一步使用 `p2b/scripts/p2b_guard.py:write_policy(input_dir,out)` 产生实际macOS sandbox；成功open白名单由Python audit保存，OS拒读probe在audit之前执行。不得用仅audit hook代替OS sandbox。

五个环境变量固定1：OMP_NUM_THREADS、OPENBLAS_NUM_THREADS、MKL_NUM_THREADS、VECLIB_MAXIMUM_THREADS、NUMEXPR_NUM_THREADS。进程内SciPy vendor patch禁止API并发；有限独立进程由 `solver_process_slots.py` 全局上限3。SciPy初始Delta_raw小于1时clamp1；physical p、目标、边界、A0和所有缩放完全沿用冻结API。真实probe数单独统计，不以nfev/success/成本下降替代mean geometry gate。

开发主四组实际调用（绝对输入/输出如实记录）：

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 /usr/bin/sandbox-exec -f 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/p2b/groups/development/solver_policy.sb' 'REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python' 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/p2b/scripts/solve_groups.py' --input 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/p2b/development/input' --a0 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/p2b/development/A0/recon.json' --configuration 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/p2b/development/configuration/frozen_configuration.json' --out 'REPOSITORY_ROOT/outputs/blade-surface-20261006-exec-a01/p2b/groups/development' --mode groups
```

上面目录已完成，仅为真实命令记录，不能在同目录重跑。negative用 `--mode negatives`；两次方法对照用 `--mode single --group A1_new|A3_D12 --method sparse_lsmr`；paired用 `--mode perturbations --group A3_C4|A3_D4|A3_D12`，每个OS进程顺序20repeat、三组最大3进程。seed20261006与全部20组12ID二维offsets已在 `configuration/paired_perturbations.json` 冻结；A1每repeat逐字节复制共同baseline，新增优化0。

自动8使用 `automatic8/{input,configuration}` 与原A0，原driver `--mode single --group A3_automatic`；先geometry_frozen再 `q_eval_common_RGB.py --input <original reviewed8 canonical input> --group <auto frozen group>`，供root用同12首pixel评价，原float32 q_eval保留。

长32使用已批准canonical `long32/input` 与 `input_bundle_missing.py` 显式false/null解析器；其余solver数学不变。`run_automatic_long_queue.py` 固定auto→fresh32A0→精确first8state check→long配置freeze→2dense+2sparse矩阵→source-free method mean geometry gate。队列调度v2提前利用C4/D4已完整20释放槽，最多2long进程；D12paired保留第三槽。I/O-only longwrapper按group分别写read audit，旧wrapper SHA副本在helper_history，实际current SHA在long冻结prereg和helper_preparation_io_amendment_audit。若long mean超过1.667mm，保留四组实际结果为NUMERICAL_INCONCLUSIVE，不重扫。

正式三窗尚需root的开发20D12门槛/δ及最终公有输入一次冻结授权。root填 `formal_test_jobs_template.json` 的三窗路径、planned frame/time、全部manifest/annotation/copyaudit/RGB/mask SHA和capture-approved perframe camera合同；状态改为 `ROOT_AUTHORIZED_FORMAL_TEST_SOLVES` 后调用 `run_formal_tests.py --root-approved-plan <authorized exact metadata file> --max-parallel-windows 1|2`。每窗本窗freshA0/q0，固定4组；全三窗12组state/result/trace/common12q总SHA写 `p2b/groups/formal_tests/all12_geometry_frozen.json` 后root才开启任何正式测试几何评分。wrapper正常沙箱smoke与5种fail-closed合同拒绝测试均无数据优化；v2 static T假设失败保留，v3逐值使用已审批每帧T、不改输入或放宽容差。

只读复算入口 `postprocess_solver_group.py` 输出逐点CSV/原始、白化、鲁棒块及scaled梯度，验证residual byte equality；`build_solver_report.py` 只汇总自己冻结结果，生成REPORT/solver_runs.csv，不读取独立评价。

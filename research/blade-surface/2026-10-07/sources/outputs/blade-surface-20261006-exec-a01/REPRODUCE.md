# 本轮复现与只读核验

真实Git根是 `/Users/eason/Desktop/wfcrl/wind farm RL`。本轮独立目录 `outputs/blade-surface-20261006-exec-a01`，Blender实际版本5.2.1，数值runtime为原 `blade_recon/.venv-trial/bin/python`；官方CoTracker3和pytest安装在本轮独立目录，共享依赖/日用扩展无安装操作。已运行目录和失败尝试全部保留，不直接重跑覆盖。

## 先核对身份

`experiment_protocol.json` 是正式一次冻结，SHA及freeze时间在 `provenance/formal_protocol_freeze.json`。原staged协议保留。solver仅消费独立公开plan `p2b/development/formal_test_jobs_root_approved.json`，它没有δ、source mapping、源坐标或根评分。每窗19个canonical输入文件逐一SHA、逐帧K/T/time合同，配合fresh A0/q0和原冻结配置。

总文件身份见 `ARTIFACT_SHA256.json`（不包含自身及依赖缓存/实时lease/pycache），保护基线见 `provenance/preservation_end.json`。第三方代码/权重/runtime身份分别在 `tracking/` 与 `runtime_tracking/`，权重不由大文件清单重复计算。只读检查不启动训练、渲染或优化。

```sh
WF_ROOT='/Users/eason/Desktop/wfcrl/wind farm RL'
WF_RUN="$WF_ROOT/outputs/blade-surface-20261006-exec-a01"
cd "$WF_ROOT"
blade_recon/.venv-trial/bin/python "$WF_RUN/evaluation/scripts/evaluate_surface.py" --selftest
```

17个现有窗口拟合测试已运行通过，见 `validation/software/decision.json` 及日志；数值保存数组/分支重放104项、实际禁读6项见 `numerics/test_manifest.json`。盲判定器30个自造正常/异常fixture与旧反例在 `tracking/blind_decision_review/`，没有读取实际评分。

## 本次实际命令和输入顺序

以下是本次执行记录的入口，供审计，不应向现有输出目录再次执行。完整参数与阶段日志在各 `run.log` 和各包REPRODUCE。完整重做应建立独立新run，对脚本固定RUN路径进行统一迁移并记录新身份；任何估计器修改后，现有测试须降级为开发，不能调参后继续叫盲测。

1. `numerics/`：唯一预声明隔离TRF修复、实际8个有限对照、同目标ψ+360/weakprior/strictprior分开；读许可及过程内FD/API详见该包REPORT、REPRODUCE和 `solver_api.py`。
2. `capture/capture_configuration_freeze.json`：保存4K相机/采样/7算法/source身份。原健康源copy，仅新增颜色junction。scout按有限RGB条件选固定3窗；没有源phase或几何score挑选。开发公开RGB逐格审核后，按 `p2b/scripts/prepare_input.py` 复制为canonical whitelist。
3. `p2b/development/`：fresh A0、共同12 q0、四主组、四负对照、两个额外sparse method、每个C4/D4/D12真实20配对求解。A1无点20基线只复用SHA，没有虚构优化。全部60结果在 `paired/all60_frozen.json`，真实调用/残差块/逐点/trace保留。
4. `evaluation/scripts/run_surface_batch.py`：在任何该batch真值读取前写共同recon/query hash；开发主6、方法/负对照、C4/D4的40与D12的20评分分目录保留。`development_all_sensitivity_inputs/path_map.json` 明确68个summary；`development_sensitivity.py` 产生有限δ，再由 `freeze_formal_protocol.py` 一次写正式协议。
5. 原冻结正式捕获入口：

```sh
blade_recon/.venv-trial/bin/python "$WF_RUN/capture/run_formal_capture_wrapper.py" --global-freeze "$WF_RUN/experiment_protocol.json"
```

三个固定窗口143.0–143.7、148.5–149.2、165.0–165.7s的48实际RGB、固定候选/mask产物和root逐格审核已完成。候选/annotation先冻结，再独立 `evaluate_rgb_observation_gate.py` 评价；三窗1px定位门槛均FAILED，不能改坐标或重选。继续固定12组仅作为原设置诊断。

6. 原正式求解命令：

```sh
blade_recon/.venv-trial/bin/python "$WF_RUN/p2b/development/run_formal_tests.py" --root-approved-plan "$WF_RUN/p2b/development/formal_test_jobs_root_approved.json" --max-parallel-windows 1
```

每窗sandbox fresh A0→fresh q0/精确公开合同→四组→共同原RGB query→逐点/残差诊断。全球slot锁最多3 solver进程、BLAS线程1；本轮long占2槽时正式流水线1槽。原输入t为sim_t−117的精确浮点值，不能自行round后当合同：第一次root plan错误round在优化前拒绝，输入未改，失败记录在root公开审批。

7. 12组共冻结 `p2b/groups/formal_tests/all12_geometry_frozen.json` 后，才启动独立评分：

```sh
blade_recon/.venv-trial/bin/python "$WF_RUN/evaluation/scripts/run_surface_batch.py" --jobs "$WF_RUN/evaluation/formal_test_surface_jobs.json" --out "$WF_RUN/evaluation/formal_tests" --workers 2
blade_recon/.venv-trial/bin/python "$WF_RUN/evaluation/scripts/audit_formal_integrity.py"
blade_recon/.venv-trial/bin/python "$WF_RUN/evaluation/scripts/decide_blind_test.py" --metrics "$WF_RUN/evaluation/formal_tests" --protocol "$WF_RUN/experiment_protocol.json" --observations "$WF_RUN/evaluation/test_observation_gates.json" --integrity "$WF_RUN/evaluation/formal_integrity.json" --common-freeze "$WF_RUN/evaluation/formal_tests/geometries_before_truth_read.json" --out "$WF_RUN/evaluation/blind_test_decision"
```

门槛失败时判定器写FAILED/null并退出1是有效研究结果，不能假称软件崩溃或改数据重试。逐region/方向/帧原数组、完整192点分母及有限diagnostic保留；不根据有效子集排名。所有12重建/q的24项共同hash真实比对，chronology来自冻结后才dispatch的执行链，不依赖mtime。

8. 自动/长窗：官方固定commit/权重、首次RGB query、384×512工作网格、support grid与6迭代，见 `tracking/REPRODUCE.md`。四个完成case及失败尝试分别记录。自动8 geometry与原审核RGB同8对照由root汇总；固定32为A1/D12×dense/sparse四组，原8公开数据/A0完全相同、50null不补，数值门槛先核验后独立评价。入口 `run_automatic_long_queue.py`、`long32/check_geometry_long.py` 与 `evaluation/scripts/summarize_long32.py`。
9. 局部：健康/仅颜色/50mm凹坑×3时刻×2相机，原18元数据失败保留，attempt02正式18；只修序列化，see `local_defect/REPRODUCE.md`。健康/凹坑RGB等价→门槛停止，局部恢复未运行，深度null。
10. 外观/显示：`appearance/REPRODUCE.md` 和 `validation/display/REPRODUCE.md`。原reader新schema失败保留，只有独立内存全UNKNOWN adapter通过两包各77软件检查，save/reopen仍需adapter。完整静态v2文件是 `validation/display/comparison/comparison_full_frame_v2.blend`，不代表动态工程或安装版验收。

## 权限和失败证据

source/blend/材料绑定/投影/完整source camera和WideEval保留在 `private_evaluation/`，capture/evaluation许可读取。solver、initializer、tracker和texture采取相匹配的macOS sandbox与Python repository whitelist，模板来自冻结健康公共声明，query不可从truth初始化。当前正式现存5路径probe在 `p2b/development/formal_current_private_probes/actual_system_denials.json`，kernel在Python hook安装前拒绝、0bytes。启动失败审计缺日志的范围、旧错误侧、partial UI、schema失败、float序列化失败、静态T假设错误均原样保留。

最终 `REPORT.md`、`decision.json`、`resource_ledger.json` 与保护审计说明已完成、门槛失败、未执行和不成立范围。没有FAST.Farm/MAPPO重跑、训练、commit/push/release、RGB上传或日用扩展安装。原dirty和历史110正式文件保持；94recoverable删除未恢复。所有结论仅REVIEW_ONLY。

# P2b 求解侧开发执行报告

本报告仅汇总求解侧许可输入与冻结结果；独立几何收益由 root 在结果哈希后评价，求解侧未读取 source、绑定、ID、WideEval 或评价分数。数值方法门槛与几何精度分开。

当前实际开发主组 4/4，负对照 4/4，方法对照 2/2，配对扰动 60/60，自动8 1/1，长32 4/4。A1 20次扰动因无点目标逐字节复用相同基线，状态明确 REUSED_IDENTICAL_NO_POINT_OBJECTIVE，新增优化0次。

开发四组共用 fresh A0、同12个原首帧RGB初始化q0、32活动状态、每帧其他9维固定A0。C4=[M04,M05,M06,M07]；D4=[M00,M05,M06,M11]；D12=M00…M11。主目标/模板/物理边界/weakprior/FD/初始radius1/60budget/1e-7终止冻结。点块按实际visible点数统一标准化，sigma=0.39115355830238574原像素来自公开RGB重复提取矩阵与1/12像素格模型下限；这不是现场噪声或置信区间。

| 类型/组 | nfev | 真实residual调用 | 秒 | scaled optimality | 活跃边界 | q_eval未知/12 |
|---|---:|---:|---:|---:|---:|---:|
| development_main/A1_new | 14 | 876 | 41.38 | 0.704656 | 5 | 0 |
| development_main/A3_C4 | 13 | 1028 | 53.46 | 1.97258 | 3 | 0 |
| development_main/A3_D4 | 17 | 1351 | 70.07 | 0.504215 | 3 | 0 |
| development_main/A3_D12 | 15 | 1661 | 89.86 | 0.719359 | 3 | 0 |
| negative/empty_A3 | 14 | 876 | 43.35 | 0.704656 | 5 | 0 |
| negative/half_swap | 56 | 5494 | 296.21 | 9.19683 | 6 | 0 |
| negative/wrong_blade | 28 | 2231 | 117.50 | 67.9349 | 27 | 0 |
| negative/wrong_side | 60 | 6210 | 357.57 | 7271.21 | 6 | 11 |
| development_sparse_sensitivity/A1_new | 15 | 959 | 47.39 | 3.77696 | 1 | 0 |
| development_sparse_sensitivity/A3_D12 | 27 | 3049 | 165.48 | 1.67917 | 0 | 0 |
| automatic8/A3_automatic | 16 | 1792 | 96.88 | 20.0505 | 2 | 0 |
| long32_dense/A1_new | 24 | 4604 | 959.57 | 6.20573 | 1 | 0 |
| long32_dense/A3_D12 | 16 | 3964 | 891.37 | 9.08616 | 0 | 0 |
| long32_sparse/A1_new | 31 | 6413 | 1283.15 | 19.2021 | 0 | 0 |
| long32_sparse/A3_D12 | 29 | 8235 | 1774.32 | 38.9831 | 0 | 0 |

SciPy nfev不含数值J的真实probe；真实调用数、每步Delta/实际physical step/branch摘要、active_mask与optimality均已保存。ftol停止和success不证明gtol驻点、全局最优或几何收益。不得把不动当通过。所有已生成 residual_blocks/input_points 的复算 residual 与求解冻结字节相同。

开发方法对照状态 PASS，A1 mean=0.127638mm、D12 mean=0.491322mm；原门槛 mean≤1.666667mm。完整max/各叶片统计见 method_sensitivity/geometry_comparison.json。

长32状态 NUMERICAL_INCONCLUSIVE。它保留334visible与50null计划点，fresh causal A0前8 state byte-equal原8，全部334可见射线仅支持blade0。只改变长度（128活动状态+24q）与显式false/null解析器；有限2dense+2sparse，其余配置逐值继承。失败则保留 NUMERICAL_INCONCLUSIVE，不重扫。

自动8同原RGB/A0/q0/settings/12queries，仅轨迹来源为冻结官方自动后端。几何先hash后以原审阅RGB首帧12pixels生成 q_eval_common_RGB；float32自动query版本q_eval.json原样保留。

公开相机T逐帧变化：v2静态T假设被公开dev标定否定，失败与旧版本保留。v3逐值匹配已审批每帧public T，每frame/time/K/T SHA精确校验；K逐值==dev anchor，A0帧/时刻逐值匹配。正常沙箱smoke及5个不匹配拒绝案例通过，无新增数据求解。

系统级拒读：旧4 probes保留；当前source_geometry、source_material_bindings、source_material_projection_truth、WideEval/000042及source_capture_copy.blend现存文件实际open均EPERM，发生于安装Python audit之前，内容读取0字节，见 current_private_probes/actual_system_denials.json。成功许可read清单逐进程保存。

三窗正式测试按本窗公开RGB→freshA0→freshq0→固定四组；每窗顺序、最多2窗流水线、跨全部solver进程上限3。只有全部三窗12组state/result/trace/common12q的总SHA冻结后才允许root任何测试评分，求解侧不读评分。见TEST_WINDOW_RECIPE.md、formal_test_jobs_template.json、test_helper_preparation.json。

完整数值表 solver_runs.csv；主组/negative/method/paired各目录保存result/recon/trace/geometry_frozen/q_eval与诊断。数值修复母版详见 ../../numerics/{REPORT.md,REPRODUCE.md,decision.json}。


## 实际正式三窗求解交付

固定三窗12组全部实际求解与共同q_eval冻结。root独立观测gate已告知FAILED，因此仅为原设置诊断；没有调整公开输入、重选、扩大60预算或读取root分数。正式当前五个私有文件kernel探针EPERM/0bytes通过，见 formal_current_private_probes/actual_system_denials.json。每窗流水线1、全球solver cap3。完整正式终止/nfev/cost/calls与固定9参数检查由root只读 audit记录在 ../../evaluation/formal_integrity.json；未收敛组保留。

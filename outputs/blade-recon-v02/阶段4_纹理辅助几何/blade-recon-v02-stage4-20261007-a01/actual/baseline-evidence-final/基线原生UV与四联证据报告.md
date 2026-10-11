# 轮廓解析模板基线：原生 UV 与同位置证据

2026-10-07。本目录是新基线 `A_contour_analytic` 的**保存后原生显示坐标合同证据**；独立几何对应仍 `FAILED_GATE`。没有修改算法、冻结求解、原 `metrics.json`、用户已有 Blender 窗口或原 blend。

`evaluation/actual-observations-frozen.json` 已确认是原16条RGB边界：Patch/Crack各4个fit时刻，Holdout/HoldoutPatch各4个旧独立视角。SHA为 `b8cbe518079673d1515f235b15c34bf3e20957c1bc128118726275f252d9b9b3`。最终评分另包含8个锁定独立观测，共24条；未知域审计使用完整 `final-scores/all-observations.json`，避免遗漏Locked相机记录。原16条原生face/UV审计只证明原16条记录的显示合同，并不宣称新增Locked视角的原生审计。

独立后台进程以 `--background --factory-startup` 只读打开 `baseline-native/独立纹理辅助重建.blend`，Blender5.2.1，无插件导入或源真值网格读取。逐条固定首次face/bary与保存原生三角面/UV/动态shape-key网格追溯16/16通过，最大原生UV误差 `8.0344e-09`，最大相机投影差 `.001154` 原像素，最大模型坐标差 `3.572e-06m`。packed纹理SHA逐叶片与融合PNG一致；锚点对应图集有效比例Patch/Crack均1。

这些是同一重建输出从外部算法到原生保存网格的内部一致性，不能替代RGB物质对应、三维几何定位或缺陷精度。基线recon SHA为 `0f214d235b31e0ea11b749f87fd92626c426e0d0e01597f308a125e560741fe2`，保存blend SHA `25588d90b71999d2841e7e50d07fcc19164285cb6ab9c76913712766789da81b`。实际安装扩展仅记录其路径和SHA，未用于此次独立原生builder；不能把本审计写成安装版新算法验证。

| 基线原像素评分 | training非锚 | 旧独立 | 新Locked独立 | 全部独立 | 训练frame45 P95 |
|---|---:|---:|---:|---:|---:|
| Patch | 0/3 | 0/4 | 0/4（3缺失） | 0/8（3缺失） | 12.554px |
| Crack | 0/3 | 0/4 | 1/4 | 1/8 | 13.616px |

旧评分机器字段 `planned_independent_observations=11` 实际是3个training非锚加8个heldout的旧聚合名字。它不能称11个独立观测。新v2四联图与evidence.json使用主流程summary的正确独立8与训练3分母，绿色称原RGB detector，不称锚点/训练frame45为独立留出。

`metrics-with-unknown-domain.json` 是从冻结原评分派生的独立域审计，原 `metrics.json` 保留。8个fit边界与投影边界在已声明域中known fraction均1；16个heldout没有fit轮廓域合同，fraction保持null，全部像素与3个Patch缺失保留在分母。该派生审计没有改变任何观测accepted值，完整实例既有失败没有提升。

本目录两张四联图依次为原RGB训练锚点、同face/bary图集区、保存原生Blender同位置显示和训练frame45原RGB错位。原RGB与图集值没有增强；未知图集以棋盘格显示。16个native trace NPZ、两份采样NPZ及 `native-binding-audit.json`/`evidence.json` 保留浮点坐标与各文件SHA。旧 `baseline-evidence` 保留，此目录为正确分母v2版本。

实际命令与进程日志保存在：

- `runs/baseline-native-binding-audit-final.json` 与同名 `.log`：factory后台只读原生审计，exit0，.669s。
- `runs/baseline-unknown-domain-audit.json` 与同名 `.log`：完整24条未知域审计，exit0。
- `runs/baseline-compose-evidence-final.json` 与同名 `.log`：`compose_actual_evidence_v2.py --condition A_contour_analytic`，exit0，.423s。

上述JSON以结构化command数组记录完整可复现参数和实际cwd；重复运行应使用新的输出/record目录，不能覆盖冻结评分或旧证据。显示合同通过、画面可读、成功求解或锚点纹理有效均没有改变本基线的 `FAILED_GATE`。

# 叶片表面重建：研究边界与设计资料

项目仓库：[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。本目录描述共享材料点、窗口几何与外观的设计合同；实际结果保存在独立run。它与 `blade_recon0.2` 的局部材料点实验是不同研究，不能混用输入或评分。

## 当前结果

| 证据对象 | 状态 | 结论边界 |
| --- | --- | --- |
| attempt02 | `COMPLETED_REVIEW_ONLY` | 外观、固定绑定与共享q窗口软件有记录；几何收益未建立 |
| `blade-surface-20261006-p2b-a01` | `NUMERICAL_INCONCLUSIVE / REVIEW_ONLY` | 新RGB=0，未进入主采集；[机器判定](../../../outputs/blade-surface/02_P2b前置诊断/decision.json) |
| `blade-surface-20261006-exec-a01` | `COMPLETED_WITH_NEGATIVE_OR_GATED_RESEARCH_RESULTS / REVIEW_ONLY` | 共同数值前置门通过，P2b三窗 `FAILED_GATE`，正式主增益null |
| 自动跟踪与32帧联合几何 | 长身份延续 `FAILED`；长窗 `NUMERICAL_INCONCLUSIVE` | 官方后端已运行；自动轨迹几何收益未建立 |
| 局部缺陷与外观 | `OBSERVATION_GATE_FAILED`；恢复 `NOT_RUN`、depth=null | 当前flat材质健康/凹坑RGB相同；外观/显示需独立unknown adapter，工程播放、现场精度与健康能力未验收 |

实际执行入口：总报告（本地文件：`outputs/blade-surface/03_后续独立实验/REPORT.md`）、[机器判定](../../../outputs/blade-surface/03_后续独立实验/decision.json)、[正式协议](../../../outputs/blade-surface/03_后续独立实验/experiment_protocol.json)、[复现说明](https://github.com/snode11/wind-farm-blender/blob/2c94453226bd4e31206749e579bca32c13d0ea36/research/blade-surface/2026-10-07/sources/outputs/blade-surface-20261006-exec-a01/REPRODUCE.md)。原0.2mm叶尖精度门仍为 `FAILED_GATE`。前端发布、播放与同源纹理显示不改变这些研究结论。

## 设计与原始资料

- [材料点与几何合同](叶片表面重建_Codex交接_2026-10-05.md)：输入、模型职责、评价和停止规则。
- [实施范围说明](先发给Codex_执行说明.md)：未来独立实验的冻结与执行要求。
- [P2b协议草案](叶片表面重建_方案v3修订资料_2026-10-06/P2b_experiment_protocol_DRAFT_v3.json)：未决字段保留null，不替代实际run正式协议。
- [原方案v2](叶片表面重建_方案v3修订资料_2026-10-06/原方案_v2_未改动.md)、[原执行说明v2](叶片表面重建_方案v3修订资料_2026-10-06/原执行说明_v2_未改动.md)：冻结来源原文，保持字节身份，不作为当前执行指令。
- [原件与恢复索引](叶片表面重建_方案v3修订资料_2026-10-06/README_清理后.md)、[迁移映射](迁移记录_2026-10-06.json)：用于定位原始来源和恢复路径。

源码、构建ZIP、公开附件、日常安装与运行窗口分别核验。原始证据不得用当前源码覆盖，新的实验使用独立输出目录并保留失败分母及冻结阈值。

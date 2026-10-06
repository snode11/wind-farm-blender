# 叶片表面重建：方案与交接资料

项目仓库：[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。2026-10-07 已按当前目录补充[网页 GPT 交接材料](../../../outputs/web-gpt-reconstruction-handoff-20261005/README.md)，其中包含本地新增模块快照和三轮实验报告；GitHub main 的缺项见交接核对记录。

## 2026-10-06 当前状态与阅读顺序

本目录保存方案、交接和文档迁移记录；实际实验及冻结结论保存在各自 `outputs/` run。首先阅读最新[实际执行总报告](../../../outputs/blade-surface-20261006-exec-a01/REPORT.md)、[机器判定](../../../outputs/blade-surface-20261006-exec-a01/decision.json)和[复现步骤](../../../outputs/blade-surface-20261006-exec-a01/REPRODUCE.md)，再读下面保留的 v3 设计。文件名中的 2026-10-05 是交接日期，不是最新结果日期。

| 记录 | 实际状态 | 结论边界 |
| --- | --- | --- |
| 旧 attempt02 | `COMPLETED_REVIEW_ONLY` | 外观/绑定/窗口软件已有记录，几何精度未验收 |
| P2b-0，`blade-surface-20261006-p2b-a01` | `NUMERICAL_INCONCLUSIVE / REVIEW_ONLY` | 该 run 未开始 P2b 主采集，新 RGB=0；[原判定](../../../outputs/blade-surface-20261006-p2b-a01/decision.json)不改写 |
| 独立新 run，`blade-surface-20261006-exec-a01` | `COMPLETED_WITH_NEGATIVE_OR_GATED_RESEARCH_RESULTS / REVIEW_ONLY` | 共同数值前置门通过，P2b 三窗最终 `FAILED_GATE`，正式主增益 `null` |
| 新 run 自动跟踪与 32 帧联合几何 | 自动长身份延续 `FAILED`；长窗 `NUMERICAL_INCONCLUSIVE` | 官方后端实际运行，自动轨迹几何收益未建立；不再用方案阶段的 `NOT_RUN` 描述当前 CoTracker 研究 |
| 新 run 局部缺陷与外观 | `OBSERVATION_GATE_FAILED`；局部恢复 `NOT_RUN`、depth=`null` | 当前 flat 材质健康/凹坑 RGB 相同；外观/Blender 软件完成但需独立 unknown adapter，未验收工程播放、现场精度或健康能力 |

v3 方案、草案 JSON 和原 v2 备份保留编制时状态、未决 `null` 及历史权限文字；后续实际执行以各 run 的授权、正式协议、报告与判定为准。本次文档对齐没有重跑实验、改变冻结阈值、产品源码或安装版。NREL 产品当前为[0.3.16 ZIP](../../blender/releases/0.3.16发布核对.md)，该发布不包含本次表面研究的精度验收。

## 方案修订与迁移阶段（历史记录）

目录原用途：未实施的研究方案及修订交接；按项目规则放在 `docs/proposal/`。2026-10-06 从 Downloads 移入，原位置不留重复副本。以下“尚未执行”只对应迁移和方案修订阶段。

- [保留的方案 v3](叶片表面重建_Codex交接_2026-10-05.md)：文件名保留原交接日期，设计正文版本为 2026-10-06 v3，顶部已列后续实际状态。
- [原方案修订说明](先发给Codex_执行说明.md)：记录当时仅方案修订的范围；当时 P2b 尚未运行。
- [P2b协议草案](叶片表面重建_方案v3修订资料_2026-10-06/P2b_experiment_protocol_DRAFT_v3.json)：待冻结字段保持null。
- [原方案v2备份](叶片表面重建_方案v3修订资料_2026-10-06/原方案_v2_未改动.md)与[原说明v2备份](叶片表面重建_方案v3修订资料_2026-10-06/原执行说明_v2_未改动.md)：历史文本，不能当当前执行指令。
- [修订资料清理说明](叶片表面重建_方案v3修订资料_2026-10-06/README_清理后.md)及[迁移记录](迁移记录_2026-10-06.json)。

迁移当时已有 attempt02 仍为 REVIEW_ONLY、几何精度未验收，P2b-0/P2b 是未执行方案。该次迁移只移动文档和更新路径，没有启动诊断、捕获、求解、依赖安装或发布；后续两轮实际执行分别见顶部状态表和下方记录。

实测资料保持在原run：

- [正式报告](../../../outputs/blade-surface-20261005-manual-a01/REPORT.md)
- [复现步骤](../../../outputs/blade-surface-20261005-manual-a01/REPRODUCE.md)
- [清理与恢复记录](../../../outputs/cleanup-blade-surface-20261006/清理记录.md)

修订hash清单和清理JSON保存当时的路径与字节身份；迁移后的路径见本目录迁移记录。源码、正式结果、安装版和废纸篓归档没有随此次文档迁移改变。


## 2026-10-06 实际执行补充

此段记录来源对话后续明确授权的实际执行；上文 DOCUMENT_UPDATE_ONLY 是此前方案修订阶段，原v3方案/协议草案及v2备份保留历史身份。新授权与计划见[本轮执行记录](../../../outputs/blade-surface-20261006-p2b-a01/plan_and_authorization.md)。

P2b-0 已完成：旧正式证据/四种x-q交叉/同网格位移/受约束信息诊断，以及原6次+唯一修正6次数值对照。最终为 **NUMERICAL_INCONCLUSIVE / REVIEW_ONLY**；P2b 主采集未启动（新RGB=0），未执行盲测或声称几何收益。详见[总报告](../../../outputs/blade-surface-20261006-p2b-a01/REPORT.md)、[复现](../../../outputs/blade-surface-20261006-p2b-a01/REPRODUCE.md)、[机器判定](../../../outputs/blade-surface-20261006-p2b-a01/decision.json)、[当前执行协议](../../../outputs/blade-surface-20261006-p2b-a01/experiment_protocol.json)。旧attempt02保持COMPLETED_REVIEW_ONLY。


## 2026-10-06 新run完整实际执行

来源用户后续明确授权的独立run `blade-surface-20261006-exec-a01` 已完成所有可执行冻结实验；此前方案阶段与P2b-0保留历史身份。共同数值门槛经一次隔离TRF修复通过，实际开发四主组/四负对照/60配对扰动、三固定窗×四组12诊断求解与独立评价全部完成。三个正式定位gate均超冻结1px，P2b最终 **FAILED_GATE / REVIEW_ONLY**，主增益为null，没有重选或放宽。

独立官方自动后端四个case完成，长身份延续FAILED；同8自动轨迹几何对照未建立收益。固定32帧四次联合几何及数值资格为 **NUMERICAL_INCONCLUSIVE**，失败不重扫。局部健康/颜色/50mm凹坑对照及保留视角验证完成；当前flat材质下健康/凹坑RGB相同，OBSERVATION_GATE_FAILED，局部恢复NOT_RUN、深度null。外观/Blender软件检查与完整静态v2实际完成，需要独立unknown adapter；未验收工程播放、现场精度或健康能力。

见[总报告](../../../outputs/blade-surface-20261006-exec-a01/REPORT.md)、[复现](../../../outputs/blade-surface-20261006-exec-a01/REPRODUCE.md)、[机器判定](../../../outputs/blade-surface-20261006-exec-a01/decision.json)、[正式协议](../../../outputs/blade-surface-20261006-exec-a01/experiment_protocol.json)。原source/replay/blend、旧110正式文件与dirty基线保留；未训练/重跑MAPPO、commit/push/release、上传RGB或改日用安装。

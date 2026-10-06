# 叶片表面三维重建：2026-10-07 研究交接

项目唯一仓库：[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。当前 [研究入口](https://github.com/snode11/wind-farm-blender/tree/main/research/blade-surface/2026-10-07) 提供现有算法与三轮实验的讨论材料，网页 GPT 可以直接读这个目录。

本次发布包含交接说明、71 个位于 `sources/<原项目相对路径>` 的原字节研究源码/报告文件、带行号的源码文本，以及 ZIP、来源清单和检查记录。产品 main 的 manifest 保持 **0.3.13**；根目录 `blade_recon/`、`wfrl/nrel_reconstruction/` 没有通过这次发布同步。本目录是研究快照，安装 ZIP、产品源码、本地磁盘与实验冻结实现要分别判断。

## 阅读顺序

| 文件 | 用途 |
| --- | --- |
| [00_发给网页版GPT的提问.md](00_发给网页版GPT的提问.md) | 可直接粘贴给网页 GPT 的任务，要求先读现状与实验 |
| [01_项目背景与GitHub本地差异.md](01_项目背景与GitHub本地差异.md) | 目标、六个表面模块、产品/研究快照的区别 |
| [03_实验进展与下一轮讨论依据.md](03_实验进展与下一轮讨论依据.md) | 三轮状态、失败门槛、下一轮需判断的问题 |
| [02_本地关键源码.md](02_本地关键源码.md) | 带原路径、行号与 SHA 的关键源码文本 |
| [sources/](sources/) | 71 个原字节源码/报告，沿用原项目相对路径 |
| [本地源码参考包.zip](本地源码参考包.zip) | 便于作为附件提供的讨论包；内部文档保留本地建包时身份，当前公开导航以本目录 MD 为准 |
| [snapshot_manifest.json](snapshot_manifest.json) | 来源路径、字节数、SHA 与本地快照身份 |
| [packaging_check.json](packaging_check.json) | 本地 ZIP/来源字节检查记录 |
| [github_alignment.json](github_alignment.json) | **推送前** GitHub 基准；旧 main SHA 与缺项不代表发布后的研究目录 |

先读 01、03，再使用 00 提问；讨论实现细节时阅读 02 和对应 `sources/` 文件。网页模型若不能读取某一文件，应明确指出，再按需提供 MD 或 ZIP 附件，不要假定它已读完仓库。

## 已有能力与最新结论

旧逐帧轮廓算法仍保留，六个表面模块已实现显式 RGB/标定输入、固定材料 q 绑定、跨帧共享 q 的公平窗口求解、原 RGB atlas/sidecar 和独立 Blender 显示。当前整体状态没有裂缝/凹坑局部形状参数，外观与显示通过不能替代几何或健康监测验收。

| 实验 | 正式结论 |
| --- | --- |
| [10 月 5 日 attempt02](sources/outputs/blade-surface-20261005-manual-a01/REPORT.md) | `REVIEW_ONLY`；外观/绑定/联合求解已运行，0.337 mm 的均距差未建立有意义收益 |
| [10 月 6 日 P2b-0](sources/outputs/blade-surface-20261006-p2b-a01/REPORT.md) | `NUMERICAL_INCONCLUSIVE`；当轮未开始主采集、新 RGB=0，不能沿用为后续状态 |
| [10 月 6 日 exec-a01](sources/outputs/blade-surface-20261006-exec-a01/REPORT.md) 与 [总判定](sources/outputs/blade-surface-20261006-exec-a01/decision.json) | 短窗数值前置通过；P2b `FAILED_GATE`、主增益 null；长身份失败；32 帧数值不确定；局部观测失败，恢复 `NOT_RUN`、depth=null |

最新外观/Blender 软件检查通过需要 **显式 unknown adapter**；legacy reader 直接读取缺 `observed_sections` 的新 recon 仍失败，保存重开仍需该 adapter。没有日用兼容、现场几何精度、实时播放或健康诊断验收。本次只发布已有研究材料，没有重跑实验或把失败判定改成成功。

## 材料范围与复现限制

真实 RGB 已经生成，exec-a01 共 197 张实际实验 capture RGB；核心输入和结果仍在本地。此公开小快照不含全部 4K RGB/mask/标定、轨迹/逐帧大数组、视频、`.blend`、CoTracker 权重或临时环境。需要图像分析时，请按 run、相机、时间和文件类型索取已保留材料，不能写成尚未生成。

三轮 [存储状态补充](sources/outputs/blade-surface-20261006-exec-a01/LOCAL_STORAGE_STATUS_20261006.md) 说明后续清理边界。`sources/` 中原正式报告、协议与判定保留原文，其中指向未收录历史图像、场景、日志或其它附件的链接不保证可在这个目录打开；报告原文中的“全部保留”与旧环境命令也不代表当前全部文件仍在。

清理记录、历史上传回执、修改前备份与完整历史环境没有收录，不能从本快照推定存在某个项目恢复 Release。推送前正确仓库的 `blade-surface-experiments-20261006` tag/release 查询均为 404；本次提供的是 GitHub 研究目录。讨论下一轮可以复用这些证据，完整复现仍需要补齐实际输入、冻结环境与运行条件。

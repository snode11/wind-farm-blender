---
name: windfarm-blender-dev
description: Use when developing, debugging, testing, or documenting Wind Farm Blender's native frontend, cameras, replay, Blender bridge integration, or extension packaging and installation.
---

# Windfarm Blender 开发

用于本仓库的原生 Blender 前端及扩展交付。从实际 Git 根目录工作，遵守 [AGENTS.md](../../../AGENTS.md)。链接相对本 skill 目录；专题中的模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 按任务选专题

先确定用户要改变的行为、运行对象和可观察的完成条件，再读取相关专题。混合任务读取所涉及的专题；不默认加载全部参考。

| 任务 | 按需读取 | 默认验证范围 |
| --- | --- | --- |
| 文案、操作说明、项目文档或 skill | 本文及对应说明；[静态校验](references/verification.md#纯文档与-skill) | frontmatter、链接、路径及要求一致性 |
| WFRL 首页、手动姿态、柔性运动、播放、场景资源或性能 | [场景、运动与回放](references/scene-playback.md) | 受影响的宿主／原生检查；完整播放或性能按任务另验 |
| Down、机舱／三相机、预览采集、重建纹理或缺陷 | [相机、资源与重建](references/cameras-reconstruction.md) | 几何／状态、原生窗口和 GPU 采集分别判断 |
| 雷达、曲线、单位、尾流、Bridge 或连接恢复 | [测量、后端与协议](references/measurement-backend.md) | 生产／消费合同、场景归属、有效性及生命周期 |
| PNG 后台动画、Down 视频或冻结试验作业 | [视频与后台渲染](references/video-jobs.md) | 输出、最终状态、进程收尾、真实媒体及所需恢复范围 |
| 扩展构建、安装核对或正式播放测量 | [维护与验证入口](references/verification.md) | 明确源码／ZIP／安装对象，选择匹配的环境和证据 |

`wfcrl-env-me` 环境或策略算法任务按其工作目录和规则处理；本 skill 只覆盖它与 Blender 的接入。网页风场实验室按[独立方案](../../../docs/proposal/platform/风场网页实验室_第一版方案与分工汇总.md)处理，不默认套用原生面板或扩展流程。

## 共通约束

- MAPPO 离线回放复用正式包，无需 Bridge、FAST.Farm 或网络；界面、相机、回放和文档任务不触发求解。算法需要新结果时使用独立输出。后端 Replay、Interactive、Formal Training 分别核对环境。
- 区分工作区源码、构建 ZIP、公开附件与实际安装；按所测对象确认模块来源和载荷身份。版本号、源码同步或其他载荷的通过记录不代替当前对象核对，具体入口见[运行来源](references/verification.md#运行来源)。
- 场景与消费者保留各自归属，运动、读数和统计按对应时钟工作；数据缺失、无效或过期时保留其状态。手动运动、合成外形与缺陷、显示增强的能力按专题说明，不推定物理变化、新观测或实体安全效果。
- 软件合同、后台脚本、可见窗口、真实输入、安装载荷、完整媒体、科学精度与现场效果分别判定。源采样、显示时间轴和实际 FPS 分开；`POST_PIXEL` 只证明视口绘制，理想射线与瞬时成像不证明硬件采样、曝光或保护延迟。
- 按[验证入口](references/verification.md)完成与任务匹配的检查，报告实际对象、环境、结果及未覆盖范围。纯文档与 skill 只做静态检查；扩展载荷需要更新时才选择构建／安装流程。

## 要求与资料来源

[操作说明](../../../前端readme.md)、[实现与接口](../../../blender_frontend/README.md)、[待实施要求与验收条件](../../../前端更新计划.md)及[发布状态与验证范围](../../../docs/blender/发布状态与验证范围.md)分别负责当前用法、结构、未实施要求及验证结论。修改描述前核对代码、配置和测试；本 skill 不另存发布版本、测试通过数量或开发记录。

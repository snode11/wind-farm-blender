# 文档索引

项目仓库：[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。2026-10-07 的本地文档、源码与实验结果交接快照见网页 GPT 交接入口（本地路径：`outputs/web-gpt-reconstruction-handoff-20261005/README.md`），2026-10-07 更新；交接目录名保留首次建立日期。

核对日期：2026-10-08。这里汇集操作说明、算法解释、开发记录、研究方案和历史验证。按用途分类；文件里的状态、输入、版本和验证环境仍需分别阅读。

## 从哪里开始

| 你想了解什么 | 阅读入口 |
| --- | --- |
| 平台目前有哪些功能、哪些仍待验证 | [Blender 前端功能总览与规划](blender/Blender前端功能总览与规划.md) |
| 公开安装、源码与已知限制 | [发布状态与验证范围](blender/发布状态与验证范围.md)、[安装说明](blender/INSTALL.md)、[0.3.18 发布核对](blender/releases/0.3.18发布核对.md) |
| 如何使用回放、相机、净空和缺陷编辑 | [用户使用手册](blender/用户使用手册.md)、[Blender 文档分类](blender/README.md) |
| IPPO、集中式 MAPPO 和训练命令 | [训练环境与入口](training/MAPPO_SETUP.md)、[参数共享 IPPO 流程](training/IPPO_TRAINING_EXPLAINED.md) |
| 当前叶片表面重建研究结论 | [2026-10-06 方案与实际执行入口](proposal/叶片表面重建_2026-10-06/README.md) |
| 接下来有哪些研究方案 | [研究方案分类](proposal/README.md) |

## 本次核对后的状态

| 主题 | 当前可据文档与证据确认的范围 |
| --- | --- |
| NREL / WFRL Blender 公开安装 | 当前公开发布为 **0.3.18**，安装附件 `wfrl_blender-0.3.18.zip`，manifest 版本 `0.3.18`、172 文件。见 [GitHub 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18)与[发布核对](blender/releases/0.3.18发布核对.md)。 |
| 0.3.18 软件修复与门槛 | 场景／播放隔离、C2 槽位与采集修复、GPU 事务复用和默认关闭诊断已交付。发布源码 CPU 215 passed、2 skipped、233 subtests passed；发布前宿主 117 passed、1 skipped、6 subtests passed，172 文件安装匹配，65 个正式资产摘要不变。两轮不合并，原 0.2 mm 仍 FAILED_GATE；见[发布前验证](blender/validation/NREL本地0.3.18修复与验证.md)。 |
| 0.3.17.1 历史新增内容 | 同源 MAPPO 601 样本纹理同步对照；保留原几何示例和独立合成 120 样本纹理入口，修复各来源显示设置串用、取消副作用与来源／时钟校验。该版仅发布 ZIP；0.3.18 保留此功能，纹理外观、几何精度及缺陷恢复仍未验收。 |
| 源码和安装 | 0.3.18 源码提交 [`6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02`](https://github.com/snode11/wind-farm-blender/commit/6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02) 已推送 main，v0.3.18 指向该提交，源码复建与最终验证 ZIP 完全一致。自动 Source code 是开发归档，安装使用扩展 ZIP 并重启；本地缺陷编辑和纹理检查 CLI 等未随本次公开。 |
| GW184 | **0.4.0 独立解压运行包**；与 NREL 扩展分别使用。其原三路视频方案未形成完整三路 MP4 交付。 |
| 净空 / TLS | 软件回放与数值研究已有记录；精度仍为 `REVIEW_ONLY / PENDING_ACCEPTANCE`，现场精度和保护延迟未验收。 |
| 重建研究 | 最新表面实验 `exec-a01` 为完成研究执行、得到负结果或被门槛阻断：P2b `FAILED_GATE / REVIEW_ONLY`、主增益 `null`；局部观测门槛失败，局部恢复 `NOT_RUN`。旧动态原型仍为 `DY1 NOT PASSED`，与便携分屏软件验证分别判断。 |
| 训练 | 旧 SB3 `train_mappo.py` 链路实际是参数共享 IPPO；集中式 MAPPO 有独立实现与入口。历史 FLORIS 功率增益不代表当前重新验证、FAST.Farm 效果或现场收益。 |

版本来源：[GitHub 0.3.18 发布记录](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18)。实际发布包、发布前本地验证和研究结论的完整边界，以对应报告为准。

## 按用途分类

| 目录 | 内容与入口 |
| --- | --- |
| `overview/` | [平台定位与长期目标](overview/风场物理仿真平台_功能列表与终极目标.md)，保留早期平台描述；最新具体功能先读 Blender 总览。 |
| `setup/` | [历史 Windows 本地复现](setup/setup_local.md)，路径、平台和旧环境限制已标注。 |
| `training/` | [环境与入口](training/MAPPO_SETUP.md)、[IPPO 全流程](training/IPPO_TRAINING_EXPLAINED.md)、[PPO 执行细节](training/PPO_算法执行细节.md)、[历史尾流控制实验](training/WAKE_STEERING_BREAKTHROUGH.md)。 |
| `demos/` | [演示指令单](demos/演示指令单.md)，当前 Blender 操作与历史 PowerShell / RViz 命令分开。 |
| `blender/` | [操作与功能](blender/README.md)，其中 `releases/` 保存按版本的交付核对，`validation/` 保存历史开发与验证记录。 |
| `development/` | [叶尖计算与后端接入](development/叶尖代码与后端接入.md)、[叶片晃动开发记录](development/叶片晃动方案.md)。 |
| `lidar/` | [双束 TLS 候选实施与验证总报告](lidar/双束TLS候选实施与验证总报告.md)。 |
| `proposal/` | [按研究主题分类](proposal/README.md)：重建、净空、缺陷、相机视频、平台原型及带日期的表面研究交接。 |
| `img/` | [观测图一](img/observation.png)、[观测图二](img/observation1.png)；作为说明资源保留，不单独证明实现或验收。 |
| `maintenance/` | [本次对齐与整理记录](maintenance/整理记录_2026-10-06.md)、[文件迁移清单](maintenance/文件迁移记录_2026-10-06.json)。 |

## 维护和阅读规则

- 当前操作说明跟随已发布版本与实际实现；历史报告保留原始版本、日期、输入、数字、失败和适用环境。
- 研究方案不等于实施结果。已执行方案的首页提供最新报告入口；`null`、未执行、失败、仅软件验证和未验收状态不能互换。
- 原始 v2 备份、协议草案 JSON、原材料 TXT、已有迁移及哈希记录保留原文。冻结协议的实际执行版本从对应 run 读取。
- [根 README](../README.md) 与 [CHANGELOG](../CHANGELOG.md) 记录已公开发布版本；[前端操作说明](../前端readme.md)、[实现与维护](../blender_frontend/README.md)和[更新计划](../前端更新计划.md)分别承担自己的职责。
- 本次发布同步 0.3.18 扩展源码、ZIP 与文档；发布源码复建和 CPU 检查与发布前安装／原生验证分别记录，没有重新运行全部原生验收、训练或求解。原有失败门槛保持。

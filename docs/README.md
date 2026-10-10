# 文档索引

项目仓库：[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。2026-10-07 的本地文档、源码与实验结果交接快照见网页 GPT 交接入口（本地路径：`outputs/web-gpt-reconstruction-handoff-20261005/README.md`），2026-10-07 更新；交接目录名保留首次建立日期。

核对日期：2026-10-10。这里汇集操作说明、算法解释、开发记录、研究方案和历史验证。按用途分类；文件里的状态、输入、版本和验证环境仍需分别阅读。

## 从哪里开始

| 你想了解什么 | 阅读入口 |
| --- | --- |
| 平台目前有哪些功能、哪些仍待验证 | [Blender 前端功能总览与规划](blender/Blender前端功能总览与规划.md) |
| 公开安装、源码与已知限制 | [发布状态与验证范围](blender/发布状态与验证范围.md)、[安装说明](blender/INSTALL.md)、[0.3.20+1 发布核对](blender/releases/0.3.20+1发布核对.md) |
| 如何使用回放、相机、净空和缺陷编辑 | [用户使用手册](blender/用户使用手册.md)、[Blender 文档分类](blender/README.md) |
| IPPO、集中式 MAPPO 和训练命令 | [训练环境与入口](training/MAPPO_SETUP.md)、[参数共享 IPPO 流程](training/IPPO_TRAINING_EXPLAINED.md) |
| 当前叶片表面重建研究结论 | [2026-10-06 方案与实际执行入口](proposal/叶片表面重建_2026-10-06/README.md) |
| 接下来有哪些研究方案 | [研究方案分类](proposal/README.md) |

## 本次核对后的状态

| 主题 | 当前可据文档与证据确认的范围 |
| --- | --- |
| NREL / WFRL Blender | 当前公开发布为 **0.3.20+1**，标签 `v0.3.20.1`；唯一手动附件 `wfrl_blender-0.3.20+1.zip`，173 文件。恢复默认三相机按钮及单路观察中的复位相机引用；见[发布核对](blender/releases/0.3.20+1发布核对.md)。 |
| 0.3.19／0.3.20 展示改进 | 当前以打开几何重建示例为统一入口，切换几何／同源纹理，保留共享播放和手动视角；两侧完整风机显示及 T2/T3 切换修复已发布。历史完整工作流验证见[0.3.19 核对](blender/releases/0.3.19发布核对.md)，不能替代 0.3.20+1 的验证范围。 |
| 0.3.18 历史软件修复 | 已交付场景／播放隔离、C2 槽位与采集修复、GPU 事务复用和 Tools 版本／来源信息；短时视口帧率采样默认关闭。发布源码检查与发布前安装／原生窗口验证分别记录；原 0.2 mm 叶尖精度门仍为 `FAILED_GATE`，见[发布前验证](blender/validation/NREL本地0.3.18修复与验证.md)。 |
| 0.3.17.1 历史新增内容 | 同源 MAPPO 601 样本纹理同步对照；保留原几何示例和独立合成 120 样本纹理入口，修复各来源显示设置串用、取消副作用与来源／时钟校验。该版仅发布 ZIP；0.3.18 保留此功能，纹理外观、几何精度及缺陷恢复仍未验收。 |
| 源码和安装 | 当前 Release 仅提供 ZIP，扩展实现源码未推送；使用／维护文档随后单独同步。公开扩展源码仍为 0.3.18 / [`6dfa0eef`](https://github.com/snode11/wind-farm-blender/commit/6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02)，其历史隔离复建验证保留。v0.3.20.1 指向既有文档提交，自动 Source code 不对应当前实现；安装使用专用扩展 ZIP 并重启。 |
| GW184 | **0.4.0 独立解压运行包**；与 NREL 扩展分别使用。其原三路视频方案未形成完整三路 MP4 交付。 |
| 净空 / TLS | 软件回放与数值研究已有记录；精度仍为 `REVIEW_ONLY / PENDING_ACCEPTANCE`，现场精度和保护延迟未验收。 |
| 重建研究 | 最新表面实验 `exec-a01` 为完成研究执行、得到负结果或被门槛阻断：P2b `FAILED_GATE / REVIEW_ONLY`、主增益 `null`；局部观测门槛失败，局部恢复 `NOT_RUN`。旧动态原型仍为 `DY1 NOT PASSED`，与便携分屏软件验证分别判断。 |
| 训练 | 旧 SB3 `train_mappo.py` 链路实际是参数共享 IPPO；集中式 MAPPO 有独立实现与入口。历史 FLORIS 功率增益不代表当前重新验证、FAST.Farm 效果或现场收益。 |

版本来源：[GitHub 0.3.20+1 发布记录](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.20.1)。实际发布包、发布前本地验证和研究结论的完整边界，以对应报告为准。

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
- 2026-10-09 的文档核对仅同步当时已发布状态、补充研究导航和修复引用；未运行训练、求解、渲染、构建、安装或发布。
- 2026-10-10 在 0.3.20+1 ZIP 发布完成后，单独同步使用／维护文档及必要 JSON 证据到 GitHub；文档同步不新增运行验收，既有验证按轮次保留，原有失败门槛保持。

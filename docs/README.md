# 文档索引

项目仓库为 [snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。文档按功能、使用、实现、约束和验证对象组织；包身份、实际安装和研究接受状态分别说明。

## 阅读入口

| 内容 | 入口 |
| --- | --- |
| 当前功能与限制 | [Blender 功能总览](blender/Blender前端功能总览与规划.md)、[发布状态与验证范围](blender/发布状态与验证范围.md) |
| 安装和用户操作 | [安装说明](blender/INSTALL.md)、[前端说明](../前端readme.md)、[用户手册](blender/用户使用手册.md) |
| 独立风机手动控制 | [控件、运行与保存规则](blender/风机手动控制.md) |
| 实现与维护 | [前端维护说明](../blender_frontend/README.md)、[Blender 文档索引](blender/README.md) |
| 待实施要求与验收 | [前端更新计划](../前端更新计划.md) |
| 叶片重建算法 | [Blade Recon 0.2](../blade_recon0.2/README.md)、[材料点对应研究](../blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md) |
| 表面与动态研究 | [表面研究入口](../research/blade-surface/README.md)、[动态研究模块](../wfrl/nrel_reconstruction/README.md) |
| 雷达合同与计算 | [数据合同](../wfrl/lidar/REPLAY_FORMAT.md)、[雷达算法](../wfrl/lidar/README.md)、[TLS 研究](lidar/双束TLS候选实施与验证总报告.md) |
| 训练与环境 | [训练入口](training/MAPPO_SETUP.md)、[参数共享 IPPO](training/IPPO_TRAINING_EXPLAINED.md)、[环境设置](setup/setup_local.md) |
| 网页实验室 | [预览、异步计算与结果接口分工](proposal/platform/风场网页实验室_第一版方案与分工汇总.md)；待实现与目标 Ubuntu 环境验证 |
| 研究要求 | [研究方案分类](proposal/README.md) |

## 身份与接受状态

| 对象 | 当前范围 |
| --- | --- |
| NREL 公开安装包 | **0.3.24**；安装使用 [v0.3.24 专用扩展 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.24/wfrl_blender-0.3.24.zip)，包身份与适用验证见[交付身份](../publication/blender-0.3.24.json)。 |
| 项目源码 | [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work) 包含当前前端、后端、研究、测试与文档；源码分支不等于既有公开安装包或日常安装载荷。 |
| 本地日常安装 | **0.3.28 本地包**，实际模块 `bl_ext.user_default.wfrl_blender`；181 文件与本地 ZIP 一致，尚未公开发布。包身份、五组安装版回归及新进程检查的范围见[日常安装核对](../publication/daily-installation.json)。 |
| GW184 | **0.4.0 独立解压运行包**，模型、配置和 NREL 扩展分别使用。 |
| 材料点研究 | Patch **2/8**、Crack **7/8**、跨相机 RGB **0/264**；真实同材料点三维误差 `null`，`FAILED_GATE / NOT_ACCEPTED`；未替换正式 601 样本资产。 |
| 其他研究门槛 | 叶尖 0.2 mm 门槛 `FAILED_GATE`；雷达 `REVIEW_ONLY / PENDING_ACCEPTANCE`；表面 P2b `FAILED_GATE / REVIEW_ONLY`、主增益 `null`、局部恢复 `NOT_RUN`；独立动态原型 `DY1 NOT PASSED`。 |

## 文档职责

- `overview/` 描述平台用途与目标，`development/` 描述有效的几何与后端接口，`demos/` 提供使用入口。
- `blender/releases/` 说明对应安装包的身份、证据和限制；`blender/validation/` 说明具体输入、环境与验证边界。测试数量只属于对应对象，不跨包相加。
- `proposal/` 区分待实施要求与研究限制。冻结输入、原始测量、协议及来源快照按其身份读取，不作为当前产品能力。
- [源码与资源分发](PUBLICATION.md)列出运行输入、研究证据及其恢复方式；机器环境和可恢复隔离区不属于公开分发。
- 根 README 说明项目用途、公开入口与能力；使用说明面向操作，维护说明面向实现，计划只维护待实施要求。不维护变更日志或开发过程记录。

# 文档索引

发布版本为 **0.5.0.0.1**（GitHub 标签 `v0.5.0.0.1`，Blender manifest 为 `0.5.0+0.1`）。安装与公开载荷身份以 `publication/daily-installation.json` 和发布交付记录为准。

项目仓库为 [snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。文档按功能、使用、实现、约束和验证对象组织；包身份、实际安装和研究接受状态分别说明。

## 阅读入口

| 内容 | 入口 |
| --- | --- |
| 当前功能与限制 | [Blender 功能总览](blender/Blender前端功能总览与规划.md)、[发布状态与验证范围](blender/发布状态与验证范围.md) |
| 自动检查与本地复用 | [Python 合同、Blender 后台与发布身份检查](development/自动检查.md) |
| 脚本与策略评估 | [脚本索引](../scripts/README.md)、[Blender checkpoint 评估](demos/Blender策略评估.md) |
| 安装和用户操作 | [安装说明](blender/INSTALL.md)、[前端说明](../前端readme.md)、[用户手册](blender/用户使用手册.md) |
| 独立风机手动控制 | [控件、运行与保存规则](blender/风机手动控制.md) |
| X100 毫米波雷达 | [单台四预设、机舱 XZ、塔距诊断与离线覆盖](blender/X100雷达.md)、[面积采样、安装搜索与叶片估计](blender/X100观测研究.md)、[数据与采集接口](../wfrl/radar_x100/README.md)、[融合要求](proposal/radar/CTSAI_X100_Blender融合方案.md) |
| 实现与维护 | [前端维护说明](../blender_frontend/README.md)、[Blender 文档索引](blender/README.md) |
| 待实施要求与验收 | [前端更新计划](../前端更新计划.md) |
| 叶片重建算法 | [Blade Recon 0.2](../blade_recon0.2/README.md)、[材料点对应研究](../blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md) |
| 叶片响应与载荷研究 | [九项响应、弹性扭转输出要求及研究用DEL](development/叶片响应与研究DEL.md) |
| FAST.Farm 物理选项 | [塔架柔性、动态失速、塔影与短算例对比](development/FASTFarm物理选项.md)、[BeamDyn 叶片弹性扭转](development/BeamDyn叶片弹性扭转.md)、[OC3 海上单桩与波浪](development/OC3海上单桩与波浪.md) |
| 表面与动态研究 | [表面研究入口](../research/blade-surface/README.md)、[动态研究模块](../wfrl/nrel_reconstruction/README.md) |
| 雷达合同与计算 | [数据合同](../wfrl/lidar/REPLAY_FORMAT.md)、[雷达算法](../wfrl/lidar/README.md)、[TLS 研究](lidar/双束TLS候选实施与验证总报告.md) |
| 训练与环境 | [算法与训练入口](training/MAPPO_SETUP.md)、[检查点保存](training/MAPPO_SETUP.md#检查点写入与产物选择)、[正式 CLI 中断与恢复](training/MAPPO_SETUP.md#正式-cli-的中断与恢复)、[参数共享 IPPO](training/IPPO_TRAINING_EXPLAINED.md)、[单智能体 PPO](training/PPO_算法执行细节.md)、[环境设置](setup/setup_local.md) |
| Bridge 与正式进程 | [消息队列、fatal、等待年龄与停止清理](development/BlenderBridge协议.md)；工作区合同，Windows 实机尚未验证 |
| Driver 与奖励限制 | [缺测、单位、交换预算与算例保留](training/MAPPO_SETUP.md#fastfarmdriver-测量与交换预算)、[超速监测范围](training/MAPPO_SETUP.md#安全监测的范围)、[reward 同步要求与实现偏差](training/MAPPO_SETUP.md#奖励归一化要求与实际状态)、[FAST.Farm 风况请求、输入与统计](training/FASTFarm风况.md) |
| 网页实验室 | [预览、异步计算与结果接口分工](proposal/platform/风场网页实验室_第一版方案与分工汇总.md)；待实现与目标 Ubuntu 环境验证 |
| 研究要求 | [研究方案分类](proposal/README.md) |

## 身份与接受状态

| 对象 | 当前范围 |
| --- | --- |
| NREL 公开安装包 | **0.5.0.0.1**；安装使用 [v0.5.0.0.1 专用扩展 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.5.0.0.1/wfrl_blender-0.5.0%2B0.1.zip)，包身份与适用验证见[交付身份](../publication/blender-0.5.0.0.1.json)。 |
| 项目源码 | [`main`](https://github.com/snode11/wind-farm-blender/tree/main) 包含当前前端、后端、研究、测试与文档；源码分支不等于既有公开安装包或日常安装载荷；本地工作区提交的检查范围见[工作区源码验证](blender/发布状态与验证范围.md#工作区源码验证提交-bb470611)。 |
| 发布源码 | `v0.5.0.0.1` 源码标签及匹配归档；扩展 ZIP 可由该归档独立重建；软件合同、保存证据及适用范围见[源码软件合同与验证](blender/发布状态与验证范围.md#源码软件合同与验证)。 |
| 本地日常安装 | **0.5.0.0.1 公开 ZIP 载荷**，实际模块 `bl_ext.user_default.wfrl_blender`；239 个文件与本地安装归档匹配。149 个新后台进程模块来源、持久启用及配置保护见[日常安装核对](../publication/daily-installation.json)。 |
| X100 观测研究 | 扩展提供面积材料采样、工程检测、安装报告导入和有拒绝条件的叶片估计；配套源码提供有限候选搜索与离线评价。同源几何数值与原生画布证据见[研究验证](blender/发布状态与验证范围.md#x100-观测研究工作区验证)。 |
| GW184 | **0.4.0 独立解压运行包**，模型、配置和 NREL 扩展分别使用。 |
| 材料点研究 | Patch **2/8**、Crack **7/8**、跨相机 RGB **0/264**；真实同材料点三维误差 `null`，`FAILED_GATE / NOT_ACCEPTED`；未替换正式 601 样本资产。 |
| 其他研究门槛 | 叶尖显示精度的源码全时间轴与本地 0.3.36 安装版抽样分别见[验证范围](blender/发布状态与验证范围.md#研究与物理边界)；材料点研究保持 FAILED_GATE，雷达 REVIEW_ONLY / PENDING_ACCEPTANCE，表面 P2b FAILED_GATE / REVIEW_ONLY、主增益 null、局部恢复 NOT_RUN，独立动态原型 DY1 NOT PASSED。 |

## 文档职责

- `overview/` 描述平台用途与目标，`development/` 描述有效的几何与后端接口，`demos/` 提供使用入口。
- `blender/releases/` 说明对应安装包的身份、证据和限制；`blender/validation/` 说明具体输入、环境与验证边界。测试数量只属于对应对象，不跨包相加。
- `proposal/` 区分待实施要求与研究限制。冻结输入、原始测量、协议及来源快照按其身份读取，不作为当前产品能力。
- [源码与资源分发](PUBLICATION.md)列出运行输入、研究证据及其恢复方式；机器环境和可恢复隔离区不属于公开分发。
- 根 README 说明项目用途、公开入口、当前能力与限制；使用说明面向操作，维护说明面向实现，计划只维护待实施要求。文档与代码注释按当前功能和业务概念组织，不维护版本修改日志、开发过程记录或临时执行清单。

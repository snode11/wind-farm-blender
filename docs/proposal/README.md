# 研究方案与交接分类

项目仓库统一为 [snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。2026-10-07给网页GPT的源码与实验补充快照见交接入口（本地路径：`outputs/web-gpt-reconstruction-handoff-20261005/README.md`）。

核对日期：2026-10-08。方案正文保留其编制时范围，首页补充实际执行进展或后续替代入口。返回[总索引](../README.md)。

## 表面研究的最新入口

[叶片表面重建 · 2026-10-06](叶片表面重建_2026-10-06/README.md)汇集 v3 方案、原 v2 备份、协议草案与后续执行记录。最新独立 run 已完成可执行实验，但 P2b 为 `FAILED_GATE / REVIEW_ONLY`，主增益 `null`；自动长身份延续失败、32 帧联合几何数值结论不确定、局部观测门槛失败、局部恢复未执行。应先读该 README 的最新状态，再读方案和对应实际报告。

原 v2 文本、草案 JSON 和迁移哈希记录保留历史字节身份；它们不是最新执行指令，也不能用草案中的 `null` 推定实际冻结协议仍未完成。

## 按主题分类

| 主题 | 文档 | 当前阅读定位 |
| --- | --- | --- |
| `reconstruction/` | [固定三摄首轮方案](reconstruction/固定三摄叶片三维重建与表面缺陷首轮诊断方案.md) | 早期方案，查看首页的后续实现与研究入口。 |
| 重建 | [NREL 5MW 固定三摄 v1.1](reconstruction/NREL5MW_固定三摄叶片三维重建与表面缺陷首轮诊断方案_v1.1.md) | 后续修订和最小验证路线，保留编制时边界。 |
| 重建 | [首轮执行清单与阶段 A 准备](reconstruction/固定三摄叶片重建_首轮执行清单与阶段A实现准备.md) | 阶段 A 的历史准备说明，具体完成状态看最新入口。 |
| 重建 | [现有视频表面证据与根部约束诊断](reconstruction/NREL5MW_现有视频表面证据补齐与根部约束诊断_v1.0.md) | P0–P5 已完成，保留正式 v2；完整几何验收仍待建立。 |
| 重建 | [动态叶片与叶尖面外读数方案](reconstruction/NREL_视频动态叶片与叶尖面外读数验证方案_v0.1.md) | 独立动态研究；`DY1 NOT PASSED`，仅四个保存估计时刻。 |
| `lidar/` | [激光净空雷达演示方案](lidar/激光净空雷达演示方案.md) | 原演示设计，当前操作看 Blender 手册。 |
| 净空 | [双束重建与 S1 报警方案](lidar/双束重建净空与一号光束报警方案.md) | 原设计与后续软件实现；算法精度状态看 [TLS 总报告](../lidar/双束TLS候选实施与验证总报告.md)。 |
| `defects/` | [缺陷编辑与固定相机成像开发规格](defects/叶片缺陷编辑与固定相机成像验证开发规格.md) | 编辑器软件已有 GW184 0.4.0 / NREL 0.3.16 实现；成像可辨识与缺陷恢复另行验证。 |
| `cameras/` | [GW184 启停三相机视频方案](cameras/GW184单机启停三相机视频方案.md) | 模型与编辑器已交付，完整三路 MP4 尚未生成。 |
| `platform/` | [工业 AGI / 4D 世界模型原型计划](platform/工业AGI_4D世界模型_可实现性审计与Prototype计划.md) | 限定范围的原型计划，计划与新增实施、收益验证分别判断。 |
| 平台原材料 | [material0](platform/source-material/material0.txt)、[material1](platform/source-material/material1.txt)、[material2](platform/source-material/material2.txt)、[material3](platform/source-material/material3.txt) | 世界模型、工业 AI 与平台架构构想的原始材料；保留原文，未经统一事实核验，不作为完成证据或自动执行指令。 |

## 状态优先级

具体 run 的最新 `REPORT.md`、`decision.json` 和实际冻结协议优先解释该 run；当前操作与发布说明优先解释可使用功能；旧方案、执行说明和原材料按其日期阅读。这里的整理没有启动任何研究实验或改变冻结条件。

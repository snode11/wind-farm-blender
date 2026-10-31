# 研究方案与接口设计

项目仓库为[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。[返回文档总索引](../README.md)。方案描述有效目标、输入合同、对照和验收条件；实际运行的状态以各证据对象的冻结协议、机器判定和报告为准。软件发布、研究模型和科学精度分别判断。

## 研究边界

| 研究对象 | 当前结论与入口 |
| --- | --- |
| 静态NREL单叶片 | 正式v2保留，坐标及RGB边界候选未采用，完整几何验收待建立；[模块说明](../../wfrl/nrel_reconstruction/README.md) |
| 动态NREL叶片 | `REVIEW_ONLY`、`DY1 NOT PASSED`，仅四个保存估计时刻，其余未插值；[动态方案](reconstruction/NREL_视频动态叶片与叶尖面外读数验证方案_v0.1.md) |
| 共享材料点表面研究 | P2b `FAILED_GATE / REVIEW_ONLY`、主增益null；自动长身份延续失败，32帧联合几何数值结论不确定，局部恢复未执行；[当前研究与合同](../../research/blade-surface/README.md) |
| 局部材料点候选 | 独立Patch2/8、Crack7/8，跨相机RGB0/264，`FAILED_GATE / NOT_ACCEPTED`，真实同材料三维误差null；[blade_recon v0.2](../../blade_recon0.2/README.md) |

上述研究输入、评分及结论独立，不能相互替代，也未替换前端601样本正式纹理资源。

## 按主题阅读

| 主题 | 设计与要求 |
| --- | --- |
| NREL表面重建 | [固定三摄方案](reconstruction/NREL5MW_固定三摄叶片三维重建与表面缺陷首轮诊断方案_v1.1.md)、[视频证据与根部诊断](reconstruction/NREL5MW_现有视频表面证据补齐与根部约束诊断_v1.0.md) |
| GW184可观测性 | [固定三摄诊断](reconstruction/固定三摄叶片三维重建与表面缺陷首轮诊断方案.md)、[输入与表面诊断接口](reconstruction/GW184固定三摄输入与表面可观测性接口.md)；独立对象，不能用于NREL验收 |
| 激光净空 | [演示与使用边界](lidar/激光净空雷达演示方案.md)、[双束重建与S1报警合同](lidar/双束重建净空与一号光束报警方案.md) |
| X100 毫米波雷达 | [Blender 仿真、实测回放与实时接入最终方案](radar/CTSAI_X100_Blender融合方案.md)；待实现，设备协议与真实样例待补足 |
| 缺陷与相机 | [缺陷编辑与成像规格](defects/叶片缺陷编辑与固定相机成像验证开发规格.md)、[GW184同步视频要求](cameras/GW184单机启停三相机视频方案.md) |
| 网页实验室 | [交互、异步计算与结果接口](platform/风场网页实验室_第一版方案与分工汇总.md)；网页预览与 Ubuntu 计算链路待实现及目标环境验证 |
| 工业控制原型 | [三机偏航与集体变桨原型计划](platform/工业AGI_4D世界模型_可实现性审计与Prototype计划.md) |
| 原始构想材料 | [material0](platform/source-material/material0.txt)、[material1](platform/source-material/material1.txt)、[material2](platform/source-material/material2.txt)、[material3](platform/source-material/material3.txt)；保留原文，未经统一事实核验，不作为完成证据或执行指令 |

冻结原文、协议草案和迁移哈希保持各自字节身份；草案null不表示实际运行协议未冻结，实际运行也不能回写原始草案为通过。

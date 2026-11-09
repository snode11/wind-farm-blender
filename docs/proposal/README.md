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
| NREL表面重建 | [三摄离线视频与单叶片三维重建要求](NREL5MW_三摄离线视频与单叶片三维重建验证方案_v1.0.md)、[实现与研究边界](../../wfrl/nrel_reconstruction/README.md) |
| 机舱视觉与叶片健康 | [载荷与健康状态系统设计（PDF）](../../../output/pdf/机舱视觉_叶片载荷与健康状态_系统设计方案.pdf)；单台150°对地相机覆盖下半转动平面，承接叶尖／拐点空间轨迹和三叶净空，规定同源误差、条件载荷、健康辨析、周期缺测与独立验证；载荷反演、定量结构参数和疲劳分析待实现、待验证 |
| GW184可观测性 | [输入与表面诊断接口](reconstruction/GW184固定三摄输入与表面可观测性接口.md)；独立对象，不能用于NREL验收 |
| 激光净空 | [演示与使用边界](lidar/激光净空雷达演示方案.md)、[双束重建与S1报警合同](lidar/双束重建净空与一号光束报警方案.md) |
| X100 毫米波雷达 | [融合要求与实施边界](radar/CTSAI_X100_Blender融合方案.md)；三目相机与 X100 在 T1 Down 位的一体化产品见[雷视融合叶尖净空监测系统产品立项书（Word）](radar/T1Down雷视融合叶尖净空监测系统_产品立项书.docx)，其中指标为建议目标、仿真结果待实测确认；公开扩展具备几何仿真、单台双预设、PLY 软件回放、本地通道及机舱 XZ／塔距／零载荷参考；工作区还具备面积材料采样、有限安装搜索和有拒绝条件的叶片估计，见[观测研究](../blender/X100观测研究.md)。厂家解码、实机标定、实测净空与雷视融合待实现和验收；[当前用法](../blender/X100雷达.md) |
| 雷达工程模型、风况与叶片载荷 | [毫米波雷达与 FAST.Farm 接口及待实现要求](radar/毫米波雷达与FASTFarm修改建议.md)；工作区的未标定检测模型、v2 安装报告、风况三层记录与根部力统计；实机标定、风向旋转、展向内力和独立场内入流探针待实现 |
| 缺陷与相机 | [缺陷编辑与成像规格](defects/叶片缺陷编辑与固定相机成像验证开发规格.md)、[GW184同步视频要求](cameras/GW184单机启停三相机视频方案.md) |
| 网页实验室 | [交互、异步计算与结果接口](platform/风场网页实验室_第一版方案与分工汇总.md)；网页预览与 Ubuntu 计算链路待实现及目标环境验证 |
| 工业控制原型 | [三机偏航与集体变桨原型计划](platform/工业AGI_4D世界模型_可实现性审计与Prototype计划.md) |
| 原始构想材料 | [material0](platform/source-material/material0.txt)、[material1](platform/source-material/material1.txt)、[material2](platform/source-material/material2.txt)、[material3](platform/source-material/material3.txt)；保留原文，未经统一事实核验，不作为完成证据或执行指令 |

冻结原文、协议草案和迁移哈希保持各自字节身份；草案null不表示实际运行协议未冻结，实际运行也不能回写原始草案为通过。

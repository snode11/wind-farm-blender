# 研究模块与结果

研究源码位于 [wind-farm-rl-all-work](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work) 分支。运行输入、冻结结果与研究证据按[源码与资源分发说明](../docs/PUBLICATION.md)及 [project-resources-v1](https://github.com/snode11/wind-farm-blender/releases/tag/project-resources-v1) 取得。算法实现、仿真显示、软件回归与科学验收分别判断。

| 研究对象 | 规范入口 | 当前结论 |
| --- | --- | --- |
| 三摄轮廓重建 | [blade_recon](../blade_recon/README.md)、[算法说明](../blade_recon/docs/算法说明_三摄视频叶片三维重建_v0.1.md) | 10 维参数化轮廓基线；合成验证不代表真实风机精度 |
| 动态输入与纹理辅助几何 | [blade_recon0.2](../blade_recon0.2/README.md)、[职责与合同](../blade_recon0.2/docs/纹理辅助几何_职责与合同.md) | 13 维全局状态与显式材料点候选；Patch 2/8、Crack 7/8、跨相机 RGB 0/264，`FAILED_GATE / NOT_ACCEPTED` |
| 叶片表面与材料身份 | [表面研究](blade-surface/README.md) | 固定材料绑定、窗口求解和图集存在实现；几何收益未建立，局部深度恢复未执行 |
| NREL 静态／动态视频研究 | [nrel_reconstruction](../wfrl/nrel_reconstruction/README.md) | 静态正式模型与候选分别保留；动态仅四个保存估计时刻，`REVIEW_ONLY / DY1 NOT PASSED` |
| 双束净空与 TLS | [雷达算法](../wfrl/lidar/README.md)、[候选研究](../docs/lidar/双束TLS候选实施与验证总报告.md) | `REVIEW_ONLY / PENDING_ACCEPTANCE`，未建立现场精度及保护能力 |
| GW184 合成成像 | [项目说明](../projects/gw184-single/README.md) | 固定三相机、刚性动画与六类合成缺陷；源码运行需恢复未分发的 DTU 输入，完整三路 320 秒视频尚未生成 |

前端内置几何／同源纹理使用独立的 601 样本保存资产，状态为 `SAME_SOURCE_SIMULATION / NOT_ACCEPTED`。外部研究候选没有替换这些正式资源。当前安装、源码、公开扩展与研究证据的身份见[发布状态与验证范围](../docs/blender/发布状态与验证范围.md)。

材料点 F/G 原始输入、冻结脚本和完整运行产物未随当前资源分发，不能完整精确复现；CoTracker 的第三方源码与权重也不随包提供。具体可用范围见[分发说明](../docs/PUBLICATION.md)。

复查必须使用报告规定的输入、模型、时间、评分域和失败分母。缺失值保持 `null`，未运行保持 `NOT_RUN`；渲染、保存重开、图像增强或软件通过不能提升科学接受状态。新的求解和渲染使用独立输出目录，不覆盖冻结证据。

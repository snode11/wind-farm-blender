# 叶片表面、纹理与材料点研究

本研究关注固定相机 RGB 中的材料身份能否支持叶片几何约束，以及保存估计表面上的纹理是否具有可追溯依据。源码、研究结果和 Blender 显示分别使用自身输入与验收条件。项目源码入口为 [wind-farm-rl-all-work](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work)；资源与证据按[分发说明](../../docs/PUBLICATION.md)及 [project-resources-v1](https://github.com/snode11/wind-farm-blender/releases/tag/project-resources-v1) 恢复。

## 实现与输入合同

| 入口 | 职责与限制 |
| --- | --- |
| [blade_recon](../../blade_recon/README.md)与[算法说明](../../blade_recon/docs/算法说明_三摄视频叶片三维重建_v0.1.md) | 参数化轮廓基线；`input_bundle.py` 与 `surface_io.py` 读取显式 RGB、标定、时间和未知域；`surface_geometry.py` 与 `window_surface_fit.py` 保持材料三角面绑定及共享窗口目标 |
| [blade_recon0.2](../../blade_recon0.2/README.md)与[纹理辅助几何合同](../../blade_recon0.2/docs/纹理辅助几何_职责与合同.md) | 13 维全局状态、RGB 身份跟踪及可选重投影残差；固定估计三角面绑定不自动证明真实同材料身份 |
| [材料点对应研究](../../blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md) | 冻结独立比较、完整计划分母、跨相机拒绝原因和恢复清单 |
| [NREL 视频模块](../../wfrl/nrel_reconstruction/README.md) | 独立模板、轮廓拟合、留出 RGB 与 evaluated surface 评分；不读取评价真值进行拟合 |
| [Blade Recon 显示](../../docs/blender/BladeRecon可视化使用说明.md) | 读取保存结果及 packed 图像；显示增强只改变外观，不添加观测或局部形状参数 |

求解器使用声明的 RGB、标定、已许可健康设计资产和未知域；源动态网格与评价真值仅供独立评分。未知、遮挡、身份丢失和质量拒绝保留在完整分母中，不由几何预测补造 UV，不通过重绑定或重新选点恢复失败身份。

## 当前发现

| 对象 | 结论与证据边界 |
| --- | --- |
| 固定材料点窗口几何 | 三个固定留出窗口的门槛失败，受控几何收益未建立，主增益保持 `null`；有限投影改善不能代替表面恢复 |
| 自动长身份与长窗口 | 自动长身份延续失败；32 帧联合几何为 `NUMERICAL_INCONCLUSIVE`，不能宣称稳定长序列收益 |
| 局部凹坑恢复 | RGB 可观测性门槛失败，局部恢复为 `NOT_RUN`，有效深度为 `null` |
| 局部材料点候选 | Patch 2/8，Crack 7/8（87.5% 低于 90% 门槛）；跨相机 RGB 接受 0/264，`FAILED_GATE / NOT_ACCEPTED`；真实同材料点三维误差与主增益均为 `null` |
| 静态 NREL 表面 | 正式模型保留；中心／尺度与 RGB 边界候选未采用，完整几何验收仍待建立 |
| 动态 NREL 表面 | 仅原视频索引 42–45 四个时刻保存估计；其余 96 帧为 `UNAVAILABLE; no interpolation`，`REVIEW_ONLY / DY1 NOT PASSED` |
| 图集与原生显示 | 图集、固定绑定及保存重开有对应软件证据；缺少 `observed_sections` 或内嵌健康 loft 的结果不能假定兼容当前安装 reader，须使用相应显式适配器或独立原生文件 |

固定材料点窗口结论对应恢复后的 `outputs/blade-surface/03_后续独立实验/decision.json` 及其独立评分文件；完整输入、数值设置与失败原因以冻结证据为准。当前局部候选的比较以[材料点对应研究](../../blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md)为准；不同研究的样本、模型与门槛不能合并成同一个接受率。

## 使用与复查边界

前端正式同源资产为 601 个 10 Hz 保存样本，映射到 MAPPO 117–177 s；外部研究没有替换这些资产。几何和纹理仍为 `NOT_ACCEPTED`，0.2 mm 叶尖门槛仍为 `FAILED_GATE`。固定灰度图集的模糊、条带、接缝、错贴和未观测区域保留，放大或增强不增加观测分辨率。

材料点 F/G 实验的原始输入、冻结脚本及完整运行产物不在当前分发中；报告和有限保存结果不能支持其完整精确复现。CoTracker 的第三方源码和权重不随资源包提供；固定来源与许可按相应 provenance 核对。

研究资源不是 Python 环境、设备标定或现场测量证明。复查前按分发清单恢复所需文件，核对报告的冻结输入、源码和依赖身份；缺少资源时明确标为不可复现或未验证，不以公开源码代替实验输入。新的求解、采集和渲染需使用独立输出目录，并按对应任务授权执行。

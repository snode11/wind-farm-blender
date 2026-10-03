# Wind Farm Blender

两个独立用途的 Blender 包，均要求 **Blender 5.2+**。

| 产品 | 当前版本 | 用途 | 下载与使用 |
| --- | --- | --- | --- |
| GW184 三相机与叶片缺陷 | **0.4.0** | 参考 DTU 的 GW184 尺寸合成刚性模型、固定三相机、六类缺陷编辑 | [下载独立运行 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.4.0/gw184_three_camera_defects-0.4.0.zip)，完整解压后启动；[使用说明](docs/blender/GW184三相机与缺陷编辑器.md) |
| NREL 5MW / WFRL 前端 | **0.3.13** | 三机柔性回放、MAPPO、三相机、默认双束与 TLS 候选 | [下载 Blender 扩展 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.13/wfrl_blender-0.3.13.zip)，通过 Install from Disk 安装；[前端说明](前端readme.md) |

[0.4.0 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.4.0) 同时提供两个 ZIP。GW184 的版本号不表示 NREL 扩展已升级；原 0.3 系列发布继续保留。

**NREL 0.3.13 已同步完整构建源码与内置回放资源：** 可从本版源码构建扩展；Blender 安装请使用 Release 附件 ZIP。NREL 0.3.10–0.3.12 的历史源码归档仍不包含当时仅 ZIP 发布的全部实现；GW184 0.4.0 的完整实现继续通过独立运行 ZIP 提供。

## [NREL 5MW 单叶片视频重建实验](https://github.com/snode11/wind-farm-blender/releases/tag/nrel-video-single-blade-20261003) · 2026-10-03 · NREL Single-Blade Video Reconstruction

- 发布已经跑通的三摄视频 → 单叶片模型 → 独立评分研究链路，包含两轮结果、P0–P5 诊断、源码、配置、评分数据、模型与关键图表。
- 独立分支为 [`experiments/nrel-video-single-blade`](https://github.com/snode11/wind-farm-blender/tree/experiments/nrel-video-single-blade)，固定标签为 `nrel-video-single-blade-20261003`；完整原始材料见 [Release 附件](https://github.com/snode11/wind-farm-blender/releases/tag/nrel-video-single-blade-20261003)，恢复及检查方法见 [实验发布说明](docs/research/nrel-video-single-blade/README.md)。
- 正式结果保留第二轮 v2，P4/P5 候选未采用；本次发布前 209 项 NREL 相关测试通过，附件内容与归档源文件签名核验通过。

已知限制：状态仍为 **PIPELINE_COMPLETE_PARTIAL_GEOMETRY**，完整几何与工程用途验收尚未建立；当前工作属于 B 的单叶片几何重建验证，纹理、缺陷地图、损伤计量与现场验证未完成。本研究归档不改变上方两个 Blender 包的版本和下载入口。

## [0.3.13](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.13) · 2026-10-02 · NREL 双束 TLS 候选与完整源码同步

- NREL 5MW / WFRL 扩展新增内置“TLS 候选 / S1”回放；原“双束净空 / S1”继续使用旧法默认，S1 独立报警与旧 B2 回放兼容保留。
- 两种方法共享原 117–177 s、40 Hz 三机保存几何；TLS 在同一开发片段的 400 对公共有效样本中 MAE 由 4.268685 m 降至 0.776096 m，独立固定转速工况约 0.943/0.970 m，按数据集分别报告。
- 同步当前 NREL 前端、雷达算法/读取器、相关工具、测试及内置源/旧法/TLS结果资源；克隆本版源码后可直接构建包含两种回放的扩展。0.3.10–0.3.12 的历史源码归档保持原样。
- GW184 0.4.0 六类缺陷独立包继续保留；本次雷达更新仅进入 NREL 扩展。

已知限制：两种方法仍为 **REVIEW_ONLY / PENDING_ACCEPTANCE**；TLS 是显式候选，未通过用途联合精度门槛或现场验证。软件/安装/窗口回归不代表全程帧率、连续时间漏测率、Windows/Linux 或设备精度验收。实际验证范围见 [0.3.13 发布核对](docs/blender/0.3.13发布核对.md)。

## [0.4.0](https://github.com/snode11/wind-farm-blender/releases/tag/v0.4.0) · 2026-09-30 · GW184 三相机与叶片缺陷独立包

- 新增 `gw184_three_camera_defects-0.4.0.zip` 独立运行包，完整解压后启动；内含模型参考数据、共享代码、固定三相机、六类合成缺陷编辑和配置保存恢复入口。
- GW184 使用参考 DTU 分布与 FFA 翼型构建的 184 m 尺寸合成刚性模型，面向三相机成像与缺陷检查；不替换 NREL 5MW 柔性回放、MAPPO 或双束净空功能。
- NREL 扩展继续使用原 `wfrl_blender-0.3.12.zip`，本次原样提供该附件，0.3 系列历史发布保持。
- 同步 Bridge 后端 macOS/MPI 启动环境修复：移除继承的作业身份，保留显式传输配置，并在 macOS 上默认使用回环 TCP。该修复需更新后端仓库源码，不通过安装 NREL 扩展 ZIP 更新。

已知限制：GW184 包为解压运行项目，不是 Blender 可安装扩展；合成缺陷不耦合结构或气动物理。第一阶段整体验收、现场真实性、完整视频、Windows/Linux 实机和长期性能未完成；NREL 双束仍为 REVIEW_ONLY / PENDING_ACCEPTANCE。

## [0.3.12](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.12) · 2026-09-29 · 单路轻量显示与双束回放

- 独立单路及三路中的单路放大固定使用轻量实体视图，保留材质颜色；移除单路材质切换和“精确材质预览”按钮。
- 保留相机参数、播放／暂停／单步与原图采集的独立质量设置，返回三路恢复原画质。
- 集成可携带双束数据及读取器：S1／S2／S3 为 10°／12°／14°，S2/S3 按同时刻、同叶片配对，S1 有效命中独立报警；保留旧 B2 回放。

已知限制：双束为研究估计，MAE 约 4.269 m，高精度柔性叶尖反演尚未完成。macOS Blender 5.2.1 的安装及短时窗口回归不代表完整片段帧率、Windows/Linux 或现场精度验收。数据仍为 REVIEW_ONLY。

## [0.3.11](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.11) · 2026-09-28 · 三相机单步控制

- 2×2 展示与三列对照的播放／暂停旁新增“单步”。
- 点击后暂停并同步前进一帧，末帧保持停止；保留单路放大和静态安装示意。

本版 ZIP 在 macOS Blender 5.2.1 验证了两种布局、暂停逐帧、播放中单步及末帧停止；不代表其他平台验证。

## [0.3.10](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.10) · 2026-09-27 · 2×2 展示与单路放大

- 新增默认 2×2 展示：三路相机加静态安装示意，保留三列对照。
- 支持单路放大／返回、统一播放控制与时间显示，完善预览及采集质量恢复。
- 播放验收区分完整完成、超时、中止、错误和缺少绘制，未完成不再提前输出 PASS；减少部分重复显隐写入和暂停时重绘。

已知限制：第四格是静态安装示意，不是第四路相机。原停止异常未复现但根因未定位，切换仍有绘制空档；不承诺稳定 30／60 FPS、全周期无遮挡或拼接成功。

## [0.3.9](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.9) · 2026-09-27 · 三相机默认取景与展示改进

- **三相机连续取景**：C1 叶根、C2 中段、C3 叶尖观察同一叶片的不同部分，相邻范围重叠，不要求三等分。推荐安装位置、盒体朝向与视场成为 MAPPO 默认配置。
- **支架贴合与整盒调整**：支架安装端贴住机舱，盒体保持外伸；同一步完成选位置、调整方向、确认或取消，保留布局导入导出与相机撤销。
- **原生三路观察**：按 C1→C2→C3 等宽排列，增大标签、显示同一仿真时间；提供流畅／高清预览，同刻 PNG 原图采集保留原始质量。
- **干净的默认画面**：默认关闭叶尖轨迹与挠度辅助物，小幅调整光照和材质；需要分析时仍可主动打开辅助显示。
- **沿用原启动方式**：已安装匹配的 0.3.9 扩展后，原 macOS 启动命令加载默认三相机，无需额外导入 JSON；离线演示不重新运行求解器或训练。

0.3.9 发布时的限制：根部与极尖端有局部遮挡，固定相机不保证全周期完整可见；三路播放仍有低帧率及待排查的切换异常。相机布局不等于拼接结果或实体标定。2×2 展示、单路放大与进一步性能优化属于[后续计划](前端更新计划.md)，未计入本版已完成项。

## 快速开始

1. 下载 [wfrl_blender-0.3.13.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.13/wfrl_blender-0.3.13.zip)，保留压缩格式。
2. Blender → **Edit → Preferences → Get Extensions → Install from Disk**，选择 ZIP 并启用 WFRL Blender。更新扩展后重启 Blender。
3. 新建 General 场景，鼠标置于三维视图，按 **N**，在 **MAPPO** 侧栏加载 **MAPPO · 60 秒**。
4. 通过 **View → 三相机 → 三路对照** 打开默认 2×2 展示，也可切换三列、放大单路、播放／暂停或单步。第四格是静态安装示意。
5. 查看双束时，在 **MAPPO → 净空** 选择 **双束净空 / S1**（旧法默认）或 **TLS 候选 / S1**；旧 B2 回放仍可切换。

ZIP 内置离线回放数据与读取器；离线观看无需配置 Python 后端、MPI 或 FAST.Farm。请安装 Release 附件 ZIP；自动生成的 **Source code** 含 0.3.13 构建源码，须先构建扩展后安装。安装与验证范围见[安装说明](docs/blender/INSTALL.md)和[当前发布状态](docs/blender/发布状态与验证范围.md)。

## 已发布历史

### [0.3.8](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.8) · 2026-09-23 · 三相机堆叠与整盒安装

- 三台上下堆叠的小相机共用盒体，替代四台独立相机；加载 MAPPO 时自动创建。
- 先在机舱上安装整个盒体，再分别调整 C1–C3 俯仰角；支持整盒 XYZ / 水平朝向、三路观察和独立视场。
- 保留同刻/序列原图、内外参、布局导入导出与撤销；盒体位置和相机参数共同保存及恢复。
- 移除第四槽、四路入口和旧试用布局，修复重复加载残留及机身关联。

已知限制：盒体及光学参数为仿真假设，不包含拼接算法；固定视野不保证全转动周期无盲区。原有 REVIEW_ONLY 数据不变；鼠标选点人工验收、Windows/Linux、实物标定与长期稳定性未完成。

### [0.3.7](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.7) · 2026-09-23 · 四相机观察与图像采集

- 新增 T1 的 C1–C4 独立相机：安装位置、双视场、输出尺寸、草稿确认/取消、布局导入导出与相机专用撤销。
- 新增原生单路/四路观察窗口，共享场景和回放时钟；保留材质与颜色，移除旧离屏四格自动刷新，退出释放临时观察相机。
- 支持四路同刻原图和时间序列 PNG 导出，保存相机内外参、来源与仿真时间，取消或失败后恢复回放状态。
- 提供同一片 B1 叶片四段固定相机试用布局 v2；新增 Blender 内现有 MP4 导出与 RTSP 循环推流。
- 修复扩展注册时机、编辑与导入失败回滚、撤销及窗口退出清理问题。

已知限制：四路仍会掉帧；固定布局不持续跟踪 B1，叶根有盲区、相邻取景有重叠。内置预弯 v3 数据仍为 REVIEW_ONLY，固定三束覆盖率未通过；Windows/Linux 与长期稳定性未验收。

### [0.3.5](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.5) · 2026-09-20 · 机舱视角与轨迹淡化

- 新增独立机舱相机，默认 120° 广角，支持云台摇杆、相机模式下滚轮变焦和复位；保留原 T1/T2/T3 Down 视角及按钮。
- 叶尖轨迹用红、绿、蓝对应三片叶片；相邻两圈定格对比 2 个仿真秒后，用 0.75 秒淡出并重新记录。
- 修复保存重开后 T2/T3 视角的数据归属；切换相机保持回放时刻、读数及轨迹。

### [0.3.4](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.4) · 2026-09-18 · 预弯回放与轨迹渐隐

- 默认内置 BeamDyn 预弯 v3 三机 60 秒回放，新增独立未受载参考及根系 xyz 挠度对照。
- 叶尖轨迹按仿真时间渐隐，保留 1.5 秒；暂停与视角切换保持轨迹状态。
- 修复净空页 Down 视角切换；净空真值采用叶片末截面表面至同高度移动塔壁的距离。

### [0.3.3](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.3) · 2026-09-17 · 雷达反馈与塔顶附件修复

- 新增净空绿/红/灰状态灯，以及三束光线有效测量时的短暂亮橙、加粗提示。
- 修复塔顶密封圈漂移、穿过雷达外壳及塔筒焊缝未随柔性塔架运动的问题。
- 旧场景加载时更新密封圈几何，倒退、重播与保存重开保持附件位置正确。

### [0.3.1](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.1) · 2026-09-16 · 叶尖挠度对照与柔性塔架

- 新增 T1 三叶片挠度核对、独立刚性参考、实际/参考点、分量对照及叶尖跟随特写。
- 内置 v2 三机柔性塔架数据，机舱、叶轮和雷达跟随塔架运动；净空真值使用变形塔筒截面。
- 面板分为“挠度 / 净空 / 工具”，共享播放与进度控制；明确叶片编号，移除双视图入口。

### [0.3.0](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.0) · 2026-09-16 · 三机 MAPPO 离线回放

- Demo 升级为完整三机 MAPPO 60 秒回放，ZIP 内置九片叶片形变、测量及同源功率/载荷遥测。
- 统一播放、暂停、单步、停止、复位与进度定位，支持曲线和 JSON 导出。
- 新增侧前方近景、内侧 Down 视角、彩色叶尖轨迹、红色叶尖标记及 B2 原始斜距。

### [0.2.5](https://github.com/snode11/wind-farm-blender/releases/tag/v0.2.5) · 2026-09-15 · 雷达回放进度与观察视角

- 新增 0–100% 回放定位、播放/暂停/重新播放及片尾统计。
- 新增测量区 45° 透视与雷达特写，切镜头保留工况、进度和暂停状态。
- 整理后端运行面板，改进 B2 有效样本和测量区经过次数的说明。

### [0.2.4](https://github.com/snode11/wind-farm-blender/releases/tag/v0.2.4) · 2026-09-13 · 雷达展示与旧场景修复

- 完善叶片、机舱、密封件材质及雷达支架、螺钉、盖板和线缆细节。
- 测量卡片显示时刻、叶片编号和读数年龄，区分新测量、保留值与等待。
- 修复编辑模式下加载旧场景导致网格修改或 Blender 崩溃的问题。

## 使用与资料

- [详细变更记录](CHANGELOG.md)
- [前端功能、安装与演示说明](前端readme.md)
- [完整用户手册](docs/blender/用户使用手册.md)
- [功能总览、模型区别与后续规划](docs/blender/Blender前端功能总览与规划.md)
- [Blender 前端实现与维护](blender_frontend/README.md)
- [0.3.10 更新计划与历史验证](前端更新计划.md)
- [GW184 项目与视频任务](projects/gw184-single/README.md) · [六类缺陷编辑说明](projects/gw184-single/DEFECT_EDITOR.md)
- [雷达算法说明](wfrl/lidar/README.md)

当前 NREL 双束记录中，94/94 次完整过叶均含 S2/S3 同刻同叶片配对，共 400 个有效样本；S1 有效叶片命中仍独立报警。这不是每帧命中或始终只触发两束的保证。旧三束覆盖率失败记录按原版本阅读；当前数据仍为 **REVIEW_ONLY / PENDING_ACCEPTANCE**，回放不代表现场实测。源码测试、安装包验证、窗口显示、播放性能与实体实验分别判断。

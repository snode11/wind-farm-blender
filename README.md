# Wind Farm Blender

基于 Blender 的风场三维展示与离线仿真回放。

当前版本 **0.3.5**，要求 **Blender 5.2+**。

[下载安装包](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.5/wfrl_blender-0.3.5.zip) · [安装说明](docs/blender/INSTALL.md) · [用户手册](docs/blender/用户使用手册.md)

## 发布记录

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
- [安装包与校验说明](dist/README-lidar.md)
- [Blender 前端说明](blender_frontend/README.md)
- [雷达算法说明](wfrl/lidar/README.md)

当前物理数据为 REVIEW_ONLY，固定三束测量覆盖率未通过；回放不代表现场实测。详见 [0.3.5 发布核对](docs/blender/0.3.5发布核对.md)。

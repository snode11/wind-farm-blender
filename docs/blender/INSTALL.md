# NREL / WFRL Blender 0.3.12 安装

本页仅适用于 NREL 5MW 前端。GW184 三相机与缺陷编辑器 0.4.0 是另一个解压运行包，见 [GW184 使用说明](GW184三相机与缺陷编辑器.md)，无需安装或替换 NREL 扩展。

要求 **Blender 5.2+**。Windows、macOS 和 Linux 使用同一扩展 ZIP；已记录的安装和窗口验证为 macOS Blender 5.2.1，其他平台未完成本轮实机验收。

## 安装与升级

1. 下载 [wfrl_blender-0.3.12.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.12/wfrl_blender-0.3.12.zip)，保留压缩格式。
2. 保存现有 Blender 工作，进入 **Edit → Preferences → Get Extensions → Install from Disk**，选择 ZIP 并启用 **WFRL Blender**。
3. 升级后退出并重启 Blender，让新模块和资源生效；下载 ZIP 或修改仓库不会替换当前进程中的扩展。
4. 新建 General 场景，在三维视图按 **N → MAPPO → 加载 MAPPO · 60 秒**，不要把旧 `.blend` 当作新版默认场景。
5. 主视图先显示 T1 总览。通过 **View → 三相机 → 三路对照** 打开默认 2×2：三路相机和一格静态安装示意；可切三列、放大单路、播放／暂停或单步。
6. 在 **MAPPO → 净空 → 双束净空 / S1** 加载随包双束记录，也可切回原 B2 回放。双束是研究估计，缺失不表示距离为零或状态安全。

## 离线使用

ZIP 内置预弯 v3 三机 60 秒回放、双束数据与读取器、默认三相机配置。离线观看无需克隆仓库、配置 Python 后端、MPI 或 FAST.Farm，也不运行新的求解或训练。

加载 MAPPO 会生成共盒三相机及外伸支架，应用连续重叠的默认取景。固定光心随机舱运动，不持续追踪叶片；根部和极尖端仍可能遮挡。独立单路和单路放大固定使用轻量实体显示，三路保留流畅／高清，原图采集使用独立原始质量设置。

安装、布局和采集见[详细前端说明](../../前端readme.md)、[三相机说明](T1三相机使用说明.md)。更新后先检查界面和回放；新版完整片段性能、跨平台和现场精度仍需分别验证。

## 源码与验证范围

0.3.10–0.3.12 仅发布安装 ZIP。[发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.12)自动生成的 **Source code** 归档不包含这些版本的全部实现，不是安装包。本次文档更新也不会同步扩展源码。

只有拿到与目标版本匹配的完整源码和数据资源后，才可从仓库根目录使用 `python3 scripts/blender/build_extension.py` 构建。不能用公开旧源码构建后仅改版本号冒充新版。实现与维护见[前端实现说明](../../blender_frontend/README.md)，版本及验证边界见[当前发布状态](发布状态与验证范围.md)。

## 现有 MP4 输出

**MAPPO → 视频输出**支持现有 MP4 导出和 RTSP 循环推流；推流需要 FFmpeg 与 MediaMTX。此入口播放已有视频，不是 Blender 视口直播，也不是三相机自动生成三路视频。详见[前端说明的视频输出段落](../../前端readme.md#视频输出)。

## Bridge 后端更新

本扩展包含连接、通信、协议与运行面板；不包含 Python Bridge 服务及启动器。仅安装 `wfrl_blender-0.3.12.zip` 不会更新后端源码。2026-09-30 随 0.4.0 发布同步的 macOS/MPI 启动环境修复位于仓库 `wfrl/blender_bridge/isolated_trainer.py`；使用已配置的后端时，同步仓库源码后重启 Bridge。已运行的训练或求解需先正常停止。

GW184 独立运行包不依赖 Bridge。

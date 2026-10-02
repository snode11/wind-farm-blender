# NREL / WFRL Blender 0.3.13 安装

本页适用于 NREL 5MW 前端。雷达更新只发布到本扩展；GW184 三相机与缺陷编辑器 0.4.0 保持独立，见 [GW184 使用说明](GW184三相机与缺陷编辑器.md)。

要求 **Blender 5.2+**。Windows、macOS 和 Linux 使用同一扩展 ZIP；本版验证结果见[发布核对](0.3.13发布核对.md)，Windows/Linux 尚未完成本轮实机验收。

## 安装与升级

1. 下载 [wfrl_blender-0.3.13.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.13/wfrl_blender-0.3.13.zip)，保留压缩格式。
2. 保存现有 Blender 工作，进入 **Edit → Preferences → Get Extensions → Install from Disk**，选择 ZIP 并启用 **WFRL Blender**。
3. 升级后退出并重启 Blender，让新模块和资源生效；下载 ZIP 或修改仓库不会替换当前进程中的扩展。
4. 新建 General 场景，在三维视图按 **N → MAPPO → 加载 MAPPO · 60 秒**，不要把旧 `.blend` 当作新版默认场景。
5. 主视图先显示 T1 总览。通过 **View → 三相机 → 三路对照** 打开默认 2×2：三路相机和一格静态安装示意；可切三列、放大单路、播放／暂停或单步。
6. 在 **MAPPO → 净空 → 双束净空 / S1** 加载默认 `hub-axis.v1` 记录；显式选择 **TLS 候选 / S1** 加载 `hub-tls.v1`。仍可切回原 B2 回放。

## 离线使用

ZIP 内置预弯 v3 三机 60 秒回放、旧法与 TLS 两份双束结果层、读取器和默认三相机配置。两种双束方法共享原 40 Hz 源数据。离线观看无需克隆仓库、配置 Python 后端、MPI 或 FAST.Farm，也不运行新的求解或训练。

加载 MAPPO 会生成共盒三相机及外伸支架，应用连续重叠的默认取景。固定光心随机舱运动，不持续追踪叶片；根部和极尖端仍可能遮挡。独立单路和单路放大固定使用轻量实体显示，三路保留流畅／高清，原图采集使用独立原始质量设置。

安装、布局和采集见[详细前端说明](../../前端readme.md)、[三相机说明](T1三相机使用说明.md)。新版完整片段性能、跨平台和现场精度需分别验证。

## 从公开源码构建

0.3.13 同步 NREL 前端、雷达算法、处理工具、测试和自包含构建资源。完整下载并解压 [v0.3.13 的 Source code](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.13)，或克隆仓库后检出该标签。使用 Python 3.11+，从仓库根目录执行：

```bash
python3 scripts/blender/build_extension.py
```

构建生成 `dist/wfrl_blender-0.3.13.zip`、`.zip.sha256` 与 `.inventory.json`。保留源码中的整个 `blender_frontend/wfrl_blender/assets/` 和仓库级 `wfrl/`；构建会校验共享源与两种双束结果层，并将规范读取器和协议放入扩展。离线包构建不需要外部原始 FAST.Farm / VTP 数据。自动生成的 Source code 是开发归档，Blender 安装使用构建后的扩展 ZIP。

0.3.10–0.3.12 的历史源码归档仍对应原提交，不追改。GW184 0.4.0 的实现继续随独立运行 ZIP 提供。实现与维护见[前端实现说明](../../blender_frontend/README.md)，本版检查见[发布核对](0.3.13发布核对.md)。

## 现有 MP4 输出

**MAPPO → 视频输出**支持现有 MP4 导出和 RTSP 循环推流；推流需要 FFmpeg 与 MediaMTX。此入口播放已有视频，不是 Blender 视口直播，也不是三相机自动生成三路视频。详见[前端说明的视频输出段落](../../前端readme.md#视频输出)。

## Bridge 后端更新

本扩展包含连接、通信、协议与运行面板；不包含 Python Bridge 服务及启动器。仅安装 `wfrl_blender-0.3.13.zip` 不会更新后端源码。使用已配置的后端时，同步仓库源码后重启 Bridge；已运行的训练或求解需先正常停止。GW184 独立运行包不依赖 Bridge。

## 升级后确认雷达行为

默认“双束净空 / S1”与“TLS 候选 / S1”分别显示方法身份，S2/S3 卡片与 S1 报警独立。原 0.3.12 内置源记录的 T1/T2/T3 分别有 33/33/28 次完整过叶，94/94 次均有同刻同叶片配对，共 400 个有效样本，每次至少连续 3 个保存采样点；0.3.13 沿用该源。此处“正常过叶触发两束”指过叶窗口含有效配对，不要求每一帧都有读数，也不关闭 S1。

TLS 仍是研究候选，默认旧法不变。两种方法均为 **REVIEW_ONLY / PENDING_ACCEPTANCE**；缺失不表示距离为零或状态安全，软件验证不等于现场精度或保护延迟验收。

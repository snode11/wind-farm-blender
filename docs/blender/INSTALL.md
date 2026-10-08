# NREL / WFRL Blender 0.3.18 安装

更新日期：2026-10-08。本页适用于 NREL 5MW 前端 **0.3.18**，修复场景／播放隔离、C2 槽位和相机采集状态，增加 GPU 事务复用、默认关闭的诊断及 float64 姿态读取；保留同源纹理、独立合成、原几何和六类合成缺陷入口。GW184 三相机与缺陷编辑器 0.4.0 保持独立，见 [GW184 使用说明](GW184三相机与缺陷编辑器.md)。

要求 **Blender 5.2+**。发布前最终 ZIP 的 macOS Blender 5.2.1 LTS 隔离安装、GPU／多窗口和日常安装检查通过；发布源码复建与该 ZIP 全包字节一致，没有重做全部原生检查。Windows/Linux 未实测，完整范围见[0.3.18 发布核对](releases/0.3.18发布核对.md)。

## 发布身份与验证

GitHub 当前公开包为 **0.3.18**，[v0.3.18](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18) 与扩展源码提交 [`6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02`](https://github.com/snode11/wind-farm-blender/commit/6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02) 对应。扩展 ZIP 为 172 文件，SHA-256 `f5fb69e6e8cdc58edf1d1f76fd4eacc1e108b688768635cc3a5a05c04bdfc400`。

发布源码 CPU 检查为 **215 passed、2 skipped、233 subtests passed**，与发布前宿主 **117 passed、1 skipped、6 subtests passed** 分开记录；本机 172 文件安装、真实 GPU／多窗口和原 0.2 mm **FAILED_GATE** 的边界见[发布前修复与验证](validation/NREL本地0.3.18修复与验证.md)。软件修复与公开交付不改变科学接受状态。

## 安装与升级

1. 下载 [wfrl_blender-0.3.18.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.18/wfrl_blender-0.3.18.zip)，保留压缩格式。
2. 保存现有 Blender 工作，进入 **Edit → Preferences → Get Extensions → Install from Disk**，选择 ZIP 并启用 **WFRL Blender**。
3. 升级后退出并重启 Blender，让新模块和资源生效；下载 ZIP 或修改仓库不会替换当前进程中的扩展。
4. 新建 General 场景，在三维视图按 **N → MAPPO → 加载 MAPPO · 60 秒**。**打开几何重建示例…** 仍可打开包内便携几何示例；当前文件未保存时，Blender 会先提示保存。
5. 在 **MAPPO → 同源纹理同步对照** 查看右侧 601 个保存样本与固定灰度图集，切换中段／叶尖观察点、原始／对比增强、完整取景及 CURRENT 恢复。上一／下一样本先暂停并在首尾钳制；连续播放两侧共同循环。
6. **独立合成纹理样例**继续提供 120 个 10 Hz 保存样本（0–11.9 s），保留独立游标；样本步进会暂停左侧 MAPPO，两者不是同源同步。两个纹理来源分别保留显示和观察设置，退出后返回及保存重开保留当前所选来源。

公开版本和 manifest 版本均为 `0.3.18`。从历史 `0.3.17+1` 升级时安装新 ZIP 并重启；`0.3.17+1` 的 build metadata 只属于 0.3.17.1 历史包，不作为当前版本标识。

## 原三机回放与雷达入口

需要原三机展示时，新建 General 场景，在 **N → MAPPO → 加载 MAPPO · 60 秒** 加载随包数据。主视图先显示 T1 总览；**View → 三相机 → 三路对照** 打开默认 2×2（三路相机和一格静态安装示意），也可切三列、放大单路、播放／暂停或单步。

在 **MAPPO → 净空 → 双束净空 / S1** 加载默认 `hub-axis.v1` 记录；显式选择 **TLS 候选 / S1** 加载 `hub-tls.v1`，仍可切回原 B2 回放。

## NREL 叶片缺陷

加载 **MAPPO · 60 秒** 后，在 **MAPPO → NREL 5MW · 叶片缺陷** 点击 **启动缺陷编辑器**。选择 T1/T2/T3 的任一叶片，支持六类合成缺陷的新建、修改、预览确认／取消、撤销重做、健康对照、JSON 读写和保存重开；操作及三相机分析见 [NREL 缺陷编辑器](NREL缺陷编辑器.md)。

缺陷随保存的柔性回放运动，不改变结构或气动物理及测量结果。G 分析的 VISIBLE/PARTIAL 不证明图片可辨识。既有叶尖精度回归失败与控制台切换原生崩溃记录保留，见 [0.3.16 发布核对](releases/0.3.16发布核对.md)；本版未通过整体窗口稳定性验收。

## 离线使用

ZIP 内置预弯 v3 三机 60 秒回放、旧法与 TLS 两份双束结果层、读取器、默认三相机配置、NREL 缺陷编辑器、T1/B1 默认修补痕迹及同步重建分屏示例。两种双束方法共享原 40 Hz 源数据。示例场景、MAPPO 数据和四张 packed 天空／地表纹理均在包内，无需作者本机路径、克隆仓库、Python 后端、MPI 或 FAST.Farm。

0.3.15／0.3.16 原始几何分屏示例右侧三片叶片各保存 601 个 10 Hz 重建样本，采用 CONSTANT 保持；60 Hz 显示时间轴不会生成额外重建测量。播放时刻、保存样本时刻和保持时长分别显示，例如 frame 52 为 117.850 s／117.800 s／50 ms。绿为输入约束支持，橙为模型推断，均是算法估计。软件播放通过不代表稳定 60 FPS 或重建精度验收。

0.3.18 保留 `assets/blade_recon_mappo_tex/`（0.3.17.1 首次交付）：同一次两相机实验的 601 个保存状态、嵌入 JSON 与三张 packed 固定灰度图集，0–60 s 对应 MAPPO 117–177 s，按同源摘要恢复。原图为 Workbench 仿真材质色，图集存在模糊、条带、接缝和叶尖错贴／拖影；几何与纹理准确性仍为 NOT_ACCEPTED，中段／叶尖按钮是人工观察点，不代表缺陷检测。

0.3.18 还保留 0.3.17 加入的 `assets/blade_recon_synth_tex/` 保存重建结果与三片 RGBA 图集。**叶片三维重建** 标签支持独立结果导入、packed 纹理、原始／增强灰度／青洋红显示及保存重开。人工近景和显示增强不构成自动缺陷检测或几何精度验收；外部求解研究代码不随扩展打包。详见 [Blade Recon 使用说明](BladeRecon可视化使用说明.md)。

加载 MAPPO 会生成共盒三相机及外伸支架，应用连续重叠的默认取景。固定光心随机舱运动，不持续追踪叶片；根部和极尖端仍可能遮挡。独立单路和单路放大固定使用轻量实体显示，三路保留流畅／高清，原图采集使用独立原始质量设置。

安装、布局和采集见[详细前端说明](../../前端readme.md)、[三相机说明](T1三相机使用说明.md)。新版完整片段性能、跨平台和现场精度需分别验证。

## 从公开源码构建

**0.3.18 已同步匹配扩展源码与 ZIP。** 下载完整 [v0.3.18 Source code](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18)，或克隆仓库后检出 `v0.3.18`，保留整个 `blender_frontend/wfrl_blender/assets/` 与仓库级 `wfrl/`。从仓库根目录运行 `python3 scripts/blender/build_extension.py`，生成 `dist/wfrl_blender-0.3.18.zip` 及相应 inventory／SHA-256 文件；源码发布复建已确认与最终验证 ZIP 完全一致。自动 Source code 仍是开发归档，Blender 安装用构建后的扩展 ZIP。

0.3.17.1 历史版仅发布 ZIP，`v0.3.17.1` 指向当时提交 `9d7b5596a781b8ea5040d18b2f009268313a8821`，不能据该旧标签构建 0.3.17.1 的完整功能；本次不追改旧标签。本地新增缺陷编辑器和纹理检查 CLI 尚未公开，不能据 0.3.18 扩展源码同步推定这些独立入口可用。

以下是 **0.3.13 历史构建步骤**。0.3.13 同步 NREL 前端、雷达算法、处理工具、测试和自包含构建资源。完整下载并解压 [v0.3.13 的 Source code](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.13)，或克隆仓库后检出该标签。使用 Python 3.11+，从仓库根目录执行：

```bash
python3 scripts/blender/build_extension.py
```

构建生成 `dist/wfrl_blender-0.3.13.zip`、`.zip.sha256` 与 `.inventory.json`。保留源码中的整个 `blender_frontend/wfrl_blender/assets/` 和仓库级 `wfrl/`；构建会校验共享源与两种双束结果层，并将规范读取器和协议放入扩展。离线包构建不需要外部原始 FAST.Farm / VTP 数据。自动生成的 Source code 是开发归档，Blender 安装使用构建后的扩展 ZIP。

0.3.10–0.3.12 的历史源码归档仍对应原提交，不追改。GW184 0.4.0 的实现继续随独立运行 ZIP 提供。实现与维护见[前端实现说明](../../blender_frontend/README.md)，0.3.13 历史检查见[对应发布核对](releases/0.3.13发布核对.md)，当前检查见[0.3.18 发布核对](releases/0.3.18发布核对.md)。

## 现有 MP4 输出

**MAPPO → 视频输出**支持现有 MP4 导出和 RTSP 循环推流；推流需要 FFmpeg 与 MediaMTX。此入口播放已有视频，不是 Blender 视口直播，也不是三相机自动生成三路视频。详见[前端说明的视频输出段落](../../前端readme.md#视频输出)。

## Bridge 后端更新

本扩展包含连接、通信、协议与运行面板；不包含 Python Bridge 服务及启动器。仅安装 `wfrl_blender-0.3.18.zip` 不会更新后端源码。使用已配置的后端时，同步适用后端源码后重启 Bridge；已运行的训练或求解需先正常停止。0.3.18 源码同步范围为扩展、构建脚本与相关测试，后端服务仍需单独核对。GW184 独立运行包不依赖 Bridge。

## 升级后确认雷达行为

默认“双束净空 / S1”与“TLS 候选 / S1”分别显示方法身份，S2/S3 卡片与 S1 报警独立。原 0.3.12 内置源记录的 T1/T2/T3 分别有 33/33/28 次完整过叶，94/94 次均有同刻同叶片配对，共 400 个有效样本，每次至少连续 3 个保存采样点；0.3.13–0.3.18 沿用该源。此处“正常过叶触发两束”指过叶窗口含有效配对，不要求每一帧都有读数，也不关闭 S1。

TLS 仍是研究候选，默认旧法不变。两种方法均为 **REVIEW_ONLY / PENDING_ACCEPTANCE**；缺失不表示距离为零或状态安全，软件验证不等于现场精度或保护延迟验收。

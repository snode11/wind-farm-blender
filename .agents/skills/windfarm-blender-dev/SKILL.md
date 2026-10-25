---
name: windfarm-blender-dev
description: Use when developing, troubleshooting, or documenting this repository's native Blender frontend or extension delivery.
---

# Windfarm Blender 开发

从实际 Git 根目录工作，遵守 [AGENTS.md](../../../AGENTS.md)。本文链接相对 skill 目录；模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

按需查[操作说明](../../../前端readme.md)、[实现与接口](../../../blender_frontend/README.md)、[待实施要求与验收条件](../../../前端更新计划.md)及[发布状态与验证范围](../../../docs/blender/发布状态与验证范围.md)。修改描述前核对代码、配置和测试；本 skill 不另存版本或通过数量。

## 运行来源与入口

- MAPPO 离线回放复用正式包，无需 Bridge、FAST.Farm 或网络；界面、相机、回放和文档任务不触发求解。算法需要新结果时使用独立输出。后端 Replay、Interactive、Formal Training 分别核对环境；[网页实验室](../../../docs/proposal/platform/风场网页实验室_第一版方案与分工汇总.md)使用独立计算与网页渲染链路。
- 工作区、构建 ZIP、公开附件和安装分别识别。兼容版本读 `blender_manifest.toml`，实际来源读 `addon.__file__`／`frontend_diagnostics.py`，载荷核对 `build-info.json`、SHA-256 与 inventory；版本号或源码同步不证明一致。替换安装后重启 Blender，[日常安装身份](../../../publication/daily-installation.json)单独核对。
- 项目启动器加载安装扩展；仓库 `scripts/blender/run_wfrl_macos.sh` 已包含 `launch`，直接运行 `scripts/blender/wfrl_launcher.py` 才加此子命令。普通手动连接需要 `Load & Validate Scene`，启动器主动连接提供自动加载。

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| 手动场景、姿态与变桨 | `manual_scene.py`、`manual_control.py`、`manual_motion.py`、`manual_pose.py`；仓库 `scripts/blender/open_manual_control.py` | [手动控制](../../../docs/blender/风机手动控制.md) |
| Down 视频与后台作业 | `manual_down.py`、`manual_video*.py`；仓库 `scripts/blender/manage_manual_video.py`、`wfrl/camera_video/` | [视频操作与依赖](../../../docs/blender/风机手动控制.md#导出变桨视频) |
| 柔性运动、外形与测量 | `farm_flex.py`、`turbine_geometry.py`、`deflection.py` | [实现说明](../../../blender_frontend/README.md) |
| 场景、播放归属与性能 | `scene_builder.py`、`playback.py`、`performance.py` | [验证入口](references/verification.md) |
| 重建对照与纹理 | `split_reconstruction*.py`、`split_turbine_context.py`、`blade_recon_*.py` | [操作说明](../../../前端readme.md)、[Blade Recon](../../../docs/blender/BladeRecon可视化使用说明.md) |
| Down、机舱与三相机 | `cameras.py`、`nacelle_camera.py`、`stacked_camera_rig.py`、`custom_camera*.py` | [Down](../../../docs/blender/T1Down机舱下视.md)、[机舱](../../../docs/blender/NREL机舱相机.md)、[三相机](../../../docs/blender/T1三相机使用说明.md) |
| 缺陷编辑与分析 | `nrel_defects/`、`repair_marks.py` | [NREL 缺陷编辑器](../../../docs/blender/NREL缺陷编辑器.md) |
| 雷达读数、报警与计算 | `clearance_replay.py`、`clearance_visual.py`、`radar_feedback.py`；仓库 `wfrl/lidar/`、`scripts/lidar/` | [结果合同](../../../wfrl/lidar/REPLAY_FORMAT.md)、[算法](../../../wfrl/lidar/README.md)、[计算流程](../../../scripts/lidar/README.md) |
| 后端连接与训练 | `runtime.py`、`transport.py`、`panels/run.py`；仓库 `wfrl/blender_bridge/` | [训练入口与合同](../../../docs/training/MAPPO_SETUP.md) |
| 扩展构建与安装 | 仓库 `scripts/blender/build_extension.py`、`scripts/blender/validate_frontend_package.py` | [验证与打包命令](references/verification.md) |

[动态研究查看器](../../../wfrl/nrel_reconstruction/README.md)可能使用冻结源码，不随工作区修改更新；[GW184](../../../projects/gw184-single/README.md)使用独立模型与配置，不混用 NREL 身份。

## 关键约束

### 运动与场景

- 手动场景默认 rootkink，可选 NREL，与 MAPPO 的对象、网格、材质和环境隔离；进入前须处于对象模式并停止、断开后端。刚性运动不生成对应物理或雷达数据，界面范围不代表实体安全限值。
- 批量编辑只传播当前字段，分别变桨仅初始化模式切换的机组。普通旋转按墙钟积分、变速保留方位、零 RPM 不运行；`PitchClock` 保存实际桨距，暂停与恢复不推进未显示姿态。JSON 整体校验、按编号应用，保存同刻实际姿态；载入、重开、撤销／重做后暂停。
- T1 冻结试验使用 `PitchTrial.pose_at(t)`／`TrialClock`，与普通时钟分开；目标整体确认，未选叶片与 T2/T3 保持原参数，锁定 T1 及外形，保存配置与时间。复位与结束语义查手动控制专题。
- 回放姿态、读数、S1 与统计共用仿真时钟；寻址／重播不重复累计或暴露未来事件，插值不补测量。场景共享网格时拒绝第二时钟；`playback.py` 管理进程内唯一原生 player，切场景、关窗及延迟恢复不得抢占其他播放。
- 重建拒绝占用其他场景的风场资源；重开文件保留已认领后端会话身份，等待重连同步。恢复失败保持未就绪。可见性优化保留隐藏 T1 测量、塔筒／共享结构、缺陷与导出消费者；同刻切视图补齐工作集，缓存区分来源、源帧与工作集，重绑／换模型失效。

### 相机、资源与重建

- Down 随各自机舱／塔顶运动，不随叶轮或锁定世界重力；手动 Down 绑定条目 `yaw_object`。Down、独立机舱相机与 C1/C2/C3 分别维护光学和复位规则；三相机按真实槽位路由、共用时钟，支架端贴合，不追踪叶片或强制三等分。
- 替换相机须事务重绑场景、局部视口与退出快照，保留忙时保护、撤销及失败回滚；复位保留时刻、播放和自由视图。保存写真实槽位与原画质，保留局部镜头，失败也恢复观察状态；采集中异常或原生修改须中断并保留用户修改。关闭只释放自有资源，容忍删除的 RNA，失败可重试；保护共享、fake-user、shape-key 及无关数据。
- rootkink 为合成展示外形，沿用 NREL 运动、载荷与测量；切换结束冲突会话，网格与共享引用共同提交或回滚。重建右侧只有叶片是估计，塔筒、机舱和轮毂是共享仿真结构，随源姿态更新并核验绑定。
- 同源纹理绑定 manifest、模型、摘要与保存样本时钟；独立 `synth_tex` 来源和游标分开。播放、切模式及恢复保留手动视角，增强不生成未观测细节或检测结果。便携场景保留 packed 图片、无 linked libraries，以 `__WFRL_BUNDLED_MAPPO__` 和 manifest SHA-256 识别内置源；只恢复同哈希包，不匹配清空报错，不任意回退或使用不支持的 `//` 路径。
- 缺陷是 `physics_coupled=False` 的 NREL 合成几何，不直接套用 GW184；预览不写提交配置，删除辅助轮廓不删缺陷。G 分析冻结完整三相机配置，变更后重新冻结。保留完整画幅与真实遮挡；入框、射线可见、全周期无遮挡、图像检测、拼接及实体标定分别判断。

### 测量与后端

- B2 主统计只用 B2，分母为全部 expected 样本，偏差为同次估计减真值；不借其他叶片或真值补缺。双束同刻、同叶片配对，S1 报警独立；`hub-axis.v1`／`hub-tls.v1` 保留方法、来源及结果层身份。缺失、过期、空统计不补零，缺包、损坏或身份不符清旧读数；结构叶尖、展示表面、测量参考和光束长度分开，无读数或无报警不等于安全。
- `BridgeServer.poll()` 无客户端仍推进；快照／统计与可靠生命周期／安全／错误事件分队列，溢出报 `EVENT_OVERFLOW` 并失败停止，重连保留等待年龄但不保证完整观察历史。全场功率要求同一步全部预期机组及合计有限；Interactive 与 Formal Training／SB3 分别核对。MPI 清除继承作业身份、保留显式传输配置，macOS 设置仅作缺省；后端修改后重启 Bridge。

### 视频作业

- Down 便捷视频只驱动所选机组，源场景暂停；冻结试验视频使用最近确认配置，按 `t=k/fps` 生成整数帧，不依赖预览。均用独立场景，其余机组保持快照，后台副本停用墙钟；严格媒体验证和 MP4／JSON 拒绝覆盖提交不可省略，短片不替代完整视频或监管验证。
- 冻结试验监管需完整工作区、FFmpeg／FFprobe 及支持 `flock / waitid(WNOWAIT)` 的 macOS／Linux Python 3.11+（`WFRL_VIDEO_PYTHON`）。扩展 ZIP 不含配套脚本及 `wfrl.camera_video`，安装入口通过偏好 `project_dir` 选工作区。注册容忍受限数据访问，场景可用后恢复；按身份、凭据及锁恢复，复制场景不认领原作业，取消不信任磁盘 PID。
- 仅 `final=true` 的 `COMPLETE / FAILED / CANCELLED` 是最终结果；`LOST` 不等于失败或取消，`--detach` 只确认启动。`retry` 只从匹配完整母版重做编码、验证与发布，不恢复未渲染帧；保留目标预检及失败母版，只清理自有暂存，不改写成功状态。

## 验证与交付

按[维护与验证入口](references/verification.md)选择相关检查；纯文档只静态校验。软件、安装载荷、窗口、真实输入、完整片段 FPS、科学精度与现场效果分别判定；采样、显示时间轴和实际 FPS 分开。`POST_PIXEL` 仅证明视口绘制，理想射线、瞬时成像及 60 Hz 时间轴不证明硬件采样、曝光或保护延迟。叶尖保存帧 0.2 mm、插值帧 5 mm 门槛及适用结论查验收与发布文档，不放宽阈值或改正式资产制造通过。

- 扩展源码、资源、规范协议或内置读取器进入安装版时重建 ZIP；skill、说明及项目侧后端改动不自动要求打包。构建时生成的 `protocol.py`、`_vendor/lidar/` 分别来自仓库 `wfrl/blender_bridge/messages.py`、`wfrl/lidar/`；修改规范源，不手改生成副本或替换冻结模型绕过校验。
- 内置雷达结果层共享 `assets/mappo/`，换源须匹配 sidecar；外部便携双束带自身 `source/`。normal/close 是外部刚性示意，其结果包和原始 VTP 不随扩展提供。交付核对包身份及适用安装，上传后遵守 AGENTS.md；打包不等于物理结果生成、科学验收或公开发布。

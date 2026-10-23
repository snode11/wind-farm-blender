---
name: windfarm-blender-dev
description: 开发、排查和验证本仓库 Blender 原生前端及相关文档，涵盖手动控制、视频导出、回放、相机、重建、净空雷达和扩展交付。
---

# Windfarm Blender 开发

从实际 Git 根目录工作，遵守 [AGENTS.md](../../../AGENTS.md)。本文件链接相对 skill 目录；模块路径以 `blender_frontend/wfrl_blender/` 为基准，标为“仓库”的路径从 Git 根目录解析。

操作看[前端使用说明](../../../前端readme.md)，结构与接口看[前端实现说明](../../../blender_frontend/README.md)，待实施要求看[前端更新计划](../../../前端更新计划.md)，交付和验收状态看[发布状态与验证范围](../../../docs/blender/发布状态与验证范围.md)。修改描述前核对相关代码、配置和测试；本 skill 不另存版本状态或测试通过数量。

## 范围与运行来源

- MAPPO 离线回放复用正式结果包，不需要 Bridge、FAST.Farm 或网络。界面、相机、回放和文档任务不触发物理求解；算法任务需要新结果时使用独立输出，不覆盖正式源。
- 后端 Replay、Interactive、Formal Training 分别核对入口与环境。[网页实验室](../../../docs/proposal/platform/风场网页实验室_第一版方案与分工汇总.md)使用独立计算与网页渲染链路，不以 Blender TCP 连接或原生前端回归作为验收。
- 核对工作区源码、构建 ZIP、公开附件和实际安装目录：兼容版本读 `blender_manifest.toml`，实际来源读 `addon.__file__`，载荷核对 `build-info.json`、SHA-256 与 inventory。同版本号、GitHub Source code 归档或源码同步均不证明安装载荷一致；替换安装后重启 Blender。日常安装证据见[安装核对](../../../publication/daily-installation.json)。
- 日常模块通常为 `bl_ext.user_default.wfrl_blender`，隔离验证为 `bl_ext.wfrl_frontend.wfrl_blender`；`frontend_diagnostics.py` 可核对来源。手动源码入口为仓库 `scripts/blender/open_manual_control.py`，macOS 包装器 `scripts/blender/打开手动恒速变桨.command` 使用独立配置与工厂启动，支持 `WFRL_BLENDER_BIN`。
- 项目启动器加载已安装扩展；仓库 `scripts/blender/run_wfrl_macos.sh` 已包含 `launch`，直接调用 `scripts/blender/wfrl_launcher.py` 才加该子命令。普通手动连接需要 `Load & Validate Scene`，启动器主动连接提供自动加载。

## 模块入口

按任务读取对应模块和专题，不默认加载全部资料。

| 功能 | 主要模块 | 专题 |
| --- | --- | --- |
| 手动场景、姿态与变桨 | `manual_scene.py`、`manual_control.py`、`manual_motion.py`、`manual_pose.py`、`panels/manual_control.py` | [手动控制](../../../docs/blender/风机手动控制.md) |
| 手动视频与作业管理 | `manual_video.py`、`manual_video_job.py`、`manual_video_render.py`；仓库 `scripts/blender/manage_manual_video.py`、`wfrl/camera_video/` | [视频操作与依赖](../../../docs/blender/风机手动控制.md#导出变桨视频) |
| 柔性回放、叶片外形与挠度 | `farm_flex.py`、`turbine_geometry.py`、`deflection.py`、`tower_motion.py`、`prebend.py`、`tip_tracking.py`、`panels/farm_replay.py` | 前端实现说明的运动与测量参考 |
| 场景恢复、播放归属与性能 | `playback.py`、`frontend_diagnostics.py`、`performance.py`、`playback_benchmark.py` | [验证入口](references/verification.md) |
| 重建对照与共享结构 | `split_reconstruction.py`、`split_reconstruction_ui.py`、`split_reconstruction_timing.py`、`split_mappo_texture.py`、`split_turbine_context.py` | 前端使用说明的重建对照 |
| 独立重建与纹理检查 | `blade_recon_data.py`、`blade_recon_review.py`、`blade_recon_surface.py`、`blade_recon_inspection.py`、`split_surface_texture.py` | [Blade Recon](../../../docs/blender/BladeRecon可视化使用说明.md) |
| Down 与独立机舱相机 | `cameras.py`、`panels/gimbal.py`、`nacelle_camera.py`、`nacelle_visibility.py` | [Down](../../../docs/blender/T1Down机舱下视.md)、[机舱相机](../../../docs/blender/NREL机舱相机.md) |
| 三相机安装、观察与采集 | `stacked_camera_rig.py`、`custom_cameras.py`、`native_camera_views.py`、`custom_camera_capture.py`、`custom_camera_preview.py` | [三相机](../../../docs/blender/T1三相机使用说明.md) |
| 缺陷编辑与几何分析 | `nrel_defects/`、`repair_marks.py` | [NREL 缺陷编辑器](../../../docs/blender/NREL缺陷编辑器.md) |
| 雷达读数、报警与显示 | `clearance_replay.py`、`clearance_visual.py`、`radar_feedback.py`、`panels/clearance.py` | [结果包合同](../../../wfrl/lidar/REPLAY_FORMAT.md) |
| 雷达计算与打包 | 仓库 `wfrl/lidar/`、`scripts/lidar/` | [算法](../../../wfrl/lidar/README.md)、[计算流程](../../../scripts/lidar/README.md) |
| 后端连接与训练 | `runtime.py`、`transport.py`、`panels/run.py`；仓库 `wfrl/blender_bridge/`、`scripts/blender/wfrl_launcher.py` | [训练入口与合同](../../../docs/training/MAPPO_SETUP.md) |
| 扩展构建与隔离安装 | 仓库 `scripts/blender/build_extension.py`、`scripts/blender/validate_frontend_package.py` | 本文交付约束与验证入口 |

[动态研究查看器](../../../wfrl/nrel_reconstruction/README.md)与扩展重建对照分别识别；研究包可能使用自身冻结源码，工作区修改不会更新它。[GW184](../../../projects/gw184-single/README.md)使用独立模型与配置，不混用 NREL 身份。

## 必须保留的行为

### 运动、时钟与场景

- 工作区手动场景使用 rootkink，对象、网格、材质和环境独立于 MAPPO；进入前须处于对象模式并停止、断开活动后端。手动刚性运动不产生对应功率、载荷、尾流、形变或雷达数据，界面范围不代表实体安全限值。
- 批量编辑只传播当前字段；统一桨距覆盖三片独立值，批量开启分别变桨只初始化发生模式切换的机组。普通旋转按墙钟积分，改变 RPM 保留方位，零 RPM 不运行。姿态 JSON 先整体校验、按机组编号应用，只保存同刻姿态；载入、重开、撤销／重做与失效绑定后暂停。
- 手动 T1 试验以 `PitchTrial.pose_at(t)` 和 `TrialClock` 共用时间，固定正 RPM，支持 `SMOOTH / LINEAR` 变桨。目标暂存后整体确认，未选叶片保持起始桨距，T2/T3 参数不变；试验期间锁定 T1 参数，复位恢复起点，结束保留最近冻结配置。`.blend` 保存试验配置与时间。
- 回放姿态、读数、S1 和统计共用仿真时钟；寻址、重播不重复累计或暴露未来事件。源采样、显示时间轴与实际 FPS 分开，插值不生成缺失测量。
- 回放按场景绑定，linked scene 共用网格时拒绝第二时钟；缺对象、缺包或恢复失败保持未就绪，不退回假读数。Blender 同进程只有一个原生 player，归属由 `playback.py` 管理；切场景、关窗与延迟恢复不得抢占其他场景播放。
- 可见性优化须保留隐藏 T1 的结构叶尖测量、塔筒与共享结构更新，以及缺陷分析和导出消费者。同刻切视图补齐工作集；缓存按来源、源帧与工作集区分，重绑或换模型失效。共享、fake-user、shape-key 和无关网格不得作为自有垃圾释放。

### 相机与资源

- Down 随各自机舱和塔顶运动，不随叶轮旋转、不锁定世界重力方向；手动 Down 使用条目的 `yaw_object` 和独立归属。独立机舱相机、Down 与 C1/C2/C3 的光学参数和复位规则分别维护，保留完整画幅与真实结构遮挡。
- 三相机按真实槽位路由，共用场景和时钟；盒体贴合约束作用于支架端，固定相机不追踪叶片，也不要求三等分。默认复位保留时刻、播放与自由视图，遵守忙时保护、撤销和失败回滚。
- 相机替换须在同一事务重绑场景、局部视口及控制器退出快照。保存写入真实槽位相机和原画质，不写临时观察代理；主镜头不覆盖观察窗口局部镜头，保存失败也恢复观察状态。
- 关闭逐项释放自有资源，容忍已删除 RNA，保留失败项供重试并保护共享数据。采集中非有限状态或原生修改须中断，恢复事务且保留用户修改；PNG 原图质量与观察窗口显示质量分开。
- 表面入框、有限射线可见、全周期无遮挡、图像可辨识、拼接和实体标定分别判断；不得隐藏或缩小结构来制造通过。

### 重建、外形与缺陷

- rootkink 是合成展示外形，继续使用 NREL 保存运动、载荷和测量；外形切换须结束冲突编辑／观察会话，网格与共享结构引用共同提交或回滚。
- 重建右侧塔筒、机舱和轮毂是共享仿真结构，只有叶片是重建估计。共享结构在源姿态与塔筒更新后同步；来源或绑定变化时重新核验。
- 同源纹理绑定 MAPPO manifest、模型、文件摘要和保存样本时钟；独立 `synth_tex` 的来源与游标分开。观察点只定位一次，播放保留手动视角，模式切换与保存恢复不重新取景；增强只改变显示，不生成未观测细节或自动检测结果。
- 便携场景以 `__WFRL_BUNDLED_MAPPO__` 和 manifest SHA-256 识别内置源，保留 packed 图片及无 linked libraries。失效路径只恢复同哈希内置包；已有目录仍需校验，不匹配清空报错，不回退任意默认包或不支持的 `//` 路径。
- 缺陷为 `physics_coupled=False` 的合成几何，JSON 绑定 NREL 健康参考，不能直接套用 GW184。临时预览不持久化为提交配置；删除辅助轮廓不删缺陷。G 分析须冻结完整三相机配置，配置变化后重新冻结；几何 `VISIBLE/PARTIAL` 不代表图片检测通过。

### 测量与后端

- B2 主统计只用 B2，分母为全部 expected 样本，偏差为同次估计减真值；不得借其他叶片或真值补缺。双束须同刻、同叶片配对，S1 有效报警独立于双束；无读数或无报警不等于安全。
- `hub-axis.v1` 与 `hub-tls.v1` 保持各自方法、来源和结果层身份。缺失、过期或空统计不补零；缺包、损坏或身份不符清除旧读数。结构叶尖、展示表面、测量参考和光束显示长度不能混用。
- `BridgeServer.poll()` 无客户端时仍推进会话；可合并快照／统计与可靠生命周期／安全／错误事件分队列，真实溢出报告 `EVENT_OVERFLOW` 并失败停止。重连保留等待年龄，不承诺完整观察历史。
- 全场功率要求同一步全部预期机组及合计有限，不能用部分合计冒充全场；有效单机仍可用。Interactive 与 Formal Training／SB3 路径分别核对。MPI 清除继承作业身份，保留显式传输配置，macOS 传输设置仅作缺省；项目侧后端修改后重启 Bridge。

### 视频作业

- 使用最近确认的冻结试验，按 `t=k/fps` 生成整数帧，不依赖预览进度；后台场景副本停用墙钟，只驱动 T1，其余机组保持快照姿态。输出规格和具体工况见视频专题，不作为其他任务的默认要求。
- 导出需要完整工作区、FFmpeg／FFprobe、独立 Python 3.11+（`WFRL_VIDEO_PYTHON`）及 `flock / waitid(WNOWAIT)`；扩展 ZIP 不包含所需项目脚本和 `wfrl.camera_video`。安装入口通过偏好 `project_dir` 选择配套工作区，渲染后台使用其源码；注册时须容忍 Blender 受限数据访问，并在场景可用后恢复作业。
- 作业按身份、凭据与锁恢复；复制场景不认领原作业。取消经验证端点停止原始进程组，不信任磁盘 PID；仅带 `final=true` 的 `COMPLETE / FAILED / CANCELLED` 为最终结果，`LOST` 不等于失败或取消，`--detach` 只确认启动。
- `retry` 只从校验完整且匹配的母版重试编码、验证和发布，不恢复未渲染帧。目标预检、严格媒体验证和拒绝覆盖提交必须保留；失败保留母版，仅清理已确认归属的暂存，成功状态不改写。

## 验证与交付

按改动读取[维护与验证入口](references/verification.md)，选择相关宿主、原生后台、实际窗口或隔离安装检查。纯文档只做静态校验；相关检查通过且没有新变化或未解决问题后结束，不默认运行整套测试。

软件回归、安装载荷、可见窗口、真实鼠标／键盘、完整片段 FPS、科学精度及现场效果分别判定。`POST_PIXEL` 仅代表视口绘制完成；理想射线、瞬时成像和 60 Hz 时间轴不证明硬件采样、曝光、稳定 FPS 或保护延迟。叶尖保存帧 0.2 mm、插值帧 5 mm 门槛及其他未通过的验收按待实施要求保留，不放宽阈值或改正式资产制造通过。

- 扩展源码、资源、规范协议或内置读取器进入安装版时重建 ZIP；skill、说明及项目侧后端改动不自动要求打包。构建、安装和上传均按任务范围执行。
- 修改规范源：`protocol.py` 来自仓库 `wfrl/blender_bridge/messages.py`，`_vendor/lidar/` 来自仓库 `wfrl/lidar/`。不手改生成副本或用替换冻结模型消除校验失败。
- 内置雷达结果层共享 `assets/mappo/`，更换源不能沿用不匹配 sidecar；外部便携双束含自身 `source/`，整体移动校验。normal/close 为外部刚性示意，其结果包和原始 VTP 不随扩展提供。
- 实际交付核对包身份及适用安装验证；附件上传后的处理遵守 AGENTS.md。打包不等于物理结果生成、科学验收或公开发布。

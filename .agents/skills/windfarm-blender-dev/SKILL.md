---
name: windfarm-blender-dev
description: 开发、排查或验收本仓库 Blender 原生前端的风机手动控制、MAPPO 回放、重建对照、相机、NREL 缺陷编辑、净空雷达与扩展交付时使用。
---

# Windfarm Blender 开发

从实际 Git 仓库根目录执行命令；本文件链接相对 skill 目录。遵守 [AGENTS.md](../../../AGENTS.md) 的工作区保护、文档与注释规范。按任务读取相关模块和资料，正文只维护当前功能、要求与限制。

## 任务边界

简要说明涉及模块、最小方案和可观察的验收标准。正常开发完成相关实现与验证；仅文档、方案、基线或报告任务按所请求的范围执行。

| 需求 | 默认处理范围 |
| --- | --- |
| 界面与显示 | 面板、文案、布局、观察相机、光束显示；复用现有 Camera 相机、演示入口和正式结果包。 |
| 回放逻辑 | 加载、切换、暂停、寻址、时钟和读数；核对 Blender 接入与独立读取器，复用保存数据验证。 |
| 数据合同 | schema、字段、摘要、校验和统计口径；评估读取器、发布端、旧包兼容性与扩展内置副本，不削弱校验来迁就数据。 |
| 物理算法 | 标定、估计公式、真值定义、几何与求解参数；按任务决定后处理或新求解，生成独立结果包，不覆盖原数据。 |

MAPPO · 60 秒是随包柔性结果回放，离线查看不需要 Bridge、FAST.Farm 或网络。后端 Replay、Interactive、Formal Training 涉及推理或训练，须单独确认入口和环境。纯界面、相机、回放或 skill 修改不触发物理求解。

网页实验室按[方案与分工](../../../docs/proposal/platform/风场网页实验室_第一版方案与分工汇总.md)处理：已确认方向为网页即时预览、Ubuntu 异步计算和网页结果渲染，运行与回放链路绕过 Blender，不要求现有 Blender TCP 连接。逐机操作适配、独立计算入口、结果导出和网页联调仍待实现与验证，目标 Ubuntu 尚未完成验证；本 skill 的原生前端检查不作为该链路的验收。

## 运行来源与包身份

- 分别核对工作区源码、构建 ZIP、公开附件和实际安装目录。Blender 兼容版本与扩展版本读取 blender_manifest.toml（本地文件：`blender_frontend/wfrl_blender/blender_manifest.toml`）；包身份核对 `build-info.json`、SHA-256 与 inventory，不以版本字符串推定载荷一致。
- 日常启动器加载 `bl_ext.user_default.wfrl_blender`；隔离验证使用 `bl_ext.wfrl_frontend.wfrl_blender`。以实际 `addon.__file__` 和加载环境为准，源码或资源修改不会自动更新安装版；安装后须重启 Blender。
- `frontend_diagnostics.py` 提供实际模块、目录、构建身份和数据来源。其默认关闭的近 2 秒视口采样仅用于排障，不能替代完整片段性能验收。
- 发布身份按[发布状态与验证范围](../../../docs/blender/发布状态与验证范围.md)及对应包证据核对；当前日常安装身份见[安装核对](../../../publication/daily-installation.json)。验证结论只归属于证据指向的载荷与范围，同一版本号不证明载荷相同。GitHub 自动 Source code 归档不证明与安装 ZIP 对应。

## 按功能定位

用户操作看[前端使用说明](../../../前端readme.md)，实现与维护看[前端实现说明](../../../blender_frontend/README.md)。以下模块路径以 `blender_frontend/wfrl_blender/` 为基准；标为“仓库”的路径从 Git 根目录解析。

| 功能 | 维护入口 | 按需资料 |
| --- | --- | --- |
| 独立风机手动控制与墙钟转动 | `manual_scene.py`、`manual_control.py`、`manual_motion.py`、`panels/manual_control.py` | [手动控制说明](../../../docs/blender/风机手动控制.md)及验证入口的手动控制检查 |
| MAPPO 统一侧栏、三机柔性运动与挠度 | `panels/farm_replay.py`、`farm_flex.py`、`tower_motion.py`、`prebend.py`、`deflection.py`、`tip_tracking.py`、`assets/mappo/` | 前端实现说明的时间轴、柔性运动与测量参考章节 |
| 场景恢复、全局播放归属与诊断 | `playback.py`、`farm_flex.py`、`clearance_replay.py`、`frontend_diagnostics.py`、`__init__.py` | [验证入口](references/verification.md)的场景与播放检查 |
| 重建统一入口、模式切换与视图 | `split_reconstruction.py`、`split_reconstruction_ui.py`、`split_reconstruction_timing.py`、`split_mappo_texture.py`、`split_texture_settings.py`、`cameras.py`、`assets/examples/` | 前端使用说明的重建对照章节 |
| 右侧共享塔筒、机舱、轮毂及机械结构 | `split_turbine_context.py`；调用方 `farm_flex.py`、`split_mappo_texture.py`、`split_reconstruction.py` | 本文“重建对照”及共享结构回归 |
| 独立重建结果、纹理导入和检查 | `blade_recon_data.py`、`blade_recon_review.py`、`blade_recon_surface.py`、`blade_recon_inspection.py`、`split_surface_texture.py`、`panels/blade_recon.py` | [Blade Recon 使用说明](../../../docs/blender/BladeRecon可视化使用说明.md) |
| NREL 九片叶片缺陷编辑、JSON 与 G 分析 | `nrel_defects/`、`farm_flex.py`、`repair_marks.py`；仓库 `scripts/blender/nrel_defects.py`、`scripts/blender/open_nrel_defect_editor.py` | [NREL 缺陷编辑器](../../../docs/blender/NREL缺陷编辑器.md) |
| 三相机盒体、支架与默认配置 | `stacked_camera_rig.py`、`panels/stacked_camera_rig.py`、`custom_cameras.py`、`assets/cameras/t1-three-camera-default.json` | [T1 三相机说明](../../../docs/blender/T1三相机使用说明.md) |
| GRID / STRIP / WATCH、窗口清理与原图采集 | `native_camera_views.py`、`installation_schematic.py`、`custom_camera_capture.py`、`custom_camera_preview.py`、`panels/custom_camera_output.py`；播放归属见 `playback.py` | 验证入口的相机生命周期与实际窗口检查 |
| NREL 单路机舱相机、光学画幅与可见性 | `nacelle_camera.py`、`nacelle_visibility.py`、`cameras.py`；仓库 `scripts/blender/check_nrel_nacelle_camera.py`、`scripts/blender/render_camera_video.py` | [机舱相机参数与模型限制](../../../docs/blender/NREL机舱相机.md)及验证入口的机舱相机检查 |
| 云台、总览和雷达外观 | `cameras.py`、`panels/gimbal.py`、`clearance_visual.py`；涉及几何时读 `scene_builder.py`、`turbine_geometry.py` | 前端使用说明与前端实现说明 |
| B2、双束净空与 S1 报警 | `clearance_replay.py`、`radar_feedback.py`、`panels/clearance.py`、`panels/farm_replay.py`；仓库 `wfrl/lidar/replay.py`、`wfrl/lidar/dual_beam_replay.py`、`wfrl/lidar/evidence.py` | [结果包合同](../../../wfrl/lidar/REPLAY_FORMAT.md) |
| 雷达标定、计算与结果打包 | 仓库 `wfrl/lidar/physics.py`、`wfrl/lidar/sampling.py`、`wfrl/lidar/dual_beam.py`、`wfrl/lidar/dual_beam_package.py`、`scripts/lidar/` | [雷达算法说明](../../../wfrl/lidar/README.md)、[计算流程](../../../scripts/lidar/README.md) |
| 后端连接与启动器 | `runtime.py`、`transport.py`、`panels/status.py`、`panels/run.py`；仓库 `wfrl/blender_bridge/server.py`、`backend_session.py`、`isolated_trainer.py`，以及 `scripts/blender/wfrl_launcher.py`、`scripts/blender/wfrl_blender_bootstrap.py` | 前端实现说明的实时连接与控制生命周期章节、[训练入口与合同](../../../docs/training/MAPPO_SETUP.md) |
| 柔性工作量、计时、重绘与完整播放 | `farm_flex.py`、`deflection.py`、`playback_benchmark.py`、`performance.py`、`runtime.py`；仓库 `scripts/blender/run_playback_benchmark.py` | 验证入口的柔性工作量与完整播放检查 |
| 构建、隔离安装与包校验 | `blender_manifest.toml`；仓库 `scripts/blender/build_extension.py`、`scripts/blender/validate_frontend_package.py` | 本文“扩展与数据交付”及验证入口 |

NREL 动态研究的仓库入口为 `scripts/blender/open_nrel_dynamic_review.py` 与 `wfrl/nrel_reconstruction/dynamic_review.py`。本地研究查看器（本地文件：`outputs/nrel-video-dynamic/runs/20261004-integrated/README.md`）使用自身 `viewer/frozen-source/` 和研究包，独立运行；工作区改动不会更新冻结源码。其状态为 `REVIEW_ONLY / DY1 NOT PASSED`，仅四个保存估计时刻，其余帧为 `UNAVAILABLE; no interpolation`。该查看器与 601 样本重建对照分别判定。GW184 的独立模型与配置见[项目说明](../../../projects/gw184-single/README.md)，不混用 NREL 几何身份。

## 当前行为与约束

### 独立风机手动控制

- **N → 风机控制 → 创建 / 进入手动场景** 创建独立三机刚性场景，对象、网格、材质与环境独立于 MAPPO。进入须处于对象模式，并先停止、断开后端及处理待执行命令。手动姿态与转动不启动物理求解，不生成相应功率、载荷、尾流、形变或雷达读数；界面角度与转速范围不代表实体运行或安全限值。
- 每台风机保留独立参数；选择“全部”不改值，编辑只同步当前字段。统一桨距显式覆盖三片叶片的独立桨距，多值字段保留提示。转动按墙钟时间积分，改变 RPM 保留当前叶轮角度，不受 Blender FPS 或 MAPPO 60 秒片段限制；零 RPM 保持暂停，全部零 RPM 时禁用运行。
- 保存保留参数与姿态；重开、撤销、重做、无窗口显示或场景结构无效时暂停。返回原场景保留其数据和播放位置，同一会话仅在原生 player 未被其他场景占用时恢复原有播放；重开文件不自动恢复播放。

### 回放时间、场景与播放

- MAPPO 使用 40 Hz 保存源和固定 60 Hz 显示时间轴；重建使用 10 Hz 保存样本。源采样率、时间轴与实际绘制帧率分别报告。修改 Blender FPS 只改变目标墙钟播放速度。
- 姿态、卡片、S1 和累计统计共用仿真时钟；暂停单步前进一帧，播放中单步先暂停，末帧钳制。后退、寻址与重播不重复累计统计或暴露未来事件，显示插值不生成缺失测量。
- 回放实例按场景绑定；FULL_COPY 缺少对象时保持未就绪，共用网格的 linked scene 拒绝第二个时钟。Blender 同进程只有一个全局原生 player，归属由 `playback.py` 的开始／结束回调确定，不能由各窗口播放标志推断多个播放器。
- 原生撤销／重做释放旧 RNA 引用并按恢复后的场景身份重绑回放，保留相机、机组、时间与时间轴设置；缺包或绑定失败保持未就绪，不降为刚性读数。删除场景后仍可清理、重新加载和卸载扩展。
- MAPPO 驱动九片叶片及三机塔架柔性运动。独立 normal/close 净空演示使用刚性姿态示意，由仓库 `scripts/blender/open_clearance_demo.py` 加载外部包，工况切换从片段起点开始；只切视角保留工况、时刻和暂停状态。
- 工作区源码按消费方的机组工作集计算整叶网格；隐藏 T1 时挠度比较仍采样三个结构叶尖，普通回放保留 T1 塔筒与挂载更新，供 T2/T3 视图及首次创建的共享结构使用。缺陷几何分析可请求全量，单机导出仅更新显式所选机组；不能仅按视口显隐移除这些依赖。
- 同刻切视图须补齐所需网格。形变缓存按源帧与工作集区分，叶片重绑清缓存，换包或换模型重新绑定；可见整叶与隐藏结构叶尖的计算须保持读数一致。遥测每次加载校验后保留 NumPy 数组供插值复用，不使用跨加载缓存，不改变缺测、单位与来源。

### 重建对照

- MAPPO → **打开几何重建示例…** 使用 `assets/examples/mappo_reconstruction_split.blend`，统一侧栏以一个按钮切换几何／同源纹理，两侧呈现完整风机。右侧塔筒、机舱、轮毂和机械细节复用源 T1 仿真结构，只有三片叶片属于重建估计。
- `split_turbine_context.py` 共享结构网格，独立管理变换与可见性；切换 T2/T3 不应隐藏右侧结构。稳定刷新复用有效绑定，来源对象、网格或保存身份变化时重新校验；结构同步在源姿态和柔性塔筒写入之后执行。数据刷新次数或对象复用不能证明 FPS 提升。
- 几何示例每片叶片有 601 个绝对 shape keys，`CONSTANT` 保持且无 drivers。同源纹理严格绑定 MAPPO manifest、模型、包文件摘要和全部 601 样本绝对时钟；每个样本保持 6 个显示帧，原生连续播放两侧共同循环。绿、橙均为算法估计。
- 中段／叶尖观察点只定位一次。手动缩放、平移和绕转后继续播放保留用户视角，禁止播放或视图回调自动跟随。原始／增强显式切换回两侧整机；模式切换保留时刻、暂停／播放状态及有效视图。保存重开恢复模式、显示、时间与视角，不重新执行按钮取景。
- 统一侧栏提供播放／暂停、单帧步进、复位和退出；右侧视口左下角显示时间、样本和颜色 HUD。上一／下一样本、进度滑块、自定义位置、自动跟随、完整取景、恢复布局和独立合成入口不在统一侧栏显示，底层工具保留。左侧相机切换保留右侧独立视图。
- 独立 `synth_tex` 的 120 样本游标与 MAPPO 来源、时钟分开；同源纹理归类为 `SAME_SOURCE_SIMULATION`。显示设置、退出返回和失败回滚按来源隔离，保存后依靠 embedded 数据和 packed 图片恢复。
- 便携示例保存 `wfrl_farm_flex_path=__WFRL_BUNDLED_MAPPO__`，依据保存的 manifest SHA-256 匹配安装目录的 `assets/mappo/`；保留 packed 天空／地表图及无 linked libraries 的可携带性。失效路径只恢复同哈希内置包，已有目录也须通过加载校验；不匹配则清空并报错，不改为任意默认包或读取器不支持的 `//` 路径。
- 几何与纹理仍为 `NOT_ACCEPTED`，纹理辅助几何的科学门槛仍为 `FAILED_GATE`。增强只改变显示，缩放不增加观测分辨率；图集条带、接缝、错贴及拖影如实保留，人工观察点不作为自动缺陷检测结果。

### NREL 单路机舱相机

- 使用现有 MAPPO 视口与回放，继承机舱和塔顶运动、不继承叶轮自转；独立于 C1/C2/C3。名义安装与光学参数由 `nacelle_camera.py` 定义，详细坐标和画幅见机舱相机说明；选中已有相机保留原生姿态与镜头，显式复位才恢复名义基准。
- 保持完整光学画幅及水平／垂直视场关系；手动变焦不改传感器尺寸。视频导出分别记录请求尺寸、实际尺寸和内参，不能由输出分辨率推定现场可辨识度；切换观察镜头不重新生成同源纹理图集。
- 保留机舱、叶根封口、塔筒和结构遮挡，不通过隐藏或缩小结构制造照片匹配。名义参数下当前模型未达到参考照片的近根外表面连续延伸至叶尖效果；表面入框、有限射线可见、完整轮廓入框与图片可辨识分别判断，不将模拟几何检查视为实体安装或现场标定通过。

### 三相机与资源生命周期

- C1/C2/C3 按实际槽位路由，缺失明确报错。三路共用场景和时钟；GRID 是三路加静态安装示意的 2×2，STRIP 为三列。WATCH／单路放大固定使用 SOLID 材质颜色，返回三路恢复原画质；PNG 原图质量独立管理。
- 主窗口切换镜头不覆盖原生观察窗口的局部相机。观察期间保存使用原始画质及真实槽位相机，不写入临时代理；正常保存和文件写入失败后恢复当前观察状态。原生撤销关闭观察会话并恢复所属场景设置。
- 盒体贴合约束作用于支架安装端；固定相机不自动追踪叶片，不要求三等分或按目标叶长裁图。连续投影、重叠、射线可见、无遮挡和拼接成功分别判断，不隐藏真实结构制造通过结果。
- **恢复默认三相机** 从内置 JSON 恢复盒体与三路参数，保留帧、播放状态和自由视图，支持布局撤销；无盒体时不隐式创建，采集、草稿及安装／后端忙碌时拒绝。配置与原生状态均有效时保持对象身份和撤销历史，失败回滚。
- 当前源码的 `custom_cameras._publish()` 在同一事务中重绑定场景、局部视口及同场景未关闭控制器的 `snapshot`、`external_snapshot`、`session.snapshot` 相机引用，包含编辑状态，随相机对象一起提交或回滚；`_refresh_references()` 仅在 WATCH / LAYOUT 刷新当前观察相机。窗口清理处理已删除 RNA、重复关闭和释放失败，保留未释放资源以供清理重试，向用户报告错误。延迟恢复播放须再次核对全局播放归属，不打断其他场景的 player。
- 原图采集核对原生与评估相机状态；非有限值或采集中途原生修改须中断，恢复事务状态并保留用户修改。真实 GPU 事务、PNG/state 一致性、资源释放和 FPS 各自验证。

### NREL 缺陷编辑

- `nrel_defects/` 管理九片叶片的合成缺陷、预览确认／取消、撤销重做、健康对照与 JSON；`repair_marks.py` 的默认修补痕迹独立保留。缺陷网格、辅助轮廓和分析支持区域沿同一保存柔性运动更新，`physics_coupled=False`，不修改 FAST.Farm / BeamDyn 结果或雷达测量。
- JSON 绑定 NREL 健康参考几何与参数化身份，不直接使用 GW184 配置或按叶长自动缩放。保存与重开恢复已提交配置，不把临时预览写成已应用状态。
- 删除辅助轮廓不改变缺陷配置；寻址和显隐跳过失效对象，重新生成标记时依据当前配置恢复。关闭逐项释放自有资源，保护共享数据；释放失败保留会话并提示重试，不阻断其他叶片管理器清理。
- G 分析要求 C1/C2/C3 完整并使用冻结的相机配置；安装、父级、矩阵、镜头或分辨率变化需关闭重开编辑器以重新冻结。`VISIBLE/PARTIAL` 只表示几何可见性，不能升级为图片可辨识或检测通过；外观审查状态单独判断。
- 当前 0.2 mm 叶尖精度门槛仍为 `FAILED_GATE`。显示编辑、保存重开或软件回归通过不改变该结论，也不证明物理损伤或修复效果。

### 净空雷达与数据合同

- 旧 B2 模式的主卡片和主统计只使用 B2，统计分母按全部 expected 样本；B2 无效时不借用 B1/B3 或真值补齐。真值、估计和偏差来自同次测量，偏差为估计减真值。
- 双束由 S2/S3 同保存时刻、同叶片的有效命中配对重建；S1 有效命中独立报警，双束无效不能抑制 S1。无净空或无报警不等于安全，显示保持不代表保护延迟。
- 默认 `hub-axis.v1` 与显式 TLS 候选 `hub-tls.v1` 绑定同份源几何和各自结果层，核对方法身份、版本和摘要。保留 `REVIEW_ONLY / PENDING_ACCEPTANCE`，TLS 软件接入不构成精度验收或默认方法切换依据。
- 无效、缺失、过期或空统计不转成零净空／零误差；保留整组读数有限保持与过期规则。缺包、损坏、机型或版本不匹配时清除旧读数并显示原因。保存路径失效时只匹配同 manifest 哈希内置包，已有目录不被替换，加载仍严格核验保存身份。
- 区分结构叶尖、展示叶尖、测量参考和平滑显示表面；不为画面吻合改写测距。光束显示长度不参与测量，亮线末端不宣称真实命中点。

### 后端生命周期与训练回传

- 后端 `BridgeServer.poll()` 无客户端时仍推进 `BackendSession.tick()`，读取进度、检测退出和失败并清理已结束会话资源。快照与统计可以合并为最新状态，可靠生命周期、安全事件和错误使用独立有界队列；真实溢出必须报告 `EVENT_OVERFLOW` 并失败停止，不能静默丢弃。重连恢复最新状态及保留的可靠事件，不承诺完整观察历史；等待时间计入读数年龄。
- 全场功率仅在同一步全部预期机组读数及合计均有限时有效，缺测不补零、不以部分合计冒充全场；有效单机通道保持可用，完整未返回的源通道保留 `unsupported`。Interactive 的迭代统计与共享 actor、集中式 critic 目标按[训练入口与合同](../../../docs/training/MAPPO_SETUP.md)核对，不套用 Formal Training 或 SB3 的更新路径。
- MPI 启动环境清除继承作业身份，保留 BTL/PML 及其下划线子项的显式传输配置；macOS 的 `self,tcp` 和 `lo0` 仅作缺省值。项目侧服务器、训练启动器和 MPI 环境独立于扩展 ZIP，修改后重启 Bridge；前端源码行为进入安装载荷仍需重新构建并核对实际安装来源。

## 验证选择

按改动读取[维护与验证入口](references/verification.md)，选择宿主、原生后台、实际窗口或隔离安装检查。先确认脚本的输入、输出、副作用和模块来源；相关检查通过且没有新改动或未解决问题即可结束。

纯文档修改核对格式、链接、路径和要求一致性。可见交互需实际窗口证据；源代码导入、后台 PASS 或渲染图不能代替安装版原生窗口验证。交付时说明实际命令、环境与结果，区分“已运行通过”“仅静态检查”“尚未验证”，不混用不同载荷、平台或测试范围的证据。

`POST_PIXEL` 表示视口绘制完成，不是显示器扫描输出或实体相机同步；60 Hz 时间轴不等于稳定 60 FPS。理想射线、保存几何和软件回归不证明现场漏测率、硬件采样、净空精度或保护延迟，不通过放宽容差隐藏差异。

`performance.py` 的可选计时只汇总 CPU 回调：`flex.deformation_cache` 覆盖 `deformed_at()` 查询、命中和计算，`flex.rebind_blade` 独立记录。缓存命中／未命中、参与机组、叶片与塔筒写入、隐藏叶尖采样计数用于核对工作集；嵌套计时不能相加，也不构成 FPS 验收。

## 扩展与数据交付

- 只有任务涉及扩展构建或交付时才调用构建与安装工具。扩展 Python／资源、规范 Bridge 协议或内置读取器变化要进入安装版时需重建 ZIP；skill、AGENTS.md、外部说明或项目侧启动器／后端修改不要求重建，除非改变打包内容。
- 修改规范源：`protocol.py` 来自仓库 `wfrl/blender_bridge/messages.py`；内置 `_vendor/lidar/` 来自 `wfrl/lidar/` 的 `replay.py`、`evidence.py`、`dual_beam_replay.py`、`molas_cl.py`。不手改生成副本，也不替换冻结模型或正式资产来源来消除校验失败。
- 构建使用 `python3 scripts/blender/build_extension.py --output <新目录>`；工具默认拒绝覆盖已有 ZIP、校验和及 inventory，仅显式指定 `--overwrite` 时允许替换。默认包含 `assets/mappo/`、`assets/dual_beam/`、`assets/dual_beam_tls/` 和规范重建示例；两种雷达结果层通过 `../mappo` 共享源，严格校验方法及完整来源摘要。
- 外部覆盖分别使用 `--dual-package <旧法匹配目录>` 与 `--dual-tls-package <TLS匹配目录>`；更换 farm 源不能沿用不匹配 sidecar。外部便携双束包含自身 `source/`，须整体移动并校验；normal/close 外部包和原始 VTP 不随扩展提供。
- 对实际包生成并核对 manifest、ZIP、SHA-256 和 inventory，交付前完成适用的安装版验证。重建 ZIP 不等于生成物理结果、通过科学验收或公开发布。附件上传与文档同步遵守 AGENTS.md 的边界。
- 项目启动器加载已安装扩展。`scripts/blender/run_wfrl_macos.sh` 已包含 `launch`，调用时不再追加该子命令；直接调用 `wfrl_launcher.py` 才使用 `launch`。普通手动后端连接需要 `Load & Validate Scene`，启动器主动连接流程提供自动加载。

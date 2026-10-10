---
name: windfarm-blender-dev
description: 开发、排查和验收本仓库 Blender 原生前端的 MAPPO 柔性回放、三相机、双束净空与 S1 报警、旧 B2 回放及安装交付。
---

# Windfarm Blender 开发

本文件的链接相对 skill 目录；命令从 Git 仓库根目录执行。适用规则见 [AGENTS.md](../../../AGENTS.md)。

文档维护日期为 **2026-10-10**，当前公开安装版为 **NREL 0.3.20+1**，要求 Blender 5.2+。本版唯一手动附件为 [wfrl_blender-0.3.20+1.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.20.1/wfrl_blender-0.3.20%2B1.zip)，173 文件、99,010,020 bytes，上传前本地 SHA-256 为 `e6e279f5f88c730f3c96158bfca525a9d04f1a09915ad3a997d1d29b00a9f5ba`；扩展实现源码未推送，使用／维护文档在 ZIP 发布后另行同步。公开扩展实现基线仍为 [v0.3.18 / `6dfa0eef`](https://github.com/snode11/wind-farm-blender/commit/6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02)。v0.3.20.1 标签指向既有文档提交 `8e49b84db1b28de49b61b12924d540ae37f11b8b`，自动 Source code 不代表本版对应实现或安装包。实际交付与验证见[0.3.20+1 发布核对](../../../docs/blender/releases/0.3.20+1发布核对.md)，规范 `assets/examples/mappo_reconstruction_split.blend` 必须保留。

0.3.20+1 补回三相机 **恢复默认三相机**，恢复原盒体和三路参数，支持撤销；WATCH / LAYOUT 退出快照随相机事务重绑定。修复前身本地 0.3.20 ZIP 的相关宿主为 **78 passed**，日常安装 `bl_ext.user_default.wfrl_blender` 后台复位回归及原生面板实际点击通过；窗口保留 frame 61 / subframe 0.5、自由视图和 WATCH 引用。最终版本与该已验证前身的全部功能及资产文件字节一致，仅 manifest / build-info 版本及简短权限说明不同，CRC 与 Blender 扩展校验通过。最终版本未重新隔离安装或重跑完整可见窗口，见[本地验证摘要](../../../outputs/release-0.3.20-plus1-20261010/local-verification.json)。没有新增 FPS 或科学接受结论；原 **NOT_ACCEPTED／FAILED_GATE**、雷达研究与平台限制不变，上传后没有远端附件复验。

0.3.20 已发布重建结构复用、T2/T3 切换时的右侧结构显示及完整取景修复，0.3.20+1 继承。历史 0.3.19 的 CPU **73 passed / 174 subtests passed**、172 文件隔离安装和原生窗口 stage32 仅属于[该版发布核对](../../../docs/blender/releases/0.3.19发布核对.md)；其窗口覆盖完整风机、模式往返、共享播放、一次观察点、背面绕转／缩放保留、原始／增强整机取景、退出返回及保存重开。真实鼠标滚轮只属于此前本地轮次，0.3.19 最终包未重复输入。不同轮次不合并，未做跨版本正式资产清单比较，不宣称 65 项摘要不变。

0.3.18 的场景恢复、播放归属、固定 C2、采集状态、GPU 事务复用、Tools 来源信息及 float64 姿态修复仍保留。该版源码 CPU 215／2／233 与发布前宿主117／1／6、172 文件安装及源码复建结论仅属于历史版本，见[0.3.18 发布核对](../../../docs/blender/releases/0.3.18发布核对.md)和[当时修复与验证](../../../docs/blender/validation/NREL本地0.3.18修复与验证.md)。原 0.2 mm 叶尖门槛仍失败，不能以软件交付改变接受状态。新增研究 CLI 未据此成为公开入口；日常安装也不随发布自动更新。

历史 0.3.17.1（包内 0.3.17+1）仅发布 ZIP，同源纹理和 QA 修复的当版范围见[历史发布核对](../../../docs/blender/releases/0.3.17.1发布核对.md)，旧标签不追改。0.3.13 曾同步完整 NREL 前端、雷达、工具、测试及构建资源，对应构建与 ZIP 安装证据见[0.3.13 发布核对](../../../docs/blender/releases/0.3.13发布核对.md)。历史 0.3.10–0.3.12 也是 ZIP 单独发布；GW184 0.4.0 保持独立运行包。


## 2026-10-10 统一重建对照合同（0.3.20+1 ZIP 已发布）

0.3.19 已将 MAPPO 的 **打开几何重建示例…** 作为统一入口，0.3.20+1 继续沿用；当前分屏用一个按钮切换几何／同源纹理，两侧显示完整风机。0.3.20 起右侧复用源风机塔筒、机舱、轮毂及机械细节，它们不是重建成果；三片叶片及同源固定灰度图集仍是未接受的估计结果。公开 0.3.18 的历史多入口、布局按钮及自动近景说明不作为当前统一流程要求。

- 保留中段／叶尖两个一次定位观察点，禁止因播放或视图回调自动跟随；手动缩放、平移、绕转后继续播放保留用户视角。
- 原始／增强显式切换均先呈现两侧整机，再由用户滚轮放大寻找现有图集细节；模式切换保留当前时刻、暂停／播放状态及有效视图，保存重开恢复模式、显示设置、时间和视角，不重复执行原始／增强按钮的取景动作。
- 统一侧栏保留播放／暂停、单帧步进、复位和退出，时间／样本／颜色 HUD 位于右侧视口左下角。隐藏上一／下一样本、进度滑块、自定义位置、自动跟随、完整取景、恢复布局和独立合成入口；底层工具与独立来源资产保留，不波及其他功能页。
- 601 样本同源摘要和绝对时钟校验、10 Hz 保存／60 Hz 显示保持、embedded／packed 恢复继续保留。固定图集增强不改变观测，放大不新增分辨率，人工观察点不是自动缺陷检测。`NOT_ACCEPTED`、`FAILED_GATE` 及雷达研究边界不变。

已交付唯一手动附件 `wfrl_blender-0.3.20+1.zip`，未推送扩展源码；公开扩展实现基线仍为 0.3.18。身份与本轮检查见[0.3.20+1 发布核对](../../../docs/blender/releases/0.3.20+1发布核对.md)；此前本地窗口证据保留原轮次，不能改写成本轮最终版本已新执行。

## 先分类并给出最小方案

实施前简要说明需求类别、涉及模块、最小方案和可观察的验收标准；小改动用几句话即可，不要求另写计划或停等批准。混合需求逐项分类，跨层影响需要更新方案。正常开发应完成实现、相关验证和本次改动引入的问题修复；仅方案、基线或报告任务按其范围结束，原样报告失败。

| 类别 | 判断与默认边界 |
| --- | --- |
| 纯界面 | 面板布局、文案、卡片、观察相机、光束显示；复用 Camera 相机与演示入口，不修改正式结果包，不重跑 FAST.Farm。 |
| 回放逻辑 | 加载、切换、暂停、寻址、重播、时间映射与读数保留；检查 Blender 接入和独立读取器，复用现有数据验证。 |
| 数据格式 | schema、字段、摘要、校验与累计状态合同；先评估读取器、发布端、旧包兼容性及扩展内置副本。不得为了让旧包通过而削弱校验。 |
| 物理算法 | 标定、估计公式、真值定义、几何、求解参数；明确是否需要后处理或新求解。仅在任务范围包含这些工作时执行，生成新版本包而非覆盖旧包。 |

日常操作和排障先看 [前端使用说明](../../../前端readme.md)；模块、数据合同与维护看 [前端实现说明](../../../blender_frontend/README.md)；算法与物理重生成看 [雷达算法说明](../../../wfrl/lidar/README.md) 和 [计算流程](../../../scripts/lidar/README.md)。[根 README](../../../README.md) 与 CHANGELOG 记录已发布版本，[前端更新计划](../../../前端更新计划.md) 保存方案及待实施事项，历史状态按日期和版本阅读。仅阅读当前任务需要的章节，已有上下文足够时无需重复读取。

先辨认功能路径：MAPPO · 60 秒是随包柔性结果回放；双束净空 / S1 使用 `hub-axis.v1`，TLS 候选 / S1 显式使用 `hub-tls.v1`，两个结果层绑定同一份源几何及各自覆盖数据；独立 normal/close 是外部旧雷达包的刚性姿态示意；后端 Replay、Interactive、Formal Training 分别涉及推理或训练。离线回放无需 Bridge、FAST.Farm 或网络，不能因查看净空或相机而启动求解器。

## 按需求定位

以下实现路径以 `blender_frontend/wfrl_blender/` 为基准；按需读取，不加载所有模块。

| 需求 | 实现入口 | 补充资料 |
| --- | --- | --- |
| NREL 九片叶片缺陷编辑、健康对照、JSON 与 G 分析 | `nrel_defects/`、`farm_flex.py`；本地未公开工具 `scripts/blender/nrel_defects.py` | [NREL 缺陷编辑器](../../../docs/blender/NREL缺陷编辑器.md)、[移植验证](../../../docs/blender/validation/NREL缺陷编辑器移植验证.md)及 [0.3.16 发布核对](../../../docs/blender/releases/0.3.16发布核对.md) |
| MAPPO＋重建分屏、同源纹理、独立合成及显示设置隔离 | `split_mappo_texture.py`、`split_surface_texture.py`、`split_texture_settings.py`、`split_reconstruction.py`、`split_reconstruction_timing.py`、`split_reconstruction_ui.py`、`panels/farm_replay.py`、`cameras.py`、`assets/examples/` | [0.3.15 发布核对](../../../docs/blender/releases/0.3.15发布核对.md)；前端实现说明和前端使用说明的分屏章节 |
| Blade Recon 独立结果导入、模型回放与场景切换 | `blade_recon_data.py`、`blade_recon_review.py`、`panels/blade_recon.py`、`__init__.py` | [Blade Recon 使用说明](../../../docs/blender/BladeRecon可视化使用说明.md)；前端实现说明「Blade Recon 叶片三维重建开发入口」 |
| NREL 视频动态研究查看器 | 仓库级 `scripts/blender/open_nrel_dynamic_review.py`、`wfrl/nrel_reconstruction/dynamic_review.py` | 合并研究入口与状态（本地路径：`outputs/nrel-video-dynamic/runs/20261004-integrated/README.md`）；独立冻结原型，按下方说明分流 |
| MAPPO 统一侧栏、播放、单步与视角 | `panels/farm_replay.py`、`farm_flex.py`、`clearance_replay.py`、`__init__.py` | 前端实现说明「统一侧栏与固定时间轴」；前端使用说明「时间、回放与观察视角」 |
| 三机柔性运动、挠度和轨迹 | `farm_flex.py`、`tower_motion.py`、`prebend.py`、`deflection.py`、`tip_tracking.py`、`assets/mappo/` | 前端实现说明「三机结果包与九片形变」「塔架、叶片参考与净空」 |
| 旧 B2、独立 normal/close 卡片与读数 | `panels/clearance.py`、`clearance_replay.py`、`radar_feedback.py`；仓库级 `wfrl/lidar/replay.py`、`evidence.py` | [结果包合同](../../../wfrl/lidar/REPLAY_FORMAT.md)「Legacy replay 1.0」；[用户手册](../../../docs/blender/用户使用手册.md)「独立激光净空雷达」 |
| 双束净空、S1 报警与可携带包 | `farm_flex.py`、`panels/farm_replay.py`、`panels/clearance.py`；仓库级 `wfrl/lidar/dual_beam.py`、`dual_beam_replay.py`、`dual_beam_package.py` | 结果包合同「Dual-beam review v1」；雷达算法说明「当前双束链路」 |
| 云台、侧视、总览与雷达外观 | `cameras.py`、`panels/gimbal.py`、`clearance_visual.py`；几何关联时再读 `scene_builder.py`、`turbine_geometry.py` | 前端实现说明「雷达外观如何建模」；前端使用说明「给观众展示时的建议顺序」 |
| 三相机盒体、支架贴合与默认取景 | `stacked_camera_rig.py`、`panels/stacked_camera_rig.py`、`custom_cameras.py`、`assets/cameras/t1-three-camera-default.json` | [T1 三相机说明](../../../docs/blender/T1三相机使用说明.md)；前端使用说明「三相机安装与取景」 |
| GRID / STRIP / WATCH 窗口及原图采集 | `native_camera_views.py`、`installation_schematic.py`、`custom_camera_capture.py`、`custom_camera_preview.py`、`panels/custom_camera_output.py` | 前端实现说明「当前三相机维护要点」；前端使用说明「三路观看与图片采集」 |
| 播放性能、重绘与生命周期 | `playback_benchmark.py`、`runtime.py`、`performance.py`；仓库级 `scripts/blender/run_playback_benchmark.py` | [0.3.13 发布核对](../../../docs/blender/releases/0.3.13发布核对.md) 和 [0.3.15 发布核对](../../../docs/blender/releases/0.3.15发布核对.md)及发布状态；历史原始记录位于本地 `docs/blender/validation/前端更新验证记录.md`，按对应版本读取 |
| 后端连接、启动器与运行状态 | `runtime.py`、`transport.py`、`panels/status.py`、`panels/run.py`；仓库级 `scripts/blender/wfrl_launcher.py`、`wfrl_blender_bootstrap.py` | 前端实现说明「启动与模式切换」「实时连接与控制生命周期」 |
| 测距、标定与物理数据生产 | 仓库级 `wfrl/lidar/physics.py`、`sampling.py`、`dual_beam.py`；`scripts/lidar/postprocess_dual_beam.py`、`package_dual_beam.py` | 雷达算法说明、计算流程；按任务决定是否需后处理或新求解 |
| 打包、安装与交付验证 | `blender_manifest.toml`；仓库级 `scripts/blender/build_extension.py`、`validate_frontend_package.py` | 发布状态与验证范围；前端实现说明「修改、安装与验证」 |

NREL 动态研究使用合并目录的本地统一启动器 `outputs/nrel-video-dynamic/runs/20261004-integrated/打开研究查看器.command`，载入该目录 `viewer/frozen-source/` 的冻结源码及 `estimation/attempt01/result/review-package/` 的研究包，在独立窗口运行，无需安装扩展；工作区脚本改动不会自动更新冻结查看器。该原型仍为 `REVIEW_ONLY / DY1 NOT PASSED`，保存估计只覆盖四个时刻，其余帧为 `UNAVAILABLE; no interpolation`。它与 0.3.15 的 601 样本便携分屏示例分别判定，播放同步检查通过不代表动态计量通过。

[dist/README-lidar.md](../../../dist/README-lidar.md) 与 [lidar-delivery.json](../../../dist/lidar-delivery.json) 记录的是 **0.3.9 历史交付**，其中 normal/close 路径仍供独立雷达入口使用；不能将它们当成当前 0.3.20+1 的完整交付清单。该旧入口见 `scripts/blender/open_clearance_demo.py`、`verify_lidar_delivery.py`。其余历史说明与验收记录同样按版本限定。

NREL 缺陷以已有保存运动驱动展示网格、辅助轮廓及支持区域，不重写 FAST.Farm / BeamDyn 或雷达结果。几何 G 的 VISIBLE/PARTIAL 不证明图片可辨识。0.3.16 安装版保存重开通过；当轮叶尖挠度 0.2 mm 阈值回归最大方向误差约 0.895 mm，仍失败；源码窗口检查后控制台切换原生 SIGSEGV 原因未定，不能写整体窗口稳定性通过。0.3.18 修复 float64 姿态读取后的安装版同样本真实网格最大分量误差约 1.0613 mm，独立源运输约 1.0665 mm，网格与源差约 0.0290 mm，仍为 FAILED_GATE；消除量化抵消后不能称精度提升。原始源文件缺失，未重导出或重跑 FAST.Farm，也未改正式数据或阈值。日常安装不会随文档或 ZIP 发布自动更新。

## 必须保留的行为

- **旧 B2 模式**的主卡片及主统计使用 B2；B2 无效时不借用 B1/B3 或真值补齐。真值、估计和有符号偏差来自同一次测量，偏差为估计减真值。
- **双束模式**由 S2/S3 同一保存时刻、同一叶片的有效命中配对重建；S1 有效叶片命中独立报警，双束无效不能抑制 S1。没有净空或没有报警不等于安全；报警显示保持不代表保护延迟。保留 `REVIEW_ONLY` / `PENDING_ACCEPTANCE` 与各自统计口径，不把 B2 合同套用到双束。
- 默认旧法与显式 TLS 候选分别核对方法身份、算法版本及来源摘要，界面按实际读取器显示。保存重开时，失效旧安装路径只能按保存的 manifest 哈希定位匹配内置包；不匹配时清空并报错，不能替换方法或重新绑定不同数据。TLS 软件交付不等于精度验收，研究数值及独立工况见 [TLS 总报告](../../../docs/lidar/双束TLS候选实施与验证总报告.md)。
- 无效、缺失、过期和空统计不转为零净空或零误差；维持现有整组读数有限保留与过期规则。缺包、损坏、机型或版本不匹配时清除旧读数并显示可排查的未就绪原因。
- 姿态、卡片、S1 报警和累计统计共用仿真时钟；固定帧映射到固定仿真时刻。修改 Blender FPS 只改变目标墙钟播放速度；暂停、后退、寻址与重播不重复累计统计或暴露未来事件。显示几何插值不能生成缺失测量。
- 0.3.18 的场景恢复实例按场景绑定；FULL_COPY 缺少对象时保持未就绪，共用网格的 linked scene 拒绝第二个时钟。Blender 同进程是单一全局原生 player，按开始／结束回调 owner scene 判断播放冲突和取消归属，不能靠各窗口的播放标志宣称多个原生 player 并行。
- 0.3.18 的 C1/C2/C3 按实际槽位路由，缺失明确报错；采集核对原生与评估相机状态，非有限值或采集中途原生修改明确中断，恢复事务状态并保留用户修改。GPU 9/9 → 3/3 只证明本轮事务复用，PNG/state 一致不证明 FPS 提升或整套无泄漏。
- MAPPO 的 40 Hz 源数据映射到固定 60 Hz 时间轴；暂停单步前进一帧，播放中单步先暂停再前进，末帧钳制。源采样率、显示时间轴与实际绘制帧率分别报告。
- **原几何示例**右侧三片叶片各有 601 个绝对 shape keys，`CONSTANT` 保持、无 drivers；10 Hz 重建样本、40 Hz MAPPO 源与 60 Hz 固定时间轴分开报告。底层样本步进先暂停并在首尾钳制，统一侧栏仅提供单帧步进；播放时间与样本时间／保持时长在右侧视口左下角分别显示。左侧相机切换保留右侧独立视图；布局管理和保存重开复用原生生命周期，侧栏不显示恢复布局／完整取景按钮。
- **同源纹理同步对照**严格绑定实际 MAPPO manifest、模型与包文件摘要，核对全部 601 样本绝对时钟；10 Hz 样本每 6 个显示帧保持，绿橙均为算法估计。原生连续播放两侧共同循环；底层样本端点钳制。独立合成纹理的 120 样本游标与 MAPPO 时间分开，不能冒充同步；统一侧栏隐藏其入口但保留资产／工具。来源显示设置、退出返回及失败回滚继续隔离，保存后依靠 embedded 数据及 packed 图片恢复。0.3.19 统一流程中的观察点只定位一次，播放不自动跟随，显式原始／增强切换回两侧整机；其余视图导航保留用户调整。纹理／几何／缺陷未验收，实际图集条带、接缝与拖影须如实保留。
- 便携示例保存 `wfrl_farm_flex_path=__WFRL_BUNDLED_MAPPO__`，按已保存的 manifest SHA-256 `d3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde` 恢复到当前安装 `assets/mappo/`。不清空路径，也不用读取器不支持的 `//` 相对目录代替；不匹配时沿用现有失败处理。四张天空/地表图已 packed，无 linked libraries。
- 独立 normal/close 工况切换加载对应片段并从起点开始；仅切观察视角保留工况、进度与暂停状态，不更改雷达标定。保持 MAPPO、双束、独立雷达和后端运行的来源提示及状态隔离；旧 Local Demo/SYNTH 不能作为当前真实训练或现场精度证据。
- **刚性姿态示意仅指独立 normal/close 路径**；MAPPO 根据保存数据驱动九片叶片和三机塔架柔性运动，不能统称为刚性动画。区分结构叶尖、展示叶尖、测量参考表面及平滑显示表面；先核对定义和实际命中点，不能为使画面吻合而改写保存测距。光束显示长度不参与测距，其亮线末端不宣称真实命中点。
- 三相机共用场景和时钟；GRID 为三路加静态安装示意的 2×2，STRIP 为三列，WATCH / 单路放大固定用 SOLID 材质颜色显示，返回三路恢复其画质。视图切换不改盒体标定和相机布局；PNG 原图质量独立管理，采集异常也需恢复回放与预览状态。
- 盒体贴合约束作用于支架安装端；固定相机不自动追踪叶片，不要求三等分或按目标叶长裁图。连续投影、相邻重叠、射线可见性、完整无遮挡与拼接成功分别判断，不能隐藏真实结构来制造通过结果。
- 三相机 **恢复默认三相机** 从内置默认 JSON 恢复盒体和 C1/C2/C3，保留帧、播放状态和自由视图，通过完整布局事务支持撤销；无盒体时不隐式创建，采集、草稿及安装／后端忙碌时拒绝。重复默认且原生有效时保持对象和历史；失败回滚。`custom_cameras._publish()` 重绑定 WATCH / LAYOUT 的当前相机及两个退出快照，避免退出观察后恢复已删除相机。

## 分层验证

按验收标准选择现有测试，先核对输入、输出和前置条件。必要时补充针对性回归；相关检查通过且没有新改动或未解决问题时即可结束，不为凑覆盖范围反复测试。文档改动只需相应静态校验。

| 验证层 | 现有入口与边界 |
| --- | --- |
| 宿主机 | 从根目录用 `PYTHONPATH=blender_frontend:. python -m pytest <选定测试> -q`，选择现有依赖环境。前端按模块选 `blender_frontend/tests/test_*.py`；分屏用 `blender_frontend/tests/test_split_reconstruction.py` 检查视口归属、布局／加载生命周期与取景缓存，用同目录 `test_split_reconstruction_timing.py` 检查样本映射、保持时长、步进、端点与无效元数据，两者不打开 Blender 窗口。B2 用 `tests/lidar/test_replay.py`，双束按需选同目录 `test_dual_beam.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py`。`blender_frontend/tests/test_extension_package.py` 会构建 ZIP，禁止打包的任务必须排除调用构建的检查。 |
| Blender 原生 | `blender_frontend/tests/blender/` 下，MAPPO 用 `mappo_demo_regression.py`、`farm_flex_regression.py`；挠度/视角用 `tip_deflection_regression.py`、`farm_front_view_regression.py`；双束用 `dual_beam_regression.py`；三相机默认配置用 `three_camera_default_regression.py`；旧雷达按需用 `clearance_ux_regression.py`、`clearance_acceptance_regression.py`、`clearance_replay_smoke.py`。先确认脚本支持后台及所需数据，再用 `--background --factory-startup --python-exit-code 1 --python <脚本>`；该命令模板不用于可见窗口脚本。 |
| 实际窗口 | 同目录 `native_camera_enabled_regression.py` 检查当前三相机参与、轻量单路和切换恢复；`dual_beam_window.py` 检查默认双束或用 `WFRL_BUILTIN_METHOD=TLS` 显式检查候选；`clearance_gui_acceptance.py` 检查旧雷达。使用独立 Blender 配置和 `WFRL_TEST_OUTPUT`，按各脚本约定区分输出文件与目录。检查真实窗口的可读性、构图、按钮和错误提示，渲染图不替代窗口；旧 `native_camera_views_regression.py` 默认导入裸源码包，不能当作安装版证明。 |
| 分屏实际窗口 | `blender_frontend/tests/blender/split_reconstruction_window.py` 检查布局、完整取景、视口隔离、步进及工作区恢复。先在可见窗口打开已有 601 样本 MAPPO／重建场景副本，核对脚本要求的对象和另一工作区；设置新 `WFRL_TEST_OUTPUT` 目录及实际 `WFRL_ADDON_MODULE`，按需以 `WFRL_EXPECTED_ADDON_ROOT` 核对安装目录。该脚本不用后台模板，不重建输入或求解结果；可选保存／重开使用新的 `WFRL_SPLIT_SAVE` 路径及 `WFRL_SPLIT_REOPEN=1`。 |
| 安装 ZIP | `scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender路径>` 构建并在隔离配置中验证实际 ZIP，默认仅后台检查；0.3.13 完整源码默认含两种内置双束结果层，另测外部旧法包时可给 `--dual-package <匹配数据目录>`。它会打包，只在任务包含构建/安装验证时运行。后续窗口检查沿用其 `environment.json`，不能把后台结果写成窗口通过。 |
| 完整片段与性能 | 用 `scripts/blender/run_playback_benchmark.py` 驱动可见窗口 `demo_playback_benchmark.py`，按需选 GRID / STRIP、单路、三路、关闭重开及播放/暂停切换路径。隔离安装版使用 `--installed-profile <验证目录>`，输出到新目录。按每路推进、终止及进程日志区分完成、超时、中止、错误和缺绘制；不能只看平均 FPS 或单个 PASS。 |

安装版测试使用其实际 `bl_ext.<仓库名>.wfrl_blender` 模块：日常安装通常为 `bl_ext.user_default.wfrl_blender`，隔离验证工具为 `bl_ext.wfrl_frontend.wfrl_blender`。按脚本支持设置 `WFRL_ADDON_MODULE`，记录实际 `addon.__file__` 和环境，确认扩展实现从安装目录载入，不能用源码导入冒充安装版。部分旧脚本硬编码包路径或写演示 `.blend`，先核对前置条件并隔离输出；双束脚本需要可用内置数据或 `WFRL_DUAL_PACKAGE` 指定的匹配包。

按改动选择验收行为：MAPPO 加载及 T1/T2/T3 切换；旧法/TLS 的实际估计、方法身份、S1 与 B2 模式各自读数；无效、过期与独立报警；暂停/单步/末帧、后退/寻址/重播历史一致；GRID/STRIP 放大/返回、WATCH 关闭恢复；缺包清空、丢失旧安装路径的哈希恢复、不匹配清空及保存重开。涉及旧入口时再测 normal/close 两工况，纯布局至少检查相关按钮与实际窗口。

`POST_PIXEL` 只表示视口绘制完成，不是显示器扫描输出或实体相机同步；60 Hz 时间轴不能当作稳定 60 FPS。40 Hz 保存几何、理想射线和软件回归不能证明硬件采样、现场漏测率、净空精度或保护延迟。测试期望须匹配运行时数据表示，不能靠放宽容差掩盖差异。

报告给出实际命令、环境和结果，区分“已运行通过”“仅静态检查”“尚未验证”。缺依赖则说明缺项；历史 PASS、宿主机、源码入口、安装 ZIP、窗口和不同平台的证据分别记录。

## 扩展与数据交付判断

仅在扩展构建/交付任务中读取 [构建脚本](../../../scripts/blender/build_extension.py) 和 [隔离安装验证工具](../../../scripts/blender/validate_frontend_package.py)。扩展 Python/资源、规范 Bridge 协议或内置 lidar 模块变化要进入安装版时需要新 ZIP；仅 AGENTS.md、skill、外部说明或项目侧启动器/后端实现变化无需重建，除非同时改变被打包内容。修改规范源文件，不手改生成的 `protocol.py`、`_vendor/lidar/` 副本；当前内置 lidar 模块包括 `replay.py`、`evidence.py`、`dual_beam_replay.py`、`molas_cl.py`。

现有命令为 `python3 scripts/blender/build_extension.py`，默认会写入同版本 dist 文件；验证构建用 `--output <新目录>` 或 `build(output_dir)` 隔离输出。0.3.13 完整源码默认构建内置 MAPPO、旧法和 TLS 两个结果层，并校验方法身份及完整源摘要。外部覆盖分别用 `--dual-package <旧法匹配目录>` 与 `--dual-tls-package <TLS匹配目录>`；换用其他 farm 源而未提供匹配双束包时不能沿用原 sidecar。按本次实际包生成和核对 manifest、ZIP、SHA-256 与 inventory，不套用旧 0.3.9 清单或哈希。

交付前完成本地包检查和适用的安装版验证；安装后重启 Blender，再加载匹配数据。项目启动器走已安装扩展，仓库修改不会自动生效；普通手动后端连接需要 `Load & Validate Scene`，启动器的主动连接流程另有自动加载。附件上传成功即完成附件交付，默认不再下载远端附件或进行上传后的文件、字节、校验和比对，仅用户明确要求时执行。若本次任务包含文档同步，仍按 AGENTS.md 完成当前入口与主工作区文档对齐；保留已有修改及历史验证范围。

0.3.13 交付的离线资源包括 `assets/mappo/`、`assets/dual_beam/` 与 `assets/dual_beam_tls/`；两种结果层通过 `../mappo` 共享同一源，默认仍为旧法。外部 normal/close 包和原始 VTP 不随扩展提供。外部可携带双束包则包含自身 `source/`，须整体移动并校验源与结果摘要。界面/播放器修改复用结果包；标定/公式变化是否可复用原始输出、几何/物理变化是否需要新求解，按雷达算法说明「物理结果生产与重新发布」和计算流程判断。重建 ZIP 不等于更新物理结果、物理验收通过或发布。

0.3.20+1 保留 0.3.15 首次交付的 `assets/examples/mappo_reconstruction_split.blend`，由 MAPPO → **打开几何重建示例…** 打开；场景、MAPPO 数据和四张 packed 纹理均在 ZIP 内。0.3.18 已同步匹配扩展源码；0.3.15／0.3.16 当时仅发布 ZIP，不能追溯改写为当时已同步。

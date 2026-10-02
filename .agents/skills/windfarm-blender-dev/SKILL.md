---
name: windfarm-blender-dev
description: 开发、排查和验收本仓库 Blender 原生前端的 MAPPO 柔性回放、三相机、双束净空与 S1 报警、旧 B2 回放及安装交付。
---

# Windfarm Blender 开发

本文件的链接相对 skill 目录；命令从 Git 仓库根目录执行。适用规则见 [AGENTS.md](../../../AGENTS.md)。

本次维护基线为 **2026-10-02 / 0.3.13**，要求 Blender 5.2+；后续以实际 [manifest](../../../blender_frontend/wfrl_blender/blender_manifest.toml) 和对应[发布状态与验证范围](../../../docs/blender/发布状态与验证范围.md) 核对版本，不把本段当作永久版本约束。0.3.13 已同步完整 NREL 前端、雷达、处理工具、测试及构建资源源码；对应标签的独立源码构建与实际 ZIP 安装证据见 [发布核对](../../../docs/blender/0.3.13发布核对.md)。历史 0.3.10–0.3.12 为 ZIP 单独发布，不能据新版源码同步推定旧标签已补齐。GW184 0.4.0 保持独立运行包，雷达更新只进入 NREL。

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
| MAPPO 统一侧栏、播放、单步与视角 | `panels/farm_replay.py`、`farm_flex.py`、`clearance_replay.py`、`__init__.py` | 前端实现说明「统一侧栏与固定时间轴」；前端使用说明「时间、回放与观察视角」 |
| 三机柔性运动、挠度和轨迹 | `farm_flex.py`、`tower_motion.py`、`prebend.py`、`deflection.py`、`tip_tracking.py`、`assets/mappo/` | 前端实现说明「三机结果包与九片形变」「塔架、叶片参考与净空」 |
| 旧 B2、独立 normal/close 卡片与读数 | `panels/clearance.py`、`clearance_replay.py`、`radar_feedback.py`；仓库级 `wfrl/lidar/replay.py`、`evidence.py` | [结果包合同](../../../wfrl/lidar/REPLAY_FORMAT.md)「Legacy replay 1.0」；[用户手册](../../../docs/blender/用户使用手册.md)「独立激光净空雷达」 |
| 双束净空、S1 报警与可携带包 | `farm_flex.py`、`panels/farm_replay.py`、`panels/clearance.py`；仓库级 `wfrl/lidar/dual_beam.py`、`dual_beam_replay.py`、`dual_beam_package.py` | 结果包合同「Dual-beam review v1」；雷达算法说明「当前双束链路」 |
| 云台、侧视、总览与雷达外观 | `cameras.py`、`panels/gimbal.py`、`clearance_visual.py`；几何关联时再读 `scene_builder.py`、`turbine_geometry.py` | 前端实现说明「雷达外观如何建模」；前端使用说明「给观众展示时的建议顺序」 |
| 三相机盒体、支架贴合与默认取景 | `stacked_camera_rig.py`、`panels/stacked_camera_rig.py`、`custom_cameras.py`、`assets/cameras/t1-three-camera-default.json` | [T1 三相机说明](../../../docs/blender/T1三相机使用说明.md)；前端使用说明「三相机安装与取景」 |
| GRID / STRIP / WATCH 窗口及原图采集 | `native_camera_views.py`、`installation_schematic.py`、`custom_camera_capture.py`、`custom_camera_preview.py`、`panels/custom_camera_output.py` | 前端实现说明「当前三相机维护要点」；前端使用说明「三路观看与图片采集」 |
| 播放性能、重绘与生命周期 | `playback_benchmark.py`、`runtime.py`、`performance.py`；仓库级 `scripts/blender/run_playback_benchmark.py` | [0.3.13 发布核对](../../../docs/blender/0.3.13发布核对.md) 与发布状态；历史原始记录位于本地 `docs/blender/前端更新验证记录.md`，按对应版本读取 |
| 后端连接、启动器与运行状态 | `runtime.py`、`transport.py`、`panels/status.py`、`panels/run.py`；仓库级 `scripts/blender/wfrl_launcher.py`、`wfrl_blender_bootstrap.py` | 前端实现说明「启动与模式切换」「实时连接与控制生命周期」 |
| 测距、标定与物理数据生产 | 仓库级 `wfrl/lidar/physics.py`、`sampling.py`、`dual_beam.py`；`scripts/lidar/postprocess_dual_beam.py`、`package_dual_beam.py` | 雷达算法说明、计算流程；按任务决定是否需后处理或新求解 |
| 打包、安装与交付验证 | `blender_manifest.toml`；仓库级 `scripts/blender/build_extension.py`、`validate_frontend_package.py` | 发布状态与验证范围；前端实现说明「修改、安装与验证」 |

[dist/README-lidar.md](../../../dist/README-lidar.md) 与 [lidar-delivery.json](../../../dist/lidar-delivery.json) 记录的是 **0.3.9 历史交付**，其中 normal/close 路径仍供独立雷达入口使用；不能将它们当成 0.3.13 的完整交付清单。该旧入口见 `scripts/blender/open_clearance_demo.py`、`verify_lidar_delivery.py`。其余历史说明与验收记录同样按版本限定。

## 必须保留的行为

- **旧 B2 模式**的主卡片及主统计使用 B2；B2 无效时不借用 B1/B3 或真值补齐。真值、估计和有符号偏差来自同一次测量，偏差为估计减真值。
- **双束模式**由 S2/S3 同一保存时刻、同一叶片的有效命中配对重建；S1 有效叶片命中独立报警，双束无效不能抑制 S1。没有净空或没有报警不等于安全；报警显示保持不代表保护延迟。保留 `REVIEW_ONLY` / `PENDING_ACCEPTANCE` 与各自统计口径，不把 B2 合同套用到双束。
- 默认旧法与显式 TLS 候选分别核对方法身份、算法版本及来源摘要，界面按实际读取器显示。保存重开时，失效旧安装路径只能按保存的 manifest 哈希定位匹配内置包；不匹配时清空并报错，不能替换方法或重新绑定不同数据。TLS 软件交付不等于精度验收，研究数值及独立工况见 [TLS 总报告](../../../docs/lidar/双束TLS候选实施与验证总报告.md)。
- 无效、缺失、过期和空统计不转为零净空或零误差；维持现有整组读数有限保留与过期规则。缺包、损坏、机型或版本不匹配时清除旧读数并显示可排查的未就绪原因。
- 姿态、卡片、S1 报警和累计统计共用仿真时钟；固定帧映射到固定仿真时刻。修改 Blender FPS 只改变目标墙钟播放速度；暂停、后退、寻址与重播不重复累计统计或暴露未来事件。显示几何插值不能生成缺失测量。
- MAPPO 的 40 Hz 源数据映射到固定 60 Hz 时间轴；暂停单步前进一帧，播放中单步先暂停再前进，末帧钳制。源采样率、显示时间轴与实际绘制帧率分别报告。
- 独立 normal/close 工况切换加载对应片段并从起点开始；仅切观察视角保留工况、进度与暂停状态，不更改雷达标定。保持 MAPPO、双束、独立雷达和后端运行的来源提示及状态隔离；旧 Local Demo/SYNTH 不能作为当前真实训练或现场精度证据。
- **刚性姿态示意仅指独立 normal/close 路径**；MAPPO 根据保存数据驱动九片叶片和三机塔架柔性运动，不能统称为刚性动画。区分结构叶尖、展示叶尖、测量参考表面及平滑显示表面；先核对定义和实际命中点，不能为使画面吻合而改写保存测距。光束显示长度不参与测距，其亮线末端不宣称真实命中点。
- 三相机共用场景和时钟；GRID 为三路加静态安装示意的 2×2，STRIP 为三列，WATCH / 单路放大固定用 SOLID 材质颜色显示，返回三路恢复其画质。视图切换不改盒体标定和相机布局；PNG 原图质量独立管理，采集异常也需恢复回放与预览状态。
- 盒体贴合约束作用于支架安装端；固定相机不自动追踪叶片，不要求三等分或按目标叶长裁图。连续投影、相邻重叠、射线可见性、完整无遮挡与拼接成功分别判断，不能隐藏真实结构来制造通过结果。

## 分层验证

按验收标准选择现有测试，先核对输入、输出和前置条件。必要时补充针对性回归；相关检查通过且没有新改动或未解决问题时即可结束，不为凑覆盖范围反复测试。文档改动只需相应静态校验。

| 验证层 | 现有入口与边界 |
| --- | --- |
| 宿主机 | 从根目录用 `PYTHONPATH=blender_frontend:. python -m pytest <选定测试> -q`，选择现有依赖环境。前端按模块选 `blender_frontend/tests/test_*.py`；B2 用 `tests/lidar/test_replay.py`，双束按需选同目录 `test_dual_beam.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py`。`blender_frontend/tests/test_extension_package.py` 会构建 ZIP，禁止打包的任务必须排除调用构建的检查。 |
| Blender 原生 | `blender_frontend/tests/blender/` 下，MAPPO 用 `mappo_demo_regression.py`、`farm_flex_regression.py`；挠度/视角用 `tip_deflection_regression.py`、`farm_front_view_regression.py`；双束用 `dual_beam_regression.py`；三相机默认配置用 `three_camera_default_regression.py`；旧雷达按需用 `clearance_ux_regression.py`、`clearance_acceptance_regression.py`、`clearance_replay_smoke.py`。先确认脚本支持后台及所需数据，再用 `--background --factory-startup --python-exit-code 1 --python <脚本>`；该命令模板不用于可见窗口脚本。 |
| 实际窗口 | 同目录 `native_camera_enabled_regression.py` 检查当前三相机参与、轻量单路和切换恢复；`dual_beam_window.py` 检查默认双束或用 `WFRL_BUILTIN_METHOD=TLS` 显式检查候选；`clearance_gui_acceptance.py` 检查旧雷达。使用独立 Blender 配置和 `WFRL_TEST_OUTPUT`，按各脚本约定区分输出文件与目录。检查真实窗口的可读性、构图、按钮和错误提示，渲染图不替代窗口；旧 `native_camera_views_regression.py` 默认导入裸源码包，不能当作安装版证明。 |
| 安装 ZIP | `scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender路径>` 构建并在隔离配置中验证实际 ZIP，默认仅后台检查；0.3.13 完整源码默认含两种内置双束结果层，另测外部旧法包时可给 `--dual-package <匹配数据目录>`。它会打包，只在任务包含构建/安装验证时运行。后续窗口检查沿用其 `environment.json`，不能把后台结果写成窗口通过。 |
| 完整片段与性能 | 用 `scripts/blender/run_playback_benchmark.py` 驱动可见窗口 `demo_playback_benchmark.py`，按需选 GRID / STRIP、单路、三路、关闭重开及播放/暂停切换路径。隔离安装版使用 `--installed-profile <验证目录>`，输出到新目录。按每路推进、终止及进程日志区分完成、超时、中止、错误和缺绘制；不能只看平均 FPS 或单个 PASS。 |

安装版测试使用其实际 `bl_ext.<仓库名>.wfrl_blender` 模块：日常安装通常为 `bl_ext.user_default.wfrl_blender`，隔离验证工具为 `bl_ext.wfrl_frontend.wfrl_blender`。按脚本支持设置 `WFRL_ADDON_MODULE`，记录实际 `addon.__file__` 和环境，确认扩展实现从安装目录载入，不能用源码导入冒充安装版。部分旧脚本硬编码包路径或写演示 `.blend`，先核对前置条件并隔离输出；双束脚本需要可用内置数据或 `WFRL_DUAL_PACKAGE` 指定的匹配包。

按改动选择验收行为：MAPPO 加载及 T1/T2/T3 切换；旧法/TLS 的实际估计、方法身份、S1 与 B2 模式各自读数；无效、过期与独立报警；暂停/单步/末帧、后退/寻址/重播历史一致；GRID/STRIP 放大/返回、WATCH 关闭恢复；缺包清空、丢失旧安装路径的哈希恢复、不匹配清空及保存重开。涉及旧入口时再测 normal/close 两工况，纯布局至少检查相关按钮与实际窗口。

`POST_PIXEL` 只表示视口绘制完成，不是显示器扫描输出或实体相机同步；60 Hz 时间轴不能当作稳定 60 FPS。40 Hz 保存几何、理想射线和软件回归不能证明硬件采样、现场漏测率、净空精度或保护延迟。测试期望须匹配运行时数据表示，不能靠放宽容差掩盖差异。

报告给出实际命令、环境和结果，区分“已运行通过”“仅静态检查”“尚未验证”。缺依赖则说明缺项；历史 PASS、宿主机、源码入口、安装 ZIP、窗口和不同平台的证据分别记录。

## 扩展与数据交付判断

仅在扩展构建/交付任务中读取 [构建脚本](../../../scripts/blender/build_extension.py) 和 [隔离安装验证工具](../../../scripts/blender/validate_frontend_package.py)。扩展 Python/资源、规范 Bridge 协议或内置 lidar 模块变化要进入安装版时需要新 ZIP；仅 AGENTS.md、skill、外部说明或项目侧启动器/后端实现变化无需重建，除非同时改变被打包内容。修改规范源文件，不手改生成的 `protocol.py`、`_vendor/lidar/` 副本；当前内置 lidar 模块包括 `replay.py`、`evidence.py`、`dual_beam_replay.py`、`molas_cl.py`。

现有命令为 `python3 scripts/blender/build_extension.py`，默认会写入同版本 dist 文件；验证构建用 `--output <新目录>` 或 `build(output_dir)` 隔离输出。0.3.13 完整源码默认构建内置 MAPPO、旧法和 TLS 两个结果层，并校验方法身份及完整源摘要。外部覆盖分别用 `--dual-package <旧法匹配目录>` 与 `--dual-tls-package <TLS匹配目录>`；换用其他 farm 源而未提供匹配双束包时不能沿用原 sidecar。按本次实际包生成和核对 manifest、ZIP、SHA-256 与 inventory，不套用旧 0.3.9 清单或哈希。

交付前完成本地包检查和适用的安装版验证；安装后重启 Blender，再加载匹配数据。项目启动器走已安装扩展，仓库修改不会自动生效；普通手动后端连接需要 `Load & Validate Scene`，启动器的主动连接流程另有自动加载。发布附件上传成功后按 AGENTS.md 结束，默认不再下载远端附件或进行上传后的文件、字节、校验和比对，仅用户明确要求时执行。

0.3.13 交付的离线资源包括 `assets/mappo/`、`assets/dual_beam/` 与 `assets/dual_beam_tls/`；两种结果层通过 `../mappo` 共享同一源，默认仍为旧法。外部 normal/close 包和原始 VTP 不随扩展提供。外部可携带双束包则包含自身 `source/`，须整体移动并校验源与结果摘要。界面/播放器修改复用结果包；标定/公式变化是否可复用原始输出、几何/物理变化是否需要新求解，按雷达算法说明「物理结果生产与重新发布」和计算流程判断。重建 ZIP 不等于更新物理结果、物理验收通过或发布。

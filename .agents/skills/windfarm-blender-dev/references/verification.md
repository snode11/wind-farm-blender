# 维护与验证入口

仅在选择或执行验证时读取本文件。命令从 Git 仓库根目录执行；宿主测试路径以 `blender_frontend/tests/` 为基准，原生脚本以 `blender_frontend/tests/blender/` 为基准，其他路径明确标为仓库路径。

## 按功能选择

| 改动范围 | 宿主测试 | Blender 检查与适用范围 |
| --- | --- | --- |
| 独立手动场景、参数与转动 | `test_manual_motion.py`、`test_manual_pose.py` | 后台 `manual_control_regression.py` 检查刚性姿态、转速连续性、参数隔离、保存重开和 demo 保护；`manual_pose_regression.py` 检查预设几何、逐机相对微调、姿态 JSON 与原子校验，`manual_pose_undo_regression.py` 检查姿态工具撤销重做。按需选 `manual_control_undo_regression.py`、`manual_scene_recovery_regression.py` 检查场景撤销与损坏重建。实际窗口 `manual_control_window.py` 检查原侧栏、混合桨距概览和定时运动，`manual_playback_window.py` 检查往返原回放的播放归属。 |
| NREL／rootkink 叶片外形 | `test_blade_models.py` | 后台 `blade_model_regression.py` 检查几何绑定、九片保存运动、20 次外形切换的网格数量、单次读包、共享／fake-user／shape-key 保护、时间与相机保留、部分及完整绑定失败恢复和保存重开；实际窗口 `blade_model_window.py` 检查外形选择与原生播放，`shared_context_switch_regression.py` 检查退出重建后的 20 次模型切换、共享绑定失败恢复及保存重开。合成外形的软件检查不代表新机型物理精度。 |
| 重建模式、共享结构和样本时钟 | `test_split_mappo_texture.py`、`test_split_reconstruction.py`、`test_split_reconstruction_timing.py`、`test_split_texture_settings.py`、`test_mappo_texture_package.py` | 后台 `split_turbine_context_regression.py` 检查结构复用、T2/T3、稳定刷新、保存重开和资产保持；实际窗口 `reconstruction_workflow_window.py` 检查统一流程。 |
| 场景恢复与播放归属 | `test_scene_playback_lifecycle.py`、`test_native_camera_lifecycle.py`、`test_native_camera_playback.py` | 后台 `scene_playback_lifecycle_regression.py` 检查 FULL_COPY 缺失对象和 linked scene 第二时钟拒绝；`replay_history_regression.py` 检查原生撤销／重做、九叶片与时钟、缺包拒绝假回退及删除场景清理；实际窗口 `native_camera_playback_regression.py` 检查独立窗口组的按钮归属、正常起停、关闭与延迟恢复；`native_camera_enabled_regression.py` 检查跨场景关闭及观察会话。 |
| NREL 单路机舱相机、投影与可见性 | `test_nacelle_camera.py`、`test_camera_projection.py`、`test_nacelle_visibility.py` | 后台 `nacelle_camera_intrinsics_regression.py` 检查机械基准、原生投影、手动姿态保留、复位及输出比例恢复；仓库 `scripts/blender/check_nrel_nacelle_camera.py` 检查保存柔性运动下的取景与有限射线可见性，实际视口交互另需可见窗口。 |
| T1/T2/T3 Down 挂载与光学预设 | 投影计算使用 `test_camera_projection.py` | 后台 `farm_down_mount_regression.py` 检查三机挂载、显式复位、保存迁移、手动姿态保留和独立相机保护；`t1_down_mount_regression.py` 检查外壳挂点、外伸光心与保存回放；`nacelle_camera_regression.py` 检查 Down 和独立机舱相机的切换、光学参数隔离及任意滚转／俯仰极点下的屏幕方向投影。照片参考构图按实际模型、姿态和画幅另做静态／有限角度检查，区分区域表面与精确后缘端点可见性，不能由软件回归推定完整运动无遮挡。 |
| 三相机默认布局、事务与槽位 | `test_stacked_camera_rig.py`、`test_stacked_camera_swivel.py`、`test_custom_cameras.py`、`test_custom_camera_v3.py`、`test_custom_camera_sidebar.py` | 后台 `three_camera_default_regression.py`；`custom_camera_edit_binding_regression.py` 检查编辑确认、取消、退出快照及发布失败回滚，仅提供源码后台证据，不包含 GPU 或鼠标交互；相关按钮及恢复结果还需实际窗口。 |
| 相机窗口、资源释放与原图采集 | `test_native_camera_lifecycle.py`、`test_custom_camera_retirement.py`、`test_custom_camera_capture.py`、`test_custom_camera_preview.py` | 后台 `custom_camera_retirement_regression.py` 检查布局部分释放失败后的对象／镜头数据重试及共享数据保护。实际窗口 `native_camera_enabled_regression.py` 检查参与路由、单路和异常清理；`frontend_layout_regression.py` 检查 GRID/STRIP、放大返回和退出；`custom_camera_capture_window.py` 检查真实 GPU 事务、失败和取消恢复；`native_camera_save_regression.py` 检查主镜头隔离、保存及实际写入失败恢复、重开无代理和观察中撤销。 |
| NREL 缺陷编辑与几何分析 | `test_nrel_defects.py`、`test_nrel_defect_visibility.py`、`test_nrel_editor_playback.py` | 后台 `nrel_editor_resource_regression.py` 检查辅助对象删除、共享保护、关闭重试及已删除场景清理；`nrel_defect_editor_regression.py` 检查事务与保存重开，`nrel_defect_ui_state_regression.py` 检查源码 RNA 列表与按钮状态；实际窗口 `nrel_defect_window_regression.py`；`nrel_editor_playback_regression.py` 检查跨场景播放隔离、原窗口恢复、切场景与关窗保护。 |
| MAPPO 柔性运动、挠度与取景 | `test_tip_deflection.py` 及受影响模块的现有宿主测试 | 后台按需选 `mappo_demo_regression.py`、`farm_flex_regression.py`、`tip_deflection_regression.py`、`farm_front_view_regression.py`；实际构图需可见窗口。失败的物理门槛不随软件检查通过而改变。 |
| 柔性工作集、遥测数组与 CPU 计时 | `test_farm_flex_workload.py`、`test_farm_telemetry_arrays.py`、`test_performance.py`；按消费方选择 `test_comparison_visibility.py`、`test_tip_deflection.py`、`test_camera_video_geometry.py`、`test_nrel_defect_visibility.py` | 源码后台 `farm_flex_workload_regression.py` 检查保存／插值／终点时刻、可见与隐藏 T1 结构叶尖一致、同刻切换工作集、重绑缓存失效、模型切换时钟、首次共享 T1 塔筒与单机 T2 导出。共享结构及外形绑定另选表中对应原生脚本；不证明安装载荷或 FPS。 |
| 后端断连、可靠事件与前端回传 | 仓库 `tests/blender_bridge/test_disconnect_progress.py`、`tests/blender_bridge/test_training_progress.py`；前端 `test_transport.py`、`test_training_stats_protocol.py`、`test_live_runtime.py`、`test_runtime_redraw.py` | 宿主检查断连期间生命周期、可合并统计、可靠事件、实际溢出失败、重连年龄和有界发送；原生连接按实际后端与安装来源另验，宿主模拟后端不证明真实训练完成。 |
| 全场功率与 Interactive 目标 | 仓库 `tests/blender_bridge/test_farm_power_validity.py`、`tests/test_interactive_ppo_objective.py` | 宿主检查完整机组读数、缺测传播、迭代统计与固定张量 PPO 目标；不启动物理求解。算法与路由依据[训练入口与合同](../../../../docs/training/MAPPO_SETUP.md)。 |
| 项目启动器与 MPI 环境 | `test_launcher.py`；仓库 `tests/blender_bridge/test_isolated_trainer.py` | 检查启动参数、继承作业身份清理、显式传输配置和取消逻辑；真实 MPI / FAST.Farm 运行须在目标环境单独验证，不归入扩展 ZIP 安装检查。 |
| 雷达方法、统计与便携包 | `test_dual_beam_builtin.py`；仓库 `tests/lidar/test_replay.py`、`test_dual_beam.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py` 均位于 `tests/lidar/` | 后台 `dual_beam_regression.py`、`clearance_replay_smoke.py`；实际窗口 `dual_beam_window.py`、`clearance_gui_acceptance.py`。按需核对旧 B2、默认双束及 TLS 各自合同。 |
| 构建工具与安装包 | `test_frontend_package_validation.py` 测工具逻辑；`test_extension_package.py` 会构建产品 ZIP | 仓库 `scripts/blender/validate_frontend_package.py` 构建并隔离安装，默认执行后台检查；实际窗口另选相应脚本。 |
| 完整片段与播放性能 | 按基准工具改动选择对应宿主测试 | 仓库 `scripts/blender/run_playback_benchmark.py` 驱动实际窗口 `demo_playback_benchmark.py`，分别判断各路绘制、完成状态与进程退出。 |

## 宿主与原生后台

宿主使用已有依赖环境，按表选择测试，不直接运行整个测试目录。柔性工作集与遥测测试需要 NumPy；Interactive 目标测试还需 PyTorch，后端检查按其导入核对项目依赖，不为文档检查安装环境：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=blender_frontend:. python -m pytest <选定测试路径> -q -p no:cacheprovider
```

先阅读脚本入口、导入方式和输出行为。支持后台的原生脚本使用：

```bash
<Blender可执行文件> --background --factory-startup --python-exit-code 1 --python <脚本路径>
```

此模板不能用于实际窗口脚本。使用已有安装 profile 时，先核对脚本是否支持工厂启动；工厂启动不是安装版测试的通用前提。

`farm_flex_workload_regression.py` 只支持工作区源码，必须使用独立 `--background --factory-startup` 进程；脚本断言模块目录与空白注册状态，不读取安装模块选择变量。结果写到标准输出的 `FARM_FLEX_WORKLOAD_REGRESSION_PASS`，不保存场景或报告，不需要 `WFRL_TEST_OUTPUT`。工作集计数与数值一致性不能替代真实窗口、安装包或物理精度检查。

`split_turbine_context_regression.py` 要求后台运行，默认导入工作区 `wfrl_blender`。安装版模式指定 `WFRL_ADDON_MODULE=bl_ext.<仓库名>.wfrl_blender` 并设置精确的 `WFRL_EXPECTED_ADDON_ROOT`；`WFRL_TEST_OUTPUT` 必须是不存在的新目录。可用 `WFRL_SPLIT_INPUT` 选择输入场景副本，保存输出不得覆盖规范资产。

表中手动控制原生脚本均支持 `WFRL_ADDON_MODULE` 和 `WFRL_EXPECTED_ADDON_ROOT`，安装模式不添加工作区源码导入路径，`WFRL_TEST_OUTPUT` 必须是不存在的新目录。后台检查使用独立工厂启动场景；`manual_control_regression.py` 默认检查 MAPPO 隔离，设 `WFRL_MANUAL_EMPTY_ONLY=1` 仅检查从空白场景进入。撤销与恢复脚本依赖工厂场景的 `Cube`，保存重开脚本在输出目录写入并重开自己的 `.blend`；窗口脚本不得使用 `--background`。

机舱相机的两个原生脚本均支持 `WFRL_ADDON_MODULE` 和 `WFRL_EXPECTED_ADDON_ROOT`，安装模式不回退到工作区源码。`nacelle_camera_intrinsics_regression.py` 会创建测试对象、保存重开临时文件并加载 MAPPO，应使用独立工厂启动进程；其 `WFRL_TEST_OUTPUT` 是目录且允许已有路径，须显式选择新目录以免覆盖 `checks.json`。`check_nrel_nacelle_camera.py` 通过 `-- --output <新目录>` 指定输出，生成 PNG、场景与参数；复用保存运动，不运行物理求解。渲染与有限首命中采样不能证明实际窗口交互、整片表面连续可见或现场图片可辨识。

Down 的 `farm_down_mount_regression.py` 和 `t1_down_mount_regression.py` 通过 `WFRL_TEST_RUNTIME` 选择安装模块，并以 `WFRL_EXPECTED_ADDON_ROOT` 核对精确目录；`nacelle_camera_regression.py` 使用 `WFRL_ADDON_MODULE` 和 `WFRL_EXPECTED_ADDON_ROOT`。叶片外形的后台与窗口脚本使用 `WFRL_TEST_ADDON_MODULE` 和 `WFRL_EXPECTED_ADDON_DIR`。这些脚本的 `WFRL_TEST_OUTPUT` 均显式选择新目录；其中 Down 挂载与叶片外形脚本要求目录不存在，`nacelle_camera_regression.py` 允许已有目录并会写入 `checks.json`。安装模式要求所选 profile 已启用扩展，不使用源码模式的 `--factory-startup`。保存重开只写各自输出目录或专用临时目录中的场景，实际窗口脚本不得使用 `--background`。

`replay_history_regression.py`、`shared_context_switch_regression.py`、`native_camera_save_regression.py` 和 `nrel_editor_resource_regression.py` 支持 `WFRL_ADDON_MODULE`、`WFRL_EXPECTED_ADDON_ROOT` 与不存在的新 `WFRL_TEST_OUTPUT`。安装模式不添加工作区源码路径；共享结构与相机保存脚本必须使用独立可见进程，临时场景不得覆盖正式资产。

## 实际窗口与安装来源

- 在独立可见 Blender 进程和配置中检查实际按钮、构图、读数及错误提示；保护用户已有脏场景。脚本操作通过与真实鼠标／键盘输入分别说明，不将后台或渲染结果写成窗口通过。
- `reconstruction_workflow_window.py` 从新的、干净的可见进程启动，通过产品入口打开内置示例。必须设置 `WFRL_ADDON_MODULE=bl_ext.<仓库名>.wfrl_blender`、`WFRL_EXPECTED_ADDON_ROOT` 和新 `WFRL_TEST_OUTPUT`，不要预先打开脏场景。
- 该脚本默认校验固定的规范几何场景摘要。目标包使用其他已确认资产时，先根据对应 inventory 与验证证据核定身份，再按包设置 `WFRL_EXPECTED_GEOMETRY_SHA256`；不能直接把失败现场的摘要填成期望值来绕过资产校验。
- 该重建脚本覆盖几何／同源纹理往返、两侧完整取景、共享播放、暂停／单帧步进、一次观察点、绕转与缩放后继续播放、原始／增强、退出返回及保存重开。窗口检查不得重建求解输入或改写正式结果。
- `native_camera_enabled_regression.py` 支持安装模块选择，安装模式不向导入路径添加工作区源码，并记录 `addon.__file__`；它不读取 `WFRL_EXPECTED_ADDON_ROOT`。`frontend_layout_regression.py` 支持模块选择，`WFRL_ADDON_ROOT` 只改变导入搜索路径，也不自动核验期望安装目录。对未内置目录断言的脚本，须另核对实际模块来源。
- `custom_camera_capture_window.py` 支持 `WFRL_ADDON_MODULE` 和 `WFRL_EXPECTED_ADDON_ROOT`；GPU 资源复用、PNG/state 一致与失败恢复各自判定，不能据此宣布完整片段 FPS 或长期无泄漏。
- `nrel_defect_editor_regression.py` 与 `nrel_defect_window_regression.py` 支持模块选择；`nrel_defect_ui_state_regression.py` 硬编码源码导入，只能提供源码后台证据。缺陷保存重开使用相同输出目录，第二个新进程设置脚本接口 `WFRL_NREL_DEFECT_PHASE=reopen`。
- `nrel_editor_playback_regression.py` 使用独立可见进程，支持 `WFRL_ADDON_MODULE`、`WFRL_EXPECTED_ADDON_ROOT` 和不存在的新 `WFRL_TEST_OUTPUT`；默认完成后退出，`WFRL_TEST_KEEP_OPEN=1` 保留窗口。脚本的恢复场景写入专用输出目录。
- 上述相机、重建和缺陷脚本的 `WFRL_TEST_OUTPUT` 是目录，按脚本要求选择新目录或保存重开专用目录。其他脚本须单独核对该变量表示文件还是目录，以及是否写入演示 `.blend`。
- 双束脚本需要匹配内置数据或 `WFRL_DUAL_PACKAGE`；`dual_beam_window.py` 用 `WFRL_BUILTIN_METHOD=TLS` 显式选择候选。方法、源摘要、S1 与双束读数须同时核对，不能用 B2 统计代替双束统计。

实际安装模块通常是日常 `bl_ext.user_default.wfrl_blender` 或隔离 `bl_ext.wfrl_frontend.wfrl_blender`。环境变量只在脚本读取时生效；没有确认导入路径和载荷身份的测试不能作为安装版证据。

## 打包与隔离安装

任务包含构建／安装验证时使用：

```bash
python3 scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender可执行文件>
```

工具构建 ZIP、创建隔离配置并执行后台检查。`--build-only` 仍构建 ZIP，仅跳过安装；禁止打包的任务须排除该工具和 `test_extension_package.py`。额外外部旧法包使用 `--dual-package <匹配目录>`，不将此参数解释为 TLS 选择。

窗口验证继承工具输出的 `environment.json` 和对应 profile，保留其安装目录与偏好。后台安装检查不能替代窗口检查，工作区源码检查也不能证明已安装资产正确。若验证所需脚本或依赖不在当前源码检出中，标明缺项，不借用其他环境的 PASS。

## 完整播放与性能

`performance.py` 默认关闭，启用后汇总 CPU 回调耗时和计数，不保存逐帧无限样本。`flex.deformation_cache` 覆盖 `deformed_at()` 的查询、命中返回与未命中计算，`flex.rebind_blade` 单独计时。核对 `flex.cache_hit` / `flex.cache_miss`、`flex.participating_turbines`、`flex.blade_updates`、`flex.vertices_written`、`flex.tower_vertices_written` 与 `flex.measurement_points`，确认整叶、塔筒及隐藏结构叶尖各自的工作量。计时可能嵌套，不能相加推算帧耗时；后台结果和减少写入次数均不是视口 FPS。

```bash
python3 scripts/blender/run_playback_benchmark.py --blender <Blender可执行文件> --output <新目录> --installed-profile <隔离验证目录>
```

`--installed-profile` 是 benchmark 启动器参数，要求该目录含有效 `environment.json` 且 profile 仍存在；启动器继承安装环境、清除源码导入覆盖并避免工厂启动。按实际需求用 `--path` 和 `--layout` 选择单路、三路、GRID/STRIP、关闭重开或播放切换路径，参数可选值以脚本定义为准。

逐路检查推进、终止、缺绘制、超时、中止和错误，结合进程结果判定完整片段。平均 FPS、单个 PASS、近 2 秒采样或暂停时刷新次数均不能替代完成判定；不把未测平台、长期运行或科学精度纳入结论。

单路完整播放后关闭、重开三路时停止推进的问题根因仍未定位；相关源码检查不能将该路径或完整片段 FPS 标为已验收。

## 纯文档与 skill

核对 YAML frontmatter、Markdown 链接和表格、所列模块／测试／参数是否存在，以及有效要求与实际实现是否分别表述。清理开发过程叙事、失效规则和重复状态；不创建执行日志，不为文档修改启动 Blender、构建包或运行物理求解。验证结果仅报告实际执行的静态检查。

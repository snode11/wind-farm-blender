# 维护与验证入口

仅按任务读取本文件。命令从 Git 根目录执行；下表宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`，其他路径明确标出。这里只说明入口与覆盖意图，实际 PASS 须对应同一载荷、环境和输入。

## 选择检查

“后台”不证明可见交互；“窗口”脚本操作与真实鼠标／键盘分别报告。按受影响行为选择，不默认运行整个测试目录。

| 范围 | 宿主入口 | 原生入口与重点 |
| --- | --- | --- |
| 手动场景与姿态 | `test_manual_motion.py`、`test_manual_pose.py` | 后台 `manual_control_regression.py`、`manual_pose_regression.py`、`manual_pose_undo_regression.py`、`manual_control_undo_regression.py`、`manual_scene_recovery_regression.py`：姿态、隔离、撤销与恢复；窗口 `manual_control_window.py`、`manual_playback_window.py`。 |
| 恒速变桨与手动 Down | `test_manual_motion.py` 的 `PitchTrialTests` | 后台 `manual_pitch_trial_regression.py`、`manual_pitch_trial_undo_regression.py`、`manual_down_copy_regression.py`：冻结参数、共同时间、未选保持、复制归属；窗口 `manual_pitch_trial_window.py`。 |
| 手动视频 | `test_manual_video_render.py`、`test_manual_video_job.py`；仓库 `tests/test_camera_video_media_recovery.py`、`tests/test_camera_video_publication.py` | 后台 `manual_video_render_regression.py`、`manual_video_launch_regression.py`、`manual_video_lifecycle_regression.py`；范围与成本见下文。 |
| 叶片外形 | `test_blade_models.py` | 后台 `blade_model_regression.py`：绑定事务、共享网格与时间；窗口 `blade_model_window.py`、`shared_context_switch_regression.py`。 |
| 重建与共享结构 | `test_split_mappo_texture.py`、`test_split_reconstruction.py`、`test_split_reconstruction_timing.py`、`test_split_texture_settings.py`、`test_mappo_texture_package.py` | 后台 `split_turbine_context_regression.py`：结构、来源与资产；窗口 `reconstruction_workflow_window.py`：模式、自由视图、播放与保存恢复。 |
| 场景与播放归属 | `test_scene_playback_lifecycle.py`、`test_native_camera_lifecycle.py`、`test_native_camera_playback.py` | 后台 `scene_playback_lifecycle_regression.py`、`replay_history_regression.py`；窗口 `native_camera_reopen_regression.py`、`native_camera_playback_regression.py`、`native_camera_enabled_regression.py`。 |
| 独立机舱相机 | `test_nacelle_camera.py`、`test_camera_projection.py`、`test_nacelle_visibility.py` | 后台 `nacelle_camera_intrinsics_regression.py`；仓库 `scripts/blender/check_nrel_nacelle_camera.py`：渲染、投影和有限射线。 |
| 三机 Down | `test_camera_projection.py` | `farm_down_mount_regression.py`、`t1_down_mount_regression.py`、`nacelle_camera_regression.py`：挂点、预设、保存迁移与光学隔离；后台结果不作窗口证据。 |
| 三相机布局与事务 | `test_stacked_camera_rig.py`、`test_stacked_camera_swivel.py`、`test_custom_cameras.py`、`test_custom_camera_v3.py`、`test_custom_camera_sidebar.py` | 后台 `three_camera_default_regression.py`、`custom_camera_edit_binding_regression.py`：默认布局、退出快照和回滚；按钮交互另用窗口。 |
| 相机窗口、释放与采集 | `test_native_camera_lifecycle.py`、`test_custom_camera_retirement.py`、`test_custom_camera_capture.py`、`test_custom_camera_preview.py` | 后台 `custom_camera_retirement_regression.py`；窗口 `native_camera_enabled_regression.py`、`frontend_layout_regression.py`、`custom_camera_capture_window.py`、`native_camera_save_regression.py`：路由、GPU、保存隔离与失败恢复。 |
| 缺陷编辑与播放 | `test_nrel_defects.py`、`test_nrel_defect_visibility.py`、`test_nrel_editor_playback.py` | 后台 `nrel_editor_resource_regression.py`、`nrel_defect_editor_regression.py`、`nrel_defect_ui_state_regression.py`；窗口 `nrel_defect_window_regression.py`、`nrel_editor_playback_regression.py`。 |
| 柔性运动与挠度 | `test_tip_deflection.py` | 后台 `mappo_demo_regression.py`、`farm_flex_regression.py`、`tip_deflection_regression.py`、`farm_front_view_regression.py`；实际构图另查窗口。 |
| 工作集与遥测 | `test_farm_flex_workload.py`、`test_farm_telemetry_arrays.py`、`test_performance.py`；按消费者选 `test_comparison_visibility.py`、`test_tip_deflection.py`、`test_camera_video_geometry.py`、`test_nrel_defect_visibility.py` | 源码后台 `farm_flex_workload_regression.py`：隐藏测量、同刻换工作集、重绑、共享塔筒和单机导出。 |
| Bridge 断连与事件 | 仓库 `tests/blender_bridge/test_disconnect_progress.py`、`tests/blender_bridge/test_training_progress.py`；前端 `test_transport.py`、`test_training_stats_protocol.py`、`test_live_runtime.py`、`test_runtime_redraw.py` | 真实连接与训练另按实际后端和安装来源验收。 |
| 全场功率与 Interactive | 仓库 `tests/blender_bridge/test_farm_power_validity.py`、`tests/test_interactive_ppo_objective.py` | 缺测传播与固定张量目标；路由见[训练合同](../../../../docs/training/MAPPO_SETUP.md)。 |
| 启动器与 MPI | `test_launcher.py`；仓库 `tests/blender_bridge/test_isolated_trainer.py` | 参数、继承环境与取消；真实 MPI／FAST.Farm 须目标环境验证。 |
| 雷达与便携包 | `test_dual_beam_builtin.py`；仓库 `tests/lidar/` 下的 `test_replay.py`、`test_dual_beam.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py` | 后台 `dual_beam_regression.py`、`clearance_replay_smoke.py`；窗口 `dual_beam_window.py`、`clearance_gui_acceptance.py`。 |
| 构建与安装 | `test_frontend_package_validation.py`；`test_extension_package.py` 会构建 ZIP | 仓库 `scripts/blender/validate_frontend_package.py`。 |
| 完整播放与性能 | 按基准工具改动选宿主测试 | 仓库 `scripts/blender/run_playback_benchmark.py` 驱动窗口 `demo_playback_benchmark.py`。 |

## 执行环境与来源

宿主使用已有依赖环境；工作集测试需要 NumPy，Interactive 目标还需要 PyTorch。不为文档任务安装运行依赖。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=blender_frontend:. python -m pytest <选定测试路径> -q -p no:cacheprovider
```

支持源码后台的脚本可用：

```bash
<Blender可执行文件> --background --factory-startup --python-exit-code 1 --python <脚本路径>
```

窗口脚本不得加 `--background`；安装模式先核对注册方式，已启用 profile 的检查不能照搬工厂启动。使用独立进程、配置和场景副本，保护用户已有脏场景与规范资产。

脚本读取的变量并不统一。日常模块通常为 `bl_ext.user_default.wfrl_blender`，隔离模块为 `bl_ext.wfrl_frontend.wfrl_blender`；安装检查必须核对实际模块目录和载荷，未使用的环境变量不产生验证作用。

| 脚本范围 | 模块选择与目录校验 |
| --- | --- |
| 普通手动场景／姿态／变桨脚本、共享结构、回放恢复、相机保存、缺陷资源／播放、机舱内参、GPU 采集 | `WFRL_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_ROOT`；安装模式不回退源码。 |
| `farm_down_mount_regression.py`、`t1_down_mount_regression.py`、`nacelle_camera_regression.py` | `WFRL_TEST_RUNTIME` + `WFRL_EXPECTED_ADDON_ROOT`。 |
| `blade_model_regression.py`、`blade_model_window.py` | `WFRL_TEST_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_DIR`。 |
| `native_camera_enabled_regression.py`、`frontend_layout_regression.py`、`nrel_defect_editor_regression.py`、`nrel_defect_window_regression.py` | 支持 `WFRL_ADDON_MODULE`，没有期望安装目录断言，须另核对来源；布局脚本的 `WFRL_ADDON_ROOT` 仅控制搜索路径。 |
| `manual_down_copy_regression.py`、三份手动视频脚本、`farm_flex_workload_regression.py`、`custom_camera_edit_binding_regression.py`、`nrel_defect_ui_state_regression.py` | 只支持工作区源码；设置安装模块变量不能将其变为安装版检查。 |

特殊输入与副作用：

- `WFRL_TEST_OUTPUT` 通常是目录，默认选不存在的新目录；其他脚本先确认是目录还是文件。缺陷保存／重开检查复用专用目录，在第二个进程设 `WFRL_NREL_DEFECT_PHASE=reopen`。允许已有目录的脚本也可能覆盖报告。
- 手动撤销／恢复脚本依赖工厂场景的 `Cube`。`manual_control_regression.py` 默认检查 MAPPO 隔离，`WFRL_MANUAL_EMPTY_ONLY=1` 缩小为空白场景范围。源码启动器 `scripts/blender/open_manual_control.py` 不等于回归测试。
- `farm_flex_workload_regression.py` 要求独立后台工厂启动，断言源码与空白注册状态；仅输出 `FARM_FLEX_WORKLOAD_REGRESSION_PASS`，不需要输出目录。
- `split_turbine_context_regression.py` 用后台，`WFRL_SPLIT_INPUT` 可选择场景副本。`reconstruction_workflow_window.py` 用干净可见进程，必须指定安装模块和精确目录。两者的 `WFRL_EXPECTED_GEOMETRY_SHA256` 须来自已确认资产及 inventory，不能取失败现场摘要来绕过校验。
- `shared_context_switch_regression.py`、`native_camera_save_regression.py`、`nrel_editor_playback_regression.py` 使用独立可见进程；编辑器播放脚本以 `WFRL_TEST_KEEP_OPEN=1` 保留窗口。
- `scripts/blender/check_nrel_nacelle_camera.py` 支持 `WFRL_ADDON_MODULE` 与 `WFRL_EXPECTED_ADDON_ROOT`，用 `-- --output <新目录>`，会生成 PNG、场景和参数。
- 双束检查需要匹配内置包或 `WFRL_DUAL_PACKAGE`；`dual_beam_window.py` 通过 `WFRL_BUILTIN_METHOD=TLS` 选择候选，须同时核对方法、源摘要、S1 与双束。

## 手动视频

三份原生视频脚本使用源码、独立后台工厂启动和新输出目录，会生成媒体或启动后台作业。真实媒体依赖 FFmpeg／FFprobe；产品监管还依赖独立 Python 3.11+ 与 `flock / waitid(WNOWAIT)`。

| 检查 | 覆盖边界 |
| --- | --- |
| 宿主视频测试 | 普通 Python 子进程、模拟编解码器与占位文件；验证监管、母版重试和发布事务，不证明真实 Blender／媒体或实际跨文件系统通过。 |
| `manual_video_render_regression.py` | 短诊断片段、逐帧姿态、Down、未选叶片及 T2/T3 保持、真实编码／解码；`WFRL_TEST_WIDTH / WFRL_TEST_HEIGHT / WFRL_TEST_SAMPLES` 可调诊断成本。没有安装目录断言。 |
| `manual_video_launch_regression.py` | 产品操作器、独立监管／渲染进程、冻结配置、源场景保持、成片与取消。默认短片，`WFRL_VIDEO_FULL_SAMPLE=1` 才生成完整样片；断言后台与源码目录。 |
| `manual_video_lifecycle_regression.py` | 撤销／重做、场景复制隔离、保存重开、控制端内存清空、注册恢复、取消和失败；在同一源 Blender 进程内重开，不覆盖另一个源 Blender 进程的恢复。 |

`manual_video_installed_regression.py` 在独立安装 profile 中，以 `WFRL_VIDEO_RECOVERY_MODE=create / recover` 分别启动两个新进程，复用同一 `WFRL_TEST_OUTPUT`；`WFRL_VIDEO_PYTHON` 指向支持监管接口的解释器。它以非媒体子进程核对项目定位、保存恢复及取消，不运行渲染或编解码。

短片、完整样片、实际交付视频和源 Blender 新进程恢复分别验证。姿态样本数不证明媒体已生成，预览窗口脚本不覆盖导出文件选择器；核对完成状态、输出和进程退出，不能以启动成功或 `--detach` 返回代替成片结果。

## 构建与隔离安装

仅构建：

```bash
python3 scripts/blender/build_extension.py --output <新目录>
```

默认拒绝覆盖 ZIP、校验和与 inventory；`--overwrite` 才允许替换。外部旧法／TLS 包分别用 `--dual-package`、`--dual-tls-package`，都须匹配源身份。

构建并隔离安装：

```bash
python3 scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender可执行文件>
```

`--build-only` 仍构建 ZIP；禁止打包的任务排除该工具和 `test_extension_package.py`。此工具的 `--dual-package` 仅为外部旧法包，不代表 TLS。窗口检查继承输出的 `environment.json` 和 profile；缺脚本、依赖或 profile 就报告缺项，不借用其他环境的 PASS。

## 完整播放与性能

```bash
python3 scripts/blender/run_playback_benchmark.py --blender <Blender可执行文件> --output <新目录> --installed-profile <隔离验证目录>
```

`--installed-profile` 要求有效 `environment.json` 与仍存在的 profile；启动器继承安装环境、清除源码覆盖且不使用工厂启动。以 `--path / --layout` 选择实际路径，取值查脚本。

每路核对起始绘制、持续推进、终段、心跳和完成状态，结合超时、中止、错误、缺绘制及进程退出判断。寻址不充当循环完成；`native_camera_reopen_regression.py` 连续验证单路到末帧、关闭、三路重开并重播到末帧，以及暂停、往返和延迟续播失效；安装模式须使用 `WFRL_ADDON_MODULE` 与 `WFRL_EXPECTED_ADDON_ROOT`。各项证据须匹配实际载荷，不由另一条独立三路成功推定该路径通过。

`performance.py` 只计 CPU 回调；`flex.deformation_cache`、`flex.rebind_blade` 及缓存／机组／顶点／隐藏叶尖计数用于诊断，嵌套耗时不能相加。平均 FPS、近两秒诊断和减少写入次数不能代替完整片段验收。

## 纯文档与 skill

仅核对 frontmatter、链接／锚点、表格、模块／测试路径、脚本参数和要求一致性；删除重复与失效描述。无需启动 Blender、运行渲染或求解、构建包；只报告实际静态检查。

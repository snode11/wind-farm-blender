# 维护与验证入口

命令从 Git 根目录执行。下表列主要入口：宿主文件在 `blender_frontend/tests/`，原生脚本在 `blender_frontend/tests/blender/`，“仓库”路径从根目录解析。按受影响行为选择，其他用例按相邻模块查找；后台、脚本窗口与真实鼠标／键盘分别报告，PASS 须匹配载荷、环境和输入。

## 按功能选检查

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| WFRL 主入口与状态 | `test_home_state.py` | 后台 `home_entry_smoke.py`：真实 RNA／操作器配合布局适配器，检查页面、相机草稿取消、回放推进、手动场景往返与卸载；不代替可见窗口排版或真实鼠标操作。 |
| 手动场景与姿态 | `test_manual_motion.py`、`test_manual_pose.py` | 后台 `manual_control_regression.py`、`manual_pose_undo_regression.py`、`manual_scene_recovery_regression.py`、`manual_scene_link_compatibility_regression.py`、`manual_rotor_conflict_regression.py`；窗口 `manual_control_window.py`。 |
| 手动装配、精度与静态复用 | `test_blade_models.py`、`test_scene_mesh_lifecycle.py` | 后台 `manual_model_regression.py`、`manual_geometry_compatibility_regression.py`；跨场景、回放接头、导出副本和环境共用 `model_environment_integration.py`。 |
| 冻结试验、普通变桨与 Down | `test_manual_motion.py`、`test_manual_down.py` | 后台 `manual_pitch_trial_regression.py`、`manual_pitch_trial_undo_regression.py`、`manual_down_copy_regression.py`、`manual_rate_pitch_regression.py`、`manual_down_regression.py`；窗口 `manual_pitch_trial_window.py`。 |
| 视频作业与媒体 | `test_manual_video_render.py`、`test_manual_video_job.py`；仓库 `tests/test_camera_video_media_recovery.py`、`tests/test_camera_video_publication.py` | 见下文视频验证边界；便捷视频取消另查可见渲染。 |
| 叶片外形 | `test_blade_models.py` | 后台 `blade_model_regression.py`；窗口 `blade_model_window.py`、`shared_context_switch_regression.py`。 |
| 环境几何与贴地 | `test_landscape_geometry.py`、`test_landscape_distribution.py` | 后台 `landscape_geometry_regression.py`；同机位输出与多窗口背景另外核对。 |
| 重建、纹理与共享结构 | `test_split_reconstruction.py`、`test_split_mappo_texture.py`、`test_mappo_texture_package.py` | 后台 `split_turbine_context_regression.py`；窗口 `reconstruction_workflow_window.py`。 |
| 场景与播放归属 | `test_scene_mesh_lifecycle.py`、`test_scene_playback_lifecycle.py`、`test_native_camera_playback.py` | 后台 `scene_playback_lifecycle_regression.py`、`replay_history_regression.py`；窗口 `native_camera_reopen_regression.py`、`native_camera_playback_regression.py`。 |
| 窗口录制、视口隔离与刷新 | `test_recording_runtime.py`、`test_live_viewport_isolation.py`、`test_runtime_redraw.py` | 窗口 `frontend_sessions_window.py` 验证真实截图、场景删除、窗口关闭、播放标签和其他视口保护。 |
| 实时场景归属与尾流有效性 | `test_live_scene_ownership.py`、`test_wake_payload.py` | 后台 `live_contracts_regression.py` 验证非活动场景及所属 View Layer、无消息时删除归属场景、显式重连、尾流过期与恢复、过期期间开关及逐 View Layer 隐藏保护；不启动真实后端。 |
| 相机预览与宽／高画幅 | `test_camera_projection.py`、`test_custom_camera_preview.py` | 宿主检查场景播放归属、短边下限及 GPU 舍入边界；后台 `live_contracts_regression.py` 核对预览尺寸、FOV 与正式输出保持，不测 GPU 像素或 FPS。 |
| 普通后台动画渲染 | `test_animation_render.py` | 后台 `animation_render_regression.py` 验证短 PNG、取消、源状态和 MAPPO 运动；不代替完整片段或 FPS 验收。 |
| 机舱相机与 Down | `test_camera_projection.py`、`test_nacelle_visibility.py` | 后台 `nacelle_camera_intrinsics_regression.py`、`farm_down_mount_regression.py`、`t1_down_mount_regression.py`、`nacelle_camera_regression.py`；仓库 `scripts/blender/check_nrel_nacelle_camera.py` 做投影、射线与渲染检查。 |
| 三相机布局与绑定 | `test_stacked_camera_rig.py`、`test_custom_cameras.py`、`test_custom_camera_v3.py` | 后台 `three_camera_default_regression.py`、`custom_camera_edit_binding_regression.py`；按钮交互另验。 |
| 相机窗口、释放与采集 | `test_native_camera_lifecycle.py`、`test_custom_camera_retirement.py`、`test_custom_camera_capture.py` | 后台 `custom_camera_retirement_regression.py`；窗口 `native_camera_enabled_regression.py`、`frontend_layout_regression.py`、`custom_camera_capture_window.py`、`native_camera_save_regression.py`。 |
| 相机加载与场景共享 | `test_custom_camera_retirement.py`、`test_custom_camera_preview.py` | 后台 `custom_camera_load_invalidation_regression.py` 检查真实文件加载、通知、几何指纹与适配渲染请求；`custom_camera_shared_object_regression.py` 检查跨场景 Object 拒绝及独立 Object 共享 Camera DATA。两者只接受新输出目录，不验证 GPU 像素或窗口操作。 |
| 缺陷编辑与播放 | `test_nrel_defects.py`、`test_nrel_editor_playback.py` | 后台 `nrel_editor_resource_regression.py`、`nrel_defect_editor_regression.py`；窗口 `nrel_defect_window_regression.py`、`nrel_editor_playback_regression.py`。 |
| 柔性运动与工作集 | `test_tip_deflection.py`、`test_farm_flex_workload.py`、`test_farm_telemetry_arrays.py`、`test_performance.py` | 后台 `farm_flex_regression.py`、`tip_deflection_regression.py`、`farm_flex_workload_regression.py`；另查受影响的对照、缺陷及视频消费者，构图另验窗口。 |
| 源包解析缓存与完整性 | `test_farm_flex_package_cache.py` | 宿主检查基础／双束／TLS 同源复用、只读数组、独立结果与读取器、同大小／mtime 篡改拒绝、LRU 上限及文件／扩展生命周期；不证明视口性能。 |
| 播放测量与内存口径 | `test_playback_benchmark.py`、`test_performance.py` | 按下文完整播放入口取得实际绘制证据；主动暂停与停滞分别统计，摘要入口为仓库 `scripts/blender/summarize_frontend_validation.py`。平台 RSS 换算检查不代替内存泄漏测量。 |
| Bridge 与训练 | `test_transport.py`、`test_live_runtime.py`；仓库 `tests/blender_bridge/`、`tests/test_interactive_ppo_objective.py` | 回合、动作及训练路径按[训练合同](../../../../docs/training/MAPPO_SETUP.md)选择对应测试；模拟测试不代替真实连接／训练。 |
| 连接恢复与身份 | `test_connection_recovery.py`、`test_transport.py`、`test_ui_state.py`；仓库 `tests/blender_bridge/test_disconnect_progress.py` | 后台 `connection_recovery_regression.py` 核对真实 RNA／操作器确认及 socketpair 握手，不连接 TCP 服务、不运行后端，也不验证模态窗口布局。 |
| 快照与曲线回合、单位合同 | `test_chart_snapshot_contract.py`、`test_charts.py`、`test_history_export.py`；仓库 `tests/blender_bridge/test_messages.py` | 使用实际 `SnapshotAdapter` 到 `ChartHistory` 的生产与消费合同，校验 rpm／deg／MW 且拒绝单位不符的有效或无效记录；不启动训练或验证控制效果。 |
| 启动器与 MPI | `test_launcher.py`；仓库 `tests/blender_bridge/test_isolated_trainer.py` | MPI／FAST.Farm 须目标环境验证。 |
| 雷达与便携包 | `test_dual_beam_builtin.py`；仓库 `tests/lidar/` 的 `test_replay.py`、`test_dual_beam_replay.py`、`test_dual_beam_portable.py` | 后台 `dual_beam_regression.py`、`clearance_replay_smoke.py`；窗口 `dual_beam_window.py`、`clearance_gui_acceptance.py`。 |
| 构建与安装 | `test_frontend_package_validation.py`、`test_extension_package.py` | 见下文构建入口；后者会构建 ZIP。 |

## 执行环境与来源

使用已有依赖环境；NumPy／PyTorch 等按选定测试要求准备，不为文档任务安装依赖。

持续集成的宿主依赖、触发条件与后台选择见[自动检查](../../../../docs/development/自动检查.md)。`configs/ci-native-tests.txt` 是后台 CI 的脚本选择；运行器继续核对来源、正常退出、完成标记和最终报告。README 的身份摘要用仓库 `scripts/ci/check_release_identity.py --release publication/blender-0.3.39.json` 检查；显式 `--write` 从身份记录同步摘要，不更改记录本身或验证范围。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=blender_frontend:. python -m pytest <选定测试路径> -q -p no:cacheprovider
<Blender可执行文件> --background --factory-startup --python-exit-code 1 --python <支持源码后台的脚本路径>
```

窗口脚本不加 `--background`；安装检查保留所需 profile，不照搬工厂启动。使用独立进程、配置、场景副本和新输出目录，保护用户脏场景及规范资产。

仓库 `scripts/blender/run_regressions.py --list` 查看已纳入脚本的模式、输入、输出与超时；用 `--tests` 选择脚本、`--output` 指定新目录、`--scratch-root` 指定临时目录父路径。它串行运行源码，不打包或安装。PASS 要求来源、稳定摘要、正常退出、完成标记及最终结果；定时器注册返回不算完成，未运行、跳过、超时及失败分别保留。

日常模块通常为 `bl_ext.user_default.wfrl_blender`，隔离模块为 `bl_ext.wfrl_frontend.wfrl_blender`。参数按脚本读取，设置未使用的变量不产生安装验证：

| 脚本 | 模块与来源校验 |
| --- | --- |
| 普通手动、共享结构、回放恢复、相机保存／内参／采集及缺陷资源／播放 | 通常用 `WFRL_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_ROOT`；逐份确认，安装模式不回退源码。 |
| `farm_down_mount_regression.py`、`t1_down_mount_regression.py`、`nacelle_camera_regression.py` | `WFRL_TEST_RUNTIME` + `WFRL_EXPECTED_ADDON_ROOT`。 |
| `blade_model_regression.py`、`blade_model_window.py` | `WFRL_TEST_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_DIR`。 |
| 相机启用、布局、缺陷编辑器及缺陷窗口脚本 | `WFRL_ADDON_MODULE`，无期望目录断言，另核对来源；布局的 `WFRL_ADDON_ROOT` 仅控制搜索路径。 |
| `manual_down_copy_regression.py`、三份源码视频脚本、`farm_flex_workload_regression.py`、`custom_camera_edit_binding_regression.py`、`nrel_defect_ui_state_regression.py` | 仅源码；安装模块变量不能改变验证层。 |
| `live_contracts_regression.py` | 仅源码，直接导入工作区扩展；使用独立工厂启动后台进程，安装模块变量不能改变验证层。未纳入 `run_regressions.py`，按上文 `--python` 入口直接运行。 |

特殊前置条件：

- `WFRL_TEST_OUTPUT` 的文件／目录合同逐份核对，已有目录可能覆盖报告。缺陷保存／重开复用专用目录，第二个进程设 `WFRL_NREL_DEFECT_PHASE=reopen`。手动撤销／恢复依赖工厂 `Cube`，`WFRL_MANUAL_EMPTY_ONLY=1` 会缩小隔离检查范围。
- `farm_flex_workload_regression.py` 要求源码、后台工厂启动与空白注册，仅输出 `FARM_FLEX_WORKLOAD_REGRESSION_PASS`，无需输出目录。
- `live_contracts_regression.py` 必须指定尚不存在的 `WFRL_TEST_OUTPUT` 目录，由脚本创建并写入 `result.json`；检查正常退出、`LIVE_CONTRACTS_PASS` 与报告，不复用正式场景或已保存结果目录。
- 共享结构后台检查可用 `WFRL_SPLIT_INPUT` 指定场景副本；`reconstruction_workflow_window.py` 要求干净可见进程、安装模块及精确目录。`WFRL_EXPECTED_GEOMETRY_SHA256` 从已确认资产及 inventory 取得，不能照抄失败现场摘要绕过校验。
- `scripts/blender/check_nrel_nacelle_camera.py` 支持 `WFRL_ADDON_MODULE`／`WFRL_EXPECTED_ADDON_ROOT`，参数 `-- --output <新目录>`，生成 PNG、场景及参数。双束使用匹配内置包或 `WFRL_DUAL_PACKAGE`；窗口以 `WFRL_BUILTIN_METHOD=TLS` 选候选，同时核对方法、源摘要、S1 与双束。

## 视频验证边界

源码视频脚本使用独立后台工厂启动和新目录；真实媒体需 FFmpeg／FFprobe，监管依赖见主文档。

| 入口 | 覆盖与限制 |
| --- | --- |
| 宿主视频测试 | 模拟编解码器与占位文件核对监管、重试及发布事务，不证明真实 Blender、媒体或跨文件系统通过。 |
| `manual_video_render_regression.py` | 短片逐帧姿态、Down、未选叶片／T2/T3 保持及真实编解码；`WFRL_TEST_WIDTH / WFRL_TEST_HEIGHT / WFRL_TEST_SAMPLES` 调诊断成本。 |
| `manual_video_launch_regression.py` | 操作器、独立监管／渲染、冻结配置、源场景、成片及取消；`WFRL_VIDEO_FULL_SAMPLE=1` 才生成完整样片。 |
| `manual_video_lifecycle_regression.py` | 撤销、复制隔离、保存重开、内存清空、注册恢复、取消及失败；仅同一源 Blender 进程内重开。 |
| `manual_video_installed_regression.py` | 安装 profile 下设 `WFRL_VIDEO_RECOVERY_MODE=create / recover`，两个新进程复用同一 `WFRL_TEST_OUTPUT`；指定 `WFRL_VIDEO_PYTHON`、安装模块与期望目录。非媒体子进程仅验证项目定位、恢复和取消，不渲染或编解码。 |

短片、完整样片、交付视频与源 Blender 新进程恢复分别验收；预览不覆盖导出文件选择器，姿态数量或 `--detach` 不证明成片。核对最终状态、输出及进程退出。

## 构建、安装与完整播放

```bash
python3 scripts/blender/build_extension.py --output <新目录>
python3 scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender可执行文件>
python3 scripts/blender/run_playback_benchmark.py --blender <Blender可执行文件> --output <新目录> --installed-profile <隔离验证目录>
```

- 构建默认拒绝覆盖 ZIP、校验和与 inventory，`--overwrite` 才替换；`--dual-package`／`--dual-tls-package` 分别选择匹配源的旧法／TLS 包。
- 隔离验证的 `--build-only` 仍构建 ZIP；禁止打包时排除该工具及 `test_extension_package.py`。其 `--dual-package` 仅指旧法包。窗口继承输出的 `environment.json` 及 profile；缺依赖、脚本或 profile 如实报告。
- 性能入口的 `--installed-profile` 要求有效 `environment.json` 及仍存在的 profile，清除源码覆盖且不使用工厂启动；`--path / --layout` 取值查脚本。逐路核对初始绘制、持续推进、终段、心跳、最终状态及退出，结合错误／超时判断，寻址不算完成。
- `native_camera_reopen_regression.py` 覆盖单路末帧→关闭→三路重开／重播及延迟恢复；安装模式须传模块与期望目录，不借其他路径推定通过。`performance.py` 只计 CPU 回调，嵌套耗时不相加；平均 FPS、缓存计数或短时诊断不替代完整片段验收。
- 播放报告保留 `observation_s`、`intentional_pause_s`、`running_observation_s` 和暂停区间；停滞判断扣除已确认的静止主动暂停，FPS 分母仍是完整墙钟时长。峰值 RSS 与峰值差分别报告，不把峰值差称为当前内存增长或无泄漏证明。

日常安装以[安装身份](../../../../publication/daily-installation.json)为准；相同 manifest 版本不代表与公开附件相同。保留的最终 ZIP、inventory 和 SHA-256 在 [package](../../../../publication/daily-installation-current/package/)，运行来源及配置保护见记录中的证据链接。后台身份／注册、可见窗口和完整播放结论分别读取，其他候选载荷的性能记录不得转记到当前安装。

清理后的可用产物与恢复映射见[清理记录](../../../../publication/daily-installation-cleanup.json)。原始日志和 JSON 中的临时路径保留测量时来源，不改写成当前路径；留存 `environment.json` 不表示隔离 profile 仍存在。使用安装验证或性能入口前核对 profile，缺失时须建立并验证匹配的新隔离环境，不能把历史目录当作可运行配置。

## 纯文档与 skill

只核对 frontmatter、链接／锚点、表格、模块／测试路径、参数及要求一致性；无需 Blender、渲染、求解或打包，只报告实际静态检查。

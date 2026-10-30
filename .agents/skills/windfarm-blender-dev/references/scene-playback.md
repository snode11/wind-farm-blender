# 场景、运动与回放

用于 WFRL 首页、手动控制、柔性回放、场景归属、工作集及播放性能任务。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| WFRL 主入口与状态 | `panels/home.py`、`home_state.py`；复用回放／相机／手动／导出／后端操作器，保留原页签 | [操作入口](../../../../前端readme.md#wfrl-主入口)、[验证入口](verification.md) |
| 手动场景、姿态与变桨 | `manual_scene.py`、`manual_control.py`、`manual_operators.py`、`manual_rna.py`、`manual_motion.py`、`manual_pose.py`；仓库 `scripts/blender/open_manual_control.py` | [手动控制](../../../../docs/blender/风机手动控制.md) |
| 柔性运动、外形与测量 | `farm_flex.py`、`turbine_geometry.py`、`deflection.py` | [实现说明](../../../../blender_frontend/README.md) |
| 场景、播放归属与性能 | `runtime.py`、`scene_builder.py`、`playback.py`、`playback_benchmark.py`、`performance.py` | [验证入口](verification.md) |

## 手动场景与姿态

- 手动场景默认 rootkink，可选 NREL，与 MAPPO 的对象、网格、材质和环境隔离；进入前须处于对象模式并停止、断开后端。
- 刚性运动不生成对应物理或雷达数据，界面范围不代表实体安全限值。
- 批量编辑只传播当前字段，分别变桨仅初始化模式切换的机组。
- 普通旋转按墙钟积分、变速保留方位、零 RPM 不运行；静止默认 90°，运行命令 0°，暂停／停转命令 90°，普通变桨默认 5°/s，复位立即到 90°。`PitchClock` 保存实际桨距，场景恢复不推进未显示姿态。基本风机控制面板不绘制冻结试验、Down 视频、外形或显示精度控件。
- JSON 整体校验、按编号应用，保存同刻实际姿态；载入、重开、撤销／重做后暂停。
- T1 冻结试验使用 `PitchTrial.pose_at(t)`／`TrialClock`，与普通时钟分开；目标整体确认，未选叶片与 T2/T3 保持原参数，锁定 T1 及外形，保存配置与时间。
- 复位与结束语义查手动控制专题。

## 回放、共享资源与缓存

- 回放姿态、读数、S1 与统计共用仿真时钟；寻址／重播不重复累计或暴露未来事件，插值不补测量。
- 场景共享网格时拒绝第二时钟；`playback.py` 管理进程内唯一原生 player，切场景、关窗及延迟恢复不得抢占其他播放。
- 重建拒绝占用其他场景的风场资源；重开文件保留已认领后端会话身份，等待重连同步。
- 恢复失败保持未就绪。
- 可见性优化保留隐藏 T1 测量、塔筒／共享结构、缺陷与导出消费者；同刻切视图补齐工作集，缓存区分来源、源帧与工作集，重绑／换模型失效。
- `farm_flex._load_package()` 每次加载完整校验源与结果层摘要，再按解析后的源路径及源 manifest 摘要复用解析数据；LRU 最多保留两个源，大小／mtime 不代替哈希。
- 基础、双束和 TLS 共享只读几何、运动及塔架数组，各次读取器、结果层身份与输出样本独立；`read_package()` 保留五元组接口。
- 遥测数组只在该次加载内复用，不跨加载共享；文件加载与扩展生命周期清除源缓存。

外形切换、共享重建结构或纹理消费者受影响时，同时读取[相机、资源与重建](cameras-reconstruction.md)。实时连接的场景归属见[测量与后端](measurement-backend.md#实时场景归属)。

## 可观察的验收

涉及运动或时钟的修改，按受影响行为选择起点、推进、暂停／恢复、寻址及结束等时刻，记录场景、对象、源帧／时间和实际状态；单帧正确不证明完整播放或生命周期正确。性能测量的时长、主动暂停、FPS 和 RSS 口径见[构建、安装与完整播放](verification.md#构建安装与完整播放)。

叶尖保存帧 0.2 mm、插值帧 5 mm 门槛及适用结论查[待实施要求与验收条件](../../../../前端更新计划.md)和[发布范围](../../../../docs/blender/发布状态与验证范围.md)；不放宽阈值或改正式资产制造通过。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| WFRL 主入口与状态 | `test_home_state.py` | 后台 `home_entry_smoke.py`：真实 RNA／操作器配合布局适配器，检查页面、相机草稿取消、回放推进、手动场景往返与卸载；不代替可见窗口排版或真实鼠标操作。 |
| 手动场景与姿态 | `test_manual_motion.py`、`test_manual_pose.py` | 后台 `manual_control_regression.py`、`manual_control_state_regression.py`、`manual_pose_undo_regression.py`、`manual_scene_recovery_regression.py`、`manual_scene_link_compatibility_regression.py`、`manual_rotor_conflict_regression.py`；窗口 `manual_control_window.py`。 |
| 手动装配、精度与静态复用 | `test_blade_models.py`、`test_scene_mesh_lifecycle.py` | 后台 `manual_model_regression.py`、`manual_geometry_compatibility_regression.py`；跨场景、回放接头、导出副本和环境共用 `model_environment_integration.py`。 |
| 冻结试验、普通变桨与 Down | `test_manual_motion.py`、`test_manual_down.py` | 后台 `manual_pitch_trial_regression.py`、`manual_pitch_trial_undo_regression.py`、`manual_down_copy_regression.py`、`manual_rate_pitch_regression.py`、`manual_down_regression.py`；窗口 `manual_pitch_trial_window.py`。 |
| 叶片外形 | `test_blade_models.py` | 后台 `blade_model_regression.py`；窗口 `blade_model_window.py`、`shared_context_switch_regression.py`。 |
| 环境几何与贴地 | `test_landscape_geometry.py`、`test_landscape_distribution.py` | 后台 `landscape_geometry_regression.py`；同机位输出与多窗口背景另外核对。 |
| 场景与播放归属 | `test_scene_mesh_lifecycle.py`、`test_scene_playback_lifecycle.py`、`test_native_camera_playback.py` | 后台 `scene_playback_lifecycle_regression.py`、`replay_history_regression.py`；窗口 `native_camera_reopen_regression.py`、`native_camera_playback_regression.py`。 |
| 窗口录制、视口隔离与刷新 | `test_recording_runtime.py`、`test_live_viewport_isolation.py`、`test_runtime_redraw.py` | 窗口 `frontend_sessions_window.py` 验证真实截图、场景删除、窗口关闭、播放标签和其他视口保护。 |
| 柔性运动与工作集 | `test_tip_deflection.py`、`test_farm_flex_workload.py`、`test_farm_telemetry_arrays.py`、`test_performance.py` | 后台 `farm_flex_regression.py`、`tip_deflection_regression.py`、`farm_flex_workload_regression.py`；另查受影响的对照、缺陷及视频消费者，构图另验窗口。 |
| 源包解析缓存与完整性 | `test_farm_flex_package_cache.py` | 宿主检查基础／双束／TLS 同源复用、只读数组、独立结果与读取器、同大小／mtime 篡改拒绝、LRU 上限及文件／扩展生命周期；不证明视口性能。 |
| 播放测量与内存口径 | `test_playback_benchmark.py`、`test_performance.py` | 按[完整播放入口](verification.md#构建安装与完整播放)取得实际绘制证据；主动暂停与停滞分别统计，摘要入口为仓库 `scripts/blender/summarize_frontend_validation.py`。平台 RSS 换算检查不代替内存泄漏测量。 |

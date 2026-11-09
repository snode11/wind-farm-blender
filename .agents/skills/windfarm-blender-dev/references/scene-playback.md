# 场景、运动与回放

用于 WFRL 首页、手动控制、柔性回放、场景归属、工作集及播放性能任务。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| WFRL 主入口与状态 | `panels/home.py`、`home_state.py`；复用回放／相机／手动／导出／后端操作器，保留原页签 | [操作入口](../../../../前端readme.md#wfrl-主入口)、[验证入口](verification.md) |
| 手动场景、姿态、变桨与 Down | `manual_scene.py`、`manual_scene_links.py`、`manual_control.py`、`manual_motion.py`、`manual_pose.py`、`manual_down.py`、`manual_dynamics.py`、`live_stream.py`、`live_stream_view.py`、`panels/manual_control.py`；仓库 `scripts/blender/open_manual_control.py` | [手动控制](../../../../docs/blender/风机手动控制.md) |
| 柔性运动、外形与测量 | `farm_flex.py`、`farm_flex_package.py`、`farm_flex_scene.py`、`farm_flex_geometry.py`、`farm_flex_telemetry.py`、`turbine_geometry.py`、`deflection.py` | [实现说明](../../../../blender_frontend/README.md) |
| 场景、播放归属与性能 | `runtime.py`、`scene_builder.py`、`playback.py`、`playback_benchmark.py`、`performance.py` | [验证入口](verification.md) |

## 手动场景与姿态

- 手动场景叶片外形默认 NREL，可选 rootkink，与 MAPPO 的对象、网格、材质和环境隔离；进入前须处于对象模式并停止、断开后端。
- 刚性运动不生成对应物理或雷达数据，界面范围不代表实体安全限值。
- 批量编辑只传播当前字段，分别变桨仅初始化模式切换的机组；对象缺失、装配层级损坏或跨场景共享时停止控制并提示重建。相关操作见[风机手动控制](../../../../docs/blender/风机手动控制.md)。
- 普通旋转按墙钟积分、变速保留方位、零 RPM 不运行；静止默认 90°，运行命令 0°，暂停／停转命令 90°，变桨速度 `wfrl_manual_pitch_rate_deg_s` 默认 5°/s，复位、预设与载入立即到位。每机 `pitch_actual_b{i}_deg`／`pitch_start_b{i}_deg` 保存实际桨距与本次起点，场景恢复不推进未显示姿态。
- JSON 整体校验、按编号应用，保存同刻实际姿态；载入、重开、撤销／重做后暂停。
- Down 相机俯仰按手动叶片外形取 `manual_down.MANUAL_DOWN_PITCH_DEG`（rootkink −78°，NREL −82.5°），写入相机属性 `wfrl_down_pitch_deg` 并由 `cameras.down_gimbal()` 读取；左右侧视相机为 Down 绕主轴 ±47°。
- 视口叶片摆动／塔架运动与相机抖动由 `manual_control.MOTION_HOOKS` 驱动，保存前恢复刚性模型与静止相机；Down 单路／三路导出暂停视口运动并按导出时钟逐帧生成。
- 现场视频分屏只在当前会话保留流地址，不与仿真时钟同步。

## 回放、共享资源与缓存

- 回放姿态、读数、S1 与统计共用仿真时钟；寻址／重播不重复累计或暴露未来事件，插值不补测量。
- 场景共享网格时拒绝第二时钟；`playback.py` 管理进程内唯一原生 player，切场景、关窗及延迟恢复不得抢占其他播放。
- 重建拒绝占用其他场景的风场资源；重开文件保留已认领后端会话身份，等待重连同步。
- 场景保存结果包 manifest 摘要，恢复时即使原路径仍存在也核对该身份；路径失效时仅寻找同摘要内置包，恢复失败保持未就绪。候选预检失败保留当前回放；进入替换前还原旧包施加的叶片／支撑形变，替换后的绑定失败撤回就绪状态并显示具体原因。
- 可见性优化保留隐藏 T1 测量、塔筒／共享结构、缺陷与导出消费者；同刻切视图补齐工作集，缓存区分来源、源帧与工作集，重绑／换模型失效。
- `farm_flex._load_package()` 委托 `farm_flex_package.py`，每次加载完整校验源与结果层摘要，解析使用同次已校验的字节；再按解析后的源路径及源 manifest 摘要复用解析数据。LRU 最多保留两个源，大小／mtime 不代替哈希。JSON 拒绝重复键及非有限数；文件清单拒绝绝对路径、父目录逃逸和缺失或非普通文件的目标；文件可以是指向包外的符号链接，其内容同样按清单摘要校验。双束结果层可显式引用同源包，并独立核对其完整清单与摘要。
- 基础、双束和 TLS 共享只读几何、运动及塔架数组，各次读取器、结果层身份与输出样本独立；`read_package()` 保留五元组接口。
- 预检还核对场景布局、基础三束安装元数据；若提供遥测，须核对几何／时间轴身份，包含每台机组的来源摘要，以及每个通道的非空单位、源通道和有限数值。遥测数组只在该次加载内复用，不跨加载共享；文件加载与扩展生命周期清除源缓存。
- 偏航跨越 ±180° 时，几何、结构叶尖和遥测共用最短弧插值；转子方位保留展开角度，不按偏航规则折返。

外形切换、共享重建结构或纹理消费者受影响时，同时读取[相机、资源与重建](cameras-reconstruction.md)。实时连接的场景归属见[测量与后端](measurement-backend.md#实时场景归属)。

## 叶片载荷与三叶响应

- `imbalance.py` 读取独立分析层，接受六项位移／力矩或完整九项（包含根部力）指标。`panels/farm_replay.py` 仅在包中存在时显示 Fx/Fy/Fz 全片段和最近完整转统计，缺项显示不可用；当前工作区内置资产已包含九项：叶尖xyz、根部Mx/My/Mz与Fx/Fy/Fz；其他来源和安装载荷另核对。
- 根部力／力矩标签标注随叶根和变桨运动的 r 系，RootMzr 为叶根扭矩。TipRDxr/yr/zr 是 Wiener–Milenković 旋转参数，不直接换算为扭角；1 m预弯使局部展向方向变化，现有端点通道不能唯一分离弯曲与弹性扭转；当前NNodeOuts=0，未提供数值。
- 仓库 `scripts/analysis/export_blade_response.py` 排除 INVALID 数值列并保留单位诊断；必需 BeamDyn 通道缺失、单位错误或非有限时拒绝，不回退 ElastoDyn 或填零。
- 展向内力待实现：当前对应输入 NNodeOuts=0，需独立算例配置 NNodeOuts、OutNd 和 OutList 节点通道并重新求解；显示截面不代替载荷输出。细节见[离线不平衡分析](../../../../blender_frontend/README.md#离线不平衡分析)，宿主检查入口为 `blender_frontend/tests/test_imbalance.py`（仓库路径）。
- 弹性扭转待实现：独立算例配置非零NNodeOuts、有效OutNd及节点三向位移／旋转OutList，保留根部姿态和预弯／预扭参考并重新求解，验证坐标运输与弯曲／扭转分离；仅输出节点载荷不足以得到扭角。
- 仓库 `scripts/analysis/blade_del.py` 已提供研究用根部Mx/My DEL，四点雨流、残余半循环、默认m=10、等效1 Hz且不作均值修正；回放117～177 s的4 Hz相对40 Hz差异约2.5%以内，每3 s一个点偏低约10%～34%，不可推广到全记录或认证级疲劳。方法、窗口及限制见[叶片响应与研究DEL](../../../../docs/development/叶片响应与研究DEL.md)。

## 可观察的验收

涉及运动或时钟的修改，按受影响行为选择起点、推进、暂停／恢复、寻址及结束等时刻，记录场景、对象、源帧／时间和实际状态；单帧正确不证明完整播放或生命周期正确。性能测量的时长、主动暂停、FPS 和 RSS 口径见[构建、安装与完整播放](verification.md#构建安装与完整播放)。

叶尖保存帧 0.2 mm、插值帧 5 mm 门槛及适用结论查[待实施要求与验收条件](../../../../前端更新计划.md)和[发布范围](../../../../docs/blender/发布状态与验证范围.md)；不放宽阈值或改正式资产制造通过。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| WFRL 主入口与状态 | `test_home_state.py` | 后台 `home_entry_smoke.py`：真实 RNA／操作器配合布局适配器，检查页面、相机草稿取消、回放推进、手动场景往返与卸载；不代替可见窗口排版或真实鼠标操作。 |
| 手动场景与姿态 | `test_manual_motion.py`、`test_manual_pose.py` | 后台 `manual_control_regression.py`、`manual_control_state_regression.py`、`manual_control_undo_regression.py`、`manual_pose_regression.py`、`manual_pose_undo_regression.py`、`manual_scene_recovery_regression.py`、`manual_environment_regression.py`；窗口 `manual_control_window.py`、`manual_playback_window.py`，真实鼠标 `manual_repair_mouse_window.py`（手动运行）。 |
| 变桨速度、Down、结构运动与现场视频 | `test_manual_motion.py`、`test_live_stream.py`、`test_manual_down_publish.py` | 后台 `manual_down_video_regression.py`、`manual_dynamics_regression.py`、`triple_down_rig_regression.py`、`live_stream_register.py`；跨场景模型与环境隔离 `model_environment_integration.py`；窗口 `manual_down_video_window.py`（手动运行）。 |
| 叶片外形 | `test_blade_models.py` | 后台 `blade_model_regression.py`；窗口 `blade_model_window.py`、`shared_context_switch_regression.py`。 |
| 环境几何与贴地 | `test_landscape_geometry.py`、`test_landscape_distribution.py` | 后台 `landscape_geometry_regression.py`；同机位输出与多窗口背景另外核对。 |
| 场景与播放归属 | `test_scene_mesh_lifecycle.py`、`test_scene_playback_lifecycle.py`、`test_native_camera_playback.py` | 后台 `scene_playback_lifecycle_regression.py`、`replay_history_regression.py`；窗口 `native_camera_reopen_regression.py`、`native_camera_playback_regression.py`。 |
| 窗口录制、视口隔离与刷新 | `test_recording_runtime.py`、`test_live_viewport_isolation.py`、`test_runtime_redraw.py` | 窗口 `frontend_sessions_window.py` 验证真实截图、场景删除、窗口关闭、播放标签和其他视口保护。 |
| 柔性运动与工作集 | `test_tip_deflection.py`、`test_farm_flex_workload.py`、`test_farm_telemetry_arrays.py`、`test_performance.py` | 后台 `farm_flex_regression.py`、`tip_deflection_regression.py`、`farm_flex_workload_regression.py`；另查受影响的对照、缺陷及视频消费者，构图另验窗口。 |
| 源包解析缓存与完整性 | `test_farm_flex_package_cache.py`；仓库 `tests/test_farm_flex_package.py` | 宿主检查基础 B2 原始观测与累计结果一致性、合法空观测、基础／双束／TLS 同源复用、只读数组、独立结果与读取器、同大小／mtime 篡改拒绝、LRU 上限及文件／扩展生命周期；不证明视口性能。 |
| 柔性回放保存恢复 | `test_scene_playback_lifecycle.py` | 后台 `farm_reopen_settings_regression.py` 保存重开并核对时间映射、输出／预览设置、机组和包身份；`WFRL_REOPEN_VARIANT=farm/dual/tls` 选择来源，要求新 `WFRL_TEST_OUTPUT`。 |
| 播放测量与内存口径 | `test_playback_benchmark.py`、`test_performance.py` | 按[完整播放入口](verification.md#构建安装与完整播放)取得实际绘制证据；主动暂停与停滞分别统计，摘要入口为仓库 `scripts/blender/summarize_frontend_validation.py`。平台 RSS 换算检查不代替内存泄漏测量。 |

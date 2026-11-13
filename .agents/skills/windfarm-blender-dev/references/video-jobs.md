# 视频与后台渲染

用于普通 PNG 后台动画、手动场景 Down 单路／三路视频、现场视频分屏及已有 MP4 导出／RTSP 推流。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| 手动 Down 视频 | `manual_down.py` 入口；`manual_down_jobs.py` 编排与所有权；`manual_down_motion.py` 逐帧运动；`manual_down_render.py` 编码与绘制；`manual_down_ui.py` 操作器与注册；`manual_dynamics.py` 结构运动 | [实现结构](../../../../blender_frontend/README.md#手动-down-相机与导出)、[视频操作与依赖](../../../../docs/blender/风机手动控制.md#down-相机与视频导出) |
| 现场视频分屏 | `live_stream.py`、`live_stream_view.py` | [现场视频分屏](../../../../docs/blender/风机手动控制.md#现场视频分屏) |
| 普通 PNG 后台动画 | `animation_render.py`、`animation_render_ui.py`、`animation_render_worker.py` | [实现与边界](../../../../blender_frontend/README.md#52-统一侧栏与固定时间轴)、[验证入口](verification.md) |
| 已有 MP4 导出与 RTSP | `video_output.py`、`panels/video_output.py` | [操作说明](../../../../前端readme.md#视频输出) |

## 普通 PNG 动画

- 普通 PNG 动画使用独立场景副本、独立 Blender 进程及任务配置目录，按冻结的场景相机、帧范围／步长和原画质输出到唯一目录；取消、失败保留已生成帧，正常退出和完整帧数共同确认完成。分文件多视图逐帧核对所有启用视图。
- 相机流畅预览预算不进入快照；合成器独立文件输出节点须禁用，UI 与后台递归核对。
- worker写 `RENDERED` 表示帧已写完、`FAILING` 表示失败已记录；两者均非终态。控制端等进程退出后才确认COMPLETE／FAILED／CANCELLED，失败原因不被迟到取消覆盖。
- 源场景重载、撤销／重做及卸载取消自有任务；恢复时核对保存目录的job身份、计数和状态。残留RUNNING／FAILING／RENDERED对应进程仍存活或身份未知时为RECOVERING，保留占用，只读查询、不按磁盘PID发信号；退出后缺可信终态则FAILED，不凭RENDERED补认成功，不续渲染。

## 手动 Down 视频

- `manual_down.py` 直接导出公开函数、常量、操作器与注册函数，`__init__.py` 仍从此注册，现场视频仍从此导入 `ffmpeg_executable()`。相机、标注与外形由 `manual_down_cameras.py` 管理。`_EXPORT`／`_JOBS` 只属于 `manual_down_jobs.py`，其他实现模块调用 `is_exporting()`、`_check_owner()` 和 `stop_exports()`；渲染回调与清理计时器使用该模块的函数对象，注册、重复检查和卸载使用同一身份。故障注入 patch 实际所属模块的名称，不 patch 入口重新导出的别名。
- `wfrl.manual_down_video` 与 `wfrl.manual_triple_down_video` 在手动场景中渲染。预检拒绝风机受控对象、导出相机或其父级的原生约束、动画和驱动，以及相机镜头动画。预检通过后暂停全部风机，按起始叶轮角度、RPM、起点／设定桨距与变桨速度生成逐帧计划。`apply_frame()` 直接写入转子与叶片变换，保留机组属性。
- 视口方式（`VIEWPORT_MATERIAL`／`VIEWPORT_SOLID`）需要窗口中的 3D 视图并同步阻塞。找到 FFmpeg 时离屏绘制并经管道交给 libx264，否则走 Blender 内置编码。三路导出只支持视口方式且要求 FFmpeg。单路和三路离屏绘制均在导出起始帧计算场景，风机和相机由导出时钟逐帧推进。离屏缓冲、写帧线程和编码器在同一次调用中取得和释放。编码无进展或启动失败时，结束已启动的进程，恢复场景并报告错误。`SCENE` 明确使用 EEVEE，`WORKBENCH` 使用 Workbench，窗口中可取消，后台同步渲染。
- `_configure_video()` 以完整相机画幅、单视图和步长 1 输出。导出时关闭渲染区域、裁剪、VSE 与合成器，在设置导出相机前解除时间线标记的相机绑定。`_finish()` 在成功、失败或取消时恢复渲染设置（含渲染区域、多视图和图片位深）、视图和实际桨距，清除作业占用与临时目录。`_restore()` 按标记名和帧号恢复相机绑定，帧号改变时按名称匹配。恢复帧范围、步长、帧与子帧后，再恢复场景的活动相机。
- `apply_motion()` 返回当前帧的转子、桨距、抖动和启用时的结构运动样本。离屏渲染在当前帧的所有相机画面交给编码器后追加样本，EEVEE／Workbench 以 `render_write` 确认，`render.opengl` 按下一帧到达和操作符成功返回确认。样本经 `atomic_json.write_json_array()` 按序写入作业私有文件。姿态更新、样本写入或帧序号失败，以及最终样本数不完整，均阻止发布。
- 预检确认 MP4 和逐帧 JSON 路径彼此独立，记录目标路径、目录身份以及文件状态（设备、inode、`st_mode`、大小与 mtime）或缺失状态。发布时复核预检状态，渲染和暂存期间目标出现、消失、被替换或改写会阻止提交；只改变 ctime 的元数据写入（如扩展属性、硬链接）不视为冲突。三路导出只在中心路旁写 JSON。`video_output.publish_staged()` 将全部输出暂存在目标文件系统内的作业私有目录，再逐文件提交。已有目标先移到作业私有备份并核对实际移出的条目，再排他提交。交接期间目标路径可能短暂空缺。提交冲突时保留外部写入，回滚同样使用排他恢复。外部占用或文件系统错误阻碍恢复时，错误会给出保留的备份位置。
- 单路和三路按中心与左右侧视机位计算同一个逐帧俯仰限幅，导出收尾按相机原生变换分量恢复静止姿态。`seed`（对话框“固定随机种子”）决定相机抖动与叶片／塔架运动的相位。在相同场景和渲染设置下，单路与三路中心画面逐帧一致。三路侧视相机位姿由中心位姿计算，个别轮廓边缘像素可能受 float32 舍入影响。未指定时每次随机，种子写入 JSON 的 `camera_shake.seed`／`structural_motion.seed`。
- 导出结束时 `manual_control.hold_pitch_clock()` 重置进行中变桨的计时，导出期间实际桨距保持不变，结束后不按导出耗时跳变。窗口中的交互渲染在收尾失败时，错误写入场景的 `wfrl_manual_error`，由 Down 相机面板与主页状态显示，下一次通过预检并开始导出时清除。
- 导出期间场景标记 `wfrl_manual_exporting`，`tick()` 与编号监听跳过该场景；视口结构运动与相机抖动暂停，导出按自身时钟重新生成。导出未结束时 `wfrl.manual_down_view` 不可用；期间选择的叶片外形在导出结束后由 `_apply_pending_blade_model()` 应用。缺失的叶片编号标注对象由 `update_labels()` 重建。

## 现场视频分屏

- `live_stream.py` 不导入 Blender，以 FFmpeg 子进程把流解码为固定尺寸 RGBA，只保留最新一帧，断流每 3 s 重连。`live_stream_view.py` 在修改视图前校验新地址、端口和解码尺寸，只在发起调用的 3D 视图上下文中调用 `wfrl.manual_down_view` 并分屏；缺少可用调用视图时报告错误，不选择其他视口。启动前保存该视图；Down 切换、分屏或资源启动失败时释放已取得的区域和资源，并恢复原相机、本地相机标志、透视／正交模式、视图矩阵、位置、旋转、距离、缩放偏移、裁剪、着色、叠加层和侧栏状态，其他视口保持。每个 Blender 进程一个会话；会话存在（播放中或上一路仍在停止）时 `wfrl.live_stream_start` 的 `poll` 返回 False 并给出原因，面板按钮不可用；`start()` 同样拒绝启动并保留当前会话、分屏和拉流。停止、关闭右侧区域、加载文件或停用扩展时释放纹理并请求后台收尾。后台先终止 FFmpeg，超时后强制结束，进程和管道读取线程均已退出才释放会话占用。等待期间显示停止状态与回收错误。Blender 正常退出时阻止启动新进程，并通过已持有的进程句柄终止和回收 FFmpeg。分屏把新区域放在左侧时两区域互换类型，新的 3D 视图复制分屏前的 Down 视图。流区域只在有新帧或状态变化时重绘。启动需要窗口中的 3D 视图。
- 会话绑定创建分屏的窗口、工作区、屏幕和编辑器。离开该布局时停止拉流，回到原布局时合并仍由直播占用的区域，并保留相邻 3D 视图。编辑器改作其他用途时保留用户布局。原相邻区无法安全合并时，将剩余的直播空区域恢复为 3D 视图。屏幕和插件专用占位图上的布局标记随 `.blend` 保存。重开并返回原布局时用标记和相邻区域关系核对归属。保留界面加载导致占位图被释放时，用载入前确认的屏幕、区域、编辑器和布局标记核对仍空置的直播区域。流地址和画面属于当前运行会话。

## 已有 MP4 与 RTSP

- 工作区将 MP4 复制到目标目录内私有暂存，验证源身份及字节数后排他提交。源身份比较设备、inode、大小与 mtime，POSIX 另比较 ctime，Windows 不比较 ctime。复制阶段可请求取消；等待 I/O 时保留任务占用，提交开始后等待最终结果。清理只处理仍能确认归属的暂存条目。
- MP4导出等复制线程及暂存清理线程实际退出后才公开最终状态和路径，完成标记不代替线程退出；取消、加载与卸载仍保留未退出资源的占用。
- RTSP通过FFmpeg与MediaMTX循环推送已选MP4。收尾期间为STOPPING，等待自有进程及停止线程退出才公开停止结果；未退出的进程仍保留句柄与active占用，错误状态不是清理完成证明。

四类作业的共同约束、相机抓帧FINALIZING及恢复限制见[作业终态与恢复](../../../../blender_frontend/README.md#作业终态与恢复)。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 手动 Down 视频与现场视频 | `test_manual_motion.py`、`test_manual_down_frames.py`、`test_manual_down_encoder.py`、`test_manual_down_publish.py`、`test_manual_down_lifecycle_faults.py`；`test_live_stream.py`、`test_bt_view_area_isolation.py` 检查调用视口隔离与启动失败恢复 | 后台 `manual_down_video_regression.py`、`manual_dynamics_regression.py`、`triple_down_rig_regression.py`、`live_stream_register.py`；窗口 `manual_down_video_window.py`（手动运行）。分屏画面需在交互窗口中检查。 |
| 普通后台动画渲染 | `test_animation_render.py` | 后台 `animation_render_regression.py` 验证 32 位 EXR 来源到 16 位 PNG、短动画、取消、源设置保留和 MAPPO 运动；不代替完整片段或 FPS 验收。 |
| 已有 MP4 与 RTSP | `test_video_output.py` | 安装版 `video_output_regression.py` 核对字节一致、真实 RTSP 收流与解码、停止及文件加载。需要 `WFRL_TEST_OUTPUT`、`WFRL_VIDEO_SOURCE`、`WFRL_FFMPEG`、`WFRL_MEDIAMTX`。 |

## 视频验证边界

手动 Down 视频脚本使用新的 `WFRL_TEST_OUTPUT` 目录，支持 `WFRL_ADDON_MODULE` 与 `WFRL_EXPECTED_ADDON_ROOT`。后台脚本覆盖 Workbench／后台路径与逐帧 JSON，视口离屏快速路径、三路同步写出和现场视频分屏需在可见窗口中检查；短片不替代长时导出耗时或显卡降频下的性能判定。

通用运行来源、脚本前置条件和安装模式见[验证入口](verification.md)。

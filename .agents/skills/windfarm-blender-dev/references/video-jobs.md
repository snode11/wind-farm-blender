# 视频与后台渲染

用于普通 PNG 后台动画、手动场景 Down 单路／三路视频、现场视频分屏及已有 MP4 导出／RTSP 推流。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| 手动 Down 视频 | `manual_down.py`、`manual_dynamics.py` | [视频操作与依赖](../../../../docs/blender/风机手动控制.md#down-相机与视频导出) |
| 现场视频分屏 | `live_stream.py`、`live_stream_view.py` | [现场视频分屏](../../../../docs/blender/风机手动控制.md#现场视频分屏) |
| 普通 PNG 后台动画 | `animation_render.py`、`animation_render_ui.py`、`animation_render_worker.py` | [实现与边界](../../../../blender_frontend/README.md#52-统一侧栏与固定时间轴)、[验证入口](verification.md) |
| 已有 MP4 导出与 RTSP | `video_output.py`、`panels/video_output.py` | [操作说明](../../../../前端readme.md#视频输出) |

## 普通 PNG 动画

- 普通 PNG 动画使用独立场景副本、独立 Blender 进程及任务配置目录，按冻结的场景相机、帧范围／步长和原画质输出到唯一目录；取消、失败保留已生成帧，正常退出和完整帧数共同确认完成。分文件多视图逐帧核对所有启用视图。
- 相机流畅预览预算不进入快照；合成器独立文件输出节点须禁用，UI 与后台递归核对。
- worker写 `RENDERED` 表示帧已写完、`FAILING` 表示失败已记录；两者均非终态。控制端等进程退出后才确认COMPLETE／FAILED／CANCELLED，失败原因不被迟到取消覆盖。
- 源场景重载、撤销／重做及卸载取消自有任务；恢复时核对保存目录的job身份、计数和状态。残留RUNNING／FAILING／RENDERED对应进程仍存活或身份未知时为RECOVERING，保留占用，只读查询、不按磁盘PID发信号；退出后缺可信终态则FAILED，不凭RENDERED补认成功，不续渲染。

## 手动 Down 视频

- `wfrl.manual_down_video` 与 `wfrl.manual_triple_down_video` 在手动场景本身渲染：导出前暂停全部风机，按导出开始时的叶轮角度、RPM、起点／设定桨距与变桨速度生成逐帧计划，`apply_frame()` 直接写入转子与叶片变换，不改动机组属性；完成或失败后 `_finish()` 恢复场景、视图、实际桨距与输出设置并删除临时目录。
- 视口方式（`VIEWPORT_MATERIAL`／`VIEWPORT_SOLID`）需要窗口中的 3D 视图并同步阻塞；找到 FFmpeg 时离屏绘制并经管道交给 libx264，否则走 Blender 内置编码。`SCENE`（EEVEE）与 `WORKBENCH` 使用原生渲染，窗口中可取消，后台同步渲染。三路导出只支持视口方式且要求 FFmpeg。
- MP4 先写入临时目录，再以 `shutil.move` 移到目标路径并写出同名逐帧 JSON（三路导出只在中心路旁写 JSON）；不做排他提交或成片解码校验。
- 导出期间场景标记 `wfrl_manual_exporting`，`tick()` 与编号监听跳过该场景；视口结构运动与相机抖动暂停，导出按自身时钟重新生成。

## 现场视频分屏

- `live_stream.py` 不导入 Blender，以 FFmpeg 子进程把流解码为固定尺寸 RGBA，只保留最新一帧，断流每 3 s 重连；`live_stream_view.py` 拆分当前 3D 视图并以绘制回调显示帧。每个进程一个会话，停止、关闭右侧区域、加载文件或停用扩展时结束 FFmpeg 并释放纹理。启动需要 3D 视图，后台模式下不执行分屏。

## 已有 MP4 与 RTSP

- 工作区将 MP4 复制到目标目录内私有暂存，验证源身份及字节数后排他提交。源身份比较设备、inode、大小与 mtime，POSIX 另比较 ctime，Windows 不比较 ctime。复制阶段可请求取消；等待 I/O 时保留任务占用，提交开始后等待最终结果。清理只处理仍能确认归属的暂存条目。
- MP4导出等复制线程及暂存清理线程实际退出后才公开最终状态和路径，完成标记不代替线程退出；取消、加载与卸载仍保留未退出资源的占用。
- RTSP通过FFmpeg与MediaMTX循环推送已选MP4。收尾期间为STOPPING，等待自有进程及停止线程退出才公开停止结果；未退出的进程仍保留句柄与active占用，错误状态不是清理完成证明。

四类作业的共同约束、相机抓帧FINALIZING及恢复限制见[作业终态与恢复](../../../../blender_frontend/README.md#作业终态与恢复)。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 手动 Down 视频与现场视频 | `test_manual_motion.py`、`test_live_stream.py` | 后台 `manual_down_video_regression.py`、`manual_dynamics_regression.py`、`triple_down_rig_regression.py`、`live_stream_register.py`；窗口 `manual_down_video_window.py`（手动运行）。分屏画面需在交互窗口中检查。 |
| 普通后台动画渲染 | `test_animation_render.py` | 后台 `animation_render_regression.py` 验证 32 位 EXR 来源到 16 位 PNG、短动画、取消、源设置保留和 MAPPO 运动；不代替完整片段或 FPS 验收。 |
| 已有 MP4 与 RTSP | `test_video_output.py` | 安装版 `video_output_regression.py` 核对字节一致、真实 RTSP 收流与解码、停止及文件加载。需要 `WFRL_TEST_OUTPUT`、`WFRL_VIDEO_SOURCE`、`WFRL_FFMPEG`、`WFRL_MEDIAMTX`。 |

## 视频验证边界

手动 Down 视频脚本使用新的 `WFRL_TEST_OUTPUT` 目录，支持 `WFRL_ADDON_MODULE` 与 `WFRL_EXPECTED_ADDON_ROOT`。后台脚本覆盖 Workbench／后台路径与逐帧 JSON，视口离屏快速路径、三路同步写出和现场视频分屏需在可见窗口中检查；短片不替代长时导出耗时或显卡降频下的性能判定。

通用运行来源、脚本前置条件和安装模式见[验证入口](verification.md)。

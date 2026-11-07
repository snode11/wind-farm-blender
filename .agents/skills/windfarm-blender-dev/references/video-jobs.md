# 视频与后台渲染

用于普通 PNG 后台动画、Down 便捷视频、T1 冻结试验视频及已有 MP4 导出／RTSP 推流。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| Down 视频与后台作业 | `manual_down.py`、`manual_video*.py`；仓库 `scripts/blender/manage_manual_video.py`、`wfrl/camera_video/` | [视频操作与依赖](../../../../docs/blender/手动场景研究接口.md#导出变桨视频) |
| 普通 PNG 后台动画 | `animation_render.py`、`animation_render_ui.py`、`animation_render_worker.py` | [实现与边界](../../../../blender_frontend/README.md#52-统一侧栏与固定时间轴)、[验证入口](verification.md) |
| 已有 MP4 导出与 RTSP | `video_output.py`、`panels/video_output.py` | [操作说明](../../../../前端readme.md#视频输出) |

## 普通 PNG 动画

- 普通 PNG 动画使用独立场景副本、独立 Blender 进程及任务配置目录，按冻结的场景相机、帧范围／步长和原画质输出到唯一目录；取消、失败保留已生成帧，正常退出和完整帧数共同确认完成。分文件多视图逐帧核对所有启用视图。
- 相机流畅预览预算不进入快照；合成器独立文件输出节点须禁用，UI 与后台递归核对。
- worker写 `RENDERED` 表示帧已写完、`FAILING` 表示失败已记录；两者均非终态。控制端等进程退出后才确认COMPLETE／FAILED／CANCELLED，失败原因不被迟到取消覆盖。
- 源场景重载、撤销／重做及卸载取消自有任务；恢复时核对保存目录的job身份、计数和状态。残留RUNNING／FAILING／RENDERED对应进程仍存活或身份未知时为RECOVERING，保留占用，只读查询、不按磁盘PID发信号；退出后缺可信终态则FAILED，不凭RENDERED补认成功，不续渲染。

## Down 与冻结试验视频

- Down 便捷视频只驱动所选机组，源场景暂停，独立副本逐帧渲染；冻结试验视频使用最近确认配置，按 `t=k/fps` 生成整数帧，不依赖预览。 工作区的可见窗口便捷导出在后台线程验证和提交媒体，渲染返回与 MP4／JSON 最终结果分别确认。
- 均用独立场景，其余机组保持快照，后台副本停用墙钟；严格媒体验证和 MP4／JSON 拒绝覆盖提交不可省略，短片不替代完整视频或监管验证。
- 冻结试验监管需完整工作区、FFmpeg／FFprobe 及支持 `flock / waitid(WNOWAIT)` 的 macOS／Linux Python 3.11+（`WFRL_VIDEO_PYTHON`）。
- 扩展 ZIP 不含配套脚本及 `wfrl.camera_video`，安装入口通过偏好 `project_dir` 选工作区。
- 注册容忍受限数据访问，场景可用后恢复；按身份、凭据及锁恢复，复制场景不认领原作业，取消不信任磁盘 PID。
- `FINALIZING` 是进程结束后的收尾／交付确认，取消为CANCELLING。正常监管路径先收尾进程组、控制端点和服务线程，释放监管／输出租约，再写最终状态；status.lock串行化下一attempt与终态发布，查询等待交接。`start()` 启动监管子进程失败仍先写final失败再释放租约，顺序修正待实现，不能概括为所有分支均满足要求。
- 仅 `final=true` 的 `COMPLETE / FAILED / CANCELLED` 是最终结果；`LOST` 不等于失败或取消，`--detach` 只确认启动。非 POSIX 平台查询时不检查本地租约，不可达且无匹配终态的作业报告 `LOST`、`active=False`。
- `retry` 只从匹配完整母版重做编码、验证与发布，不恢复未渲染帧；保留目标预检及失败母版，只清理自有暂存，不改写成功状态。

## 已有 MP4 与 RTSP

- 工作区将 MP4 复制到目标目录内私有暂存，验证源身份及字节数后排他提交。源身份比较设备、inode、大小与 mtime，POSIX 另比较 ctime，Windows 不比较 ctime。复制阶段可请求取消；等待 I/O 时保留任务占用，提交开始后等待最终结果。清理只处理仍能确认归属的暂存条目。
- MP4导出等复制线程及暂存清理线程实际退出后才公开最终状态和路径，完成标记不代替线程退出；取消、加载与卸载仍保留未退出资源的占用。
- RTSP通过FFmpeg与MediaMTX循环推送已选MP4。收尾期间为STOPPING，等待自有进程及停止线程退出才公开停止结果；未退出的进程仍保留句柄与active占用，错误状态不是清理完成证明。

四类作业的共同约束、相机抓帧FINALIZING及恢复限制见[作业终态与恢复](../../../../blender_frontend/README.md#作业终态与恢复)。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 视频作业与媒体 | `test_manual_video_render.py`、`test_manual_video_job.py`；仓库 `tests/test_camera_video_media_recovery.py`、`tests/test_camera_video_publication.py` | 见下文视频验证边界；便捷视频取消另查可见渲染。 |
| 普通后台动画渲染 | `test_animation_render.py` | 后台 `animation_render_regression.py` 验证 32 位 EXR 来源到 16 位 PNG、短动画、取消、源设置保留和 MAPPO 运动；不代替完整片段或 FPS 验收。 |
| 已有 MP4 与 RTSP | `test_video_output.py` | 安装版 `video_output_regression.py` 核对字节一致、真实 RTSP 收流与解码、停止及文件加载。需要 `WFRL_TEST_OUTPUT`、`WFRL_VIDEO_SOURCE`、`WFRL_FFMPEG`、`WFRL_MEDIAMTX`。 |

## 视频验证边界

`test_manual_video_job.py` 的本地运行解释器要求Python≥3.13并安装psutil和pytest，另须具备POSIX监管接口；具体命令见[手动视频监管测试](../../../../docs/development/自动检查.md#手动视频监管测试)。这与视频工具本身的Python最低版本分开。

源码视频脚本使用独立后台工厂启动和新目录；真实媒体需 FFmpeg／FFprobe，监管依赖见上文“Down 与冻结试验视频”。

| 入口 | 覆盖与限制 |
| --- | --- |
| 宿主视频测试 | 模拟编解码器与占位文件核对监管、重试及发布事务，不证明真实 Blender、媒体或跨文件系统通过。 |
| `manual_video_render_regression.py` | 短片逐帧姿态、Down、未选叶片／T2/T3 保持及真实编解码；`WFRL_TEST_WIDTH / WFRL_TEST_HEIGHT / WFRL_TEST_SAMPLES` 调诊断成本。 |
| `manual_video_launch_regression.py` | 操作器、独立监管／渲染、冻结配置、源场景、成片及取消；`WFRL_VIDEO_FULL_SAMPLE=1` 才生成完整样片。 |
| `manual_video_lifecycle_regression.py` | 撤销、复制隔离、保存重开、内存清空、注册恢复、取消及失败；仅同一源 Blender 进程内重开。 |
| `manual_video_installed_regression.py` | 安装 profile 下设 `WFRL_VIDEO_RECOVERY_MODE=create / recover`，两个新进程复用同一 `WFRL_TEST_OUTPUT`；指定 `WFRL_VIDEO_PYTHON`、安装模块与期望目录。非媒体子进程仅验证项目定位、恢复和取消，不渲染或编解码。 |

短片、完整样片、交付视频与源 Blender 新进程恢复分别验收；预览不覆盖导出文件选择器，姿态数量或 `--detach` 不证明成片。核对最终状态、输出及进程退出。

通用运行来源、脚本前置条件和安装模式见[验证入口](verification.md)。

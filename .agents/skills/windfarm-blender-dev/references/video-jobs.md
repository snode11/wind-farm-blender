# 视频与后台渲染

用于普通 PNG 后台动画、Down 便捷视频和 T1 冻结试验视频。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| Down 视频与后台作业 | `manual_down.py`、`manual_video*.py`；仓库 `scripts/blender/manage_manual_video.py`、`wfrl/camera_video/` | [视频操作与依赖](../../../../docs/blender/风机手动控制.md#导出变桨视频) |
| 普通 PNG 后台动画 | `animation_render.py`、`animation_render_ui.py`、`animation_render_worker.py` | [实现与边界](../../../../blender_frontend/README.md#52-统一侧栏与固定时间轴)、[验证入口](verification.md) |

## 普通 PNG 动画

- 普通 PNG 动画使用独立场景副本、独立 Blender 进程及任务配置目录，按冻结的场景相机、帧范围／步长和原画质输出到唯一目录；取消、失败保留已生成帧，正常退出和完整帧数共同确认完成。
- 相机流畅预览预算不进入快照；合成器独立文件输出节点须禁用，UI 与后台递归核对。
- 源场景重载、撤销／重做及扩展卸载取消任务；此入口不承诺跨进程恢复，也不代替冻结试验 MP4 监管。

## Down 与冻结试验视频

- Down 便捷视频只驱动所选机组，源场景暂停；冻结试验视频使用最近确认配置，按 `t=k/fps` 生成整数帧，不依赖预览。
- 均用独立场景，其余机组保持快照，后台副本停用墙钟；严格媒体验证和 MP4／JSON 拒绝覆盖提交不可省略，短片不替代完整视频或监管验证。
- 冻结试验监管需完整工作区、FFmpeg／FFprobe 及支持 `flock / waitid(WNOWAIT)` 的 macOS／Linux Python 3.11+（`WFRL_VIDEO_PYTHON`）。
- 扩展 ZIP 不含配套脚本及 `wfrl.camera_video`，安装入口通过偏好 `project_dir` 选工作区。
- 注册容忍受限数据访问，场景可用后恢复；按身份、凭据及锁恢复，复制场景不认领原作业，取消不信任磁盘 PID。
- 仅 `final=true` 的 `COMPLETE / FAILED / CANCELLED` 是最终结果；`LOST` 不等于失败或取消，`--detach` 只确认启动。
- `retry` 只从匹配完整母版重做编码、验证与发布，不恢复未渲染帧；保留目标预检及失败母版，只清理自有暂存，不改写成功状态。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 视频作业与媒体 | `test_manual_video_render.py`、`test_manual_video_job.py`；仓库 `tests/test_camera_video_media_recovery.py`、`tests/test_camera_video_publication.py` | 见下文视频验证边界；便捷视频取消另查可见渲染。 |
| 普通后台动画渲染 | `test_animation_render.py` | 后台 `animation_render_regression.py` 验证 32 位 EXR 来源到 16 位 PNG、短动画、取消、源设置保留和 MAPPO 运动；不代替完整片段或 FPS 验收。 |

## 视频验证边界

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

# NREL 5MW / WFRL Blender 前端实现与维护

用户操作见[前端说明](../前端readme.md)，当前交付和验证见[发布状态](../docs/blender/发布状态与验证范围.md)，待实施要求见[前端更新计划](../前端更新计划.md)。要求 Blender **5.2+**。

## 运行来源与能力范围

| 对象 | 身份与范围 |
| --- | --- |
| 公开安装包 | [NREL 0.3.36](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.36)；188 文件、99,126,084 字节；叶片外形选择、T1/T2/T3 Down 及独立风机手动控制、姿态预设、相对微调、三机概览、姿态 JSON、完整风机几何／同源纹理切换、三机柔性回放、默认三相机复位、六类合成缺陷，以及相机清理、稳定刷新、采集与播放保护。 |
| 本地工作区 | **0.3.36**；配套叶根展示装配、独立手动材质、固定显示精度、静态零件复用和环境几何预算；保留普通变桨、Down／视频、冻结试验、后台监管与播放保护。公开附件与日常安装分别核对。 |
| 项目源码 | [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work)；包含当前实现、测试与研究工具。发布源码使用 `v0.3.36` 标签；当前分支与公开 ZIP、日常安装载荷分别识别。 |
| 本地日常安装 | `bl_ext.user_default.wfrl_blender`，本地 0.3.36 安装包、188 文件，与本地 ZIP 一致；[安装核对](../publication/daily-installation.json)记录重启后来源、偏好／场景保护、手动控制和相机保存恢复。 |
| GW184 | [0.4.0 独立运行包](../docs/blender/GW184三相机与缺陷编辑器.md)；184 m 尺寸合成刚性模型、固定三相机和六类缺陷。模型与配置不能直接替代 NREL。 |

日常安装使用[本地 0.3.36 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.36/wfrl_blender-0.3.36.zip)，SHA-256 为 `c7e7360b06a340b7c6867f50f8e30ed4e4b0114ad721d2eeaa762d1a3021da20`。[安装身份](../publication/daily-installation.json)核对 188 个文件、87 个新窗口已加载模块及持久启用；保护记录核对 6 个配置和 59 个已有场景。隔离安装检查三相机默认配置和投影；日常安装版后台检查手动控制、变桨试验、两种外形、三档显示精度、Down、场景保存恢复及 45 帧叶尖显示抽样。可见窗口验证新进程加载与注册。源码的完整片段、20 轮相机开关及 GPU 采集证据见[功能检查](../outputs/manual-camera-checks-dc5mz864/summary.json)，各项来源与范围独立，不代表当前安装包的完整视频、稳定 FPS 或科学精度验收。

公开包 SHA-256 和匹配验证见[发布状态](../docs/blender/发布状态与验证范围.md)。工作区源码、构建 ZIP、已安装扩展与公开附件分别核对，不能依据版本号推定文件相同。日常启动器加载安装版；发布不会自动升级日常安装，更新安装后需要完全重启 Blender。

默认 MAPPO 为保存的预弯 v3 三机 60 秒结果，视口绘制不启动 FAST.Farm。几何／纹理 `NOT_ACCEPTED`、雷达 `REVIEW_ONLY / PENDING_ACCEPTANCE` 保持；叶尖显示门槛的源码与安装结论见[验证范围](../docs/blender/发布状态与验证范围.md#研究与物理边界)。源采样频率、显示时间轴和实际绘制帧率分别报告。

0.3.36 的 MAPPO 侧栏提供 **叶片外形**，默认 `nrel5mw`，可选择合成商用形制 `rootkink`。公开 ZIP 包含该外形选择；运行来源按实际加载模块和[安装身份](../publication/daily-installation.json)识别。

## 合成缺陷与默认补漆

`repair_marks.py` 管理 T1/B1 距叶根约 34 m 的 **1.4 × 0.7 m** 浅灰补漆。三角面索引和重心权重通过 Geometry Nodes 跟随旋转、变桨与柔性运动；仅原始 T1/B1 带此默认标记，重建叶片和 GW184 独立模型不受影响。

`nrel_defects/` 管理九片叶片的六类缺陷、预览确认／取消、撤销重做、健康对照和参考身份绑定的 JSON。`farm_flex.rebind_blade()` 与 `deform_points()` 使修改拓扑、支持区域和轮廓沿同一保存运动更新。保存只记录已提交配置。合成外观不改变气动、刚度、载荷或雷达源；G 的几何可见性不等于图片可辨识。操作和约束见[NREL 缺陷编辑器](../docs/blender/NREL缺陷编辑器.md)。

## 当前主要结构

- `manual_scene.py`、`manual_control.py`、`manual_motion.py`、`manual_pose.py` 与 `panels/manual_control.py`：独立刚性场景、墙钟积分、姿态预设、按机组当前值微调、三机概览及严格姿态 JSON。`manual_pose.py` 只使用标准库，定义 `wfrl.manual-pose.v1` 和角度边界；Blender 操作器先校验所选机组再写入参数、对象与时钟，导入后暂停。文件保存不暂停运行，运动相位以同一墙钟时刻采样；[操作说明](../docs/blender/风机手动控制.md)。
- `manual_down.py`：三机独立 Down、按稳定归属复用的叶片编号和便捷 MP4／JSON 导出。独立场景副本只驱动所选机组；真实帧数、尺寸、帧率、时间戳和完整解码通过后拒绝覆盖地提交。视口渲染依赖可见窗口，最终渲染使用原生渲染作业；此入口不接管冻结试验的后台监管和母版重试。
- `manual_geometry.py`：刚性叶片固定总览／标准／近景采样、配套叶根收口和同场景静态网格复用；构造归属、几何指纹、材质槽和消费者共同限制复用，绑定失败整体回滚。塔筒及柔性叶片不参与静态去重；用户编辑、链接、fake-user、shape-key 和跨场景资源受保护。Down 入口和视频副本使用近景，普通显示档位不影响回放与测量。
- `blade_root_display.py`：回放外形对应的叶根展示附件事务。叶片切换失败时恢复接头，保留原材质及用户数据；不改写运动、载荷或测量资源。
- `manual_scene_links.py`：原场景以稳定的手动归属标识查找其独立场景，手动场景以 Scene 指针保存返回目标，避免双向 Scene ID 引用形成依赖环。名称改变不影响查找；复制的原场景不能认领另一原场景的手动模型，归属不明确时不返回目标。加载和撤销恢复时规范存储关联；缺少标识的保存文件仅通过唯一返回关系恢复关联，显式解除的关联保持断开。
- `manual_video.py`、`manual_video_job.py`、`manual_video_render.py` 与仓库 `scripts/blender/render_manual_video.py`：保存已确认试验的场景副本，独立监管后台渲染 T1 Down，复用 `wfrl.camera_video.media` 编码与检查。`scripts/blender/manage_manual_video.py` 提供持久化作业的查询、等待、取消和完整母版重试。面板提供输出路径、保持时长、进度、取消和打开视频；安装入口通过偏好的 `project_dir` 定位完整工作区；注册阶段延迟到可访问场景后恢复作业。渲染后台使用该工作区源码，监管进程显式继承该项目的模块搜索目录。依赖独立 Python 3.11+、FFmpeg／FFprobe，以及 macOS／Linux 的 `flock` 和 `waitid(WNOWAIT)`。
- `panels/farm_replay.py`：共享播放、单步、复位及功能页；普通三机回放提供进度定位，统一重建对照隐藏进度滑块。View 页组织观察相机，切视角保留回放时刻。
- `split_reconstruction.py`、`split_reconstruction_timing.py`、`split_reconstruction_ui.py`：保存分屏生命周期、完整风机显示、独立视口、固定采样映射、统一入口、模式切换及左下角状态说明；底层样本步进工具保留，统一侧栏使用单帧步进。规范资源为 `assets/examples/mappo_reconstruction_split.blend`。
- `split_mappo_texture.py` 与 `assets/blade_recon_mappo_tex/`：同源纹理同步回放，使用原两相机实验的 601 个保存状态与动态标定生成固定 RGBA 图集，共用 MAPPO 的 60 Hz 时间轴；`split_surface_texture.py` 的 120 样本合成实验继续使用独立游标。`split_texture_settings.py` 保存各来源的显示选择并在失败切换时回滚状态。来源、模型等价和安装边界见[发布状态与验证范围](../docs/blender/发布状态与验证范围.md)。
- `deflection.py`：T1 三叶片独立未受载参考、实际结构叶尖及根系 xyz 分量。参考随同刻塔顶与转子运动；源通道对照按实际可用性显示。
- `tower_motion.py`：读取并校验 v2 塔架截面与机舱变换，插值运动；`farm_flex.py` 同步塔筒网格、叶片、机舱及雷达挂载。
- `assets/mappo/`：包含默认预弯 v3 离线记录，无需求解器、Bridge 或项目路径；数据格式代次与扩展版本号分开管理。
- `assets/dual_beam/` 与 `assets/dual_beam_tls/`：两个结果层共享 `assets/mappo/` 的 40 Hz 源数据；默认 `hub-axis.v1`，显式选择 `hub-tls.v1`。
- `stacked_camera_rig.py` 与 `panels/stacked_camera_rig.py`：共盒三相机、支架安装端贴合、盒体末端转向、确认／取消／撤销及默认布局复位。
- `custom_cameras.py`：槽位、相机参数、布局及兼容处理；`native_camera_views.py`：独立原生单路／三路观察窗口。
- `custom_camera_capture.py`：同刻或时间序列原图采集；`custom_camera_preview.py`：保留预览计算与质量恢复支持，普通原生观察不依赖逐帧 PNG 采集。
- `assets/cameras/t1-three-camera-default.json`：默认三相机取景配置，加载 MAPPO 时应用；显式重载与普通播放分开处理。
- `cameras.py`：观察相机与云台预设；Down 在各机舱底壳偏后缘侧求交定位，光心沿局部 −Z 外伸 0.2151 m，朝叶轮侧偏离正下 7.5°；采用独立 8 mm、16:9 垂直 60°光学预设，保留归属校验、失败清理与完整画框。[Down 参数与边界](../docs/blender/T1Down机舱下视.md)。
- `nacelle_camera.py`：单路机舱相机的机械转轴安装基准、8 mm 镜头及独立 59°/40°视场；`nacelle_visibility.py`：三角面与视锥裁剪、展向覆盖及有限首命中采样。[参数与当前模型限制](../docs/blender/NREL机舱相机.md)。

手动场景以 `wfrl_scene_kind=manual` 标识，使用独立命名空间的对象、网格、材质、World 与图片，复用 NREL 参考几何构造，不加载 demo 的柔性结果驱动。每台风机通过场景内对象指针绑定偏航、转子及叶片；控制前检查归属、装配层级和跨场景共享。RPM 按单调墙钟积分，改变速度保留相位；“全部”只传播正在编辑的字段，参数差异以“多值”提示，集体桨距统一覆盖三片叶片。批量设置分别变桨模式时，`_batch_update` 跳过已处于目标模式的机组；只在从关闭切换为开启时，以该机当前集体桨距初始化三片叶片。暂停时可设置叶轮角度；全部零 RPM 时禁用运行。参数和姿态随 `.blend` 保存，加载、撤销、重做、退出及失效绑定均暂停运动。首次创建与损坏重建分别形成撤销记录，重建保留返回视图。材质预览使用独立天空和轻量地形，退出恢复源场景预览设置。面板进入／返回保留源场景及播放位置，活动后端连接阻止进入；手动运动不写入后端或回放时钟，也不产生物理测量值。手动场景与三相机、缺陷编辑器的串联暂缓，不属于当前交付范围。

工作区源码的手动场景默认使用 `rootkink` 叶根拐点改型，可选 NREL；`manual_scene._ensure_blade_model()` 以独立网格保留材质、UV、装配与参数，不改 demo 网格。普通手动变桨使用独立 `PitchClock`，目标、实际桨距与暂停状态分开；速度 0 立即到位，原生冲突和失效绑定停止在最近已显示的姿态。`manual_motion.PitchTrial.pose_at(t)` 从冻结起点计算累计方位及三片桨距；`interpolation` 为 `SMOOTH` 时使用起止速度为零的三次平滑插值，为 `LINEAR` 时按各片桨距差除以共同变桨时长匀速过渡，到达目标后保持。未指定该字段的配置按 `SMOOTH` 解析。`TrialClock` 是 T1 试验的唯一时钟，普通 `_CLOCKS` 不再写入该 T1。`wfrl_manual_trial` 分开保存待确认输入、JSON 冻结配置、控制启用状态与已过时间；结束试验保留最近确认的冻结配置，下一次确认才替换。目标填写不应用，整体校验后按当前实际姿态确认。暂停／继续作用于旋转和变桨，复位恢复完整起点，结束试验后才能设置另一组或更改 T1 姿态与 RPM。模型身份、起点、目标、时长、变桨方式和试验时间随 `.blend` 保存，重开和撤销重做后暂停；姿态 JSON 仍只保存同刻姿态。

手动 **T1 Down** 向 `cameras.ensure_gimbal()` 显式提供 T1 条目的 `yaw_object`、独立相机名与资源归属，复用 `_down_mount()`、`down_gimbal()` 和完整画框适配。相机继承机舱，不随叶轮旋转或追踪叶片；切换 Down／整机保留试验时钟及状态，真实结构和自遮挡保留。这些手动试验能力包含于公开 0.3.36；视频后台仍需配套工作区源码，日常扩展为本地 0.3.36；手动工况的 RTSP 接入及识别算法不在实现范围。

手动面板的视频规格为 1920×1080、25 fps、H.264 MP4；变桨时长默认 8 s，目标保持默认 7 s，共 15 s、375 帧。`manual_video_render.frame_count()` 要求总时长对应整数帧。每帧以 `PitchTrial.pose_at(k/fps)` 从冻结起点确定累计叶轮角和三片桨距，导出不依赖预览已过时间；后台停用墙钟，只驱动 T1，T2/T3 保持快照姿态。EEVEE 默认 16 个采样，关闭运动模糊，固定完整画幅和色彩设置，不模拟实体曝光。

`wfrl.manual-video-job.v2` 记录随机作业身份和监管凭据、场景副本、场景名、冻结试验、输出路径、画幅、帧率、保持时长及编码器路径。`<文件名>_data_<标识>/` 保留 `master_frames/`、`frames.csv`、`camera.json`、`experiment.json`、`scene.blend`、作业／状态／监管端点 JSON、运行日志和检查结果。`frames.csv` 使用连续 `frame_id`／`video_pts` 和 `time_s=k/fps`，另存同刻 RPM、累计方位、三片桨距及变桨／保持标记。固定的单叶片 20° 工况保持 rootkink、T1 Down、9 RPM、4 s 匀速变桨和 176 s 保持，共 4500 帧；B2/B3 全程 0°。

`manual_video_job` 的独立监管进程继承输出与作业锁，持有原始 `Popen` 身份；在停止进程组前用 `waitid(WNOWAIT)` 保留主进程身份，避免 PID／进程组编号复用。取消覆盖同组子进程并处理主进程先退出的情况；写入最终状态前完成进程收尾。查询和取消通过绑定作业、运行实例和随机请求的 HMAC 本机端点完成，磁盘 PID 仅用于诊断。`PREPARING`、`RENDERING`、`ENCODING`、`VERIFYING`、`PUBLISHING`、`FINALIZING`、`CANCELLING` 属于运行状态；仅带 `final=true` 的 `COMPLETE`／`FAILED`／`CANCELLED` 是最终结果。`LOST` 表示无法验证监管联系，不冒充退出或取消；输出锁仍被持有时继续拒绝重复启动。源 `.blend` 保存 `wfrl_manual_video_job` 后可在重开时恢复监管，作业路径也可独立查询。场景副本不认领源场景作业。

`master.json` 只在全帧完成后封存，绑定 `job.json`、快照、相机、实验来源、姿态 CSV、逐帧 SHA-256 和编码配置。`retry --job <job.json>` 在同一作业和输出锁下核对完整母版，调用 `encode_video(..., resume=True)` 恢复受输入哈希和所有权限制的编码事务，再执行严格解码、尺寸、帧数、时间戳与时长验证。已完成编码通过其保存的 `encoding_intent` 和成片哈希复用；未知成片、符号链接、缺帧、内容或来源变化均拒绝，已成功作业不改写状态。此入口不启动 Blender，不实现逐帧渲染续传。

发布由 `wfrl.camera_video.publication` 在目标文件系统复制、刷新并提交完整文件。macOS `renamex_np(RENAME_EXCL)`、Linux `renameat2(RENAME_NOREPLACE)` 或可用的硬链接提交均拒绝替换已有目录项；原生接口不可用且目标不支持硬链接时，渲染前预检拒绝。跨文件系统复制使用目标侧暂存，发布凭据绑定暂存文件身份和成片哈希，支持提交完成但状态写入中断后的识别。普通失败及监管取消清理已记录的自有暂存，未知文件保留。输出侧锁名持久保留，只释放锁；按目标父目录身份和规范化文件名串行化同机作业。生成脚本默认等待最终结果，脱离运行须显式 `--detach`；命令和退出码见[手动视频操作说明](../docs/blender/风机手动控制.md#导出变桨视频)。

监管、恢复与发布有纯 Python 子进程及非媒体占位文件回归；真实 Blender 场景重开、编码、完整视频、Linux 及实际跨文件系统集成仍需单独验证。源码修改不表示安装版或公开附件已更新。

## 当前三相机维护要点

三台相机共同安装在 T1 机舱外伸支架末端，光心随整个盒体运动，各路保留独立方向与视场。贴合约束作用于支架安装端，不能把盒体背面贴住机舱当作默认安装；自由 XYZ 操作会解除贴合约束。

**恢复默认三相机** 复用已保存的默认 JSON，不改变回放帧、播放状态或用户自由视图。布局和原生状态都已默认时保持对象身份与历史；原生对象状态损坏时显式复位可修复。当前源码通过 `custom_cameras._publish()` 在同一事务中重绑定场景、局部视口及同场景未关闭控制器的 `snapshot`、`external_snapshot`、`session.snapshot` 相机引用，覆盖编辑确认、布局替换与默认复位；引用随相机对象一起提交或回滚。`_refresh_references()` 仅在 WATCH / LAYOUT 刷新当前观察相机。

当前目标是同一叶片的连续视场与相邻重叠，不要求三等分，不按目标叶长范围裁切图像。C2 可能因透视关系看到较远叶尖；不要求只有 C3 可见叶尖。默认参考时刻为 117 s，固定相机在旋转中允许叶片进出画面，不自动追踪叶片。

原生窗口用临时观察相机表达各路视场，共用场景和时钟。默认 GRID 为 2×2，第四格是 `installation_schematic.py` 生成的静态安装示意；STRIP 为三列对照。两种布局均有播放／暂停、单步及单路放大；镜头切换和预览画质操作只接受观察会话所属场景的上下文。独立 WATCH 和放大固定使用 SOLID 材质颜色显示，无单路画质切换；返回三路恢复其原画质。PNG 采集使用独立原始质量设置；完成、取消或异常退出时恢复仍由采集持有的回放状态。外部寻址、播放或暂停使采集失效，并保留用户当前时刻及播放状态，不用采集前的快照覆盖。

工作区的 PNG 采集调用 `render_group(..., create_texture=False)`，只回读 RGBA 像素，不建立用于显示的 GPU 纹理；预览默认创建显示纹理。两条路径共用颜色管理、相机参数、场景状态校验和资源清理。

当前源码中，观察会话分别保留代理对象与镜头数据引用，分步释放绘制回调、代理和窗口，并独立恢复预览画质及主视口。已经删除的 RNA 不阻止其余清理；未释放的资源保留供再次关闭重试。布局事务提交后，释放失败的旧相机对象与镜头数据也分别进入重试队列；对象已删除仍可回收其镜头数据，数据仍有用户时等待后续清理。正常释放时仍被其他对象共享的数据不纳入失败重试队列。准备／观察失败保存在场景的 `wfrl_native_camera_error` 并显示于三相机主面板，成功完成重开后清除。

相机窗口的播放按钮和时间提示按实际场景播放归属显示。另一场景占用播放器或活跃播放器归属不明时，播放与单步按钮禁用；空格键请求和直接调用同样拒绝暂停或抢占该播放器。关闭及延迟恢复也保留另一场景的播放。源码同步不改变既有公开 ZIP 或日常安装的身份。

工作区的单路与三路播放按钮复用回放重播入口：位于终帧时从起点播放，播放期间采用 Blender 的 `STOP_END_FRAME` 模式停在片尾，丢帧不使片段意外循环。片尾停止请求绑定场景、读取器与播放代次；关闭后的延迟续播也校验播放代次，期间发生新的播放、暂停或加载就使旧请求失效。中途关闭保留当前时刻和同场景播放，不抢占其他场景。

[源码窗口验证](../outputs/camera-reopen-verification/final-native/result.json)记录 macOS Blender 5.2.1 独立窗口中的单路完整片段、终帧关闭重开三路、三路完整片段、重复切换、暂停、关闭续播及旧延迟恢复失效。三路均有推进与终帧绘制记录；脚本调用原生操作器，不等同于物理鼠标操作、稳定 FPS、安装载荷或跨平台验收。记录中的源码摘要界定证据范围。

相机视锥并集、射线可见性与拼接成功是不同检查。参考姿态的连续投影和重叠已有证据；根部连接件、机舱和叶片自身仍会遮挡局部表面，完整无遮挡尚未通过。不能隐藏真实结构制造几何通过结果，也不能将此仿真安装作为实体标定。

`playback_benchmark.py` 区分完成、超时、中止、错误与缺绘制。场景须从声明起点推进至终段；循环只接受末端到起点边界的已验证原生播放转换，寻址不能作为完成证据。每路须有声明激活时刻对应的起始绘制、持续推进、终段、有效最终状态和新鲜心跳；晚启用从实际激活时刻检查，只有场景静止的显式暂停区间可从推进间隔中扣除。FPS 或单个 PASS 字段不能替代这些证据。旧停止异常根因仍未定位，当前完整片段性能与跨平台稳定性继续单独验证。

## 启动与模式切换

在已配置完整后端环境的 macOS 上使用以下入口，先把示例路径替换为自己的路径：

```bash
cd "/path/to/wind farm RL"
scripts/blender/run_wfrl_macos.sh \
  --blender "/Applications/Blender.app/Contents/MacOS/Blender" \
  --python /path/to/wfrl-env/bin/python \
  --scene scenes/turb3_row.yaml \
  --mpi /path/to/mpiexec \
  --fastfarm /path/to/FAST.Farm
```

启动器预检环境并启动自己拥有的 Bridge；Blender 加载**已安装扩展**，停在 MAPPO 的 T1 侧前方首帧。默认状态为 **OFFLINE RESULTS**，Bridge 在 `127.0.0.1:8765` 待用，不自动连接或开始运行。这条入口需要完整后端环境；只看离线演示时直接打开扩展并点击“加载 MAPPO · 60 秒”，或用便携入口，无需 Python 后端、MPI、FAST.Farm 和网络。

0.3.36 安装版会同时建立推荐三相机配置，默认关闭叶尖轨迹和挠度辅助物。主视图仍先显示 T1 总览，进入 **View → 三相机 → 三路对照** 查看。仅更新仓库 README 不会改变已运行的 Blender 扩展。

需要后端时，进入 **Item → WFRL / CONNECTION → 后端连接（高级）**，选择 Replay 或 Interactive，再点 Connect / Reconnect。Item 不可见时先选中一片叶片。启动脚本在握手确认后发送 `scene.load`，自动加载命令指定的 YAML；确认 **CONNECTED / READY** 后，到 **WFRL / 后端运行** 设置参数并点击 Start Replay / Start Training。Replay 需兼容 checkpoint，例如项目中的 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt`。

切换模式会断开前端，需要重新连接。运行结束先 Stop 并等待 STOPPED，再选择 MAPPO · 60 秒返回离线回放。关闭该命令启动的 Blender 后，有活动扩展会话时，启动器恢复会话并请求 `run.stop`；只有明确收到 READY、STOPPED 或 FAILED 的非运行状态才清理自己拥有的 Bridge。EOF、超时、致命错误或无效响应均视为停止未确认，保留 Bridge 并返回错误；没有活动会话时按自身进程清理流程处理。普通 Blender 窗口不运行此 bootstrap，连接已有 Bridge 后需手动 Load & Validate Scene。

| 路径 | 前端显示与数据 | 是否启动求解／训练 |
| --- | --- | --- |
| MAPPO · 60 秒 | 随包随机阵风记录，九片独立形变、遥测与 B2 卡片 | 否 |
| 双束净空 / S1、TLS 候选 | 随包旧法或 TLS 结果层、外部可携带包，S2/S3 配对和独立 S1 状态 | 否 |
| 后端 Replay | 兼容权重推理与实时遥测；本机 YAML 为 8 m/s | FAST.Farm 推理，不做 PPO 更新 |
| Interactive | 实时训练状态与遥测 | 是，包含训练更新 |
| Formal Training | 正式训练流程、进度与指标 | 是，不提供逐步实时三维遥测 |
| 独立 normal／close 雷达回放 | 外部 18 秒结果包；前端刚性姿态示意 | 否 |

当前 Demo 使用保存的 MAPPO 数据。Bridge 中仍保留旧 Backend Demo 逻辑，不能将其合成运动当成真实 FAST.Farm 验收；FLORIS 也仅支持该旧后端模式。

## 1. 场景如何搭建

前端采用 Python 程序化建模。默认几何输入来自扩展内的 [nrel5mw_geometry.json](wfrl_blender/assets/nrel5mw_geometry.json)，MAPPO 的可选合成叶片外形来自 [rootkink_geometry.json](wfrl_blender/assets/rootkink_geometry.json)。场景布局由 [scene_model.py](wfrl_blender/scene_model.py) 中的 `SceneDTO` 和 `TurbineDTO` 表达，包含机组编号、平面位置、风速、风向等信息。

scene_builder.py（本地文件：`blender_frontend/wfrl_blender/scene_builder.py`） 的 `build_scene()` 创建 `WFRL_Scene` 集合，按机组生成塔筒和上部装配，再加入基础、编号、地形、来流箭头、尾流与传感器示意，最后配置环境和渲染。当前构建器使用全局对象名；重建前检查集合及对象的场景归属，其他场景仍引用资源时拒绝重建，保留原场景及其回放。需要返回资源所属场景操作，或使用独立 Blender 进程。同场景重建会清除其同名集合及符合条件的无用户引用资源；普通切换镜头不重建场景。

几何计算集中在 [turbine_geometry.py](wfrl_blender/turbine_geometry.py)，它读取打包资源并返回顶点和面，不导入后端求解器。场景装配再使用 Blender 数据 API 创建网格、对象、父级关系和材质。日常打开已有 `.blend` 与重新构建场景是两条不同路径。

### 环境几何预算

工作区源码的 `landscape.build_landscape(collection, detail='balanced')` 使用固定空间预算：风机及生态覆盖区域保留地形采样，外围加大网格间距；远山使用较低采样，保留中心山脊和落地点。`detail='full'` 可构建完整细节供对照或特定输出使用。道路、平台和覆盖斑块继续通过 `terrain_sampler()` 贴合实际展示地面；`height()` 和 `terrain_shader()` 的调用接口保持独立手动场景兼容。

距最近风机 450 m 内的草簇保留完整几何，外围草簇保留原根部、叶尖、材质和位置，简化弯曲段；已有灌木、岩石实例继续复用原型。预算在创建时确定，不注册逐帧扫描或按活动相机隐藏背景，因此 Down、总览、三相机和离线渲染消费相同场景。保存重开保留已建几何；改变预算需要重新构建。地形和远山仅为展示环境，不进入后端地形、柔性运动或雷达测量。面数变化不等于实际视口 FPS 变化。

## 2. 风机如何建模与装配

### 2.1 主要部件

| 部件 | 实现方法 |
| --- | --- |
| 塔筒 | `tower_mesh()` 按高度—直径站点生成圆环并连接侧面，形成沿高度收缩的塔筒 |
| 叶片 | `blade_mesh()` 重采样翼型轮廓，按弦长、扭角、曲线和扫掠偏移生成截面，再沿展向插值连接 |
| 机舱 | `_rounded_nacelle_mesh()` 用纵向截面生成圆角外壳，保留配置中的长、宽、高包络 |
| 主轴与轮毂 | 主轴使用圆柱体并按轴倾角旋转；轮毂用缩放球体构造，整流罩通过环形截面收束到端点 |
| 机械细节 | [mechanical_details.py](wfrl_blender/mechanical_details.py) 补充叶根法兰、密封件、通风口、检修门与连接附件 |

叶片使用 19 个原始站点和配套翼型坐标；当前装配调用 `blade_mesh(subdiv=8, ring_points=96)`。普通截面之间采用保形 Hermite 插值，叶尖末段增加采样并收束到单点，以改善末端外观。这是展示用收尖，不能当作新的气动设计数据。

基础场景中的叶片对象复用名为 `WFRL.SharedBlade.SourceLoft` 的网格，但保留独立的对象变换与装配父级。基础场景因此可分别表达转子相位和变桨。进入 MAPPO 柔性回放后，`farm_flex.py` 改用 `subdiv=4, ring_points=48` 的展示网格，并为九片叶片分别复制 Mesh、逐帧写入独立形变；不能在这条路径继续共享可变网格。

`turbine_geometry.MODELS` 管理 `nrel5mw` 与 `rootkink` 的几何资产；两者共用 19 站径向位置、预弯、后掠和扭角。rootkink 使用较窄的叶根和靠近轮毂的宽肩部，最大弦长为 **4.85 m**，位于半径 **8.3333 m**。它只改变展示截面的弦长及翼型分配，继续由同一 NREL 保存截面变换驱动；载荷来源和雷达读数保留 NREL 身份，不重新求解，也不代表厂家 CAD 或经过重新验证的物理机型。

场景属性 `wfrl_blade_model` 保存外形选择。每次切换完整校验一次回放包，绑定和失败恢复复用该载荷。叶片、配套叶根展示附件及已验证的重建共享结构引用共同提交或回滚；共享引用完成重绑定后，只回收已无用户且属于相应模块的网格，保留共享、fake-user、shape-key 和无关网格。rootkink 的短颈、法兰和密封圈独立于 NREL 轴承尺寸，二者都只是展示装配。切换保留时刻、所选机组、可见机组、时间轴设置和相机设置。NREL 缺陷编辑或 NREL 重建对照启用时，切换 rootkink 会被拒绝；相机编辑、观察或采集会话也须先结束。相关模板位于 `projects/commercial-rootkink-reference/`，`scripts/blender/make_rootkink_blade.py` 生成叶片站表，`scripts/blender/export_geometry.py` 导出可审计几何资产。

### 2.2 运动层级

下图省略部分附件；对象全名均带 `WFRL.Turbine.<机组编号>` 前缀：

```text
机组根节点                         布局中的 x、y 平移
├── Tower                          固定塔筒
├── YawBearing                     塔顶连接件
└── YawRoot                        位于塔顶，绕局部 Z 轴偏航
    ├── Nacelle / MainShaft         机舱和主轴
    ├── ClearanceRadar...          雷达附件与光束
    └── Rotor                      主轴倾角与转子方位
        ├── Hub / Spinner          轮毂和整流罩
        ├── Blade1.PitchRoot        初始相位与预锥角
        │   └── Blade1             绕叶片局部 Z 轴变桨
        ├── Blade2.PitchRoot
        │   └── Blade2
        └── Blade3.PitchRoot
            └── Blade3
```

`PitchRoot` 分别设置三片叶片的 120° 相位间隔和预锥角，实际 `BladeN` 对象再承担局部变桨。不要把相机的 Pan / Tilt 与这些风机运动量混为一谈，也不要把所有旋转直接堆在叶片网格上。

### 2.3 高度与坐标

长度单位为米。机组根节点位于塔筒地面中心，`YawRoot` 位于塔顶；机舱未偏航时，轮毂在负 X 方向，Z 轴向上。当前塔筒高 **87.6 m**，名义叶轮半径 **63 m**，机舱外壳长宽高为 **8.03 × 3.30 × 3.30 m**。

`hub_position()` 同时使用塔高 `TowerHt`、塔顶到主轴偏移 `Twr2Shft`、悬伸量 `OverHang` 和轴倾角 `ShftTilt`。当前数据计算出的轮毂中心约为 `(-5.00, 0, 90.00) m`，不能直接把塔筒高度当作轮毂高度。

装配时，机舱外壳和部分附件相对塔顶上移 `Twr2Shft`，主轴和转子使用完整的轮毂中心坐标，偏航轴仍保持在塔顶。尺寸和装配明细见 [用户手册附录 A](../docs/blender/用户使用手册.md#附录-a风机模型尺寸与部件装配位置)。机舱、轮毂和部分附件包含展示造型，不是完整工程 CAD。

## 3. 材质与灯光如何实现

[materials.py](wfrl_blender/materials.py) 用 `PALETTE` 定义基础色，`SURFACES` 定义粗糙度范围、凹凸强度、金属度和涂层参数。叶片涂层、机舱、塔筒、金属连接件、橡胶密封件、雷达壳体和镜窗分别使用不同配置。噪声纹理连接粗糙度映射与 Bump 节点，节点按名称复用，避免重复刷新不断堆积节点。

`refresh_turbine_surfaces()` 按 WFRL 对象名称给主轴、轴承、法兰等绑定金属材质，给密封件绑定橡胶材质；雷达自己的材质槽由 `ensure_radar()` 维护。

`_apply_surface_lighting()` 调整主光方向，将曝光设为 0.45，并启用 EEVEE 阴影和局部环境遮蔽。主体材质的自发光强度设为零，以保留机舱、轮毂与叶根之间的明暗层次。以上参数只影响展示，不进入雷达计算。

## 4. 雷达外观如何建模

### 4.1 安装坐标与零件

入口为 [clearance_visual.py](wfrl_blender/clearance_visual.py) 的 `ensure_radar(scene, turbine_id, origin, directions)`。函数找到对应机组的 `YawRoot`，用固定名称查找或创建零件，并把它们挂在这个父节点下，使雷达随机舱偏航。

`origin` 是雷达光束起点在装配局部坐标中的位置，未加载结果时预览默认值为 `(-2, 0, 0)`。每个零件的位置按“光束原点 + 零件偏移”设置。下表列的是代码中的展示几何，不是硬件规格：

| 对象后缀 | 几何与定位方法 |
| --- | --- |
| `ClearanceRadar` 壳体 | 单位立方体缩放，未倒角包络为 0.20 × 0.16 × 0.25 m；中心相对光束起点上移 0.125 m |
| `.Bracket / .MountPlate / .MountPad` | 立方体构造支架、安装板和橡胶垫，沿上方连接到机舱安装区域 |
| `.Bezel / .Window` | 薄立方体构造镜窗边框与深色窗口，围绕光束起点附近布置 |
| `.CoverSeal / .ServiceCover` | 壳体侧面的密封层和盖板 |
| `.CoverScrew0…3 / .CableGland` | 32 边圆柱体，旋转到盖板法向或侧向，分别表示螺钉与线缆接头 |
| `.Cable` | 5 个控制点的三维 Bézier 曲线，自动切线并设置圆形截面厚度 |
| `.Beam1…3` | 每条由两个点组成的三维 POLY 曲线，表示三束方向 |

立方体在局部网格中边长为 2，因此 `scale` 是半尺寸；圆柱体的原始半径为 1、深度为 2。修改零件尺寸时要按这一约定理解参数，不能把缩放值直接当作完整长宽高。方形零件使用命名的 Bevel 修改器柔化边缘，刷新时复用已有修改器。

### 4.2 光束如何显示

三束光线共用橙色带自发光材质，曲线起点为 `origin`，终点为：

```text
origin + 0.95 × TipRad × normalize(direction)
```

默认预览方向相对向下竖直朝负 X 偏转；加载结果时由包内标定提供原点和方向。上述长度只是视觉示意，既不是理想首交斜距，也不是测距量程；前端不把曲线终点当作真实命中点。

雷达窗口、螺钉、线缆以及 Blender 叶片网格都不参与射线求交。真正的测距计算使用后端 AeroDyn 原生变形表面，具体算法放在 [独立雷达 README](../wfrl/lidar/README.md)。

### 4.3 为什么使用独立 BMesh

`_radar_primitive()` 在独立 BMesh 中生成立方体或圆柱，再写入新 Mesh 并通过数据 API 链接对象。它不调用 `bpy.ops.mesh.primitive_*_add`，因此不会把几何加入用户当前正在编辑的网格，也不需要修改活动对象、选择或编辑模式。

旧场景样式更新由 `refresh_saved_surface_style()` 在加载时触发，以 `wfrl_surface_revision` 防止重复升级；旧三束曲线存在时，使用其原点和方向补建附件。维护这条路径时，应保留原网格、父级、变换、场景与选择状态。不是所有建模函数都适合直接在文件加载回调中调用。

## 5. MAPPO 回放、相机与后端如何连接

### 5.1 三机结果包与九片形变

[__init__.py](wfrl_blender/__init__.py) 的 `load_demo_scene()` 创建基础场景后，调用 farm_flex.py（本地文件：`blender_frontend/wfrl_blender/farm_flex.py`） 加载 [assets/mappo](wfrl_blender/assets/mappo/manifest.json)。结果包包括：

| 文件 | 作用 |
| --- | --- |
| `manifest.json` | 当前 `wfrl.farm-flex-review.v3`、REVIEW_ONLY、布局、片段范围、参考定义及文件 SHA-256 |
| `geometry.npz` | 时刻、三机姿态和九片叶片沿展向的形变变换 |
| `data.json` | 三台机组各自的运动记录、B2 测距、净空估计／真值与统计输入 |
| `telemetry.json` | 同次 OpenFAST 实际保存的功率与转矩通道，含单位、源通道与文件摘要；当前内置 v3 未保存 `load` 通道 |
| `tower-motion.npz` | 塔筒截面位置/朝向与机舱变换，共享时间轴；用于塔架及挂载运动 |
| `deflection-t1.json` | 同源 T1 结构叶尖分量输出、刚性姿态及来源身份；v3 按保存的结构叶根坐标系 xyz 比较，边界变换取自 `geometry.npz` 的首截面 |
| `source-run.json` | 对应运行的来源、控制器和原始输出摘要 |
| `source-surfaces.json` | 生成形变数据所用的原始表面证据索引 |
| `blade-reference.json`、`reference-surfaces.npz` | v3 独立零载荷静止求解参考及对应表面，用于参考几何与实际形变对照 |

`read_package()` 校验格式、机组列表、文件哈希、数组维度、有限值、时间轴和运动一致性；遥测还要与几何摘要及时间轴匹配。工作区的场景绑定通过 `_load_package()` 取得一次完整校验载荷，构造器及挠度比较复用其中的几何、运动、塔架和叶片参考数据；每次重新加载仍独立校验，不使用跨加载缓存。公开 `read_package()` 保持五元组接口。不能在校验失败时改用假数据。原始 VTP 不需要随播放器分发；生成新结果与日常播放是不同流程。

`FarmFlex` 为九片叶片分配独立 Mesh，根据相邻源时刻和相邻展向站点插值形变，并结合机组偏航与装配变换写回局部顶点。工作区源码按消费方所需的机组工作集计算和上传整叶网格：普通单机视图选择对应机组，全景选择全部九片；缺陷编辑的寻址和几何分析可在同一时刻请求全部机组。切视图保留时钟，并在当前时刻补齐所需网格，不能以时间未变为由复用另一工作集的结果。`deformed_at()` 缓存键包含源帧和机组工作集；`rebind_blade()` 清除缓存，更换结果包或模型按新来源重新绑定。

隐藏 T1 整叶时，挠度比较仍按同一插值与坐标计算采样三个结构叶尖，保持与可见 T1 的读数一致。普通回放还更新所选机组与 T1 的塔筒及挂载结构，包含仅选择 T2/T3、暂停时首次创建重建共享结构的情况；共享结构在源姿态与塔筒更新后同步。单机视频导出显式限定所选机组，不强制保留 T1 比较或结构。隐藏整叶不等于停止这些数值与结构消费方。这里保留物理变形幅度，展示网格的采样密度不等于求解器网格密度。

本节按消费方选择工作集、下文遥测数组复用及计时入口描述公开 0.3.36 的前端行为。发布独立复验为 NOT_RUN；已有检查的来源和适用范围见[发布状态](../docs/blender/发布状态与验证范围.md#软件验证的适用对象)，日常安装按独立身份记录。工作量计数与数值一致性不证明视口 FPS。

[tip_tracking.py](wfrl_blender/tip_tracking.py) 从变形网格的叶尖位置记录轨迹，叶片 1／2／3 分别为红／绿／蓝，相邻两圈定格对比 2 个仿真秒后淡出。轨迹不是相机测量；同机切镜头保留轨迹，换机、寻址、重播时按生命周期重置。MAPPO 默认关闭轨迹，需要时主动打开。

### 5.1.1 塔架、叶片参考与净空

初始塔筒网格带回放归属标记，绑定提交时只释放无人引用且无保留条件的旧网格；三相机盒体网格使用自有命名空间供场景销毁识别。清理保护共享、fake-user、shape-key 和 linked 数据。装饰修补附件在塔筒和共享结构绑定成功后、逐帧形变开始前重建，结构绑定失败时保留原附件及采样引用。

当前包包含三机塔架同源运动；塔底保持固定，机舱、叶轮及雷达安装随塔顶平移和转动。展示保持真实尺度，未放大摇晃幅度。v3 叶片参考来自独立零载荷静止求解。工作区 `deflection.py` 用保存的首截面刚体变换运输该参考与叶根坐标系；匹配模型的首截面位于受约束叶根，气动中心偏移为零。实际点与参考点之差按这一随根部变桨的 xyz 表达，`BlPitch` 遥测不替代结构根边界姿态。参考插值同时处理旋转和已运输的轮毂位置；结构输出只用于比较。不能把已经变形的第一帧当作未受载参考。

工作区源码的保存帧 **0.2 mm**、插值帧 **5 mm** 显示门槛通过；完整时间轴、匹配原始输出和适用边界见[集成验收](../outputs/regression-repair/integration/result.json)。正式资产保持；受载 VTP 缺失使原始表面拟合无法重跑，安装载荷、求解器收敛与现场精度仍须独立验证。

`deflection-t1.json` 保留同源输出与参考所需信息。原仿真输出对照按可用通道和实际参考系解释，不能把历史面外／面内标签直接等同于当前根系 xyz；缺少通道时不制造对照值。保存帧和插值帧分别标记，缺少对照资源或摘要／时间轴损坏时必须明确提示。

v3 净空真值定义为叶片末截面表面到同一全局高度移动塔筒截面的最小距离，不是整片叶片到塔筒的最小距离，也不是仅取单个叶尖参考点。遮挡检查使用移动塔架几何；B2 固定标定估计没有补偿塔架弯曲。塔筒尚无专用虚影或位移卡片。

### 5.2 统一侧栏与固定时间轴

panels/farm_replay.py（本地文件：`blender_frontend/wfrl_blender/panels/farm_replay.py`） 是 MAPPO 统一入口，顶部共享播放控制；挠度页放 T1 叶片选择、叶尖/叶轮/Down、虚影/分量和数值表；净空页提供默认双束 / S1、TLS 候选与旧 B2 模式切换、机组 Down 和对应卡片；工具页放视角、云台、遥测、环境、截图录制和重新加载。该模式隐藏重复 Camera 面板；其他模式保留 [panels/gimbal.py](wfrl_blender/panels/gimbal.py) 与 [panels/clearance.py](wfrl_blender/panels/clearance.py)。

时间读取复用 [clearance_replay.py](wfrl_blender/clearance_replay.py) 与结果包的 ReplayReader：

```text
t = segment.start_s
    + (frame_current + frame_subframe - frame_start) / timebase_fps
```

MAPPO 固定 `timebase_fps=60`，仿真片段为 117–177 s：40 Hz 源数据 2401 个时刻映射到显示帧 1–3601。输出 FPS 改变目标播放速度，同一帧对应的仿真时刻不变；不能把 60 Hz 时间轴写成稳定 60 FPS 性能结论。

停止保留当前记录；复位到起点并暂停；单步推进 1/60 仿真秒；从头重播与片尾重播恢复播放。片尾不会添加零转速或顺桨动画。镜头切换保留时间和播放状态，不改雷达标定。云台交互通过 [cameras.py](wfrl_blender/cameras.py) 实现；MAPPO 的 `farm_flex_view` 会结束活动云台控制并清除局部相机覆盖，避免全景被旧视图锁住。

`WFRL.Camera.<机组>.Gimbal` 是对应机组的 Down 机位。`_down_mount()` 从 YawRoot 坐标 `(−3.5, −1.15, −5)` 沿 +Z 与实际机舱外壳求交，加 `(0, 0, −0.2151)` m 得到光心。yaw/pitch/roll 为 181.2°/−82.5°/175.9°，焦距 8 mm、传感器宽约 16.4224 mm；16:9 下水平／垂直视场约 91.4928°／60°，镜头偏移为零。固定相机随机舱偏航和塔顶姿态运动，保留完整画框，不随叶轮自转或追踪叶片。

`ensure_gimbal()` 对挂载标记小于 5 的相机原位恢复预设，保持对象与镜头身份；有效保存挂载保留手动姿态和光学设置。显式 Down／相机复位恢复对应预设。独立机舱与 C1/C2/C3 的传感器和复位规则分别维护。Down 切换不改回放时刻或叶片外形；有限构图、射线及后台回归不证明全周期无遮挡或现场安装安全。参数与适用范围见 [Down 说明](../docs/blender/T1Down机舱下视.md)。

`frame_change_post` 驱动读取和网格更新。保存重开时清除旧对象／场景指针缓存，按保存路径恢复读取器；随包 MAPPO 支持恢复到扩展数据目录。`.blend` 不会自动嵌入外部包，缺失或校验失败必须显示未就绪并清除读数。

### 5.3 遥测与 B2 数据合同

`farm_flex.record_telemetry()` 将三机姿态及实际保存的额外通道交给 [charts.py](wfrl_blender/charts.py)，提供每通道最多 600 点的历史、曲线与 JSON 导出。变桨显示运动记录中三叶片的平均值；当前内置 v3 没有保存 `load` 通道，保持未知，不补造 B1 叶根载荷。关闭采样会冻结读数，奖励未记录。导出保留来源和单位，不代表整个物理运行档案。历史 JSON 在目标目录的独立临时文件中完成序列化、写入与刷新后再原子替换目标；失败保留已有文件并清理自有临时文件，不发布不完整 JSON。导出状态绑定发起操作的场景和作业，切换场景不转移完成或失败提示；场景被删除或文件关闭后不向替代场景写状态。在同一 Blender 进程中重新启用插件会恢复未发布结果的状态轮询。

工作区源码的 `_read_telemetry()` 在每次加载时校验 schema、几何摘要、时间轴、机组、数组形状与有限值，将通道值保留为 NumPy 浮点数组供逐帧插值复用；重新加载独立校验，不跨加载复用数组。插值仍使用原时间轴、数值、单位和源通道，不改变可见读数或缺失通道语义。

旧 B2 模式的雷达卡片必须使用**同一条 B2 有效测量**的斜距、净空真值、估计和误差，不能把不同时间、叶片或其他光束拼在一起。误差为估计减真值；无效或过期显示缺失，不补零。读数年龄按仿真时钟计算，暂停时不随墙钟增长。

有效率分母是评估网格内预期样本；误差统计截至当前位置，整次漏测在片尾汇总。T1 当前有效率偏低，不能只展示 MAE 而省略缺测情况。数据仍是 REVIEW_ONLY；真值为仿真几何参考值，B2 是理想测距与简化算法估计，不能宣称现场精度、控制收益或数值收敛。算法与合同见 [雷达算法 README](../wfrl/lidar/README.md)。

### 5.3.1 双束与独立 S1 数据合同

0.3.36 安装包包含默认 `hub-axis.v1` 与显式可选 `hub-tls.v1` 两份双束结果层及读取器，共享原 `assets/mappo/` 40 Hz 源几何。`farm_flex.py` 按 `measurement_mode=dual_beam` 接入，`panels/farm_replay.py` 提供“双束净空 / S1”“TLS 候选 / S1”与旧 B2 切换；读取及可携带打包由规范源码 `wfrl/lidar/dual_beam_replay.py`、`dual_beam_package.py` 负责，构建时生成扩展内置副本。

S2/S3 按同一保存时刻、同一叶片的有效命中配对，S1 有效叶片命中独立报警，不能用双束无效来抑制 S1。读数、报警和累计历史共用仿真时钟，寻址不重复累计；无效值保持缺失。可携带包包含自身 `source/` 并校验完整源数据及结果摘要，移走后不依赖开发机绝对路径。损坏或缺包须清空旧状态。

固定 MAPPO 源的双束完整过叶统计：

| 机组 | 完整过叶次数 | 每次是否存在 S2/S3 配对 |
| --- | --- | --- |
| T1 | 33 | 全部存在 |
| T2 | 33 | 全部存在 |
| T3 | 28 | 全部存在 |

合计 **94/94 次完整过叶、400 个有效配对采样点**；每次完整过叶至少有连续 3 个保存采样点配对。该口径基于 40 Hz 保存几何，不要求每帧命中，不包含片段边界的不完整过叶，也不要求 S1 在正常片段保持静默。原包实际安装的双束后台回归通过，覆盖标定、配对、独立 S1、寻址、FPS 映射、换机、保存重开、缺包清除和旧 B2 重载；不是现场或完整片段性能验收。

10°/12°/14° 是当前仿真安装角。TLS 是轮毂约束直线候选，只改变方向估计：在同一开发片段 400 对上，旧法与 TLS 的 MAE 分别为 4.268685 / 0.776096 m；独立单机固定 9 rpm 工况、40/80 Hz 网格的 TLS MAE 约为 0.943/0.970 m。两种方法仍为 REVIEW_ONLY / PENDING_ACCEPTANCE，不据此切换默认，也不构成现场性能验收。诊断、独立工况与评分口径见[候选总报告](../docs/lidar/双束TLS候选实施与验证总报告.md)，0.3.13 当时的安装验证见[0.3.13 发布核对](../docs/blender/releases/0.3.13发布核对.md)。

### 5.4 独立 normal／close 雷达包

独立入口为 [open_clearance_demo.py](../scripts/blender/open_clearance_demo.py)，读取 [交付清单](../dist/lidar-delivery.json) 中 `normal-v1.1`、`close-v1.1` 两个外部包。历史演示 `.blend` 缺失时会重建场景；结果包仍必须完整。该入口使用工作区源码，不替换已安装扩展。

`clearance_replay.load(scene, path, demo)` 校验机组、尺寸、标定及片段身份，退出现有柔性回放并建立对应读取器。“正常测量／较小净空”切片后从起点播放；“测量区侧视／风场总览”仅切镜头。这两段画面是刚性姿态示意，后端数值包含柔性形变，不能把这句话套用到三机 MAPPO 网格动画。

此独立路径采用固定全局塔原点与刚性塔假设，现有生产脚本要求零偏航；不是任意父级旋转、缩放或浮式机组的通用坐标变换。三机 MAPPO 使用其独立的偏航与形变数据合同。修改相机方向不会改变任何一条路径的物理标定。

### 5.5 实时连接与控制生命周期

项目侧 [wfrl_launcher.py](../scripts/blender/wfrl_launcher.py) 负责环境预检、Bridge 生命周期与 Blender 进程；[wfrl_blender_bootstrap.py](../scripts/blender/wfrl_blender_bootstrap.py) 导入 `bl_ext.user_default.wfrl_blender`，写入路径配置、加载默认 MAPPO，并在用户主动连接后记录会话及加载 YAML。

扩展 [runtime.py](wfrl_blender/runtime.py) 和 [transport.py](wfrl_blender/transport.py) 维护连接、协议状态与回传。[panels/status.py](wfrl_blender/panels/status.py) 管连接与诊断，[panels/run.py](wfrl_blender/panels/run.py) 是唯一后端运行按钮入口。CONNECTED 表示握手；实际运行还需 READY 后显式 Start，并观察 RUNNING 与不断更新的真实快照。

同一 Blender 进程打开其他 `.blend` 时，已认领的后端会话保留客户端、会话身份及协议序号，连接标为断开且未确认；旧场景的姿态缓存和待确认命令清空。保存的离线回放可以独立恢复几何、读数和位置，不据此认定后端已停止。显式重连使用保留的会话恢复握手，确认前禁用后端运行和配置操作。这些恢复凭据只在当前 Blender 进程内保留，不写入 `.blend`，不保证退出 Blender 后恢复活动后端。

0.3.36 扩展包含前端连接、协议和运行状态处理；后端服务器、训练启动器和 MPI 环境不随扩展 ZIP 安装。后端 [backend_session.py](../wfrl/blender_bridge/backend_session.py) 只在新的控制步边界处理暂停和单步，预热或重复第 0 步的进度通知不能重复消耗单步许可。[server.py](../wfrl/blender_bridge/server.py) 在接收新连接前回收已有连接的 EOF，允许断开后立即重连，同时保留活动客户端独占。

当前后端源码的 `BridgeServer.poll()` 即使没有客户端也调用 `BackendSession.tick()`，继续读取训练进度、检测退出与失败并清理已结束会话的临时资源。快照保留最新状态，训练统计可以按类型合并；`lifecycle`、`safety_event` 和 `error` 使用独立有界可靠队列，不被统计挤出。可靠队列真实溢出时报告 `EVENT_OVERFLOW`、进入 `FAILED` 并停止会话，不静默丢弃。重连恢复当前状态、各类最新统计及仍保留的可靠事件，并计入等待期间的数据年龄；不承诺补回每个观察样本或完整观察历史。

实时全场功率要求同一步的全部预期机组都有有限标量读数，且合计有限；任一缺测或无效都使全场值保持 `null / invalid`，通道完全未返回时为 `unsupported`。有效机组自己的功率仍可显示，真实零功率仍有效，不能以部分合计或补零掩盖缺测。Interactive 在 POSIX 平台经 `IsolatedTrainer` 和 `trainer_worker` 进入 Studio Trainer 的共享 actor、集中式 critic 路径；Formal Training 使用独立训练入口。迭代统计完整性与目标定义见[训练入口与合同](../docs/training/MAPPO_SETUP.md)。

项目侧 Replay 从 checkpoint 的动作合同读取控制顺序、变换与边界，并校验观测、机组及运行时兼容性。Studio / Interactive 的 `tanh_scale` 与 CLI 高斯 yaw 策略的 `clip` 分别保留；单控制量不作为选择变换的依据。可识别的无版本模型按保存格式校验，元数据缺失或冲突时明确失败。[动作合同与兼容范围](../docs/training/MAPPO_SETUP.md#策略动作与-checkpoint)属于项目 Python 后端，更新后须重启 Bridge，不表示扩展安装包或既有模型的物理控制效果已验收。

Interactive 的 **Episode steps** 对应 `episode_steps`：`0` 保持单长回合，正值指定每回合训练控制步数；每回合独立预热，预热不占 `iters * n_steps` 的训练预算。快照的全局 `step` 保持递增，`context.episode` 区分从零编号的回合；仿真时间允许回零，遥测曲线在回合或时间边界断线，训练统计继续保留。[回合与 GAE 规则](../docs/training/MAPPO_SETUP.md#interactive-的采样与更新)由后端实现；配置面板和曲线变更需匹配的扩展安装载荷。

[isolated_trainer.py](../wfrl/blender_bridge/isolated_trainer.py) 的 `launcher_environment()` 移除继承的 OMPI/PMI/PMIX/OPAL/PRTE 作业身份变量，只保留 `OMPI_MCA_btl`、`OMPI_MCA_pml` 及各自以下划线连接的传输配置子项。macOS 未显式设置 BTL 时默认 `self,tcp`；未设置 TCP 接口 include/exclude 时默认 `lo0`，保留调用方的显式传输选择。后端源码需在重启 Bridge 后生效，重新安装扩展 ZIP 不会更新服务器或 MPI 环境。

实时 `rotor_speed` 当前由功率／效率／转矩／齿轮比推算，保留 SYNTH 与 `FastFarmDriver._rotor_speed` 来源；yaw、pitch、power、torque 的 DIRECT 标签不能推广到所有通道。离线 MAPPO 使用保存的转速记录。真实后端场景与随包随机阵风记录属于不同运行。

## 修改、安装与验证

维护修改应作用于规范源；`protocol.py` 来自 `wfrl/blender_bridge/messages.py`，内置 `_vendor/lidar/` 读取器来自 `wfrl/lidar/`。不要手改生成副本或替换冻结模型及正式资产来消除校验失败。

```bash
python3 scripts/blender/build_extension.py --output <新目录>
python3 scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender可执行文件>
```

构建生成 ZIP、SHA-256 与逐文件 inventory，默认拒绝任一同名产物已存在的输出，包括断符号链接。使用新目录保留各包身份；明确需要替换时使用 `build_extension.py --output <目录> --overwrite`。文件在同一文件系统的临时目录中完成后逐个发布，ZIP 最后发布；普通发布异常恢复原产物，恢复冲突保留备份并报告位置，不保证断电或进程强制终止时三文件整体原子性。验证工具默认构建、隔离安装并执行后台检查；`--build-only` 仍会构建 ZIP。纯文档任务仅检查格式、引用、身份与要求一致性。

ZIP 包括 MAPPO 源、旧法和 TLS 结果层、规范示例、NREL 缺陷编辑器及生成的协议／读取器。公开 0.3.18 源码的复建结论仅属于该版；本地载荷、公开附件和已安装文件必须分别匹配 inventory 与 build-info，不用 Git 提交或版本字符串替代。

按[维护与验证入口](../.agents/skills/windfarm-blender-dev/references/verification.md)选择宿主、后台、窗口与隔离安装检查。源码导入不证明安装版正确，后台通过不证明可见交互或物理精度。每项测试记录实际模块、输入、环境、输出和终止状态。

工作区源码的 [performance.py](wfrl_blender/performance.py) 默认关闭，仅在启用后汇总 CPU 回调耗时与工作量。`flex.deformation_cache` 计时位于 `deformed_at()`，包含缓存查询、命中返回及未命中计算；`flex.rebind_blade` 独立计时。`flex.cache_hit` / `flex.cache_miss`、`flex.participating_turbines`、`flex.blade_updates`、`flex.vertices_written`、`flex.tower_vertices_written` 和 `flex.measurement_points` 分别记录缓存、参与机组、整叶写入、塔筒写入及隐藏叶尖采样工作量。计时可嵌套，不能相加推算总帧耗时，也不能把计数或后台耗时换算成视口 FPS。

完整播放需要每路推进与终段证据；超时、缺绘制、错误和意外回跳不能判为通过。`POST_PIXEL` 是视口绘制回调，不是显示器扫描输出。近两秒采样、GPU 事务复用、固定 60 Hz 时间轴和暂停时稳定刷新均不能替代完整片段 FPS。

## Blade Recon 与重建对照

外部 `blade_recon0.2` 提供研究算法，扩展只读取已保存结果，不在查看时拟合。材料点候选独立结果为 Patch **2/8**、Crack **7/8**（87.5% < 90%）、跨相机 RGB **0/264**，真实同材料点三维误差 `null`，保持 `FAILED_GATE / NOT_ACCEPTED`。数据与限制见[材料点对应研究](../blade_recon0.2/docs/材料点对应实验报告_2026-10-11.md)；该研究没有替换扩展正式 601 样本资产。

独立“叶片三维重建”标签通过合成样例或 `recon.json` 创建单独场景，保留原场景与回放位置。`blade_recon_review.py`、`blade_recon_surface.py`、`blade_recon_inspection.py` 与 `panels/blade_recon.py` 负责结果、纹理和图例；图例中的轮廓支持不等于独立三维测量。`synth_tex` 是 **SYNTHETIC** 的 120 个 10 Hz 样本，来源与时钟独立。

统一入口 **MAPPO → 打开几何重建示例…** 使用 `assets/examples/mappo_reconstruction_split.blend`，在同一完整风机分屏切换几何／同源纹理。右侧塔筒、机舱、轮毂与机械细节共享源 T1 结构，只有三片叶片属于重建估计。`split_turbine_context.py` 独立管理共享结构的变换与可见性，T2/T3 选择不得隐藏右侧结构。

`split_reconstruction.py` 管理布局生命周期，`split_reconstruction_timing.py` 定义固定映射，`split_reconstruction_ui.py` 提供统一控制与左下角 HUD。601 个保存状态来自 10 Hz 重建，使用固定 60 Hz 时间轴，每个样本保持 6 帧；MAPPO 源为 40 Hz。两侧共同播放、暂停、步进与循环。

当前源码的 `split_mappo_texture.refresh()` 复用已校验的对象与结构绑定；来源对象、网格、保存清单或摘要变化时完整校验，文件加载、撤销与重做清缓存。只在矩阵、网格或全局可见性变化时写入，不重复激活显示设置或扫描全部场景。

中段／叶尖观察点只定位一次，手动缩放、平移、绕转后继续播放保留用户视图。原始／增强显式切换回两侧整机；模式切换保留时刻、播放状态和有效视角。保存重开恢复当前模式、显示、时间与视角，不重放按钮取景动作。独立合成入口及底层样本工具不在统一侧栏显示。

便携场景保存 `__WFRL_BUNDLED_MAPPO__` 与 manifest SHA-256，依据实际安装目录匹配 `assets/mappo/`，含 packed 天空／地表图，无 linked libraries。失效路径只恢复相同来源；不匹配则清空并报错，不改成任意默认包。打开入口保留 Blender 原生未保存确认，验证使用副本，不覆盖规范场景。

同源纹理为 `SAME_SOURCE_SIMULATION`，绑定来源、模型、图集与全部样本绝对时钟。embedded 数据和 packed 图片用于保存恢复。未观测 texel 保持透明；增强和放大不增加分辨率，条带、接缝、叶尖错贴与拖影如实保留。人工观察点不构成自动检测或科学精度通过。

# NREL 5MW / WFRL Blender 前端实现与维护

用户操作见[前端说明](../前端readme.md)，当前交付和验证见[发布状态](../docs/blender/发布状态与验证范围.md)，待实施要求见[前端更新计划](../前端更新计划.md)。要求 Blender **5.2+**。

## 运行来源与能力范围

| 对象 | 身份与范围 |
| --- | --- |
| 公开安装包 | [NREL 0.3.24](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.24)；独立风机手动控制、姿态预设、相对微调、三机概览、姿态 JSON、完整风机几何／同源纹理切换、三机柔性回放、默认三相机复位、六类合成缺陷，以及相机清理、稳定刷新、采集与播放保护。 |
| 项目源码 | [`wind-farm-rl-all-work`](https://github.com/snode11/wind-farm-blender/tree/wind-farm-rl-all-work)；包含当前实现、测试与研究工具。发布源码标签 `v0.3.24` 指向 `844b1de6a04a612b72b244d42749ae5ba6ee9213`；当前分支与公开 ZIP、日常安装载荷分别识别。 |
| 本地日常安装 | `bl_ext.user_default.wfrl_blender`，0.3.28 本地安装包、181 文件，包含 NREL／rootkink 叶片外形选择、T1/T2/T3 Down 机舱下视及当前相机与播放修复。[安装核对](../publication/daily-installation.json)标识实际来源与验证范围；公开附件见[交付身份](../publication/blender-0.3.24.json)。 |
| GW184 | [0.4.0 独立运行包](../docs/blender/GW184三相机与缺陷编辑器.md)；184 m 尺寸合成刚性模型、固定三相机和六类缺陷。模型与配置不能直接替代 NREL。 |

日常安装使用`outputs/frontend-stability-fixes/validation/package/wfrl_blender-0.3.28.zip`（本地文件；[逐文件清单](../outputs/frontend-stability-fixes/validation/package/wfrl_blender-0.3.28.inventory.json)），SHA-256 为 `b15c491db68fda6a2bdfce4228d5db040fa6ae0f0a69f886935ab0af9b775fda`，尚未公开发布。[安装身份](../publication/daily-installation.json)记录源码、ZIP、181 个安装文件及用户配置保护结果。[安装载荷回归](../outputs/frontend-stability-fixes/installed-regressions.json)覆盖原生撤销／重做与场景删除、三路镜头隔离、观察保存与失败恢复、缺陷辅助对象删除和关闭重试、退出重建后的模型切换及共享绑定故障恢复。窗口检查使用脚本和原生操作器；完整片段性能、长期运行、物理输入和科学精度另行验证。公开附件见[交付身份](../publication/blender-0.3.24.json)。

公开包 SHA-256 和匹配验证见[发布状态](../docs/blender/发布状态与验证范围.md)。工作区源码、构建 ZIP、已安装扩展与公开附件分别核对，不能依据版本号推定文件相同。日常启动器加载安装版；发布不会自动升级日常安装，更新安装后需要完全重启 Blender。

默认 MAPPO 为保存的预弯 v3 三机 60 秒结果，视口绘制不启动 FAST.Farm。几何／纹理 `NOT_ACCEPTED`、叶尖门槛 `FAILED_GATE`、雷达 `REVIEW_ONLY / PENDING_ACCEPTANCE` 保持。源采样频率、显示时间轴和实际绘制帧率分别报告。

本地 0.3.28 的 MAPPO 侧栏提供 **叶片外形**，默认 `nrel5mw`，可选择合成商用形制 `rootkink`。公开 0.3.24 ZIP 不包含该外形选择；运行来源按实际加载模块和[安装身份](../publication/daily-installation.json)识别。

## 合成缺陷与默认补漆

`repair_marks.py` 管理 T1/B1 距叶根约 34 m 的 **1.4 × 0.7 m** 浅灰补漆。三角面索引和重心权重通过 Geometry Nodes 跟随旋转、变桨与柔性运动；仅原始 T1/B1 带此默认标记，重建叶片和 GW184 独立模型不受影响。

`nrel_defects/` 管理九片叶片的六类缺陷、预览确认／取消、撤销重做、健康对照和参考身份绑定的 JSON。`farm_flex.rebind_blade()` 与 `deform_points()` 使修改拓扑、支持区域和轮廓沿同一保存运动更新。保存只记录已提交配置。合成外观不改变气动、刚度、载荷或雷达源；G 的几何可见性不等于图片可辨识。操作和约束见[NREL 缺陷编辑器](../docs/blender/NREL缺陷编辑器.md)。

## 当前主要结构

- `manual_scene.py`、`manual_control.py`、`manual_motion.py`、`manual_pose.py` 与 `panels/manual_control.py`：独立刚性场景、墙钟积分、姿态预设、按机组当前值微调、三机概览及严格姿态 JSON。`manual_pose.py` 只使用标准库，定义 `wfrl.manual-pose.v1` 和角度边界；Blender 操作器先校验所选机组再写入参数、对象与时钟，导入后暂停。文件保存不暂停运行，运动相位以同一墙钟时刻采样；[操作说明](../docs/blender/风机手动控制.md)。
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
- `cameras.py`：观察相机与云台预设；T1/T2/T3 Down 分别在各自机舱前部下方中心线与实际底部壳面求交定位挂点，光心下伸 0.40 m，光轴沿机舱局部 −Z。三机共用预设和完整画框规则。[Down 参数与使用边界](../docs/blender/T1Down机舱下视.md)。
- `nacelle_camera.py`：单路机舱相机的机械转轴安装基准、8 mm 镜头及独立 59°/40°视场；`nacelle_visibility.py`：三角面与视锥裁剪、展向覆盖及有限首命中采样。[参数与当前模型限制](../docs/blender/NREL机舱相机.md)。

手动场景以 `wfrl_scene_kind=manual` 标识，使用独立命名空间的对象、网格、材质、World 与图片，复用 NREL 参考几何构造，不加载 demo 的柔性结果驱动。每台风机通过场景内对象指针绑定偏航、转子及叶片；控制前检查归属、装配层级和跨场景共享。RPM 按单调墙钟积分，改变速度保留相位；“全部”只传播正在编辑的字段，参数差异以“多值”提示，集体桨距统一覆盖三片叶片。批量设置分别变桨模式时，`_batch_update` 跳过已处于目标模式的机组；只在从关闭切换为开启时，以该机当前集体桨距初始化三片叶片。暂停时可设置叶轮角度；全部零 RPM 时禁用运行。参数和姿态随 `.blend` 保存，加载、撤销、重做、退出及失效绑定均暂停运动。首次创建与损坏重建分别形成撤销记录，重建保留返回视图。材质预览使用独立天空和轻量地形，退出恢复源场景预览设置。面板进入／返回保留源场景及播放位置，活动后端连接阻止进入；手动运动不写入后端或回放时钟，也不产生物理测量值。手动场景与三相机、缺陷编辑器的串联暂缓，不属于当前交付范围。

## 当前三相机维护要点

三台相机共同安装在 T1 机舱外伸支架末端，光心随整个盒体运动，各路保留独立方向与视场。贴合约束作用于支架安装端，不能把盒体背面贴住机舱当作默认安装；自由 XYZ 操作会解除贴合约束。

**恢复默认三相机** 复用已保存的默认 JSON，不改变回放帧、播放状态或用户自由视图。布局和原生状态都已默认时保持对象身份与历史；原生对象状态损坏时显式复位可修复。当前源码通过 `custom_cameras._publish()` 在同一事务中重绑定场景、局部视口及同场景未关闭控制器的 `snapshot`、`external_snapshot`、`session.snapshot` 相机引用，覆盖编辑确认、布局替换与默认复位；引用随相机对象一起提交或回滚。`_refresh_references()` 仅在 WATCH / LAYOUT 刷新当前观察相机。

当前目标是同一叶片的连续视场与相邻重叠，不要求三等分，不按目标叶长范围裁切图像。C2 可能因透视关系看到较远叶尖；不要求只有 C3 可见叶尖。默认参考时刻为 117 s，固定相机在旋转中允许叶片进出画面，不自动追踪叶片。

原生窗口用临时观察相机表达各路视场，共用场景和时钟。默认 GRID 为 2×2，第四格是 `installation_schematic.py` 生成的静态安装示意；STRIP 为三列对照。两种布局均有播放／暂停、单步及单路放大。独立 WATCH 和放大固定使用 SOLID 材质颜色显示，无单路画质切换；返回三路恢复其原画质。PNG 采集使用独立原始质量设置，异常退出也须恢复回放与预览状态。

工作区的 PNG 采集调用 `render_group(..., create_texture=False)`，只回读 RGBA 像素，不建立用于显示的 GPU 纹理；预览默认创建显示纹理。两条路径共用颜色管理、相机参数、场景状态校验和资源清理。

当前源码中，观察会话分别保留代理对象与镜头数据引用，分步释放绘制回调、代理和窗口，并独立恢复预览画质及主视口。已经删除的 RNA 不阻止其余清理；未释放的资源保留供再次关闭重试。布局事务提交后，释放失败的旧相机对象与镜头数据也分别进入重试队列；对象已删除仍可回收其镜头数据，数据仍有用户时等待后续清理。正常释放时仍被其他对象共享的数据不纳入失败重试队列。准备／观察失败保存在场景的 `wfrl_native_camera_error` 并显示于三相机主面板，成功完成重开后清除。

相机窗口的播放按钮和时间提示按实际场景播放归属显示。另一场景占用播放器或活跃播放器归属不明时，播放与单步按钮禁用；空格键请求和直接调用同样拒绝暂停或抢占该播放器。关闭及延迟恢复也保留另一场景的播放。源码同步不改变既有公开 ZIP 或日常安装的身份。

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

公开 0.3.24 和本地 0.3.28 安装版会同时建立推荐三相机配置，默认关闭叶尖轨迹和挠度辅助物。主视图仍先显示 T1 总览，进入 **View → 三相机 → 三路对照** 查看。仅更新仓库 README 不会改变已运行的 Blender 扩展。

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

scene_builder.py（本地文件：`blender_frontend/wfrl_blender/scene_builder.py`） 的 `build_scene()` 创建 `WFRL_Scene` 集合，按机组生成塔筒和上部装配，再加入基础、编号、地形、来流箭头、尾流与传感器示意，最后配置环境和渲染。重新构建时会先清除该同名集合及符合条件的无用户引用资源；这是重建入口，不是普通切换镜头操作。

几何计算集中在 [turbine_geometry.py](wfrl_blender/turbine_geometry.py)，它读取打包资源并返回顶点和面，不导入后端求解器。场景装配再使用 Blender 数据 API 创建网格、对象、父级关系和材质。日常打开已有 `.blend` 与重新构建场景是两条不同路径。

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

场景属性 `wfrl_blade_model` 保存外形选择。每次切换完整校验一次回放包，绑定和失败恢复复用该载荷。网格绑定及已验证的重建共享结构引用在同一事务中提交或回滚；共享引用完成重绑定后，只回收已无用户且属于柔性回放的网格，保留共享、fake-user、shape-key 和无关网格。切换重新绑定叶片展示网格，保留时刻、所选机组、可见机组、时间轴设置和相机设置。NREL 缺陷编辑或 NREL 重建对照启用时，切换 rootkink 会被拒绝；相机编辑、观察或采集会话也须先结束。相关模板位于 `projects/commercial-rootkink-reference/`，`scripts/blender/make_rootkink_blade.py` 生成叶片站表，`scripts/blender/export_geometry.py` 导出可审计几何资产。

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
| `deflection-t1.json` | 同源 T1 结构叶尖分量输出、高精度姿态与塔顶参考变换；当前 v3 按根系 xyz 解释 |
| `source-run.json` | 对应运行的来源、控制器和原始输出摘要 |
| `source-surfaces.json` | 生成形变数据所用的原始表面证据索引 |
| `blade-reference.json`、`reference-surfaces.npz` | v3 独立零载荷静止求解参考及对应表面，用于参考几何与实际形变对照 |

`read_package()` 校验格式、机组列表、文件哈希、数组维度、有限值、时间轴和运动一致性；遥测还要与几何摘要及时间轴匹配。工作区的场景绑定通过 `_load_package()` 取得一次完整校验载荷，构造器及挠度比较复用其中的几何、运动、塔架和叶片参考数据；每次重新加载仍独立校验，不使用跨加载缓存。公开 `read_package()` 保持五元组接口。不能在校验失败时改用假数据。原始 VTP 不需要随播放器分发；生成新结果与日常播放是不同流程。

`FarmFlex` 为九片叶片分配独立 Mesh，根据相邻源时刻和相邻展向站点插值形变，并结合机组偏航与装配变换写回局部顶点。单机镜头只上传可见机组网格；切到全景时，在当前时刻更新全部九片再显示。这里保留物理变形幅度，展示网格的采样密度不等于求解器网格密度。

[tip_tracking.py](wfrl_blender/tip_tracking.py) 从变形网格的叶尖位置记录轨迹，叶片 1／2／3 分别为红／绿／蓝，相邻两圈定格对比 2 个仿真秒后淡出。轨迹不是相机测量；同机切镜头保留轨迹，换机、寻址、重播时按生命周期重置。MAPPO 默认关闭轨迹，需要时主动打开。

### 5.1.1 塔架、叶片参考与净空

当前包包含三机塔架同源运动；塔底保持固定，机舱、叶轮及雷达安装随塔顶平移和转动。展示保持真实尺度，未放大摇晃幅度。v3 叶片参考来自独立零载荷静止求解，随同刻塔顶与转子运动；实际点与参考点之差投影到随变桨的叶根坐标系 xyz。不能把已经变形的第一帧当作未受载参考。

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

本地实现的 `WFRL.Camera.<机组>.Gimbal` 是对应机组的 Down 固定下视机位，`<机组>` 为 T1、T2 或 T3，父级是同机组的 `YawRoot`。`_down_mount()` 以 YawRoot 坐标 `(−3.8, 0, −5)` 为起点、+Z 为方向，转换到实际 `Nacelle` 外壳自身坐标后求交；命中点转换回 YawRoot 后加偏移 `(0, 0, −0.40)` m。标准外壳的名义挂点约为 `(−3.8, 0, 0.46256)` m，光心约为 `(−3.8, 0, 0.06256)` m，位于机舱前部下方中心线。外壳缺失或射线无交点时明确报错。预设 yaw/pitch/roll 为 180°/−90°/180°，水平视场 110°，36 mm 传感器宽度对应约 12.60 mm 焦距，镜头偏移为 `(0.100, −0.070)`。光轴严格沿机舱局部 −Z，塔筒方向位于画面上方，不以光轴穿过塔轴为目标。机位继承机舱偏航、塔顶运动与微小摆动，不继承叶轮自转，也不锁定世界重力方向。`fill_camera_view()` 对 `_DOWN_GIMBAL_NAMES` 中的 T1/T2/T3 相机均调用 `fit_camera_view()`，按未被侧栏遮挡的区域适配完整画框并开启框外遮罩，视口适配不改光学参数。

三机的 `ensure_gimbal()` 均对 `wfrl_gimbal_mount_revision < 3` 的现有对象原位设置挂载和预设，保持对象及相机数据身份；满足版本 3 时保留保存的位置、手动云台姿态及光学参数，不重复复位。显式 Down 和相机复位均恢复对应机组的固定机位、光学参数及镜头偏移。独立 `NacelleGimbal` 的 8 mm、59°×40°参数和 C1/C2/C3 共盒三相机分别维护。Down 不改叶片外形或回放时刻；照片参考取景使用 rootkink 的近根拐点与保存运动，观察正下方叶片和塔筒的位置关系。参考构图使用 0.3.26 载荷的[静态检查](../outputs/down-reference-view/reference-checks.json)覆盖 T1/B1 相对垂直向下 −20°、−15°、−10° 三个姿态，拐点参考位置均投影入框；最大弦长肩部的后缘侧末端 15% 弦长区域各取 54 个面中心，分别有 12、23、23 个可见。精确后缘中点在三个姿态均被本叶片自遮挡，区域表面可见不等于后缘端点可见。有限样本不外推为全运动范围或完整叶片无遮挡，不要求参考照片的逐像素复刻。参数、参考图与检查范围见 [T1/T2/T3 Down 机舱下视](../docs/blender/T1Down机舱下视.md)；公开附件、本地安装和源码的行为按各自载荷核对。

`frame_change_post` 驱动读取和网格更新。保存重开时清除旧对象／场景指针缓存，按保存路径恢复读取器；随包 MAPPO 支持恢复到扩展数据目录。`.blend` 不会自动嵌入外部包，缺失或校验失败必须显示未就绪并清除读数。

### 5.3 遥测与 B2 数据合同

`farm_flex.record_telemetry()` 将三机姿态及实际保存的额外通道交给 [charts.py](wfrl_blender/charts.py)，提供每通道最多 600 点的历史、曲线与 JSON 导出。变桨显示运动记录中三叶片的平均值；当前内置 v3 没有保存 `load` 通道，保持未知，不补造 B1 叶根载荷。关闭采样会冻结读数，奖励未记录。导出保留来源和单位，不代表整个物理运行档案。

旧 B2 模式的雷达卡片必须使用**同一条 B2 有效测量**的斜距、净空真值、估计和误差，不能把不同时间、叶片或其他光束拼在一起。误差为估计减真值；无效或过期显示缺失，不补零。读数年龄按仿真时钟计算，暂停时不随墙钟增长。

有效率分母是评估网格内预期样本；误差统计截至当前位置，整次漏测在片尾汇总。T1 当前有效率偏低，不能只展示 MAE 而省略缺测情况。数据仍是 REVIEW_ONLY；真值为仿真几何参考值，B2 是理想测距与简化算法估计，不能宣称现场精度、控制收益或数值收敛。算法与合同见 [雷达算法 README](../wfrl/lidar/README.md)。

### 5.3.1 双束与独立 S1 数据合同

公开 0.3.24 与本地 0.3.28 安装包包含默认 `hub-axis.v1` 与显式可选 `hub-tls.v1` 两份双束结果层及读取器，共享原 `assets/mappo/` 40 Hz 源几何。`farm_flex.py` 按 `measurement_mode=dual_beam` 接入，`panels/farm_replay.py` 提供“双束净空 / S1”“TLS 候选 / S1”与旧 B2 切换；读取及可携带打包由规范源码 `wfrl/lidar/dual_beam_replay.py`、`dual_beam_package.py` 负责，构建时生成扩展内置副本。

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

公开 0.3.24 与本地 0.3.28 扩展包含前端连接、协议和运行状态处理；后端服务器、训练启动器和 MPI 环境不随扩展 ZIP 安装。后端 [backend_session.py](../wfrl/blender_bridge/backend_session.py) 只在新的控制步边界处理暂停和单步，预热或重复第 0 步的进度通知不能重复消耗单步许可。[server.py](../wfrl/blender_bridge/server.py) 在接收新连接前回收已有连接的 EOF，允许断开后立即重连，同时保留活动客户端独占。

公开源码提交 [`1c2b334b`](https://github.com/snode11/wind-farm-blender/commit/1c2b334b583dfcd1219911c12804271fe0aa6a66) 更新 [isolated_trainer.py](../wfrl/blender_bridge/isolated_trainer.py) 的 `launcher_environment`：移除继承的 OMPI/PMI/PMIX/OPAL/PRTE 等 MPI 作业身份变量，保留显式 BTL/PML 传输配置；macOS 在没有显式接口配置时使用 `self,tcp` / `lo0` 默认值。9 项宿主回归通过。需要此修复时，更新后端项目源码到该提交或更新版本，并重新启动 Bridge；重新安装 NREL ZIP 不会更新服务器。该验证不包含真实 FAST.Farm 求解或训练。

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

# WFRL Blender 前端实现 · 0.3.1

当前发布版本为 **0.3.3**：[下载](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.3)，含雷达反馈与塔顶密封圈/焊缝随动修复。详细验证见发布核对文档；以下早期版本说明保留作为历史。

当前正式版本为 **0.3.1**，要求 **Blender 5.2+**。默认 Demo 是内置 v2 塔架柔性数据的三机 MAPPO 60 秒离线回放。Windows 与 macOS 共用同一个 [安装 ZIP](../dist/wfrl_blender-0.3.1.zip)。用户先看[前端总览](../前端readme.md)及[操作手册](../docs/blender/用户使用手册.md)；本文保留建模、数据合同与维护说明。

本地 0.3.2–0.3.5 是开发阶段标记，已汇总到正式 0.3.1。默认资源已切到同源柔性塔架包，旧 v1 读取兼容仍保留。当前发布检查见 [0.3.1 发布核对](../docs/blender/0.3.1发布核对.md)，不要将旧安装版、便携目录或历史检查视为当前 ZIP 的验证。

## 0.3.1 的主要结构

- `panels/farm_replay.py`：顶部共享播放、单步、复位、进度；下方“挠度 / 净空 / 工具”。切页不改时钟或相机，双视图入口已移除。
- `deflection.py`：T1 三叶片独立参考、实际结构叶尖、分量投影及仿真输出比较。参考包含同刻塔顶刚体运动，轴向没有源输出时只展示坐标差。
- `tower_motion.py`：读取并校验 v2 塔架截面与机舱变换，插值运动；`farm_flex.py` 同步塔筒网格、叶片、机舱及雷达挂载。
- `assets/mappo/`：包含全部离线数据，无需求解器、Bridge 或项目路径。`build_extension.py` 支持传入指定物理包并校验摘要，默认打包当前内置 v2 资源。

## 启动与模式切换

在本机继续使用原命令：

```bash
cd "/Users/eason/Desktop/wfcrl/wind farm RL"
scripts/blender/run_wfrl_macos.sh \
  --blender "/Applications/Blender.app/Contents/MacOS/Blender" \
  --python /opt/anaconda3/envs/wfrl-mac/bin/python \
  --scene scenes/turb3_row.yaml \
  --mpi /opt/homebrew/bin/mpiexec \
  --fastfarm /opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm
```

启动器预检环境并启动自己拥有的 Bridge；Blender 加载**已安装扩展**，停在 MAPPO 的 T1 侧前方首帧。默认状态为 **OFFLINE RESULTS**，Bridge 在 `127.0.0.1:8765` 待用，不自动连接或开始运行。这条入口需要完整后端环境；只看离线演示时直接打开扩展并点击“加载 MAPPO · 60 秒”，或用便携入口，无需 Python 后端、MPI、FAST.Farm 和网络。

需要后端时，进入 **Item → WFRL / CONNECTION → 后端连接（高级）**，选择 Replay 或 Interactive，再点 Connect / Reconnect。Item 不可见时先选中一片叶片。启动脚本在握手确认后发送 `scene.load`，自动加载命令指定的 YAML；确认 **CONNECTED / READY** 后，到 **WFRL / 后端运行** 设置参数并点击 Start Replay / Start Training。Replay 需兼容 checkpoint，例如项目中的 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt`。

切换模式会断开前端，需要重新连接。运行结束先 Stop 并等待 STOPPED，再选择 MAPPO · 60 秒返回离线回放。关闭该命令启动的 Blender 后，启动器清理自己拥有的 Bridge。普通 Blender 窗口不运行此 bootstrap，连接已有 Bridge 后需手动 Load & Validate Scene。

| 路径 | 前端显示与数据 | 是否启动求解／训练 |
| --- | --- | --- |
| MAPPO · 60 秒 | 随包随机阵风记录，九片独立形变、遥测与 B2 卡片 | 否 |
| 后端 Replay | 兼容权重推理与实时遥测；本机 YAML 为 8 m/s | FAST.Farm 推理，不做 PPO 更新 |
| Interactive | 实时训练状态与遥测 | 是，包含训练更新 |
| Formal Training | 正式训练流程、进度与指标 | 是，不提供逐步实时三维遥测 |
| 独立 normal／close 雷达回放 | 外部 18 秒结果包；前端刚性姿态示意 | 否 |

旧 66 秒本地 SYNTH 时间线、虚构功率、脚本事件与手动覆盖物理姿态入口已从当前 Demo 移除。Bridge 中仍保留旧 Backend Demo 逻辑，不能将其合成运动当成真实 FAST.Farm 验收；FLORIS 也仅支持该旧后端模式。

## 1. 场景如何搭建

前端采用 Python 程序化建模。几何输入来自扩展内的 [nrel5mw_geometry.json](wfrl_blender/assets/nrel5mw_geometry.json)，场景布局由 [scene_model.py](wfrl_blender/scene_model.py) 中的 `SceneDTO` 和 `TurbineDTO` 表达，包含机组编号、平面位置、风速、风向等信息。

[scene_builder.py](wfrl_blender/scene_builder.py) 的 `build_scene()` 创建 `WFRL_Scene` 集合，按机组生成塔筒和上部装配，再加入基础、编号、地形、来流箭头、尾流与传感器示意，最后配置环境和渲染。重新构建时会先清除该同名集合及符合条件的无用户引用资源；这是重建入口，不是普通切换镜头操作。

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

[__init__.py](wfrl_blender/__init__.py) 的 `load_demo_scene()` 创建基础场景后，调用 [farm_flex.py](wfrl_blender/farm_flex.py) 加载 [assets/mappo](wfrl_blender/assets/mappo/manifest.json)。结果包包括：

| 文件 | 作用 |
| --- | --- |
| `manifest.json` | `wfrl.farm-flex-review.v2`（兼容读取 v1）、REVIEW_ONLY、布局、片段范围及文件 SHA-256 |
| `geometry.npz` | 时刻、三机姿态和九片叶片各 19 个站点的形变变换 |
| `data.json` | 三台机组各自的运动记录、B2 测距、净空估计／真值与统计输入 |
| `telemetry.json` | 同次 OpenFAST 保存输出中的功率、转矩、叶根载荷等通道，含单位、源通道与文件摘要 |
| `tower-motion.npz` | 塔筒截面位置/朝向与机舱变换，共享时间轴；用于塔架及挂载运动 |
| `deflection-t1.json` | 同源 T1 结构叶尖面外/面内输出、高精度姿态与塔顶参考变换 |
| `source-run.json` | 本次运行来源、控制器和原始输出摘要 |
| `source-surfaces.json` | 生成形变数据所用的原始表面证据索引 |

`read_package()` 校验格式、机组列表、文件哈希、数组维度、有限值、时间轴和运动一致性；遥测还要与几何摘要及时间轴匹配。不能在校验失败时改用假数据。原始 VTP 不需要随播放器分发；生成新结果与日常播放是不同流程。

`FarmFlex` 为九片叶片分配独立 Mesh，根据相邻源时刻和相邻展向站点插值形变，并结合机组偏航与装配变换写回局部顶点。单机镜头只上传可见机组网格；切到全景时，在当前时刻更新全部九片再显示。这里保留物理变形幅度，展示网格的采样密度不等于求解器网格密度。

[tip_tracking.py](wfrl_blender/tip_tracking.py) 从变形网格的叶尖位置记录轨迹，B1/B2/B3 分别为橙／蓝／红，最多 540 点。它不是相机测量；同机切镜头保留轨迹，换机、寻址、重播时重置。

### 5.1.1 塔架、叶片参考与净空

v2 包将三机塔筒前后/侧向两阶弯曲的同源结果接入播放；塔底保持固定，机舱、叶轮及雷达安装随塔顶平移和转动。展示保持真实尺度，未放大摇晃幅度。叶片刚性参考含同刻塔顶整体变换，实际结构叶尖从变形网格参考顶点取得，二者之差投影到面外/面内/轴向基。

`deflection-t1.json` 的源输出不用于移动实际点或参考点，只用于对照。面外/面内显示仿真值、坐标差（m）及残差（mm）；轴向没有对应源输出。保存帧和插值帧分别标记；缺少 sidecar 显示不可核对，摘要或时间轴损坏拒绝加载。

净空真值采用叶尖参考点到同高度变形塔筒截面的距离，遮挡检查使用移动塔架几何；B2 固定标定估计没有补偿塔架弯曲。塔筒尚无专用虚影或位移卡片。

### 5.2 统一侧栏与固定时间轴

[panels/farm_replay.py](wfrl_blender/panels/farm_replay.py) 是 MAPPO 统一入口，顶部共享播放控制；挠度页放 T1 叶片选择、叶尖/叶轮/Down、虚影/分量和数值表；净空页放机组 Down 与 B2 卡片；工具页放视角、云台、遥测、环境、截图录制和重新加载。该模式隐藏重复 Camera 面板；其他模式保留 [panels/gimbal.py](wfrl_blender/panels/gimbal.py) 与 [panels/clearance.py](wfrl_blender/panels/clearance.py)。

时间读取复用 [clearance_replay.py](wfrl_blender/clearance_replay.py) 与结果包的 ReplayReader：

```text
t = segment.start_s
    + (frame_current + frame_subframe - frame_start) / timebase_fps
```

MAPPO 固定 `timebase_fps=60`，仿真片段为 117–177 s：40 Hz 源数据 2401 个时刻映射到显示帧 1–3601。输出 FPS 改变目标播放速度，同一帧对应的仿真时刻不变；不能把 60 Hz 时间轴写成稳定 60 FPS 性能结论。

停止保留当前记录；复位到起点并暂停；单步推进 1/60 仿真秒；从头重播与片尾重播恢复播放。片尾不会添加零转速或顺桨动画。镜头切换保留时间和播放状态，不改雷达标定。云台交互通过 [cameras.py](wfrl_blender/cameras.py) 实现；MAPPO 的 `farm_flex_view` 会结束活动云台控制并清除局部相机覆盖，避免全景被旧视图锁住。

`frame_change_post` 驱动读取和网格更新。保存重开时清除旧对象／场景指针缓存，按保存路径恢复读取器；随包 MAPPO 支持恢复到扩展数据目录。`.blend` 不会自动嵌入外部包，缺失或校验失败必须显示未就绪并清除读数。

### 5.3 遥测与 B2 数据合同

`farm_flex.record_telemetry()` 将三机姿态及额外通道交给 [charts.py](wfrl_blender/charts.py)，提供每通道最多 600 点的历史、曲线与 JSON 导出。变桨显示三叶片平均值，载荷为 B1 叶根弯矩；关闭采样会冻结读数，奖励未记录。导出保留来源和单位，不代表整个物理运行档案。

雷达卡片必须使用**同一条 B2 有效测量**的斜距、净空真值、估计和误差，不能把不同时间、叶片或其他光束拼在一起。误差为估计减真值；无效或过期显示缺失，不补零。读数年龄按仿真时钟计算，暂停时不随墙钟增长。

有效率分母是评估网格内预期样本；误差统计截至当前位置，整次漏测在片尾汇总。T1 当前有效率偏低，不能只展示 MAE 而省略缺测情况。数据仍是 REVIEW_ONLY；真值为仿真几何参考值，B2 是理想测距与简化算法估计，不能宣称现场精度、控制收益或数值收敛。算法与合同见 [雷达算法 README](../wfrl/lidar/README.md)。

### 5.4 独立 normal／close 雷达包

独立入口为 [open_clearance_demo.py](../scripts/blender/open_clearance_demo.py)，读取 [交付清单](../dist/lidar-delivery.json) 中 `normal-v1.1`、`close-v1.1` 两个外部包。历史演示 `.blend` 缺失时会重建场景；结果包仍必须完整。该入口使用工作区源码，不替换已安装扩展。

`clearance_replay.load(scene, path, demo)` 校验机组、尺寸、标定及片段身份，退出现有柔性回放并建立对应读取器。“正常测量／较小净空”切片后从起点播放；“测量区侧视／风场总览”仅切镜头。这两段画面是刚性姿态示意，后端数值包含柔性形变，不能把这句话套用到三机 MAPPO 网格动画。

此独立路径采用固定全局塔原点与刚性塔假设，现有生产脚本要求零偏航；不是任意父级旋转、缩放或浮式机组的通用坐标变换。三机 MAPPO 使用其独立的偏航与形变数据合同。修改相机方向不会改变任何一条路径的物理标定。

### 5.5 实时连接与控制生命周期

项目侧 [wfrl_launcher.py](../scripts/blender/wfrl_launcher.py) 负责环境预检、Bridge 生命周期与 Blender 进程；[wfrl_blender_bootstrap.py](../scripts/blender/wfrl_blender_bootstrap.py) 导入 `bl_ext.user_default.wfrl_blender`，写入路径配置、加载默认 MAPPO，并在用户主动连接后记录会话及加载 YAML。

扩展 [runtime.py](wfrl_blender/runtime.py) 和 [transport.py](wfrl_blender/transport.py) 维护连接、协议状态与回传。[panels/status.py](wfrl_blender/panels/status.py) 管连接与诊断，[panels/run.py](wfrl_blender/panels/run.py) 是唯一后端运行按钮入口。CONNECTED 表示握手；实际运行还需 READY 后显式 Start，并观察 RUNNING 与不断更新的真实快照。

后端 [backend_session.py](../wfrl/blender_bridge/backend_session.py) 只在新的控制步边界处理暂停和单步，预热或重复第 0 步的进度通知不能重复消耗单步许可。[server.py](../wfrl/blender_bridge/server.py) 在接收新连接前回收已有连接的 EOF，允许断开后立即重连，同时保留活动客户端独占。

实时 `rotor_speed` 当前由功率／效率／转矩／齿轮比推算，保留 SYNTH 与 `FastFarmDriver._rotor_speed` 来源；yaw、pitch、power、torque 的 DIRECT 标签不能推广到所有通道。离线 MAPPO 使用保存的转速记录。真实后端场景与随包随机阵风记录属于不同运行。

## 6. 修改、安装与验证

### 6.1 维护入口

| 修改目标 | 优先入口 |
| --- | --- |
| 基础几何与装配 | `turbine_geometry.py`、`scene_builder.py` |
| 材质与机械附件 | `materials.py`、`mechanical_details.py` |
| 雷达外观与光束 | `clearance_visual.py` |
| 三机形变、数据校验与遥测接入 | `farm_flex.py`、`assets/mappo/` |
| MAPPO 侧栏、镜头与播放控制 | `panels/farm_replay.py` |
| 叶尖轨迹 | `tip_tracking.py` |
| 公共时钟、独立雷达回放与恢复 | `clearance_replay.py`、`panels/clearance.py` |
| 曲线与历史导出 | `charts.py`、`panels/telemetry.py` |
| 相机与云台 | `cameras.py`、`panels/gimbal.py` |
| 连接／运行状态 | `runtime.py`、`transport.py`、`panels/status.py`、`panels/run.py` |
| 原命令启动行为 | 项目侧 `scripts/blender/wfrl_launcher.py`、`wfrl_blender_bootstrap.py` |
| 后端求解／控制边界 | 项目侧 `wfrl/blender_bridge/` |
| 测距算法与物理结果生产 | `wfrl/lidar/`、`scripts/lidar/`，见 [算法说明](../wfrl/lidar/README.md) |

源码三机预览入口为 [open_farm_flex.py](../scripts/blender/open_farm_flex.py)。正式原命令走已安装扩展，不自动读取工作区扩展修改。修改扩展代码或打包资源后，从项目根目录运行：

```bash
python3 scripts/blender/build_extension.py
```

安装新生成的 ZIP 并重启 Blender，再核对 ZIP、已安装文件和便携运行时；不要只凭版本字符串判断一致。纯文档或项目侧 bootstrap／Bridge 修改无需重建 ZIP。纯显示修改复用现有结果包，不需要重跑 FAST.Farm；若修改要成为新的物理输入，需同步核对求解器机型与坐标合同。

### 6.2 回归入口与当前证据

| 验证内容 | 入口 |
| --- | --- |
| 完整 MAPPO 加载、控制、遥测、保存重开与缺包恢复 | [mappo_demo_regression.py](tests/blender/mappo_demo_regression.py) |
| 三机独立形变 | [farm_flex_regression.py](tests/blender/farm_flex_regression.py) |
| 侧前方／Down／全景与轨迹保留 | [farm_front_view_regression.py](tests/blender/farm_front_view_regression.py) |
| 原命令默认离线、主动连接与自动场景加载 | [launcher_mode_regression.py](tests/blender/launcher_mode_regression.py)，需匹配的待用 Bridge 与 `WFRL_*` 环境配置 |
| 启动器宿主测试 | [test_launcher.py](tests/test_launcher.py) |
| 雷达编辑模式重载、卡片交互 | [clearance_edit_mode_regression.py](tests/blender/clearance_edit_mode_regression.py)、[clearance_ux_regression.py](tests/blender/clearance_ux_regression.py) |
| 后端暂停／单步与立即重连 | [test_part4_backend.py](../tests/blender_bridge/test_part4_backend.py) |

按脚本说明设置独立的 `WFRL_TEST_OUTPUT`，以及所需结果包／扩展路径；不要覆盖既有验收证据。宿主测试通过不能代替 Blender 后台回归，后台回归也不能代替实际 GUI、性能和物理验收。

2026-09-16 已完成的发送前检查包括：151 项前端与数据宿主测试；隔离 ZIP 和便携回放回归；54 项后端相关测试及 27 个子检查；已安装扩展的真实 FAST.Farm 短测（暂停、单步、立即重连、继续、停止、复位）；原命令的实际窗口模式切换。ZIP／安装版的 68 个文件及便携 inventory 的 87 项均已核对。

后端测试子集明确排除了依赖已清理原始 VTP 的旧单机测试文件；完整后台测试曾有 3 项因此失败，未计为通过。尚未验收 Windows、长时训练、稳定 60 FPS、全部录制／渲染输出、数值收敛或现场精度。完整日志与运行条件见 [发送前检查](../docs/blender/0.3.0发送前检查.md)。以上为历史 0.3.0 验证范围。本次 0.3.1 使用新数据重建 ZIP，实际验证单列于 [0.3.1 发布核对](../docs/blender/0.3.1发布核对.md)。

# NREL 5MW / WFRL Blender 前端实现 · 0.3.17.1

更新日期：2026-10-08。本文说明 **NREL 5MW 0.3.17.1 安装包与本地规范源码**的实现和维护，要求 **Blender 5.2+**。用户操作、默认相机参数和演示建议见[详细前端说明](../前端readme.md)；待实施事项见[前端更新计划](../前端更新计划.md)。

安装使用 [wfrl_blender-0.3.17.1.zip](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.17.1/wfrl_blender-0.3.17.1.zip)。在 Preferences → Get Extensions → Install from Disk 安装启用并重启后，按 N → MAPPO → **加载 MAPPO · 60 秒 → 同源纹理同步对照** 查看同源灰度纹理；**独立合成纹理样例**保留原独立游标；原 **打开几何重建示例…** 保留便携几何分屏。ZIP 包含示例 `.blend`、MAPPO 数据和四张 packed 天空／地表纹理，无需作者机器路径或在线后端；历史 Release 保留。

[0.4.0 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.4.0) 另提供 `gw184_three_camera_defects-0.4.0.zip`：基于 DTU 10MW 分布和 FFA 翼型的 GW184 尺寸合成刚性模型、固定三相机及六类缺陷编辑器，完整解压后运行；它与 NREL 扩展分别运行；NREL 0.3.16 已移植六类缺陷编辑器，两套模型与配置身份分别保留。操作和模型范围见 [GW184 说明](../docs/blender/GW184三相机与缺陷编辑器.md)，项目维护见 [项目 README](../projects/gw184-single/README.md) 与 [缺陷编辑器说明](../projects/gw184-single/DEFECT_EDITOR.md)。

0.3.13 曾同步完整 NREL 源码与构建资源，这是对应版本的历史事实。**0.3.17.1 仅更新 ZIP 附件**，新增实现保存在本地规范源码，未提交／推送；标签指向远端 `main` 的 `9d7b5596a781b8ea5040d18b2f009268313a8821`，自动 Source code 不能代替当前安装 ZIP。包内兼容版本为 `0.3.17+1`，build metadata 不用于自动升级排序，须手动安装并重启。历史归档不追改；GW184 实现继续随独立 ZIP 提供。版本与分层验证见[当前发布状态](../docs/blender/发布状态与验证范围.md)与[0.3.17.1 发布核对](../docs/blender/releases/0.3.17.1发布核对.md)。

0.3.17 安装包增加保存的 v0.2 重建结果与三片 RGBA 纹理导入、packed 恢复和 shader 增强，并随包提供独立 120 样本 `synth_tex` 表面纹理对照。仅集成结果可视化；外部纹理辅助几何／健康 loft 求解器不随扩展打包。最终包验证见 [0.3.17 发布核对](../docs/blender/releases/0.3.17发布核对.md)。

默认内置预弯 v3 三机 MAPPO 60 秒离线记录。相机和材质由 Blender 即时绘制，运动与形变来自保存数据；三相机窗口没有重新运行 FAST.Farm。数据仍为 **REVIEW_ONLY**。当前 S2/S3 已验证完整过叶配对，不能与旧 normal/close 或 v3 B2 的固定三束覆盖率限制混为同一结论。

**0.3.16：T1/B1 默认修补痕迹。** 安装 ZIP 包含约 **1.4 × 0.7 m** 的浅灰色补漆外观，位于原始 T1 第一片叶片距叶根约 34 m 处。普通 MAPPO 加载、包含 T1 的 YAML 场景建立及原有 `.blend` 重开均接入这一默认配置。原 GitHub 0.3.15 ZIP 保留历史内容；日常安装不会随本次发布自动更新。

实现由 `blender_frontend/wfrl_blender/repair_marks.py` 管理：`scene_builder.build_scene()` 创建时添加，刚性旧场景由加载回调补建，`FarmFlex` 更换参考网格后、首次形变前重新绑定。保存的三角形索引和重心权重通过原生 Geometry Nodes 跟随旋转、变桨及柔性形变，无逐帧 Python 重建；隐藏辅助对象采样原网格，原叶片保留其展示修改器。仅原始 `WFRL.Turbine.T1.Blade1` 带标记，重建叶片与 GW184 独立模型不受影响。这是合成外观，不改变气动、刚度、载荷或保存的测量数据，默认修补痕迹与本版 NREL 缺陷编辑器各自保留配置。

0.3.16 安装包与本地规范源码的 NREL 缺陷编辑器位于 `wfrl_blender/nrel_defects/`，由 MAPPO 的「NREL 5MW · 叶片缺陷」面板启用。复用 GW184 的六形态、严格配置和两阶段事务，在 NREL 健康展示 loft 上重新绑定；`farm_flex.rebind_blade()` 重建修改拓扑的静态坐标和形变缓存，`deform_points()` 用相同保存运动驱动支持区域及辅助轮廓。末尾结构/展示叶尖索引、原材质标记和默认补漆附着均保留。保存 `.blend` 后由原回放加载器建立健康参考，再恢复提交 JSON，临时预览不写入保存状态。源码启动、G/A 导出和验证见 [NREL 缺陷编辑器说明](../docs/blender/NREL缺陷编辑器.md)。本次公开 Release 仅提供 ZIP，不更新日常安装；安装后需重启。

## 当前主要结构

- `panels/farm_replay.py`：共享播放、单步、复位、进度及功能页，View 页组织观察相机。切视角保留回放时刻。
- `split_reconstruction.py`、`split_reconstruction_timing.py`、`split_reconstruction_ui.py`：保存分屏生命周期、独立视口、固定采样映射、便携示例打开与样本步进；规范资源为 `assets/examples/mappo_reconstruction_split.blend`。
- `split_mappo_texture.py` 与 `assets/blade_recon_mappo_tex/`：0.3.17.1 已发布的同源纹理同步入口，使用原两相机实验的 601 个保存状态与动态标定生成固定 RGBA 图集，共用 MAPPO 的 60 Hz 时间轴；`split_surface_texture.py` 的 120 样本合成实验继续使用独立游标。`split_texture_settings.py` 保存各来源的显示选择并在失败切换时回滚状态。来源和模型等价见[首轮交付记录](../outputs/mappo-same-source-texture-20261007-a01/交付记录.md)，同功能修订的安装与回归见[自检与修复](../outputs/mappo-same-source-texture-20261007-a01/self-check/自检与修复.md)。
- `deflection.py`：T1 三叶片独立未受载参考、实际结构叶尖及根系 xyz 分量。参考随同刻塔顶与转子运动；源通道对照按实际可用性显示。
- `tower_motion.py`：读取并校验 v2 塔架截面与机舱变换，插值运动；`farm_flex.py` 同步塔筒网格、叶片、机舱及雷达挂载。
- `assets/mappo/`：包含默认预弯 v3 离线记录，无需求解器、Bridge 或项目路径；数据格式代次与扩展版本号分开管理。
- `assets/dual_beam/` 与 `assets/dual_beam_tls/`：两个结果层共享 `assets/mappo/` 的 40 Hz 源数据；默认 `hub-axis.v1`，显式选择 `hub-tls.v1`。
- `stacked_camera_rig.py` 与 `panels/stacked_camera_rig.py`：共盒三相机、支架安装端贴合、盒体末端转向、确认/取消/撤销。
- `custom_cameras.py`：槽位、相机参数、布局及兼容处理；`native_camera_views.py`：独立原生单路／三路观察窗口。
- `custom_camera_capture.py`：同刻或时间序列原图采集；`custom_camera_preview.py`：保留预览计算与质量恢复支持，0.3.12 已移除侧栏“精确材质预览”入口，普通原生观察不依赖逐帧 PNG 采集。
- `assets/cameras/t1-three-camera-default.json`：0.3.9 默认取景配置，加载 MAPPO 时应用；显式重载与普通播放分开处理。

## 当前三相机维护要点

三台相机共同安装在 T1 机舱外伸支架末端，光心随整个盒体运动，各路保留独立方向与视场。贴合约束作用于支架安装端，不能把盒体背面贴住机舱当作默认安装；自由 XYZ 操作会解除贴合约束。

当前目标是同一叶片的连续视场与相邻重叠，不要求三等分，不按目标叶长范围裁切图像。C2 可能因透视关系看到较远叶尖；这不再按旧的“只有 C3 见叶尖”判失败。默认参考时刻为 117 s，固定相机在旋转中允许叶片进出画面，不自动追踪叶片。

原生窗口用临时观察相机表达各路视场，共用场景和时钟。默认 GRID 为 2×2，第四格是 `installation_schematic.py` 生成的静态安装示意；STRIP 为三列对照。两种布局均有播放／暂停、单步及单路放大。独立 WATCH 和放大固定使用 SOLID 材质颜色显示，无单路画质切换；返回三路恢复其原画质。PNG 采集使用独立原始质量设置，异常退出也须恢复回放与预览状态。

相机视锥并集、射线可见性与拼接成功是不同检查。参考姿态的连续投影和重叠已有证据；根部连接件、机舱和叶片自身仍会遮挡局部表面，完整无遮挡尚未通过。不能隐藏真实结构制造几何通过结果，也不能将此仿真安装作为实体标定。

`playback_benchmark.py` 区分完成、超时、中止、错误与缺绘制；每路须有推进和终止证据，不能只看 FPS 或一个 PASS 字段。旧停止异常根因仍未定位，当前完整片段性能与跨平台稳定性继续单独验证。

## 启动与模式切换

在已配置完整后端环境的 macOS 上使用以下入口，先把示例路径替换为自己的路径：

```bash
cd "/path/to/wind farm RL"
scripts/blender/run_wfrl_macos.sh \
  --blender "/Applications/Blender.app/Contents/MacOS/Blender" \
  --python /opt/anaconda3/envs/wfrl-mac/bin/python \
  --scene scenes/turb3_row.yaml \
  --mpi /opt/homebrew/bin/mpiexec \
  --fastfarm /opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm
```

启动器预检环境并启动自己拥有的 Bridge；Blender 加载**已安装扩展**，停在 MAPPO 的 T1 侧前方首帧。默认状态为 **OFFLINE RESULTS**，Bridge 在 `127.0.0.1:8765` 待用，不自动连接或开始运行。这条入口需要完整后端环境；只看离线演示时直接打开扩展并点击“加载 MAPPO · 60 秒”，或用便携入口，无需 Python 后端、MPI、FAST.Farm 和网络。

匹配的 0.3.17.1 安装版会同时建立推荐三相机配置，默认关闭叶尖轨迹和挠度辅助物。主视图仍先显示 T1 总览，进入 **View → 三相机 → 三路对照** 查看。仅更新仓库 README 不会改变已运行的 Blender 扩展。

需要后端时，进入 **Item → WFRL / CONNECTION → 后端连接（高级）**，选择 Replay 或 Interactive，再点 Connect / Reconnect。Item 不可见时先选中一片叶片。启动脚本在握手确认后发送 `scene.load`，自动加载命令指定的 YAML；确认 **CONNECTED / READY** 后，到 **WFRL / 后端运行** 设置参数并点击 Start Replay / Start Training。Replay 需兼容 checkpoint，例如项目中的 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt`。

切换模式会断开前端，需要重新连接。运行结束先 Stop 并等待 STOPPED，再选择 MAPPO · 60 秒返回离线回放。关闭该命令启动的 Blender 后，启动器清理自己拥有的 Bridge。普通 Blender 窗口不运行此 bootstrap，连接已有 Bridge 后需手动 Load & Validate Scene。

| 路径 | 前端显示与数据 | 是否启动求解／训练 |
| --- | --- | --- |
| MAPPO · 60 秒 | 随包随机阵风记录，九片独立形变、遥测与 B2 卡片 | 否 |
| 双束净空 / S1、TLS 候选 | 随包旧法或 TLS 结果层、外部可携带包，S2/S3 配对和独立 S1 状态 | 否 |
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
| `manifest.json` | 当前 `wfrl.farm-flex-review.v3`、REVIEW_ONLY、布局、片段范围、参考定义及文件 SHA-256 |
| `geometry.npz` | 时刻、三机姿态和九片叶片沿展向的形变变换 |
| `data.json` | 三台机组各自的运动记录、B2 测距、净空估计／真值与统计输入 |
| `telemetry.json` | 同次 OpenFAST 保存输出中的功率、转矩、叶根载荷等通道，含单位、源通道与文件摘要 |
| `tower-motion.npz` | 塔筒截面位置/朝向与机舱变换，共享时间轴；用于塔架及挂载运动 |
| `deflection-t1.json` | 同源 T1 结构叶尖分量输出、高精度姿态与塔顶参考变换；当前 v3 按根系 xyz 解释 |
| `source-run.json` | 本次运行来源、控制器和原始输出摘要 |
| `source-surfaces.json` | 生成形变数据所用的原始表面证据索引 |
| `blade-reference.json`、`reference-surfaces.npz` | v3 独立零载荷静止求解参考及对应表面，用于参考几何与实际形变对照 |

`read_package()` 校验格式、机组列表、文件哈希、数组维度、有限值、时间轴和运动一致性；遥测还要与几何摘要及时间轴匹配。不能在校验失败时改用假数据。原始 VTP 不需要随播放器分发；生成新结果与日常播放是不同流程。

`FarmFlex` 为九片叶片分配独立 Mesh，根据相邻源时刻和相邻展向站点插值形变，并结合机组偏航与装配变换写回局部顶点。单机镜头只上传可见机组网格；切到全景时，在当前时刻更新全部九片再显示。这里保留物理变形幅度，展示网格的采样密度不等于求解器网格密度。

[tip_tracking.py](wfrl_blender/tip_tracking.py) 从变形网格的叶尖位置记录轨迹，叶片 1／2／3 分别为红／绿／蓝，相邻两圈定格对比 2 个仿真秒后淡出。轨迹不是相机测量；同机切镜头保留轨迹，换机、寻址、重播时按生命周期重置。0.3.9 默认关闭轨迹，需要时主动打开。

### 5.1.1 塔架、叶片参考与净空

当前包包含三机塔架同源运动；塔底保持固定，机舱、叶轮及雷达安装随塔顶平移和转动。展示保持真实尺度，未放大摇晃幅度。v3 叶片参考来自独立零载荷静止求解，随同刻塔顶与转子运动；实际点与参考点之差投影到随变桨的叶根坐标系 xyz。不能把已经变形的第一帧当作未受载参考。

`deflection-t1.json` 保留同源输出与参考所需信息。原仿真输出对照按可用通道和实际参考系解释，不能把历史面外／面内标签直接等同于当前根系 xyz；缺少通道时不制造对照值。保存帧和插值帧分别标记，缺少对照资源或摘要／时间轴损坏时必须明确提示。

v3 净空真值定义为叶片末截面表面到同一全局高度移动塔筒截面的最小距离，不是整片叶片到塔筒的最小距离，也不是仅取单个叶尖参考点。遮挡检查使用移动塔架几何；B2 固定标定估计没有补偿塔架弯曲。塔筒尚无专用虚影或位移卡片。

### 5.2 统一侧栏与固定时间轴

[panels/farm_replay.py](wfrl_blender/panels/farm_replay.py) 是 MAPPO 统一入口，顶部共享播放控制；挠度页放 T1 叶片选择、叶尖/叶轮/Down、虚影/分量和数值表；净空页提供默认双束 / S1、TLS 候选与旧 B2 模式切换、机组 Down 和对应卡片；工具页放视角、云台、遥测、环境、截图录制和重新加载。该模式隐藏重复 Camera 面板；其他模式保留 [panels/gimbal.py](wfrl_blender/panels/gimbal.py) 与 [panels/clearance.py](wfrl_blender/panels/clearance.py)。

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

旧 B2 模式的雷达卡片必须使用**同一条 B2 有效测量**的斜距、净空真值、估计和误差，不能把不同时间、叶片或其他光束拼在一起。误差为估计减真值；无效或过期显示缺失，不补零。读数年龄按仿真时钟计算，暂停时不随墙钟增长。

有效率分母是评估网格内预期样本；误差统计截至当前位置，整次漏测在片尾汇总。T1 当前有效率偏低，不能只展示 MAE 而省略缺测情况。数据仍是 REVIEW_ONLY；真值为仿真几何参考值，B2 是理想测距与简化算法估计，不能宣称现场精度、控制收益或数值收敛。算法与合同见 [雷达算法 README](../wfrl/lidar/README.md)。

### 5.3.1 双束与独立 S1 数据合同

当前 0.3.17.1 安装包延续默认 `hub-axis.v1` 与显式可选 `hub-tls.v1` 两份双束结果层及读取器，共享原 `assets/mappo/` 40 Hz 源几何。`farm_flex.py` 按 `measurement_mode=dual_beam` 接入，`panels/farm_replay.py` 提供“双束净空 / S1”“TLS 候选 / S1”与旧 B2 切换；读取及可携带打包由规范源码 `wfrl/lidar/dual_beam_replay.py`、`dual_beam_package.py` 负责，构建时生成扩展内置副本。

S2/S3 按同一保存时刻、同一叶片的有效命中配对，S1 有效叶片命中独立报警，不能用双束无效来抑制 S1。读数、报警和累计历史共用仿真时钟，寻址不重复累计；无效值保持缺失。可携带包包含自身 `source/` 并校验完整源数据及结果摘要，移走后不依赖开发机绝对路径。损坏或缺包须清空旧状态。

2026-09-30 直接核对原 0.3.12 ZIP 内置记录：

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

当前 0.3.17.1 扩展延续前端连接、协议和运行状态修复；后端服务器、训练启动器和 MPI 环境不随扩展 ZIP 安装。后端 [backend_session.py](../wfrl/blender_bridge/backend_session.py) 只在新的控制步边界处理暂停和单步，预热或重复第 0 步的进度通知不能重复消耗单步许可。[server.py](../wfrl/blender_bridge/server.py) 在接收新连接前回收已有连接的 EOF，允许断开后立即重连，同时保留活动客户端独占。

公开源码提交 [`1c2b334b`](https://github.com/snode11/wind-farm-blender/commit/1c2b334b583dfcd1219911c12804271fe0aa6a66) 更新 [isolated_trainer.py](../wfrl/blender_bridge/isolated_trainer.py) 的 `launcher_environment`：移除继承的 OMPI/PMI/PMIX/OPAL/PRTE 等 MPI 作业身份变量，保留显式 BTL/PML 传输配置；macOS 在没有显式接口配置时使用 `self,tcp` / `lo0` 默认值。9 项宿主回归通过。需要此修复时，更新后端项目源码到该提交或更新版本，并重新启动 Bridge；重新安装 NREL ZIP 不会更新服务器。该验证不包含真实 FAST.Farm 求解或训练。

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
| 共盒三相机、支架贴合与默认布局 | `stacked_camera_rig.py`、`panels/stacked_camera_rig.py`、`assets/cameras/t1-three-camera-default.json` |
| 单路／三路观察窗口与预览质量 | `native_camera_views.py` |
| 相机布局、参数、原图与输出信息 | `custom_cameras.py`、`custom_camera_capture.py`、`panels/custom_camera_output.py` |
| 连接／运行状态 | `runtime.py`、`transport.py`、`panels/status.py`、`panels/run.py` |
| 原命令启动行为 | 项目侧 `scripts/blender/wfrl_launcher.py`、`wfrl_blender_bootstrap.py` |
| 后端求解／控制边界 | 项目侧 `wfrl/blender_bridge/` |
| 测距算法与物理结果生产 | `wfrl/lidar/`、`scripts/lidar/`，见 [算法说明](../wfrl/lidar/README.md) |

源码三机预览入口为 [open_farm_flex.py](../scripts/blender/open_farm_flex.py)。正式原命令走已安装扩展，不自动读取工作区扩展修改。0.3.17.1 的规范修改位于本地 `blender_frontend/wfrl_blender/`，包括便携示例；保留完整 `assets/` 和仓库级 `wfrl/`，不要只复制 Python 文件。公开 `v0.3.13` 可按其历史范围构建，公开 `v0.3.15`、`v0.3.16`、`v0.3.17` 与 `v0.3.17.1` 未同步各自新版扩展实现源码。使用 Python 3.11+，在完整且匹配的源码目录从项目根目录运行以下维护命令；本次文档更新没有执行构建：

```bash
python3 scripts/blender/build_extension.py
```

构建按当前 manifest 版本生成 ZIP、SHA-256 文件与逐文件 inventory。当前0.3.17.1为169文件、98,992,149bytes，包内0.3.17+1，SHA-256 `cfaa108646d77845bfe2b07ff3f052fbaf0529231836ee09b7423f0e00345df3`；见[当前发布核对](../docs/blender/releases/0.3.17.1发布核对.md)。历史0.3.17最终交付包为 `outputs/release-0.3.17-20261007/validation/package/wfrl_blender-0.3.17.zip`，157 文件、97,836,884 bytes；SHA-256 为 `1a43e2949f14fbc7e79c2933fc84d74eedaff71dd61c7ddd5afbd90effb7f174`。仅 ZIP 上传 GitHub，清单与 SHA256 保留本地。以下 0.3.16 包信息保留其历史范围。0.3.16 最终交付包保留在 `outputs/blender-validation/nrel-defects-release-0.3.16-20261005/package/wfrl_blender-0.3.16.zip`，为 145 文件、95,604,329 bytes；SHA-256 为 `d7a95e3942e60ccc95d5c97052f478eeafab6df0f2a7f4daa7a2fd5a8fec1ba2`。ZIP 包含 MAPPO 源资源、旧法与 TLS 结果层、便携示例、NREL 缺陷编辑器及默认修补痕迹，以及由仓库规范源码生成的读取器和协议副本，离线安装不依赖源码路径。安装新生成的 ZIP 并重启 Blender，再按任务核对实际安装模块与资源；不要只凭版本字符串判断一致。纯文档或项目侧 bootstrap／Bridge 修改无需重建 ZIP。纯显示修改复用现有结果包，不需要重跑 FAST.Farm；若修改要成为新的物理输入，需同步核对求解器机型与坐标合同。

### 6.2 回归入口

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

当前0.3.17.1的隔离安装、三相机／投影和新进程后台embedded／packed恢复见[当前发布核对](../docs/blender/releases/0.3.17.1发布核对.md)；此前同功能代码的可见窗口与QA另列，不声称改版本后重做全功能窗口或FPS。历史0.3.17的独立合成纹理验证见[原发布核对](../docs/blender/releases/0.3.17发布核对.md)。0.3.16 的隔离安装、缺陷编辑器保存重开与既有失败边界见[本版发布核对](../docs/blender/releases/0.3.16发布核对.md)；0.3.15 的便携恢复、注册生命周期与实际窗口行为见[对应历史核对](../docs/blender/releases/0.3.15发布核对.md)；0.3.13 当时的测试、构建与安装检查保留于[历史发布核对](../docs/blender/releases/0.3.13发布核对.md)。历史安装包的测试数量和通过结论只适用于对应版本，可查[已发布记录](../CHANGELOG.md)和[当前发布状态](../docs/blender/发布状态与验证范围.md)。不能将历史后端短测或便携回归合并为本版验收；以下保留 0.3.9 的历史基线。

### 6.3 0.3.9 历史验证与性能边界

0.3.9 当时发布的源码、ZIP 校验值和安装验证见 [0.3.9 发布核对](../docs/blender/releases/0.3.9发布核对.md)。以下为 2026-09-27 发布前的相机与播放检查记录，不扩展为全部平台或后端验收。

| 层级 | 记录与边界 |
| --- | --- |
| 发布前相机与安装交互 | 110 项相关宿主测试通过；源码及安装版检查默认布局、支架转向、导入撤销、原生窗口和采集。发布 ZIP 的文件核对和回归范围另见发布核对 |
| 可见窗口播放 | Blender 5.2.1、820×473 逻辑窗口、流畅模式、5 秒预热；完整 60 秒片段单路约 12.43 FPS，独立三路约 11.33 FPS，三路帧序列一致、约 1 倍仿真速度；P95 帧间隔约 118 ms |
| 播放异常 | 首次单路切三路仅推进约 1.28 秒后停住，100 秒超时；独立三路复测完成不能抹除这次异常。原因尚未定位 |
| 验收脚本限制 | 0.3.9 当时的脚本超时也写 PASS；0.3.10 已修正失败判定，原历史结果不能因此追认为通过 |
| 几何取景 | 参考姿态的三路视场并集及相邻重叠已检查；根部与极尖端局部不可见，完整表面无遮挡未通过 |

播放数字来自 `POST_PIXEL` 回调记录的不同场景帧，不是显示器扫描频率，也不是全屏、投影或高清模式性能承诺。源数据 40 Hz、显示时间轴 60 Hz、实际绘制帧率分别报告。

这些播放记录来自开发机可见窗口，原始性能日志不是扩展安装 ZIP 的内容。复测时应保留完整片段是否结束、超时与暂停状态、各路帧序列和测量条件；不要将验收脚本的单个 PASS 字段作为流畅性结论。

0.3.17 安装包纳入第1、2阶段的 `texture_path`、三片 packed RGBA、atlas UV 与纹理／证据色切换。独立场景和嵌入恢复沿用原生命周期；`blade_recon0.2/blender_view.py` 另可烘焙原生几何与证据动画。图集周向周期、展向钳制像素中心，按纹理元数据及 packed 图片摘要恢复。公开 0.3.16 附件保留历史内容；阶段开发来源与证据见 本地 `blade_recon0.2/docs/阶段1、2运行与验证.md`（算法研究档案，不随公开文档提供）。

## Blade Recon 叶片三维重建开发入口

三维视口 N 侧栏增加独立 **叶片三维重建** 标签，与 Item、MAPPO、View 并列。通过 **加载合成样例** 或 **导入 recon.json** 创建单独的重建回放场景，保留原风场场景及其回放位置；**返回原场景** 只切换当前窗口的场景。输入使用 `blade_recon` 算法输出的模型参数，Blender 按保存时刻生成三维表面，不在查看时重新拟合。

实现入口为 `wfrl_blender/blade_recon_review.py` 和 `wfrl_blender/panels/blade_recon.py`；类注册及生命周期由 `wfrl_blender/__init__.py` 接入扩展现有的注册和清理机制。重建回放处理器只响应自己的场景，嵌入的输入内容用于保存重开；恢复动态回放需要启用匹配扩展或启动入口。扩展内容变更进入安装版时需要重建 ZIP 并重启 Blender，源码检查、实际安装与窗口检查分别报告。

图例区分与已拟合轮廓相符的采样标记和模型推断部分。观测标记不等于独立三维测量或整个表面可见；合成入口明确标记 **SYNTHETIC**。可选真值及相机输入、视角、原生时间轴播放和保存重开说明见 [Blade Recon 使用说明](../docs/blender/BladeRecon可视化使用说明.md)。

保存的 MAPPO／重建同步分屏由 `split_reconstruction.py` 管理视口角色、进入/退出、布局恢复和尺寸变化适配；`split_reconstruction_ui.py` 提供紧凑时间显示及采样步进，固定映射在 `split_reconstruction_timing.py`。具备 `split_reconstruction_review` 标记和 `SplitRecon.*` 保存对象的场景自动接入，普通场景不受影响。相机操作在执行时排除右侧重建视口，生命周期定时器仅处理布局和尺寸变化，不事后修复相机覆盖。工作区切换不重建无关工作区；重新载入文件后清除旧RNA引用并恢复已有对照。显式完整取景用解析透视边界容纳 T1 与叶轮旋转范围，右侧缓存全部保存姿态；自动尺寸适配保留用户左侧导航，等待重绘后才标为就绪。退出合并无论保留哪块区域，均恢复左侧原显示样式。

分屏元数据区分仿真起点、固定时间轴频率、采样步长和保存样本数。原601样本片段以60Hz时间轴、每6帧一个10Hz样本保持，界面分别显示播放时间、采样时间及保持时长；改变目标播放FPS不改映射。验证入口为 `tests/test_split_reconstruction*.py` 和 `tests/blender/split_reconstruction_window.py`，后者必须打开已有分屏副本并使用实际安装模块，不重建求解结果。


### 0.3.15 便携分屏资源与维护合同

`assets/examples/mappo_reconstruction_split.blend` 是规范示例源，随 ZIP 整体提供，不能作为中间产物清理。右侧 `SplitRecon.B1/B2/B3` 各保存 **601 个 absolute shape keys**，以 **CONSTANT holds** 驱动，**无 drivers**；四张天空／地表图片已 packed，场景没有 linked libraries。左侧保留 40 Hz MAPPO 源，右侧重建保存样本为 10 Hz；共享固定时间轴为 60 Hz、frame 1–3601，每 6 帧一个保存样本。frame 52 的播放时刻为 117.850 s，实际保持样本为 117.800 s，保持时长为 50 ms。

便携场景保存 `wfrl_farm_flex_path=__WFRL_BUNDLED_MAPPO__`，并保存 `wfrl_farm_manifest_sha256=d3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde`。这个非空 marker 使既有 `farm_flex.saved_package()` 按 manifest SHA-256 定位当前安装的 `assets/mappo/`，不更换数据来源；运行恢复后记录当前真实安装路径。不要清空保存路径，也不要替换为读取器不支持的 `//` 相对路径。manifest 不匹配时按现有缺包规则处理，不能放宽校验。

`split_reconstruction_ui.packaged_example_path()` 从实际安装模块目录寻找资源；打开 operator 使用 `wm.open_mainfile(INVOKE_DEFAULT)` 保留 Blender 原生未保存确认，不用绕过确认的执行方式。进入／退出、恢复布局、完整取景、窗口缩放适配及保存重开由现有分屏生命周期处理；`cameras.py` 和 MAPPO 面板操作时排除右侧重建视口。样本前后步进先暂停相关窗口并钳制端点，不改变输入数据或新增观测。

本版保存的 `registration_lifecycle.json` 记录重复注册、卸载和再次注册 PASS。日常安装实际模块为 `bl_ext.user_default.wfrl_blender`；隔离 ZIP 安装验证为 `bl_ext.wfrl_frontend.wfrl_blender`。证据来自 macOS Blender 5.2.1 LTS，本次文档维护仅静态检查，不重新运行 Blender、构建或求解。实际窗口行为通过不表示稳定 60 FPS、重建精度或 Windows/Linux 已验收。

0.3.17 纹理分屏复用 `split_reconstruction.py` 的原生布局和 `split_reconstruction_ui.py` 的现有侧栏。`split_surface_texture.py` 将指定 `assets/blade_recon_synth_tex/` 载入同场景独立 `SplitTexture.*` 对象，右侧局部材质预览使用独立样本游标，与左 MAPPO 时间分开标明。嵌入 JSON、模型摘要和 packed 图像摘要用于保存恢复；`blade_recon_data.py` 仅按经核验的内容摘要标明合成来源，不凭目录名猜测。独立导入前保存原视口取景和显示状态，返回时恢复；详情及安装来源见实施与验证（本地路径：`outputs/blade-texture-split-fix-20261007-b01/实施与验证.md`）。该修改已随 0.3.17 ZIP 发布；最终包后台安装与保存重开见 [发布核对](../docs/blender/releases/0.3.17发布核对.md)，此前窗口检查保留其运行来源。

0.3.17.1 已发布的入口修正：打开按钮明确标为 **打开几何重建示例…**，当前场景提供 **同源纹理同步对照**与**独立合成纹理样例**两个入口。`split_reconstruction.enter(use_texture=None)` 按当前数据恢复，保留原几何示例；显式 `use_texture=True` 切到独立合成，`use_same_source=True` 切到同源。侧栏恢复布局使用 `CURRENT` 模式，退出后的几何返回亦使用该模式。安装目录内示例若已被保存为纹理场景，应先备份，再用规范 `assets/examples/mappo_reconstruction_split.blend` 恢复；只更新 Python 文件不能纠正该资源偏移。

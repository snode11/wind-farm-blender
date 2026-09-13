# WFRL Blender 前端实现

本文面向接手 Blender 展示端的开发人员，说明场景、风机、雷达外观与相机界面如何实现。操作步骤见 [用户使用手册](../docs/blender/用户使用手册.md)；射线求交、净空估计、独立真值和结果包生产见 [雷达算法 README](../wfrl/lidar/README.md)。

当前发布版本为 **0.2.4**，要求 Blender **5.2+**。本页描述本版源码；安装包与校验信息见[交付说明](../dist/README-lidar.md)。

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

各叶片对象复用名为 `WFRL.SharedBlade.SourceLoft` 的网格，但保留独立的对象变换与装配父级。这样不必为每台机组的每片叶片复制同一份网格，也能分别表达转子相位和变桨。

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

## 5. 相机、卡片与回放如何连接

[cameras.py](wfrl_blender/cameras.py) 管理总体、侧视、云台等观察相机，[panels/gimbal.py](wfrl_blender/panels/gimbal.py) 提供云台交互。相机操作与 Pan / Tilt / Roll 姿态放在 **云台控制** 折叠区；同一 Camera 面板下的雷达卡片可独立查看，不需要加宽侧栏。

[clearance_replay.py](wfrl_blender/clearance_replay.py) 校验外部结果包与匹配机组，按固定时间轴映射取得样本，再更新 `YawRoot`、`Rotor` 和 `BladeN`。它处理包路径、场景状态、暂停、寻址和重载；既不运行 FAST.Farm，也不在 Blender 中重新计算净空。

[panels/clearance.py](wfrl_blender/panels/clearance.py) 将同一次测量的真值、B2 估计与偏差画在主卡片上，并显示测量时刻、叶片编号及读数年龄。三种状态为“新测量”“上次有效测量”“等待下一次有效测量”；等待时数值显示 `--`，保留固定行数以避免播放按钮移动。统计与数据配置放在折叠区。

“正常测量／较小净空”切换数据片段并从起点播放；“测量区侧视／风场总览”只切镜头。当前前端播放的是刚性姿态示意，柔性形变仅体现在后端产生的数据中。数据合同和时间采样规则见 [雷达算法 README 第 4 节](../wfrl/lidar/README.md#4-包格式有效性与回放时钟)。

### 5.1 加载、时钟与文件恢复

`clearance_replay.load(scene, path, demo)` 先确认后端允许切换配置，停止当前播放并清除旧读取器，再加载结果包。它继续校验片段身份、目标机组转子、NREL 5 MW 机型和尺寸、标定向量及当前不支持的机舱 roll/pitch；成功后进入 `clearance_replay` 模式，创建读取器，并回到起始帧。操作器捕获加载错误后显示具体原因和配置字段，避免失败后仍显示旧读数。

每个场景的读取器缓存在 `_READERS[scene.as_pointer()]`。固定时间基准保存在 `wfrl_clearance_timebase_fps`，时间映射为：

```text
t = segment.start_s
    + (frame_current + frame_subframe - frame_start) / timebase_fps
```

随后把 `t` 限制在片段范围内。时间基准初次加载时取 Blender 的 `fps / fps_base`，后续回放和重载保留该基准。因此渲染 FPS 变化不会改写同一帧对应的样本。

`frame_change_post` 调用 `update()`，把样本的 yaw 写到 `YawRoot`，转子方位写到 `Rotor`，各叶片桨距写到对应 `BladeN`。文件加载后，`on_load()` 清空旧指针缓存，根据场景保存的包路径重建读取器并恢复帧号；包内容仍在外部目录，保存 `.blend` 不等于把结果包嵌入文件。片段末尾通过定时回调停止播放，回调再次检查当前位置，避免旧回调停止刚重启的片段。

目前坐标接入采用固定全局塔原点和刚性塔假设：通过减去机组根节点与 `YawRoot` 平移，将包内雷达原点映射到装配局部坐标。它不是任意父级旋转、缩放或浮式机组的通用变换。当前生产脚本要求零偏航；扩展到运动机舱或任意安装姿态时，应同步设计物理标定与前端坐标合同。

## 6. 修改、安装与验证

| 想修改的内容 | 优先入口 |
| --- | --- |
| 塔筒、叶片截面或装配位置 | `turbine_geometry.py`、`scene_builder.py` |
| 机舱附件与表面效果 | `mechanical_details.py`、`materials.py` |
| 雷达外壳、支架、线缆和光束示意 | `clearance_visual.py` |
| 相机、云台与主卡片 | `cameras.py`、`panels/gimbal.py`、`panels/clearance.py` |
| 离线包加载与 Blender 时钟 | `clearance_replay.py` |
| 测距算法、真值或物理结果生产 | 仓库级 `wfrl/lidar/` 与 `scripts/lidar/`，见独立算法 README |

源码专用入口为 `scripts/blender/open_clearance_demo.py`，需要准备好的演示场景与交付清单指定的结果包。安装版不会自动读取工作区改动；更新扩展代码后，从仓库根目录运行 `python3 scripts/blender/build_extension.py`，安装对应 ZIP 并重启 Blender。构建会输出同版本的 ZIP、摘要与 inventory；现有发行状态以 [交付清单](../dist/lidar-delivery.json) 为准，不要只按版本号判断源码和安装包是否一致。

修改网格和装配后检查实际场景；修改雷达创建或旧场景更新时，运行 [编辑模式重载回归](tests/blender/clearance_edit_mode_regression.py)。该脚本需要独立 `WFRL_TEST_OUTPUT`，并支持用 `WFRL_TEST_PACKAGE_ROOT` 选择实际 ZIP 或安装版。卡片和交互检查见 [clearance_ux_regression.py](tests/blender/clearance_ux_regression.py)。

仅修改展示外观无需重新求解物理结果。模型几何若要成为新物理计算的输入，则应同步核对计算端机型和坐标，而不是只改变画面。本文按源码整理，本轮文档工作没有重新进行 Blender 窗口或物理验收。

# WFRL Blender 前端功能对等基线

> 状态：Part 1 基线 v1
>
> 日期：2026-09-04
>
> 适用范围：以 Blender 5.2.1 LTS Extension 替代现有 Studio 与 RViz 的可见前端；后端算法、物理合同、reward、安全规则、checkpoint 和场景语义保持不变。

## 1. 目的与使用方式

本文是 Blender 前端的功能合同，不是旧 Qt 控件的复刻清单。每个旧入口都有稳定功能 ID、输入、输出、后端副作用、数据真实性、迁移判定和验收证据。后续设计、实现、测试和评审统一引用这些 ID。

迁移判定含义：

| 判定 | 含义 |
|---|---|
| 保留 | 用户能力和语义必须保留，控件位置与技术实现可以改变 |
| 整合 | Studio 与 RViz 的重复能力合并成一个 Blender 入口 |
| 修正 | 保留目标，但不得继承旧实现的已知缺陷或含义歧义 |
| 开发模式 | 只放在开发或诊断界面，不进入老板展示主流程 |
| 新增 | 设计目标要求，但旧前端没有完整入口 |

## 2. 基线来源与边界

主要代码基线：

- `wfrl/studio/app.py`：Studio 面板、菜单、按钮、相机和生命周期接线；
- `wfrl/studio/view.py`：场景、姿态、传感器、地形、尾流和手动覆盖；
- `wfrl/viz/rviz_app.py`：RViz 数据源、遥测、通道、双视图、拖拽和 CLI；
- `wfrl/studio/trainer.py`：Demo、交互训练、Replay、快照和安全停止；
- `docs/演示指令单.md`：当前可复现演示流程、真实性口径和已知边界。

为核对动态通道、场景校验和验收证据，还只读检查了 `wfrl/channels/`、`wfrl/scene/`、三个固定场景及现有 probe/test。本文描述的是当前工作区行为，不把历史幻灯片中的性能数字当作新前端验收门槛。

不属于 Part 1 的内容：Blender Extension 代码、Bridge 协议实现、模型资产、安装包、截图和录屏。本文也不授权删除旧 Studio 或 RViz。

## 3. 数据真实性统一口径

Blender UI 对外只使用以下三个英文标识：

| 标识 | 含义 | 允许用途 | 当前代码映射 |
|---|---|---|---|
| `DIRECT` | 由当前物理后端或控制执行链直接回传 | 可用于遥测、约束和经既有合同允许的训练输入 | 当前 `Fidelity.DIRECT` / “直读” |
| `EXPORTED` | 从后端文件读取，或由 `DIRECT` 量按明确公式计算 | 可用于分析与展示；必须显示来源和公式/文件延迟 | 当前 `Fidelity.DERIVED` / “导出”，以及 FAST.Farm DisXY 文件 |
| `SYNTH` | 展示、代理或未经物理验证的合成效果 | 只用于感知和演示，不得冒充物理真值或进入 reward | 当前 `Fidelity.SYNTH` / “合成” |

注意：当前枚举名 `DERIVED` 在界面上显示为“导出”。迁移时统一对外写成 `EXPORTED`，但不改变既有数据计算方法。

## 4. 固定验收场景与证据类型

| 场景 ID | 场景文件 | 责任边界 | 不得宣称 |
|---|---|---|---|
| A1 Demo | `scenes/turb3_demo.yaml` | 不启动真实后端；验证安装、布局、风机层级、yaw/pitch/RPM 动画、相机、Lidar、Panel，以及 `SYNTH` 功率和脚本事件的展示壳 | 不得把功率、尾流、地形、脚本事件称为 FAST.Farm 真值；不得称为策略输出或安全层动作改写 |
| A2 Real | `scenes/turb3_ctrl3.yaml` | 连接真实 FAST.Farm；验证三控制量、实测状态、功率、载荷、通道、安全事件和安全停止 | 不得把 FLORIS proxy 或渲染地形称为 FAST.Farm 输出 |
| A3 Replay | `scenes/turb3_stagger.yaml` + 当前兼容 checkpoint | 确定性策略回放；逐字段对比 turbine ID、step、yaw、pitch、RPM、power、reward、事件和结束状态 | 不得直接复用历史性能数字作为当前验收结论 |

证据缩写：

| 缩写 | 证据 |
|---|---|
| UI | 可见界面截图或录屏，包含必要标签与状态 |
| STATE | 自动检查 Blender 对象、控件状态、状态机或消息字段 |
| NUM | 与直接后端/旧前端输出逐字段数值对比 |
| LOG | 可保存的生命周期、错误、安全事件或命令日志 |
| PROC | 进程与临时算例清理检查，确认没有孤儿进程 |

## 5. 功能对等矩阵

### 5.1 场景、模式与运行生命周期

| ID | 旧入口 | 输入 | 用户可见输出 | 后端或副作用 | Fidelity | Blender 判定 | 验收场景 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `scene.load` | Studio CLI `--scene`；窗口内无文件选择器 | YAML 路径 | 场景名称、布局和配置进入界面 | 只解析并校验；不得在校验前启动 FAST.Farm | 配置 | 修正：增加界面选择、最近场景和明确错误 | A1/A2/A3 | UI+STATE |
| `scene.validate` | `load_scene().validate()`，错误主要进控制台 | backend、机型、dt、布局、来流、控制量、传感器、地形 | 字段级错误；成功时显示“场景有效” | 不生成算例、不 spawn 进程 | 配置 | 保留并前置 | A1/A2/A3 | STATE+LOG |
| `scene.summary` | Studio 左侧“场景”树 | 已加载 Scene | 名称、后端、机型、控制步和来源 | 无 | 配置 | 保留 | A1/A2/A3 | UI |
| `scene.layout` | Studio“布局”；RViz 由 env ID 反查 | turbine ID、x/y 米制坐标 | 机组列表与 3D 位置 | A2/A3 必须与后端 case 一致 | DIRECT/配置 | 整合：以 Scene YAML 为唯一来源 | A1/A2/A3 | UI+NUM |
| `scene.inflow` | Studio“来流”；RViz CLI `--wind*`/`--turb` | 风速、风向、湍流盒 | m/s、角度、湍流文件与生效状态 | FAST.Farm 风向请求会被忽略；湍流盒覆盖 speed | DIRECT/配置 | 修正：A1 显示配置值，A2 显示“请求值/实际生效值” | A1/A2 | UI+NUM+LOG |
| `scene.controls` | Studio“控制量”；RViz `--controls` | `yaw[,pitch][,torque]` | 当前允许的控制通道 | 决定动作空间；torque 会替换基线控制器 | 配置 | 保留并增加高风险提示 | A2 | UI+STATE |
| `scene.notes` | Studio“未计入物理”；地形角标 | 场景派生说明 | 平地假设、FAST.Farm 风向限制、湍流覆盖和单机型限制 | 无 | SYNTH/说明 | 保留，展示模式也必须可见 | A1/A2 | UI |
| `mode.demo` | Studio CLI `--demo [--demo-cycles]` | 场景、Presentation 预设或开发模式循环次数 | 确定性启停、yaw、pitch、RPM、功率演示和脚本阶段事件 | 不启动 FAST.Farm/FLORIS，不加载 checkpoint，不执行策略，也不证明安全层改写 | SYNTH | 保留；按钮不得叫“开始训练”；Presentation 固定单次 66 s 序列 | A1 | UI+STATE |
| `mode.interactive-train` | Studio 默认模式 + “开始训练” | iters、n_steps、warmup、scene | 实时快照、训练曲线、安全事件 | 包装现有 `Trainer`/`SceneRuntime` | DIRECT+EXPORTED | 保留 | A2 | UI+NUM+LOG |
| `mode.formal-train` | 旧 GUI 无入口；独立 `scripts/train/train_fastfarm.py` | backend、seed、iters、n_steps、warmup、reward、checkpoint 等 | 任务配置、进度、日志和产物位置 | 启动现有正式训练脚本；不得复制算法 | 混合 | 新增 | A2 | STATE+LOG+PROC |
| `mode.replay` | Studio CLI `--replay --ckpt --replay-steps`；RViz `--model` | checkpoint、场景、步数 | 确定性动作、逐步遥测、曲线和结束状态 | 加载兼容 checkpoint，不做 PPO 更新 | DIRECT+EXPORTED | 整合 | A3 | UI+NUM+LOG |
| `connection.status` | 旧前端无统一入口 | Demo 本地模式或 Bridge 健康状态 | `LOCAL DEMO`、`DISCONNECTED`、`CONNECTED` | 只报告连接能力，不替代运行生命周期 | 元数据 | 新增；与 `run.status` 分成两个可见状态 | A1/A2/A3 | UI+STATE+LOG |
| `run.start` | Studio“▶ 开始训练”；RViz 启动后自动播放 | 当前模式与配置 | `STARTING`，随后 `RUNNING` 或 `FAILED` | Demo 本地起；其他模式启动 Bridge 会话/进程 | 混合 | 修正：按模式改变按钮文案与命令 | A1/A2/A3 | UI+STATE+LOG |
| `run.pause` | Studio“⏸ 暂停”；RViz“⏸ 暂停” | 运行中的会话 | `PAUSED`；画面和控制步冻结 | 只在控制步边界暂停，不中断正在执行的 FAST.Farm step | 混合 | 保留 | A1/A2/A3 | UI+STATE |
| `run.resume` | 同一按钮切成“▶ 继续/播放” | 已暂停会话 | 返回 `RUNNING`，时间积分不补算暂停时长 | 清除 pause 标志 | 混合 | 保留 | A1/A2/A3 | UI+STATE |
| `run.step` | RViz“⏭ 单步”；Studio 无按钮 | 已暂停会话 | Demo 推进一个脚本帧；后端推进一个完整控制步，并只刷新一次 | Demo 不接触后端；FAST.Farm 一步必须完整执行 | SYNTH/DIRECT | 保留，限定暂停态 | A1/A2/A3 | UI+NUM+STATE |
| `run.stop` | Studio“⏹ 停止”；关闭两个旧窗口 | 活动会话 | Demo 直接结束；后端会话进入 `DRAINING`，最终 `STOPPED` 或 `FAILED` | Demo 无子进程；后端会话排空预算并清理本会话子进程和 case | 混合 | 修正：Blender 事件循环不得同步阻塞 | A1/A2/A3 | UI+LOG+PROC |
| `run.close-safe` | Studio/RViz 窗口关闭事件 | 关闭窗口 | 等待当前 step、显示安全关闭、最后退出 | 关闭 driver、清理 case，避免 MPI_RECV 孤儿 | 混合 | 保留 | A2/A3 | LOG+PROC |
| `run.status` | Studio 状态栏/训练标签；RViz工具条状态 | 生命周期事件、Snapshot | 就绪、启动/热身、采样、更新、暂停、排空、结束、异常 | 无；状态必须来自实际生命周期 | 混合 | 整合成固定状态机 | A1/A2/A3 | UI+STATE+LOG |
| `run.progress` | Studio训练标签与统计表；RViz step 标签 | step、iter、phase、总量 | 当前步、轮次、阶段和完成比例 | 无 | DIRECT/EXPORTED | 保留 | A2/A3 | UI+NUM |
| `run.error` | 旧前端多为控制台异常或“异常，见控制台” | 异常对象和上下文 | 可理解、可复制、区分可恢复/致命的错误 | 失败时进入 `FAILED`，仍允许安全停止 | 混合 | 修正 | A2/A3 | UI+LOG+PROC |

### 5.2 3D 场景、视图和交互

| ID | 旧入口 | 输入 | 用户可见输出 | 后端或副作用 | Fidelity | Blender 判定 | 验收场景 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `view.world` | Studio/RViz 中央 PyVista 视口 | Scene + 最新姿态 | 全场风机、坐标、尾流和传感器 | 不修改物理 | 混合 | 保留 | A1/A2/A3 | UI+STATE |
| `view.orbit` | 3D 视口鼠标轨道操作 | 鼠标拖动 | 绕焦点旋转 | 无 | SYNTH | 保留 | A1 | UI |
| `view.pan` | 3D 视口原生交互 | 鼠标平移 | 相机平移 | 无 | SYNTH | 保留 | A1 | UI |
| `view.zoom` | 3D 视口滚轮/缩放 | 滚轮 | 相机距离/FOV变化 | 无 | SYNTH | 保留 | A1 | UI |
| `camera.world.top` | Studio“视图 → 主相机：俯视” | 无 | 全场俯视取景 | 无 | SYNTH | 保留 | A1 | UI+STATE |
| `camera.world.side` | Studio“主相机：侧视” | 无 | 全场侧视取景 | 无 | SYNTH | 保留 | A1 | UI+STATE |
| `camera.world.iso` | Studio“主相机：斜视”；旧默认斜视 | 无 | 全场工业斜视取景 | 无 | SYNTH | 保留 | A1 | UI+STATE |
| `view.dual` | RViz CLI `--dual-view`；Studio“相机传感器”总开关 | 开/关 | 世界视图 + 单机/传感器视图 | 共用数据，不重复物理求解 | SYNTH+DIRECT姿态 | 整合 | A1/A2 | UI+STATE |
| `view.split.set` | RViz分屏滑块 15–85%；Studio 固定右侧 42% | 左视口比例 | 两个视口实时调整 | 无 | SYNTH | 保留并统一 | A1 | UI+STATE |
| `view.split.preset-small` | RViz“世界小” | 25% | 左世界视图占 25% | 无 | SYNTH | 整合为布局预设 | A1 | UI |
| `view.split.preset-even` | RViz“对半” | 50% | 两视图各半 | 无 | SYNTH | 整合为布局预设 | A1 | UI |
| `view.split.preset-large` | RViz“世界大” | 80% | 左世界视图占 80% | 无 | SYNTH | 整合为布局预设 | A1 | UI |
| `camera.focus.select` | RViz CLI `--focus`；Studio相机下拉 | turbine/camera ID | 右视图切到目标机组或传感器 | 无 | SYNTH+DIRECT姿态 | 保留，UI 使用稳定 turbine ID | A1/A2 | UI+STATE |
| `camera.sensor.toggle` | Studio“视图 → 相机传感器” | 开/关 | 视锥、右视口和相机工具条整体显隐 | 不启动采样；只影响表现 | SYNTH | 保留 | A1 | UI+STATE |
| `camera.sensor.select` | Studio相机下拉 | 相机挂载项 | 切换相机位姿、目标机组和参数 | 无 | SYNTH | 保留 | A1 | UI+STATE |
| `camera.fov.set` | Studio FOV 滑块 5–90° | 角度 | 视场角与等效焦距提示 | 修改展示相机参数，不进物理 | SYNTH | 保留 | A1 | UI+STATE |
| `camera.pitch.set` | Studio俯仰滑块 -90–30° | 角度 | 相机俯仰和实时取景变化 | 修改展示相机参数，不进物理 | SYNTH | 保留 | A1 | UI+STATE |
| `camera.frustum` | Studio camera 通道/总开关 | camera pose + FOV | 主视图中的视锥线框 | 无 | SYNTH | 保留并始终标明“几何视野” | A1 | UI+STATE |
| `view.turbine-detail` | `TurbineWindow` 类已实现，但旧界面没有可达打开入口；RViz右侧 chase 视图可替代部分能力 | turbine ID | 真尺度单机、叶片、变桨和相机近景 | 无 | SYNTH+DIRECT姿态 | 修正：提供明确可达入口 | A1/A2 | UI+STATE |
| `turbine.pose.yaw` | 两旧前端实时机舱姿态 | yaw 测量或 Demo值 | `YawRoot` 随偏航变化 | 不反向写控制，除非进入独立控制功能 | DIRECT/SYNTH | 保留 | A1/A2/A3 | UI+NUM |
| `turbine.pose.pitch` | 两旧前端叶片局部变桨 | `pitch_meas`，缺失时才退回指令 | 三片叶绕局部轴变化 | 不伪造实测 | DIRECT/SYNTH | 保留实测优先规则 | A1/A2/A3 | UI+NUM |
| `turbine.pose.rpm` | 两旧前端按真实时间积分转子相位 | RPM + frame dt | 连续转动，暂停不跳帧 | 不推进物理步 | EXPORTED/SYNTH | 保留 | A1/A2/A3 | UI+NUM |
| `turbine.flex` | RViz/Studio叶片柔性表现 | `m_flap`/`m_edge` + 标定 + 显示倍率 | 叶片形变 | 只改渲染几何；倍率不得改变遥测 | EXPORTED | 保留，默认显示倍率必须可见 | A2 | UI+NUM |
| `turbine.clearance` | RViz遥测“净空m” | 真实叶片几何 + 弯矩派生挠度 | 三叶最小扫塔净空，黄/红告警 | 不改物理 | EXPORTED | 保留 | A2 | UI+NUM |
| `layout.drag` | RViz FLORIS 世界视图拖拽；FAST.Farm禁用 | turbine ID、新 x/y | 拖动中移动 actor，释放后尾流重算 | FLORIS 重建 layout；FAST.Farm运行中严禁 | DIRECT/配置 | 保留为场景编辑/FLORIS功能，按后端禁用 | A1（编辑） | UI+STATE |
| `terrain.set` | Studio“地形：无/山地/戈壁”；RViz CLI `--terrain` 含 flat | terrain preset | 地形背景与“不参与物理计算”标签 | 不修改后端地面和机位物理坐标 | SYNTH | 整合，并补全 flat/none | A1/A2 | UI+STATE |
| `wake.proxy.toggle` | Studio“风况热力图”；`--no-wake` | 开/关 | FLORIS轮毂层速度色面与色带 | 需要 FLORIS稳态求解；不得当作 FAST.Farm真尾流 | SYNTH | 保留并改名“FLORIS Proxy” | A1/A2 | UI+STATE |
| `wake.proxy.update` | Studio/RViz随 yaw/来流更新 | yaw、自由来流 | 代理尾流随姿态变化 | 后台求解，只保留最新请求 | SYNTH | 保留 | A1/A2 | UI+STATE |
| `wake.disxy` | RViz `--wake-vtk [--wake-z]` | FAST.Farm输出目录、高度 | 准实时水平尾流切面、网格与色带 | 读取 DisXY 文件；可能落后一帧且某步无新帧 | EXPORTED | 保留，显示文件时间/延迟 | A2/A3 | UI+NUM+LOG |
| `wake.rings` | Studio“尾流圆环（扩散包络）” | 开/关、RPM、风向 | 转子下游扩散、滚动、渐隐的圆环 | 纯视觉；停转自动隐藏 | SYNTH | 保留并显式标记 | A1 | UI+STATE |
| `sensor.lidar.visual` | Studio lidar 通道 | A1 配置化视觉夹具，或 A2 点位与风速 | A1 只显示安装位与几何射线；A2 显示点云/射线、色带和湍流状态 | A1 不采样；A2 采样状态随订阅开关 | SYNTH/EXPORTED | 保留；按路径切换字段和来源 | A1/A2 | UI+STATE+NUM |

### 5.3 Panel、状态、曲线和安全表达

| ID | 旧入口 | 输入 | 用户可见输出 | 后端或副作用 | Fidelity | Blender 判定 | 验收场景 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `panel.scene` | Studio左侧“场景”Dock | Scene | 场景树 | 无 | 配置 | 保留 | A1/A2 | UI |
| `panel.channels` | Studio/RViz左侧通道面板 | 通道 metadata 与 enabled | topic、保真度、订阅状态、来源 tooltip | Studio会停止采样；RViz当前只停止发布 | 混合 | 修正并采用 Studio 语义 | A1/A2 | UI+STATE |
| `panel.run` | Studio右侧“训练”Dock；RViz底部工具条 | 生命周期和 Snapshot | 控制按钮、阶段、步数、功率和奖励 | 发运行命令 | 混合 | 整合为“运行”Panel | A1/A2/A3 | UI+STATE |
| `panel.telemetry` | RViz右侧遥测表；Studio训练/传感器数据分散 | Demo frame 或最新 Snapshot | 每台 yaw、pitch指令/实测、torque、RPM、power、TI、载荷、挠度、净空 | 无 | SYNTH+DIRECT+EXPORTED | 保留并统一单位；每字段单独标来源 | A1/A2/A3 | UI+NUM |
| `panel.safety` | Studio右侧“物理与安全约束” | Demo 脚本事件或 limiter notes/counts/events | A1 标题为“演示事件”；A2/A3 展示规则、计数、最近事件和严重级 | 无；A1 不得暗示真实安全层执行 | SYNTH+EXPORTED | 保留，并按模式切换标题与来源 | A1/A2/A3 | UI+NUM+LOG |
| `panel.manual` | Studio左侧“手动摆位” | turbine、yaw/pitch/RPM | 当前手动值与锁定提示 | 只锁渲染姿态，不写入仿真/策略 | SYNTH | 修正命名为“展示姿态”，避免误认为控制 | A1 | UI+STATE |
| `telemetry.yaw` | RViz `yaw°`；Studio姿态/传感器 | 每机 yaw | 度 | 无 | DIRECT | 保留 | A2/A3 | UI+NUM |
| `telemetry.pitch-command` | RViz“桨距指令°” | PitchRef累加指令 | 度 | 指令是基线桨距的下界语义 | DIRECT | 保留且不可与实测合列 | A2/A3 | UI+NUM |
| `telemetry.pitch-measured` | RViz“桨距实测°” | avrSWAP(4) | 度 | 驱动真实姿态 | DIRECT | 保留且优先驱动叶片 | A2/A3 | UI+NUM |
| `telemetry.torque` | RViz“转矩kNm” | 发电机转矩 | kN·m | torque控制开启时外部值替代基线 | DIRECT | 保留并显示控制来源 | A2/A3 | UI+NUM |
| `telemetry.rpm` | RViz“转速rpm”；Studio姿态 | P/(ηT)/齿轮比或运行时测量字段 | rpm | 无 | EXPORTED | 保留 | A2/A3 | UI+NUM |
| `telemetry.power` | RViz“功率MW”；Studio全场功率 | A1 合成功率或 A2/A3 每机与全场功率 | MW | 无 | SYNTH/DIRECT | 保留；数值旁逐字段标来源 | A1/A2/A3 | UI+NUM |
| `telemetry.ti-load` | RViz“TI”“1P载荷” | vibration代理、真实load | TI与载荷 | 无 | EXPORTED+DIRECT | 修正：拆列并分别标来源 | A2 | UI+NUM |
| `telemetry.flex-clearance` | RViz“挠度m”“净空m” | 叶根弯矩与几何计算 | 真值尺度结果与告警色 | 无 | EXPORTED | 保留 | A2 | UI+NUM |
| `curve.train` | Studio训练曲线 | 每轮 mean_power、mean_reward | 双独立纵轴曲线 | 无 | EXPORTED | 保留 | A2 | UI+NUM |
| `curve.train-table` | Studio统计表 | iter、MW、reward、vloss、explained_var | 每轮表格 | 无 | EXPORTED | 保留，开发指标可折叠 | A2 | UI+NUM |
| `curve.demo-filter` | Studio“T1/T2/T3/全部”按钮 | turbine filter | Demo 单机或全场 yaw/pitch/power 曲线 | 无 | SYNTH | 保留为曲线筛选 | A1 | UI+STATE |
| `curve.replay` | Studio Replay history | step、power、reward；当前 yaw 只内部记录T1且最终未单独画 | 聚合回放曲线 | 无 | EXPORTED | 修正：按全部 turbine ID 保留原始序列 | A3 | UI+NUM |
| `training.stats` | 旧 Studio 训练统计与正式训练 CLI 产物 | `training_stats` 消息；JSONL 实际统计字段 | 迭代、阶段、统计值、单位、validity、data age 和 provenance | 只转发现有训练输出；不从单帧遥测估算缺失指标 | EXPORTED | 新增；未协商或未提供的字段显示“不支持” | A2 | UI+NUM+STATE+LOG |
| `history.export` | 旧 Studio/RViz 无稳定导出入口 | 当前会话有界原始历史 | JSON/CSV 会话历史，字段和缺失原因可追溯 | 只读本地前端历史，不改变仿真状态 | 混合 | 新增 | A1/A2/A3 | UI+STATE |
| `display.restore` | Studio“释放该机组/全部释放”后的下一帧接管 | turbine/channel 展示锁 | 立即恢复最近有效 Snapshot；无 Snapshot 时恢复默认姿态 | 只改展示姿态，不发送真实控制命令 | SYNTH/DIRECT | 修正：释放不等待未知下一帧 | A1/A2/A3 | UI+STATE |
| `safety.notes` | Studio安全面板顶部 | Demo 声明，或 scene、步长、规则 | A1 显示“脚本事件，不是安全层执行”；A2/A3 显示可持续控制量和规则 | 无 | SYNTH/EXPORTED | 保留 | A1/A2/A3 | UI |
| `safety.counts` | Studio“规则触发计数” | Demo 脚本计数或 SafetyLimiter summary | 事件类型与次数 | 无 | SYNTH/EXPORTED | 保留；A1 不使用“规则触发”文案 | A1/A2/A3 | UI+NUM |
| `safety.events` | Studio“最近事件” | 最多40条 Demo 脚本事件或安全层事件 | A1 显示时间/机组/阶段/详情；A2/A3 显示时间/机组/规则/请求值/执行值/原因 | A2/A3事件保持有序；A1只用于组件预览 | SYNTH/EXPORTED | 保留并保证来源可见 | A1/A2/A3 | UI+NUM+LOG |
| `safety.duty-warning` | RViz yaw/pitch单元格黄底；Studio事件 | duty与10%阈值 | 接近清零的预警 | 无 | DIRECT/EXPORTED | 整合进安全面板与对应遥测格 | A2 | UI+NUM |
| `status.data-missing` | RViz用“-”；Studio保持上一帧/等待数据 | NaN、通道缺失、抽稀 | 明确区分“不支持/等待/断线/NaN” | 无 | 混合 | 修正，禁止把缺失显示为0 | A1/A2/A3 | UI+STATE |
| `status.data-age` | 旧前端更新时间表达不统一 | 帧时间戳、预期周期、连接心跳 | 数据年龄及“新鲜/延迟/过期”；连接健康单独显示 | 无；不得用单通道过期覆盖连接状态 | 元数据 | 新增统一阈值与展示 | A1/A2/A3 | UI+STATE |
| `status.fidelity` | Studio保真度列/tooltip；RViz标题区分尾流来源 | fidelity + provenance | 颜色、标识、图例和来源详情 | 无 | 元数据 | 整合，所有科学画面强制显示 | A1/A2/A3 | UI+STATE |

### 5.4 数据通道合同

| ID | 当前 topic/来源 | 数据与单位 | 旧端消费方式 | 后端或订阅副作用 | Fidelity | Blender 判定 | 验收场景 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `channel.power` | `power` / FAST.Farm或FLORIS | 每机 MW | 表格、全场合计、曲线 | 关闭后不应继续采样该 sensor | DIRECT | 保留 | A2/A3 | NUM+STATE |
| `channel.yaw` | `yaw` / actuator | 每机度 | 机舱姿态、遥测、策略回放 | 位姿仍可由必要 Snapshot 驱动；订阅控制额外展示 | DIRECT | 保留 | A2/A3 | NUM |
| `channel.pitch-command` | `pitch` / actuator | 每机度 | 指令列；实测缺失时才回退驱动姿态 | 下界语义，不等于叶片实际角 | DIRECT | 保留并改稳定名 | A2/A3 | NUM |
| `channel.pitch-measured` | `pitch_meas` / actuator | 每机度 | 实测列与叶片姿态 | 无 | DIRECT | 保留并改稳定名 | A2/A3 | NUM |
| `channel.torque` | `torque` / actuator | 每机 N·m，UI换算kN·m | 遥测、RPM派生 | 外部控制时替代基线控制器 | DIRECT | 保留 | A2/A3 | NUM |
| `channel.rotor-speed` | Studio sensor `rotorspeed`；运行时/RViz `rotor_speed` | 每机 rpm | 转子动画与遥测 | 当前命名不一致 | EXPORTED | 修正：协议统一一个 canonical key，适配旧别名 | A2/A3 | NUM+STATE |
| `channel.blade-flap` | `m_flap` / bladeload | `(n,3)` 叶根挥舞弯矩 | 柔性、挠度、vibration | 关闭 bladeload sensor 时停止采样 | DIRECT | 保留 | A2 | NUM+STATE |
| `channel.blade-edge` | `m_edge` / bladeload | `(n,3)` 叶根摆振弯矩 | 柔性 | 同上 | DIRECT | 保留 | A2 | NUM+STATE |
| `channel.vibration` | `vibration` / 32步叶根弯矩窗口 | RMS、峰峰值；旧RViz的TI/1P列还混入FLORIS proxy特征 | 遥测 | 按rate抽稀 | EXPORTED | 修正：字段名与物理定义分开 | A2 | NUM |
| `channel.lidar` | `lidar` | A1 配置化视觉夹具，或 A2 点坐标、视线风速、u_hub、range、湍流标志 | A1 显示 `SYNTH` 安装位/射线；A2 显示 `EXPORTED` 点云与色带 | A1 不采样；A2 取消订阅必须停止视线插值并隐藏色带 | SYNTH/EXPORTED | 保留；A1 不伪造测量 | A1/A2 | UI+NUM+STATE |
| `channel.camera` | `camera` | pose、focal、FOV、mount、pitch；不含图像 | 视锥和传感器视口 | 取消订阅停止其额外采样/渲染；基础相机选择仍可配置 | SYNTH | 保留并明确“不出图像” | A1 | UI+STATE |
| `channel.acoustic` | `acoustic` | 每机0–1健康分数 | 当前固定场景未配置、旧主界面无专门列 | 0.2Hz抽稀；不进入3s控制reward | SYNTH | 开发模式，保留扩展能力 | A1 | STATE |
| `channel.wake-proxy` | RViz/Studio FLORIS水平面 | 轮毂层风速网格 m/s | 速度色面 | FLORIS求解；只保留最新可替换帧 | SYNTH | 保留 | A1/A2 | UI+STATE |
| `channel.wake-disxy` | FAST.Farm DisXY文件 | 网格、时间、速度 m/s、高度 | 真实尾流色面 | 文件轮询；不得用proxy填补缺帧 | EXPORTED | 保留 | A2/A3 | UI+NUM+LOG |
| `channel.duty` | `duty` / driver | 每控制量、每机占空比 | 单元格预警与安全事件 | 决定下一步动作是否被环境清零 | DIRECT | 保留 | A2/A3 | NUM |
| `channel.clearance` | 渲染几何计算 | `(n,3)` m | 净空列与黄/红告警 | 不改物理 | EXPORTED | 保留 | A2 | NUM |
| `channel.meta` | Snapshot `_meta` | step/t、u_inf、reward、done、farm_power | 状态、进度和曲线 | 生命周期消息不得被普通快照替代 | EXPORTED | 保留并拆分生命周期与可替换Snapshot | A2/A3 | NUM+STATE |
| `channel.error` | Sensor异常当前塞进各自topic字典 | topic、错误、时间、严重级 | 旧界面处理不一致 | 单sensor错误不得停止整场；致命错误另走生命周期 | 元数据 | 修正为统一错误事件 | A1/A2/A3 | UI+LOG |
| `channel.set` | Studio通道复选框；RViz Channels复选框 | topic + enabled | actor和色带显隐 | Studio停止采样；RViz当前仍会计算、只抑制回调 | 元数据 | 修正：统一为“订阅=采样+传输+显示” | A1/A2 | UI+STATE |
| `channel.provenance` | Studio保真度列与tooltip | type、fidelity、provenance | 来源详情 | 无 | 元数据 | 保留并在Blender详情Panel常驻可查 | A1/A2/A3 | UI+STATE |

### 5.5 展示姿态、配置和诊断入口

| ID | 旧入口 | 输入 | 用户可见输出 | 后端或副作用 | Fidelity | Blender 判定 | 验收场景 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `display.turbine-select` | Studio手动摆位机组下拉 | turbine ID | 后续滑块目标 | 无 | SYNTH | 保留 | A1 | UI+STATE |
| `display.yaw-lock` | Studio偏航滑块 -40–40° | turbine + 度 | 画面姿态与锁定状态 | 不写后端，Snapshot不覆盖该显示量 | SYNTH | 保留但与真实控制区隔 | A1 | UI+STATE |
| `display.pitch-lock` | Studio桨距滑块 0–90° | turbine + 度 | 画面姿态与锁定状态 | 不写后端 | SYNTH | 保留但与真实控制区隔 | A1 | UI+STATE |
| `display.rpm-lock` | Studio转速滑块 0–20rpm | turbine + rpm | 叶片按1×实时转动 | 不写后端 | SYNTH | 保留但与真实控制区隔 | A1 | UI+STATE |
| `display.release-turbine` | Studio“释放该机组” | turbine ID | 该机组全部显示锁解除 | 下一Snapshot重新接管 | SYNTH | 保留 | A1 | UI+STATE |
| `display.release-all` | Studio“全部释放”；菜单同义入口 | 无 | 全部显示锁解除 | 下一Snapshot重新接管 | SYNTH | 整合为一个主入口 | A1 | UI+STATE |
| `workspace.mode` | 旧前端没有演示/开发布局分级 | Presentation / Development | Presentation 隐藏日志与高级配置；Development 增加诊断入口 | 只改变布局可见性，不改变运行或数据源 | 配置 | 新增；两种布局共用状态源 | A1/A2/A3 | UI+STATE |
| `config.backend` | RViz `--backend`；Studio由YAML决定 | fastfarm/floris | 后端名称与能力状态 | 选择对应数据源/Bridge启动方式 | 配置 | 整合，以场景为默认、允许受控覆盖 | A1/A2 | UI+STATE |
| `config.paths` | `demo_studio.ps1` 与 CLI 参数中的本机路径 | 后端 Python、MPI、FAST.Farm、Bridge 和默认场景路径 | Preferences 中的路径及逐项校验结果；长路径中间省略 | 只保存机器级设置；不得写入场景或 checkpoint | 配置 | 新增统一 Preferences 入口 | A2/A3 | UI+STATE |
| `config.checkpoint` | Studio `--ckpt`；RViz `--model` | 文件路径 | checkpoint名称、兼容性和加载结果 | 读取权重；不修改原文件 | 配置 | 保留 | A3 | UI+LOG |
| `config.training` | Studio `--iters --n-steps --warmup --fast` | 训练超参 | 配置摘要和进度 | 传给现有Trainer；fast只用于冲流程 | 配置 | 保留；fast标为开发预设 | A2 | UI+STATE |
| `config.replay-steps` | Studio `--replay-steps` | 正整数 | 回放长度 | 决定后端预算 | 配置 | 保留 | A3 | UI+STATE |
| `config.pitch-demo` | RViz `--pitch-demo hold/sweep/off` | 枚举 | 外部桨距演示形态 | 仅controls含pitch时生效；sweep不真实 | 配置/SYNTH | 开发模式；Demo用确定性脚本统一 | A1/A2 | UI+STATE |
| `config.flex-scale` | RViz `--flex-scale` | 非负倍率 | 柔性显示倍率 | 不改物理/遥测；0为刚体 | SYNTH | 保留并常驻标识 | A2 | UI+NUM |
| `config.render-rate` | RViz `--render-ms`；Studio构造参数 | 毫秒 | 画面刷新速率 | 不改变控制步或仿真时间 | 配置 | 开发模式 | A1/A2 | STATE |
| `config.wake-height` | RViz `--wake-z` | 米 | DisXY切面高度 | 必须在spawn前写入driver配置 | 配置 | 保留 | A2 | UI+NUM |
| `diagnostic.preflight` | 旧脚本与 CLI 启动时才暴露依赖失败 | Blender、后端 Python、MPI、FAST.Farm、Bridge、端口和场景 | 每项独立的可用性、版本、失败原因和修复提示 | 只执行有界探测，不启动训练或创建正式算例 | 元数据 | 新增；A2/A3 启动前必过 | A2/A3 | UI+LOG+STATE |
| `diagnostic.headless` | RViz `--headless N` | 步数 | 纯文字遥测与`HEADLESS_OK` | 无Qt窗口；仍须安全关闭FAST.Farm | 混合 | 开发模式，通过Bridge CLI保留 | A2 | LOG+NUM+PROC |
| `diagnostic.wind-presets` | RViz `--list-wind`/`--wind` | calm/rated/strong/gale/veer/turb | 预设参数和说明 | veer仅FLORIS；turb覆盖恒定风速 | 配置 | 保留为场景/风况预设 | A1/A2 | UI+STATE |
| `capture.screenshot` | 旧主界面无稳定用户入口；脚本可离屏截图 | 视图、分辨率、路径 | PNG和成功提示 | 只读当前画面 | SYNTH+混合 | 新增 | A1/A2/A3 | UI+STATE |
| `capture.animation` | 旧主界面无录制入口 | 视图、帧率、时段、路径 | 动画文件和进度 | 不改变控制步；允许录制Demo/Replay | SYNTH+混合 | 新增 | A1/A3 | UI+STATE |

## 6. 四种模式的允许操作基线

`✓` 表示允许，`—` 表示禁用，`受限` 表示必须满足状态或能力检查。

| 操作 | Demo | 交互训练 | 正式训练 | Replay |
|---|:---:|:---:|:---:|:---:|
| 加载/校验场景 | ✓ | ✓ | ✓ | ✓ |
| 启动 | ✓ | ✓ | ✓ | ✓ |
| 暂停/继续 | ✓ | ✓（控制步边界） | 受限（仅后端支持时） | ✓ |
| 单步 | ✓ | 受限（暂停态） | — | ✓（暂停态） |
| 安全停止 | ✓ | ✓ | ✓ | ✓ |
| 切换世界/单机/传感器相机 | ✓ | ✓ | ✓（监看） | ✓ |
| 切换数据通道 | ✓ | ✓ | ✓（监看） | ✓ |
| 展示姿态手动锁 | ✓ | ✓，但标为SYNTH | ✓，但标为SYNTH | ✓，但标为SYNTH |
| 写入真实 yaw/pitch/torque 控制 | — | 由现有Trainer/安全层决定 | 由训练脚本决定 | 仅策略输出，不允许人工改轨迹 |
| FLORIS布局拖拽 | ✓（编辑） | 仅FLORIS且未运行 | — | — |
| 截图 | ✓ | ✓ | ✓ | ✓ |
| 动画录制 | ✓ | ✓ | ✓ | ✓ |
| 修改训练超参 | — | 启动前 | 启动前 | — |
| 选择checkpoint | — | —（现有 `Trainer` 不支持恢复训练） | 启动前可选恢复点 | 启动前必选 |

运行中禁止无提示切换模式。`STARTING`、`RUNNING`、`PAUSED`、`DRAINING` 以及断线后会话状态未确认时，场景、后端、关键路径和训练合同配置必须锁定。

## 7. 已确认差距与迁移决策

| Gap ID | 当前差距 | 风险 | Blender 必须采取的决策 |
|---|---|---|---|
| G01 | Studio的场景只能由CLI传入，窗口内不能选择、重载或展示校验结果 | 非开发人员无法自助恢复错误 | 增加场景选择、校验和字段级错误Panel |
| G02 | Studio无FLORIS交互运行闭环；RViz与Studio场景来源不同 | 同一场景可能画面和后端不一致 | Blender统一通过Scene DTO和Bridge加载 |
| G03 | Demo/Replay仍显示“开始训练” | 演示中按钮含义错误 | 按模式显示“开始演示/开始训练/提交正式训练/开始回放” |
| G04 | Studio停止会在按钮回调中同步等待排空 | Blender可能假死数十秒 | `run.stop`异步进入`DRAINING`，保持UI响应 |
| G05 | RViz取消通道只抑制publish，重计算仍继续 | “取消订阅节省计算”的承诺不成立 | Bridge端实际停止采样/序列化，Blender同步隐藏表现 |
| G06 | 单风机`TurbineWindow`类存在，但主界面没有可达打开动作 | 已写能力对用户不可用 | 提供明确的单机视图入口，不保留死代码式功能 |
| G07 | 相机默认参数在实现与旧probe描述间不一致；固定场景又显式给40°/-30° | 重载或无参数场景取景不确定 | 协议与文档冻结默认值，测试从同一常量读取 |
| G08 | RViz/Studio使用`rotorspeed`与`rotor_speed`两个名字 | 协议和Panel容易漏数据 | Protocol v1规定canonical key并在Bridge兼容旧别名 |
| G09 | RViz把部分FLORIS proxy特征与FAST.Farm真实载荷放进同一“vibration”显示路径 | 观众难以判断每列来源 | 每个字段独立fidelity与provenance，不按整表笼统标注 |
| G10 | 旧前端错误多落控制台，缺少连接/版本/断线/致命错误分级 | 单一状态既表示连接又表示运行，容易出现“已连接但已停止”等歧义 | 分成 `connection.status`（`LOCAL DEMO/DISCONNECTED/CONNECTED`）和 `run.status`（`READY/STARTING/RUNNING/PAUSED/DRAINING/STOPPED/FAILED`）两个可见状态 |
| G11 | 正式训练没有GUI入口 | Blender不能完整替代脚本工作流 | 只提交参数并监控现有`train_fastfarm.py`，不重写算法 |
| G12 | 截图依赖脚本，动画录制没有稳定用户入口 | 展示流程无法在产品内完成 | 增加截图和录制操作，并与控制步解耦 |
| G13 | 旧前端没有分别检查Blender、后端Python、MPI、FAST.Farm和连接 | 单个“就绪”掩盖缺失依赖 | 各能力独立状态和修复提示 |
| G14 | Replay历史对yaw的记录只取T1，最终主要展示聚合power/reward | 无法证明各机组轨迹对等 | A3保留每台ID的完整序列并逐字段比对 |
| G15 | 缺失值主要统一显示“-” | 不支持、未到首帧、断线和非法数值无法区分 | 使用不同状态文本/图标，数值不得默认为0 |
| G16 | Studio Demo 默认两轮，按当前时间因子约 94 s；80 s 主流程从启动到收尾只提供 66 s | 现场镜头先结束或等待动画，最后状态不可预测 | Blender Presentation 预设固定为单次 66 s 序列并在末帧进入 `STOPPED`；可调循环次数只放 Development |

## 8. Part 1 对本文的验收清单

- [x] Studio与RViz代码中可见的按钮、菜单、Panel、视图和状态均有稳定ID或被明确标为不可达/缺口。
- [x] 场景、运行、相机、通道、遥测、尾流、传感器、安全和诊断副作用已记录。
- [x] `DIRECT`、`EXPORTED`、`SYNTH` 已统一定义并映射当前代码。
- [x] Demo、真实后端和Replay三类验收场景已绑定到具体功能和证据类型。
- [x] 已区分“保留结果”与“复刻旧控件”，并记录旧实现中不得迁移的缺陷。
- [x] 产品文案、视觉层级和60–90秒演示顺序已在 `visual-direction.md`、`presentation-flow.md` 中完成，并按本轮评审修正。

本文达到功能基线完成条件后，后续实现不得静默删减矩阵行。若范围变化，应修改对应行、说明原因并重新评审相关验收证据。

## 9. Part 6 实现与证据

Part 6 的实现入口已经接入 Blender Extension 注册路径：

- `blender_frontend/wfrl_blender/panels/scene.py`、`channels.py`、`run.py`、`telemetry.py`、`safety.py`：场景、订阅、运行、遥测和安全面板。
- `blender_frontend/wfrl_blender/operators/workflow.py`：场景/通道命令和四种模式的启动参数映射。
- `blender_frontend/wfrl_blender/charts.py`：按稳定 turbine ID 保存有界历史，不把缺失值补成零，并保留单位、fidelity 和 provenance。
- `blender_frontend/wfrl_blender/panels/presentation.py` 与 `presentation.py`：相机参数、多视图、Presentation/Development 切换、截图和墙钟录制。
- `wfrl/blender_bridge/workflow.py` 与 `backend_session.py`：配置覆盖校验、checkpoint 前置校验、通道采样开关和异步安全停止。

当前可复核证据：

| 证据 | 结果 | 边界 |
|---|---|---|
| `blender_frontend/tests/blender/workflow_smoke.py` | PASS | 验证四种模式、工作流属性和操作符注册；不替代真实后端运行 |
| `blender_frontend/tests/blender/part6_presentation_smoke.py` | PASS | 验证相机瞄准/FOV 与后台注册；窗口截图录制仍需交互窗口 |
| `evidence/part6/replay_numeric.json` | PASS / `STOPPED` | `turb3_stagger.yaml` + 当前兼容 checkpoint，Bridge 数值编码与生命周期证据 |
| `evidence/part6/fastfarm_control.json` | PASS / `STOPPED` | `turb3_ctrl3.yaml`，FAST.Farm 控制会话与 drain 清理证据 |

这些证据不把 A1 `SYNTH`、A2 `DIRECT`/`EXPORTED` 和 A3 Replay 混为一谈；真实人工执行器仍由现有 Trainer/安全层决定，正式训练的 CLI 也不被伪装成实时遥测源。

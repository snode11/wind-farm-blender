# WFRL Blender 前端设计说明

## 1. 项目目标

使用 Blender 5.2.1 LTS 完整替代现有 PySide6、PyVista 和 VTK 前端，同时保留现有仿真、强化学习、安全约束、场景、checkpoint 和数据保真度语义。

最终前端必须同时支持 macOS 和 Windows。开发阶段采用“标准 Blender + WFRL Extension + 专用 Workspace”的形式。最终是否增加独立品牌启动器或安装包，等功能和视觉验收完成后再决定。

## 2. 项目边界

- 总工作区：`/Users/eason/Desktop/wfcrl`。
- 当前应用代码来源：`/Users/eason/Desktop/wfcrl/wind farm RL`。
- `wfcrl-env-me` 保持独立，不在本次迁移中合并。
- 保留工作区中现有的未提交和未跟踪文件。
- 在最终切换前，现有 Studio 和 RViz 界面继续作为功能与数值对照基准。

## 3. 硬性约束

- 开发与验收统一使用 Blender `5.2.1 LTS`。
- 前端支持 Apple Silicon macOS 和 64 位 Windows。
- Blender 接管全部可见 UI、3D 渲染、动画、相机、曲线和展示流程。
- 不重写现有 `Trainer`、`SceneRuntime`、`FastFarmDriver`、FLORIS 接入、安全规则、reward、checkpoint 和场景语义。
- 不把 PyTorch、MPI、FAST.Farm 或完整后端环境安装进 Blender 自带 Python。
- 后端在现有项目 Python 环境中作为独立进程运行。
- Blender 与后端只通过 localhost 通信。
- Blender 主线程不得执行阻塞式网络读取或 FAST.Farm 计算。
- 所有 `bpy` 对象修改必须发生在 Blender 主线程。
- `DIRECT`、`EXPORTED` 和 `SYNTH` 数据必须在 UI 中明确区分。
- 地形、代理尾流和展示特效不得被标注成真实物理后端结果。
- 功能对等和跨平台验收完成前，不删除旧前端。

## 4. 总体架构

```text
Blender 5.2.1 LTS
├── WFRL Extension
│   ├── WFRL Workspace 和 UI Panel
│   ├── 场景生成器
│   ├── 实时动画
│   ├── 尾流与传感器可视化
│   ├── 曲线与告警
│   └── 非阻塞通信 Client
│
│         localhost TCP，WFRL Protocol v1
│
└─────────────── WFRL Bridge 独立进程
                  ├── 命令分发
                  ├── 最新 Snapshot 发布
                  ├── 进程生命周期和安全停止
                  └── 现有后端适配
                         ├── Trainer
                         ├── SceneRuntime
                         ├── SafetyLimiter
                         ├── FastFarmDriver
                         └── FLORIS
```

Bridge 只是新增的适配层。它可以调用现有后端公开接口，但不得复制或修改训练算法和物理执行合同。

## 5. 产品形态

第一阶段交付采用：

```text
标准 Blender
└── WFRL Extension
    └── WFRL 专用 Workspace / App Template
```

WFRL Workspace 默认布局：

- 中央：风场世界 3D Viewport；
- 可选右侧：单机视图或传感器相机视图；
- 左侧：场景、后端、风况、地形和数据通道；
- 右侧：运行模式、训练或回放、遥测、安全约束和曲线；
- 顶部：连接状态、当前场景、仿真时间、运行状态和展示模式。

提供两种界面状态：

- 展示模式：隐藏与 WFRL 无关的 Blender 编辑区域，适合老板汇报、录屏和演示。
- 开发模式：保留 Blender 的常规编辑和诊断能力。

最终交付时再选择：

1. 继续使用标准 Blender + Extension + App Template；
2. 在相同 Extension 之上增加独立品牌启动器和安装包。

核心前端不得依赖第二项选择。

## 6. 四种运行模式

### 6.1 Demo

- 不启动 FAST.Farm 或 FLORIS；
- 使用本地确定性演示数据；
- 验证 UI、模型、动画、相机和展示流程；
- 用于快速开发和老板演示。

### 6.2 交互训练

- 对应旧 Studio 的“开始训练”；
- 使用现有 `Trainer` 和 `SceneRuntime`；
- 支持开始、暂停、继续和安全停止；
- 实时显示训练、功率和安全事件。

### 6.3 正式训练

- 对应现有 `scripts/train/train_fastfarm.py`；
- 配置 backend、seed、iters、n_steps、warmup、reward 和 checkpoint；
- Blender 只负责提交参数、显示状态和展示产物；
- 不在 Blender 中复制训练实现。

### 6.4 Replay

- 加载兼容 checkpoint；
- 使用确定性策略执行；
- 展示 yaw、pitch、RPM、功率、尾流和安全事件；
- 支持暂停、镜头切换、截图和录制。

四种模式必须在 UI 和状态机中明确分开，不能共用一个含义模糊的“开始”行为。

## 7. 通信协议

`WFRL Protocol v1` 对控制命令、生命周期、遥测和小数组使用长度前缀 UTF-8 JSON。较大的尾流网格设计为独立的长度前缀二进制消息，其中包含 metadata 和压缩浮点数据；Part 3 冻结类型判别、协商和大小限制，Part 5 实现，未协商时明确拒绝。

每条消息必须包含：

- `protocol_version`
- `type`
- `session_id`
- 单调递增的 `sequence`

Part 3 的状态、数据来源及有效性细则见实施计划的“Protocol v1 必须冻结的语义”，并在实现时落入 `docs/blender/protocol-v1.md`。业务模式固定为 `demo / interactive_training / formal_training / replay`；转速对外统一为 `rotor_speed`（rpm），Bridge 兼容旧输入 `rotorspeed`。数据按字段或通道记录 fidelity、provenance、机组 ID（适用时）、步号、带时基的时间戳与有效性；缺失或非法值使用 `null` 与原因，不能填 0。

连接状态 `LOCAL DEMO / DISCONNECTED / CONNECTED` 与运行状态 `READY / STARTING / RUNNING / PAUSED / DRAINING / STOPPED / FAILED` 独立。断线保留最后已知运行状态并明确标为未确认，重连同步前禁用后端控制；本地 Demo 不依赖后端环境检查。能力探测、连接和收发均不得阻塞 Blender 主线程。

Blender 发出的命令包括：

- 连接、健康检查和断开；
- 加载与校验场景；
- 启动四种运行模式；
- 暂停、继续、单步和停止；
- 开关数据通道；
- 设置或释放手动控制；
- 选择风况、地形、相机、视图和展示选项。

Bridge 发出的事件包括：

- 后端生命周期状态；
- 场景描述和校验错误；
- 最新仿真或训练 Snapshot；
- 安全事件；
- 曲线和历史数据；
- 通道 metadata 与 fidelity；
- 尾流网格和传感器 payload；
- 进度、可恢复警告、致命错误和安全停止确认。

Blender 只保留最新的可替换 Snapshot。安全事件和生命周期变化必须保持顺序，不得丢弃。

## 8. 风机与场景结构

每台风机在 Blender 中使用稳定层级：

```text
WFRL.Turbine.T1
├── Tower
└── YawRoot
    └── Nacelle
        └── Rotor
            ├── Blade1
            ├── Blade2
            └── Blade3
```

- `yaw` 控制 `YawRoot`；
- `pitch` 控制每片叶片的局部 pitch 轴；
- `RPM` 驱动 `Rotor` 连续旋转；
- `torque`、功率和载荷主要作为数据展示，不直接伪造结构形变；
- 柔性变形只有在有明确数据来源和尺度说明时才启用。

YAML 中的物理坐标按真实米制进入 Blender。旧前端的 5 倍显示比例不得写入物理对象尺寸，画面可读性由相机与视图控制。

## 9. 功能对等范围

功能基线取 `wfrl/studio/app.py` 与 `wfrl/viz/rviz_app.py` 的合集：

- YAML 场景加载和校验；
- FAST.Farm 和 FLORIS；
- 开始、暂停、继续、单步、停止和安全排空；
- Demo、交互训练、正式训练和 Replay；
- 世界、单机和传感器相机视图；
- yaw、pitch 指令、pitch 实测、torque、RPM、功率和载荷；
- 手动控制锁定和释放；
- 通道订阅、fidelity 和 provenance；
- 安全限制、触发次数和最近事件；
- 全场及单机实时曲线；
- 地形及“不进入物理计算”的提示；
- FLORIS proxy 尾流、FAST.Farm DisXY、尾流圆环、Lidar、相机视锥和传感器数据；
- 相机选择、FOV、pitch、focus 和多视图；
- 截图和动画录制；
- 启动中、安全停止中、断线和失败状态。

要求功能结果一致，不要求复刻 Qt 控件的原始位置。

## 10. 数据真实性与视觉表达

- `DIRECT`：后端直接回传的物理测量；
- `EXPORTED`：由后端写入文件后读取的数据，例如 FAST.Farm DisXY；
- `SYNTH`：FLORIS proxy、渲染地形、相机视锥、展示粒子等合成结果。

颜色、图例、Tooltip 和详情面板必须显示 fidelity。展示特效可以增强视觉效果，但不得覆盖原始数据图例或改变数值含义。

## 11. 验收场景

### 11.1 视觉与 Demo 验收

使用 `scenes/turb3_demo.yaml`：

- 不启动真实后端；
- 验证安装、场景生成、风机层级、yaw、pitch、RPM、相机、Lidar、Panel 和展示模式；
- 用于高频视觉迭代。

### 11.2 真实后端验收

使用 `scenes/turb3_ctrl3.yaml`：

- 启动真实 FAST.Farm；
- 验证 yaw、pitch、torque、实测状态、功率、载荷、安全事件、通道订阅和安全停止。

### 11.3 Replay 验收

使用 `scenes/turb3_stagger.yaml` 和当前兼容的 checkpoint：

- 验证确定性策略回放；
- 对比 turbine ID、step、yaw、pitch、RPM、功率、reward、事件和结束状态；
- 历史文档中的性能数字必须在当前代码上重跑后才能作为门槛。

## 12. 跨平台边界

Extension 中不写死任何本机路径。Addon Preferences 保存：

- 项目目录；
- 后端 Python；
- MPI launcher；
- FAST.Farm executable；
- 默认场景；
- localhost port。

macOS 和 Windows 使用同一通信协议和 Extension 主体，只在启动器、路径格式、MPI 与进程退出方式上做平台适配。

“Blender 前端在某个平台可运行”不等于“FAST.Farm 已在该平台验证”。UI 必须分别显示 Blender、后端 Python、MPI、FAST.Farm 和连接状态。

## 13. 验证层级

验证必须分层报告：

1. 纯 Python 协议和数学转换测试；
2. Blender background mode 的 Extension 与场景测试；
3. 可见 Blender Demo 和截图检查；
4. FLORIS 集成与数值对等；
5. Windows FAST.Farm 集成；
6. 配置原生 executable 后的 macOS FAST.Farm 集成；
7. 老板或用户的完整展示验收。

前一层通过不能被写成后一层已经通过。

## 14. 最终切换条件

满足以下条件后，Blender 才能成为默认前端：

- 功能对等矩阵全部达到规定状态；
- 关键 Replay 数值对等；
- FAST.Farm 能安全停止且没有孤儿进程；
- macOS 与 Windows 的安装和基本工作流通过；
- fidelity 和物理边界表达通过审查；
- 老板认可视觉和演示流程；
- 保留一个可恢复的旧前端版本。

旧 Studio 和 RViz 的删除不属于本设计的默认动作，必须另行审批。

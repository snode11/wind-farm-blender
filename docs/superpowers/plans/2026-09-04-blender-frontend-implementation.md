# WFRL Blender 前端实施计划

> **执行要求：** 实施时使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`，按 Part 顺序推进。每个 Part 都必须完成测试、证据记录和评审后才能进入下一层。

**目标：** 使用 Blender 5.2.1 LTS 构建专业、可展示、可操作的 WFRL 前端，完整替代现有 Studio 和 RViz，同时保持后端语义不变并支持 macOS 与 Windows。

**架构：** Blender Extension 负责 UI、3D、动画、相机、曲线和展示；独立 WFRL Bridge 运行在现有项目 Python 环境中，通过 localhost TCP 将现有 `Trainer`、`SceneRuntime`、FAST.Farm、FLORIS 和安全层连接到 Blender。

**技术栈：** Blender 5.2.1 LTS、Blender Python API（`bpy`、`gpu`、`blf`）、Python 标准库 socket/JSON/struct/zlib、现有 WFRL Python 环境、pytest、Blender background mode 测试。

**设计依据：** `docs/superpowers/specs/2026-09-04-blender-frontend-design.md`

## 全局约束

- 总工作区为 `/Users/eason/Desktop/wfcrl`，实际实现位于 `/Users/eason/Desktop/wfcrl/wind farm RL`。
- 开发与验收统一使用 Blender `5.2.1 LTS`。
- 支持 Apple Silicon macOS 和 64 位 Windows。
- 不改变现有训练算法、物理合同、reward、安全规则、checkpoint 和场景语义。
- Blender 不安装或导入 PyTorch、MPI、FAST.Farm 和完整后端环境。
- Blender 对象只能在 Blender 主线程修改。
- Blender 事件循环中不得发生阻塞式后端操作。
- `DIRECT`、`EXPORTED` 和 `SYNTH` 必须明确标注。
- 旧 Studio 和 RViz 在完整验收前保持可用。
- 独立品牌安装包不属于第一阶段交付，不影响核心代码结构。

---

## Part 1：产品定义、功能基线与视觉方向

### 本层目标

先确定“对外展示的产品是什么”，避免把旧 Studio 原样搬进 Blender。形成所有后续开发共用的功能、视觉和验收标准。

### 用户可见成果

- 一份覆盖 Studio 与 RViz 的功能对等矩阵；
- 一套 WFRL UI 信息架构；
- 一段 60～90 秒老板演示脚本；
- 一套视觉方向、配色和画面层级；
- 三个固定验收场景及其证据边界。

### 文件

- 新建：`docs/blender/feature-parity.md`
- 新建：`docs/blender/presentation-flow.md`
- 新建：`docs/blender/visual-direction.md`
- 只读分析：`wfrl/studio/app.py`
- 只读分析：`wfrl/studio/view.py`
- 只读分析：`wfrl/viz/rviz_app.py`
- 只读分析：`wfrl/studio/trainer.py`
- 只读分析：`docs/演示指令单.md`

### 实施步骤

- [ ] 列出 Studio 与 RViz 的全部按钮、菜单、Panel、视图、状态、数据通道和后端副作用。
- [ ] 给每项功能分配稳定 ID，例如 `scene.load`、`run.start`、`run.pause`、`channel.set`、`camera.select`、`wake.disxy`。
- [ ] 在功能矩阵中记录旧入口、输入、输出、后端作用、fidelity、验收场景和证据类型。
- [ ] 把四种模式固定为 Demo、交互训练、正式训练和 Replay，并明确每种模式允许的操作。
- [ ] 设计老板演示流程：进入 WFRL、加载场景、启动风机、显示尾流、选中机组、切相机、查看功率、安全事件和策略效果。
- [ ] 确定首版视觉语言为“工业数字孪生控制平台”，避免默认 Blender 灰色建模界面和过度游戏化效果。
- [ ] 选定 `turb3_demo.yaml`、`turb3_ctrl3.yaml` 和 `turb3_stagger.yaml` 分别承担 Demo、真实后端和 Replay 验收。
- [ ] 评审文案、单位、数据真实性标识和错误状态，确保非开发人员也能理解。

### 进入下一层的门槛

- 每个旧前端功能都在矩阵中有对应行；
- 老板演示流程无含义不清的按钮；
- 能明确回答哪些画面是物理真值、导出数据和合成效果；
- 用户认可整体视觉方向。

---

## Part 2：高质量静态 Blender 垂直原型

### 本层目标

在不连接真实后端的情况下，尽快做出一段“看起来已经像正式产品”的 Blender 展示，优先验证老板最关心的视觉质量。

### 用户可见成果

- WFRL 专用 Workspace；
- 三台结构正确的 NREL 5MW 风机；
- 地形、天空、灯光和材质；
- 世界视图、单机视图和机舱相机；
- 场景、运行、功率、安全等 Panel 的静态样式；
- yaw、pitch 和 RPM 的离线动画；
- 一段可直接给老板看的 60～90 秒演示。

### 文件

- 新建：`blender_frontend/wfrl_blender/blender_manifest.toml`
- 新建：`blender_frontend/wfrl_blender/__init__.py`
- 新建：`blender_frontend/wfrl_blender/workspace.py`
- 新建：`blender_frontend/wfrl_blender/state.py`
- 新建：`blender_frontend/wfrl_blender/scene_model.py`
- 新建：`blender_frontend/wfrl_blender/scene_builder.py`
- 新建：`blender_frontend/wfrl_blender/materials.py`
- 新建：`blender_frontend/wfrl_blender/cameras.py`
- 新建：`blender_frontend/wfrl_blender/panels/demo.py`
- 新建：`blender_frontend/tests/blender/static_scene_smoke.py`
- 新建：`scripts/blender/build_extension.py`

### 核心接口

```python
class SceneDTO:
    @classmethod
    def from_mapping(cls, data: dict) -> "SceneDTO": ...

def build_scene(scene: SceneDTO,
                collection_name: str = "WFRL_Scene") -> object: ...

def apply_demo_state(yaw_deg: list[float],
                     pitch_deg: list[float],
                     rpm: list[float]) -> None: ...
```

### 实施步骤

- [ ] 编写 Blender background mode 测试，要求加载 Extension 后存在 WFRL Workspace 和运行状态。
- [ ] 运行测试并保留首次失败证据。
- [ ] 建立 Extension manifest、幂等的 `register()` 和 `unregister()`。
- [ ] 创建 `WFRL.Turbine.<id>.<part>` 稳定命名和 `Tower -> YawRoot -> Nacelle -> Rotor -> Blade1..3` 层级。
- [ ] 使用现有几何来源建立程序化首版风机，不依赖外部付费模型。
- [ ] 按真实米制建立场景，不把旧前端的 5 倍显示比例写进物理尺寸。
- [ ] 建立 Eevee 兼容材质、工业风灯光、天空和渲染级地形。
- [ ] 在地形和合成效果上显示 `SYNTH / 不进入物理计算`。
- [ ] 建立世界、单机和机舱相机，并验证 1920×1080 取景。
- [ ] 实现确定性 Demo 动画，展示启动、运行、yaw、pitch 和停机。
- [ ] 连续重建两次场景，确认只清理 WFRL Collection，不影响其他 Blender 对象。
- [x] 打包当前版本 `dist/wfrl_blender-0.2.0.zip`，并保留干净 Blender 配置安装验证路径（真实 Blender smoke 受启动期 Metal 崩溃限制）。
- [ ] 输出世界视图、机舱相机和 WFRL Workspace 截图供视觉评审。

### 进入下一层的门槛

- 第一眼像 WFRL 产品，不像 Blender 默认工程；
- 风机比例、轴心、相机和动画方向正确；
- Workspace 在 Blender 重启后可恢复；
- Extension 可安装、启用、禁用和再次启用；
- 用户认可视觉方向后才继续扩展。

---

## Part 3：正式前端骨架、状态机与数据协议

### 开工基线与范围

- Part 2 已由用户在参考任务 `01a07219-343d-7152-a302-250fe6077104` 中人工认可，状态为 `ACCEPTED`；资产基线见 `docs/blender/part2-baseline.json`，验收边界见 `docs/blender/PART2.md`。
- 基线记录当前代码提交、交付资产 SHA256 与已确认的旧图删除；已有资源刷新属于 Part 2，后续提交应与 Part 3 实现分开。基线记录不等于这些资源已提交。
- 本层只实现骨架、状态机、配置、传输与协议；使用本地协议测试端验证连接。真实 Bridge/Trainer/FLORIS/FAST.Farm 闭环仍在 Part 4，尾流和传感器呈现在 Part 5。
- 不增加“场景对象与源代码一致”自动检查；保留已有几何、场景、Demo 检查和人工验收。

### 本层目标

把视觉原型整理成可长期维护的正式前端结构，并冻结 Blender 与后端之间的接口。

### 用户可见成果

- 清楚的连接与运行状态；
- 后端路径配置和环境检查；
- 连接失败、版本不兼容和断线提示；
- 稳定的 Extension 生命周期。

### 文件

- 新建：`docs/blender/protocol-v1.md`
- 新建：`wfrl/blender_bridge/__init__.py`
- 新建：`wfrl/blender_bridge/messages.py`
- 扩展：`blender_frontend/wfrl_blender/state.py`（保留离线 Demo 行为）
- 扩展：`blender_frontend/wfrl_blender/__init__.py`（注册、清理与重载）
- 新建：`blender_frontend/wfrl_blender/preferences.py`
- 新建：`blender_frontend/wfrl_blender/transport.py`
- 新建：`blender_frontend/wfrl_blender/panels/status.py`
- 新建：`tests/blender_bridge/test_messages.py`
- 新建：`blender_frontend/tests/test_ui_state.py`
- 新建：`blender_frontend/tests/blender/extension_lifecycle_smoke.py`

### 核心接口

```python
def encode_message(message: dict) -> bytes: ...

class FrameDecoder:
    def feed(self, chunk: bytes) -> list[dict]: ...

class TransportClient:
    def connect(self) -> None: ...
    def poll(self) -> list[dict]: ...
    def send(self, message: dict) -> None: ...
    def close(self) -> None: ...
```

连接与运行分别建模，不共用一条状态链：

```text
connection.status: LOCAL DEMO / DISCONNECTED / CONNECTED
run.status: READY / STARTING / RUNNING / PAUSED / DRAINING / STOPPED / FAILED

READY/STOPPED -> STARTING -> RUNNING
RUNNING -> PAUSED -> RUNNING
STARTING/RUNNING/PAUSED -> DRAINING -> STOPPED
STARTING/RUNNING/PAUSED/DRAINING -> FAILED
FAILED -> READY（错误处理完成且会话状态已确认）
```

- `LOCAL DEMO` 表示本地数据源，无需 socket；沿用 Part 2 的启动阶段可暂停、恢复到对应阶段、暂停单步、直接停止和 Reset 行为，不强制经过真实后端的排空。
- `CONNECTED` 仅在协议握手成功后成立；连接中、超时及版本错误作为连接进度/诊断显示。
- 断线只更新连接状态。保留最后已知运行状态并显示“状态未确认”和数据过期，不能伪造 `STOPPED` 或确认后端已退出；重连同步会话后才恢复控制。
- 四种业务模式保持 Demo、交互训练、正式训练、Replay；协议值固定为 `demo / interactive_training / formal_training / replay`。A1 Demo、A2 Real、A3 Replay 是验收场景分类，不能替代业务模式。
- 后端模式的状态迁移以生命周期事件为准；暂停/单步按模式与能力启用。活动会话（含 `PAUSED`、`DRAINING` 和断线未确认）禁止重复启动及更换场景、模式、后端和关键配置。

### Protocol v1 必须冻结的语义

- Envelope 保留 `protocol_version / type / session_id / sequence`；定义握手前会话标识、握手分配、重连与旧会话消息拒收规则。`sequence` 按会话及发送方向递增，允许合并可替换帧产生间隙；重复/倒退必须检测。
- 遥测、曲线、尾流和传感器数据携带业务 `mode`、`fidelity`、`provenance`、`step` 与带时基说明的 `timestamp`；机组数据使用稳定 `turbine_id`，批量数组明确 ID 顺序，风场级数据不伪造机组 ID。
- `fidelity` 使用 `SYNTH / DIRECT / EXPORTED`；混合来源按字段或通道标注，不能用整帧的单一标签覆盖差异。`provenance` 记录适用的后端会话、文件/通道或合成公式来源。
- 转速 canonical key 为 `rotor_speed`，单位 rpm；Bridge 兼容输入 `rotorspeed`，对外只输出 canonical key，同时定义双别名冲突处理。不修改现有后端通道名或物理合同。
- 数据定义 `validity / error`，区分不支持、等待首帧、有效、过期与非法值；缺失值和 NaN/Infinity 不得填 0，以 JSON `null` 加原因表示。数据年龄由接收端单调时钟结合来源年龄计算，阈值按通道固定，不混用仿真时间与墙钟。
- Part 3 实现四字节大端长度前缀 UTF-8 JSON；较大尾流二进制帧保留在 Part 5 实现。协议文档须定义其类型判别、metadata、长度/解压上限与版本协商，未协商的二进制消息明确拒绝，不能送入 JSON 解码器。

### 实施步骤

- [ ] 为消息分片、多消息同包、非法 UTF-8、超大 frame、缺少 envelope、版本不兼容和 sequence 倒退编写失败测试。
- [ ] 实现四字节大端长度前缀加 UTF-8 JSON 的 `WFRL Protocol v1`。
- [ ] 每条消息强制包含 `protocol_version`、`type`、`session_id` 和 `sequence`。
- [ ] 定义命令、生命周期、Snapshot、安全事件、曲线和尾流消息结构。
- [ ] 创建 Blender Addon Preferences，配置项目目录、后端 Python、MPI、FAST.Farm、默认场景和 port。
- [ ] 分别显示 Blender、后端 Python、MPI、FAST.Farm 和 Bridge 连接健康状态；按当前后端判定必需能力，未配置 FAST.Farm/MPI 不阻塞 LOCAL DEMO。
- [ ] 环境检查采用可取消、有超时的异步探测；不得在 Blender timer 中执行阻塞的 subprocess、connect、recv 或 send。
- [ ] 使用 `bpy.app.timers` 非阻塞轮询 socket，不在 Blender 中创建修改场景的后台线程。
- [ ] 为连接状态 × 运行状态 × 模式/能力编写 UI enable/disable 测试，覆盖暂停、排空及断线未确认时的配置锁定。
- [ ] 测试 provenance、混合 fidelity、canonical key、缺失/非法/过期值、会话重连和 sequence 规则。
- [ ] 用本地测试端覆盖握手失败、版本不兼容、超时、半包、部分发送、断线与重连；为收发队列及每次 timer 工作设置上限，可靠事件不能静默丢弃，溢出必须显式报错。
- [ ] 在后端 Python、MPI 和 FAST.Farm 均不可用时验证 Demo 加载、播放、暂停、单步与停止。
- [ ] 连续启用、禁用、重连和重载 Extension，确认没有重复 timer、handler 和 Panel。

### 进入下一层的门槛

- 协议、状态组合与传输测试全部通过，协议文档与测试覆盖上述冻结语义；
- Blender 在后端不存在、断线和错误消息下仍保持响应；
- 路径和命令参数不写死个人目录；
- Extension 重载不泄漏 timer 或 handler。

---

## Part 4：打通最小真实数据闭环

### 本层目标

用最少功能证明 Blender 能可靠控制现有后端，并由真实 Snapshot 驱动场景和数据面板。

### 数据闭环

```text
Blender Operator
→ WFRL Bridge
→ 现有 Trainer / SceneRuntime
→ Snapshot
→ Blender 状态
→ 风机姿态和遥测 Panel
```

### 文件

- 新建：`wfrl/blender_bridge/snapshot_adapter.py`
- 新建：`wfrl/blender_bridge/backend_session.py`
- 新建：`wfrl/blender_bridge/server.py`
- 新建：`wfrl/blender_bridge/__main__.py`
- 新建：`blender_frontend/wfrl_blender/operators/connection.py`
- 新建：`blender_frontend/wfrl_blender/operators/run.py`
- 新建：`blender_frontend/wfrl_blender/animation.py`
- 新建：`tests/blender_bridge/test_snapshot_adapter.py`
- 新建：`tests/blender_bridge/test_backend_session.py`
- 新建：`tests/blender_bridge/test_server.py`
- 新建：`blender_frontend/tests/test_animation_math.py`

### 核心接口

```python
class SnapshotAdapter:
    def encode(self, snapshot) -> dict: ...

class BackendSession:
    def start(self, mode: str, options: dict) -> None: ...
    def command(self, command: dict) -> None: ...
    def stop(self, timeout_seconds: float): ...

class KinematicState:
    def apply_snapshot(self, message: dict) -> None: ...
    def advance(self, frame_dt_seconds: float) -> None: ...
```

### 实施步骤

- [x] 用合成 Snapshot 编写 adapter 测试，覆盖 NumPy 转换、单位、turbine ID、NaN、fidelity、安全事件和 JSON 序列化。
- [x] 使用 fake backend 验证 connect、start、pause、resume、stop、disconnect 和 failure。
- [x] Bridge 的交互训练只包装现有 `Trainer`，不得复制 `_train()`。
- [x] 正式训练只通过 `mpiexec -n 1` 启动现有 `train_fastfarm.py`，不得在 Bridge 中另写一套算法。
- [x] Snapshot 到达时只更新目标 yaw、pitch、RPM、功率和载荷。
- [x] Blender 按真实 frame 时间积分 Rotor 角度，保持渲染与控制步解耦。
- [x] Blender 只保留最新可替换 Snapshot，但不丢弃安全事件和生命周期变化。
- [x] 首先连接 fake backend，再连接 FLORIS，最后连接 FAST.Farm。
- [x] 对比直接后端 Snapshot 与 Blender 接收结构，逐字段检查 ID、step、yaw、pitch、RPM、power 和 reward；不支持的 FLORIS pitch/RPM 保持 `null + unsupported`。
- [x] 验证后端崩溃、Blender 断开、重复 stop 和 stop timeout。
- [x] 验证安全停止后没有遗留由本次会话启动的子进程。

### 进入下一层的门槛

- Blender 在启动、暂停、继续、断线和停止过程中不假死；
- 同一 Snapshot 的数值与直接后端输出一致；
- Blender 环境中没有 PyTorch、MPI 和 FAST.Farm 依赖；
- 后端异常不会导致 Blender 崩溃；
- FAST.Farm 结束状态和排空过程在 UI 中可见。

---

## Part 5：完整 3D、尾流、传感器、大气与科学表达

### 本层目标

发挥 Blender 的视觉优势，用轻量尾流表现和大气环境增强空间感与风场尺度，同时保证尾流、传感器和装饰效果不会误导物理含义。本层不扩展为完整电影级 PBR 材质工程。

### 用户可见成果

- 平滑 yaw、pitch 和 Rotor 动画；
- FLORIS proxy 尾流；
- FAST.Farm DisXY；
- 具有空间层次的半透明尾流、流线或尾流圆环；
- Lidar 点云或射线；
- 相机视锥与传感器相机；
- 世界、单机、相机多视图；
- 晴天、阴天和黄昏三种轻量环境预设；
- 可独立开关的大气雾、空气透视和尾流视觉增强；
- 截图和动画录制。

### 文件

- 新建：`blender_frontend/wfrl_blender/wake.py`
- 新建：`blender_frontend/wfrl_blender/sensors.py`
- 新建：`blender_frontend/wfrl_blender/overlays.py`
- 新建：`blender_frontend/wfrl_blender/atmosphere.py`
- 扩展：`blender_frontend/wfrl_blender/animation.py`
- 扩展：`blender_frontend/wfrl_blender/cameras.py`
- 新建：`blender_frontend/tests/test_wake_payload.py`
- 新建：`blender_frontend/tests/test_atmosphere_presets.py`
- 新建：`blender_frontend/tests/blender/live_scene_smoke.py`
- 只读对照：`wfrl/viz/wakevtk.py`
- 只读对照：`wfrl/viz/wake_rings.py`
- 只读对照：`wfrl/channels/sensors.py`

### 实施步骤

- [x] 用精确数学测试固定 yaw 轴、pitch 局部轴、RPM 积分、暂停和手动锁优先级。
- [x] 为 FLORIS proxy、DisXY 和 Lidar 定义不同 payload 和 fidelity。
- [x] FLORIS proxy 固定标为 `SYNTH`，DisXY 固定标为 `EXPORTED`。
- [x] 尾流采用半透明体积、流线或圆环增强空间层次；物理数据决定几何和强度，颜色、透明度与粒子仅作视觉编码，并在 overlay 中保留来源和 fidelity。
- [x] 更新尾流时复用 Mesh topology，只更新 vertex、attribute、texture 或 volume data。
- [x] 较新的尾流 sequence 到达时丢弃未处理的旧可替换 frame，避免队列积压。
- [x] 实现晴天、阴天和黄昏环境预设，统一配置 World、太阳光、轻度体积雾与远景颜色衰减；这些效果只作视觉表达，不声明为气象仿真结果。
- [x] 大气和尾流视觉增强均可独立关闭；实时视口使用轻量参数，截图和动画渲染可切换高质量参数。
- [x] 实现世界、单机和相机视图，支持 FOV、pitch、focus 和视图切换。
- [x] 实现 presentation overlay，显示时间、风速、backend、fidelity、功率和运行状态。
- [x] 添加截图、相机 Render 和动画录制入口。
- [x] 使用确定性合成流进行对象/队列压力 smoke，检查 object 数量稳定和最新 sequence；完整 10 分钟内存曲线留待真实演示验收。
- [ ] 对照旧前端分别截取世界、相机、proxy、DisXY 和 Lidar 画面，并为三种环境预设各保留固定机位截图，检查含义一致、风机轮廓清楚且视觉更有空间感。

### 进入下一层的门槛

- 视口以目标 30 FPS 独立于后端控制步运行；
- 两个 Snapshot 之间 Rotor 连续旋转；
- 长时间运行不持续增加 Blender object；
- 每种可视化都能追溯来源和 fidelity；
- 关闭大气和尾流装饰后不影响仿真数据、控制状态或科学观察；
- 三种环境预设在固定机位下具有清晰差异，体积雾和半透明尾流不会遮挡风机主体；
- 用户认可 3D、相机和尾流的展示质量。

---

## Part 6：完整业务工作流与旧前端功能对等

### 本层目标

让用户可以完全在 Blender 中完成旧 Studio 与 RViz 的全部必要操作，不再依赖旧窗口。

### 文件

- 新建：`blender_frontend/wfrl_blender/panels/scene.py`
- 新建：`blender_frontend/wfrl_blender/panels/channels.py`
- 新建：`blender_frontend/wfrl_blender/panels/run.py`
- 新建：`blender_frontend/wfrl_blender/panels/telemetry.py`
- 新建：`blender_frontend/wfrl_blender/panels/safety.py`
- 新建：`blender_frontend/wfrl_blender/charts.py`
- 新建：`blender_frontend/wfrl_blender/presentation.py`
- 新建：`blender_frontend/tests/blender/workflow_smoke.py`
- 更新：`docs/blender/feature-parity.md`

### 实施步骤

- [x] 完成 Demo、交互训练、正式训练和 Replay 四种模式的入口和状态隔离。
- [x] 完成 scene、backend、wind、terrain 和 channel 配置。
- [x] 完成展示姿态的 yaw、pitch、torque 入口；真实执行器继续由现有 Trainer/安全层负责，界面不绕过该边界。
- [x] 完成 start、pause、resume、single-step、stop 和 draining 状态。
- [x] 完成功率、reward、yaw、pitch、torque、RPM 和 load 的有界历史与曲线入口。
- [x] 完成安全规则触发次数、最近事件和 command/measured 对照字段的展示入口。
- [x] 完成相机选择、FOV、pitch、focus 和多视图布局。
- [x] 完成展示模式、截图和动画录制。
- [x] 在 Demo 工作流、fake/FLORIS 既有 Bridge 证据和 Part 6 workflow smoke 下执行前端关键功能。
- [x] 使用 `turb3_stagger.yaml` 和当前兼容 checkpoint 执行 Replay 数值对等。
- [x] 使用 `turb3_ctrl3.yaml` 执行真实 FAST.Farm 控制与安全停止验收。
- [x] 在 `evidence/part6/progress.md` 和 `docs/blender/feature-parity.md` 记录 Blender 版本、OS、场景、证据路径和验证边界。
- [x] fake、FLORIS 和 FAST.Farm 证据分别记录，不互相替代。

### 进入下一层的门槛

- Part 6 范围内的基础入口、状态机、配置、遥测、相机和已声明的后端证据完成；完整用户体验对等和训练可观测性由 Part 6.5 收口；
- Replay 的 turbine ID、step、yaw、pitch、RPM、power、reward 和结束状态对等；
- 用户能完整演示而不打开旧 Studio 或 RViz；
- 后端未因前端迁移产生算法或物理语义变化。

## Part 6.5：功能对等补齐、训练可观测性与操作增强

### 本层定位

Part 6 已经建立了 Blender 中的四种模式、运行命令、遥测和展示入口，但“入口存在”不等于“用户体验已经与旧 Studio/RViz 对等”。本层是进入跨平台交付前的强制补齐阶段，专门关闭当前验收中仍然可见的缺口。不得用合成数据显示来掩盖后端没有提供的能力；凡是后端不支持的字段或命令，必须显示为“不支持/等待/未连接”，不能伪造为成功。

本层仍然保持三个边界：

- Demo/Presentation 的手动摆位是展示姿态锁，不是绕过 Trainer 和安全层的真实控制器；
- 真实 yaw、pitch、torque 只有在现有 Bridge/Trainer 明确提供能力并通过安全检查时才能发送；
- 正式训练的训练统计只能显示现有训练进程实际输出的字段，缺失字段显示原因，不从单帧遥测推导假的 loss 或 explained variance。

### 本层已冻结的决策

- 训练统计使用新的 `training_stats` 消息类型，不把训练统计伪装成普通 `snapshot` 或 `curve`。该类型在 Protocol v1 中显式登记，并通过 `training_stats_v1` capability 协商；未协商时不得发送，前端显示“不支持/未协商”。
- `training_stats` payload 固定包含 `run_id`、`record_kind`、`mode`、`step`、`agent_step`、`timestamp`、`iteration`、`phase`、可选 `episode` 和 `stats`。`stats` 是以统计名为 key 的 channel-record map，至少定义 `mean_power(MW)`、`mean_reward`、`value_loss`、`explained_variance`、`episode_return`、`learning_rate`；每个字段独立携带 `validity/error/fidelity/provenance/source_age_seconds/stale_after_seconds`。`iteration`、`step`、`agent_step` 和 `episode` 都是非负安全整数，`phase` 使用 `warmup/sampling/updating/done/waiting`。
- 正式训练通过每次运行独占的结构化 JSONL 进度文件输出训练统计。`train_fastfarm.py` 在实际阶段切换时追加进度记录，在每轮实际统计完成后追加统计记录并 flush；Bridge 在自己的工作线程中非阻塞读取并转发，Blender 只接收消息，不读取训练进程 stdout，也不在 Blender timer 中打开或解析训练文件。
- JSONL 和 `training_stats` 明确区分 `record_kind=progress/iteration_stats`，均携带独立 `run_id`；`progress` 的 `stats` 为空，仅更新阶段与进度，不刷新已有指标的来源时间。进入 warmup、sampling、updating 和正常结束时由训练进程写入真实阶段事件；失败和用户停止以生命周期为准，不伪装成 `done`。首条进度前显示等待，不由 Bridge 猜测阶段。阶段标签显示“最近确认阶段”和年龄，不承诺阻塞物理步内部的连续进度。
- 统计口径固定为：`mean_power` 是本轮各控制步风场总功率的平均值，单位 MW；`mean_reward` 对应现有 `B_rew.mean()`，明确标记“归一化训练 reward”，不可与即时原始 reward 混称；可额外提供 `mean_raw_reward`，但必须独立命名并保留实际来源。`value_loss` 对应本轮实际优化 minibatch 的 `v_log` 均值，`explained_variance` 对应现有 `ev`。没有优化样本或存在 NaN/Inf 时输出 null 和真实缺失/无效原因，不输出非法 JSON 数值。
- `step` 统一表示累计 control steps，另设 `agent_step` 表示累计 agent transitions；不得直接把当前脚本的 `global_step` 当作 control steps。`iteration` 沿用训练的全局迭代号，`episode` 仅在能确认其全局含义时提供。恢复训练从 checkpoint 元数据恢复计数；旧 checkpoint 只有在历史配置足以精确还原时才推导，否则预检明确拒绝本次可观测训练启动并说明原因。增加独立观测计数，不改变优化、奖励、采样或物理合同，也不静默改写旧训练计数逻辑。
- `training_stats_v1` 定义独立 freshness：progress 与统计记录的 `stale_after_seconds` 固定为 30 秒；年龄从真实记录产生时间开始，Bridge 转发时保留文件等待时间，前端用单调时钟继续累加。超过阈值显示 STALE，长迭代允许上轮统计自然过期；新 progress 不使旧统计恢复 valid。该规则作为协商扩展登记，不修改已有 snapshot/curve 的 2 秒规则。
- `training_stats_v1` 必须由客户端声明、服务端确认双方支持后生效；服务端逐连接保存协商结果，发送与接收两端均执行门控。生命周期能力不得重新引入未协商扩展；从 Demo 切到正式训练时，只有该连接已协商扩展才发送统计。重连重新协商，保留协议规定的 session/sequence 连续性，未协商的重连连接仍不得接收统计。
- `session_id` 表示协议会话，`run_id` 表示一次运行：每次成功创建运行分配新 run_id，重连沿用当前 run_id，Reset/新运行隔离缓存和展示锁。每次正式运行使用独占 JSONL；读端按字节偏移缓冲半行，只解析完整换行记录，校验 run_id、单行大小和记录顺序。停止/退出后排空完整尾记录再关闭读端，残缺尾行记录错误；截断、坏行和写入失败显式报告可观测性故障，不编造统计，也不把日志故障冒充训练退出。
- 功能矩阵新增稳定 ID：`history.export`、`training.stats`、`display.restore`。其中 `history.export` 和 `display.restore` 是本地前端操作，不伪装成后端命令；`training.stats` 对应 `training_stats` 消息和正式训练进度来源。
- 手动展示姿态只允许在 `PAUSED` 状态下锁定；它始终是 `SYNTH / 展示姿态`，不写入 Trainer、策略或安全执行器。暂停时转子相位冻结，修改 RPM 只更新待运行的展示设定；Resume 默认释放展示锁，用户也可显式选择“保留展示锁并继续”，此时转子按手动 RPM 积分且持续显示锁定机组、通道和 SYNTH 标记。锁保留期间只读，必须再次暂停才可修改；释放时保持转子相位连续，仅切换 RPM 来源。Reset、新运行或切换场景必须清空锁。
- 释放当前机组或全部机组时，优先立即恢复该机组最近一次有效 Snapshot；如果当前会话从未收到有效 Snapshot，才恢复该模式定义的默认姿态。释放不会等待一个可能永远不到达的“下一帧”。
- 断线不提供“停止确认”按钮，也不能把本地 socket 断开当成 `STOPPED`。断线时只允许重连/重新握手；界面保留最后已知 `run.status` 并标记“状态未确认”。只有收到后端生命周期消息，或由 Bridge 明确确认自己启动的进程已清理，才能显示 `STOPPED`。
- `training_stats` 的统计字段必须逐字段保留 `validity`、`error`、`fidelity`、`provenance`、`source_age_seconds` 和 `stale_after_seconds`。当前后端未实际提供的 `episode_return`、`learning_rate` 等字段显示为 `unsupported`，不能从 `reward` 或 `power` 推导。

### 用户可见成果

- 手动摆位具备完整的 yaw、pitch、RPM、单机释放、全部释放、锁定状态和恢复后端姿态能力；RPM 会真实驱动展示转子动画，并在暂停时保持相位。
- 运行控制在主 Presentation/Status 区域可发现，不要求用户先猜到某个隐藏 Panel；按 Demo、交互训练、正式训练和 Replay 显示正确文案、前置条件、禁用原因和当前连接/运行状态。
- 开始训练后的页面能同时看到当前 step/iteration/phase、风场总功率、当前 reward、每机功率和 reward（若后端提供）、数据年龄、fidelity 和 provenance。
- 交互训练和 Replay 有可读的实时曲线；正式训练在有输出时显示 mean power、mean reward、value loss、explained variance、episode return 等统计，并区分“尚未产生”和“不支持”。
- 用户可以导出当前运行的有界遥测/训练历史窗口，便于与旧 Studio 逐字段核对；明确显示覆盖范围与截断情况，导出不改变仿真状态，不宣称完整训练档案。

### 需要扩展的文件

- 扩展：`blender_frontend/wfrl_blender/panels/demo.py`、`blender_frontend/wfrl_blender/panels/status.py`、`blender_frontend/wfrl_blender/panels/run.py`、`blender_frontend/wfrl_blender/panels/telemetry.py`、`blender_frontend/wfrl_blender/charts.py`、`blender_frontend/wfrl_blender/state.py`、`blender_frontend/wfrl_blender/animation.py`。
- 扩展：`blender_frontend/wfrl_blender/operators/workflow.py`、`blender_frontend/wfrl_blender/runtime.py` 和 `wfrl/blender_bridge/workflow.py`，补齐能力探测、命令可用性、`training_stats` 接收和 `curve`/统计消息的前端入库。
- 扩展：`wfrl/blender_bridge/server.py` 和前端实际 transport 实现，补齐握手能力交集、逐连接协商状态、发送/接收门控、重连及模式切换测试。
- 扩展：`wfrl/blender_bridge/messages.py`、`docs/blender/protocol-v1.md`，登记 `training_stats` 消息、`training_stats_v1` capability、字段校验、序号规则和 freshness 语义。
- 扩展：`wfrl/blender_bridge/backend_session.py` 和 `scripts/train/train_fastfarm.py`，为正式训练创建运行独占 JSONL 进度文件并由 Bridge 非阻塞读取；不得改变训练算法和物理合同。
- 扩展：`wfrl/blender_bridge/snapshot_adapter.py`，统一 farm-level power/reward、数据年龄、缺失原因和 provenance；RPM 的 fidelity 必须按实际来源判定，不能按字段名固定标成 `SYNTH`。
- 扩展：`blender_frontend/wfrl_blender/charts.py`，保留导出所需的原始 `error`、source age、session/mode、时间基准、iteration/phase/episode 和 command/measured 区分，不只保留绘图点。
- 新增测试：`blender_frontend/tests/test_manual_pose_controls.py`、`blender_frontend/tests/test_training_dashboard.py`、`blender_frontend/tests/test_training_stats_protocol.py`、`blender_frontend/tests/test_history_export.py`、`blender_frontend/tests/blender/manual_pose_smoke.py`、`blender_frontend/tests/blender/training_controls_smoke.py`。
- 更新：`docs/blender/feature-parity.md`、`evidence/part6/progress.md`，新增并为 `history.export`、`training.stats`、`display.restore` 记录证据路径、真实性和真实边界；未完成项不得继续标为“已完成”。
- 新增证据目录：`evidence/part6.5/`，至少保存手动锁/释放、训练统计 JSONL→消息、过期/不支持字段和历史导出的可复核记录。

### 实施步骤

执行顺序固定为：①协议、指标口径、run_id 和能力门控；②训练 JSONL→Bridge→前端状态全链路；③展示锁和分模式运行控制；④仪表盘、曲线和导出；⑤真实后端与原生窗口验收。前两阶段先通过无 Blender 的协议和生产者/读端测试，再接入 UI。


- [ ] 把手动摆位从“只有 yaw/pitch 的几何预览”补成完整的 yaw、pitch、RPM 控件；与旧 Studio 对齐为 yaw `-40..40°/1°`、pitch `0..90°/1°`、RPM `0..20 rpm/0.1 rpm`，并显示当前机组及每个通道是否锁定。
- [ ] 增加“释放当前机组”“释放全部机组”“恢复最新 Snapshot/默认姿态”操作。释放时优先立即恢复最近一次有效 Snapshot；无 Snapshot 时回退模式默认姿态，不能等待未知的下一帧或遗留隐藏锁。
- [ ] 为手动锁按 `turbine_id + channel` 保存，且只允许在 `PAUSED` 状态下修改；Snapshot 更新、暂停、切换机组和重置时写回明确的锁定策略。
- [ ] 让手动 RPM 驱动既有真实时间转子积分，校验 0 rpm 停转、负值与超过 20 rpm 被拒绝、暂停相位冻结、显式保留锁后的恢复积分、释放时相位连续并按上述优先级恢复；在 UI 中标记 `SYNTH / 展示姿态`。
- [ ] 在 Presentation/Status 主区域提供当前模式、connection.status、run.status 和主要运行按钮；Run Panel 仍保留完整控制，但不再是唯一入口。
- [ ] 完善 `Start`、`Pause`、`Resume`、`Single-step`、`Stop`、`Reset` 的可用性矩阵：启动前检查场景和 Bridge，暂停态才允许单步，`DRAINING` 禁止重复停止；断线时只允许重连/重新握手，不把断线当作停止确认。
- [ ] 按模式显示“开始演示”“开始训练”“提交正式训练”“开始回放”等文案，并在按钮旁显示不可用原因；Demo 不得继续显示“开始训练”。
- [ ] 增加明确的 Bridge 连接/断开/重连入口和预检摘要；如支持由 Blender 启动 Bridge，必须显示已启动进程、端口、日志位置和安全停止结果，不得在后台静默拉起未知进程。
- [ ] 把 farm-level power、farm-level reward、step、iteration、phase、episode、timestamp 和 data age 放入运行摘要卡；没有后端快照或统计消息时显示 `WAITING`，过期时显示 `STALE`，不得保留旧值却伪装成实时值。
- [ ] 在遥测表中同时显示每台机组的 power、reward、yaw、pitch command、pitch measured、RPM、torque 和 load；command/measured 分列，缺失值带原因而不是填零。
- [ ] 增加 `training_stats` 消息和有界历史：至少支持实际来源存在的 `mean_power`、`mean_reward`、`value_loss`、`explained_variance`、`episode_return`、`learning_rate`；统计表和曲线要显示迭代号、阶段、时间基准、单位、fidelity 和 provenance。
- [ ] 让 `train_fastfarm.py` 在每轮实际统计完成后追加结构化 JSONL；Bridge 以非阻塞方式读取并转发，并补齐真实阶段事件、半行缓冲、坏行/截断、退出排空和 run_id 隔离。正式训练必须明确区分“进程已启动但尚无实时统计”“后端提供统计”“该字段不支持”，不能从单帧 power/reward 估算缺失指标。
- [ ] 增加当前运行有界历史窗口导出（JSON），包含 session/mode、turbine ID、step、iteration、phase、episode、timestamp/timebase、单位、功率、reward、yaw、pitch command、pitch measured、RPM、fidelity、provenance、validity、data age 和缺失原因；每条序列最多保留 600 条原始记录，以 run_id、消息 sequence 和统计记录类型区分，不按相同 step 覆盖不同阶段或记录。导出包含 schema_version、run_id、导出时间、各序列起止范围、容量、丢弃数量及 truncated 标记，并区分接收时来源年龄与导出时计算年龄。主线程仅获取有界数据副本，序列化和文件写入放入工作线程，工作线程不得访问 bpy；成功或失败由主线程展示。导出过程不得阻塞 Blender 事件循环。
- [ ] 用 fake protocol peer/transport fixture 覆盖按钮状态、断线、重连、空快照、过期快照、统计字段缺失和安全停止；用 Replay/FAST.Farm 证据覆盖 farm power/reward、step、结束状态和无孤儿进程。
- [ ] 重新执行完整的 Blender background smoke 和可交互窗口检查，至少录下：手动摆位、开始训练、暂停、继续、停止、功率/reward 实时变化、训练统计和导出结果。

### 本层验收门槛

- 手动摆位的 yaw、pitch、RPM、释放当前、释放全部和恢复接管均有 UI + STATE 证据；只允许暂停后锁定，释放优先恢复最新 Snapshot、无 Snapshot 才恢复默认姿态；RPM 动画与暂停/释放行为通过回归测试。
- 主 Presentation/Status 按模式验收：Demo、交互训练和 Replay 在 capability 支持时实际完成开始、暂停、继续、单步、停止；正式训练实际完成提交和停止，暂停/继续/单步明确禁用并显示“正式训练后端不支持”。Reset 只在 READY/STOPPED/FAILED 且确认无活动后端或清理线程时可用；所有模式均验证连接状态、运行状态及禁用原因一致。
- 交互训练或 Replay 的 farm power、reward、step 和每机遥测能与直接 Bridge/后端记录逐字段对比；无快照、过期和不支持状态不会伪造数值。
- 正式训练统计只有在 JSONL 真实来源存在并成功转发时才显示；表格、曲线和导出文件的字段、单位、迭代号、validity、data age 和 provenance 可追溯，未提供字段必须显示 `unsupported`。
- `training_stats` 未完成 capability 协商时既不得发送，也不得进入前端状态；协议测试覆盖合法消息、缺失字段、非法数值、过期统计、序号倒退、未协商拒收、重连和模式切换。progress 更新不得刷新旧统计年龄。
- 用实际训练统计计算结果核对 JSONL、Bridge 消息、UI 和导出，覆盖归一化/原始 reward 区分、control/agent step、checkpoint 恢复计数、无优化样本和非有限值。读端测试覆盖半行、坏行、文件截断、进程异常退出、最终完整行排空及同一连接连续两次运行的数据隔离。
- 导出超过 600 条的测试必须证明窗口边界、丢弃数量、truncated 标记准确；同一步不同阶段记录不互相覆盖，序列化期间 UI 仍可响应，写入失败可见且不改变运行状态。
- Part 6 的所有已勾选项重新通过；功能矩阵中剩余缺口必须明确标为“不支持/开发模式/待真实后端证据”，不能继续写成“已完成”。
- 只有本层门槛通过，才允许进入 Part 7 的跨平台安装、品牌决策和最终切换评审。

---

## Part 7：跨平台、交付、品牌决策与可回退切换

### 本层目标

把开发成果变成可在 macOS 和 Windows 安装、配置、演示和排错的产品，并在完整验收后决定最终品牌包装。

> 2026-09-07 执行范围更新：按用户要求，暂缓标准 Blender + Extension + App Template 的展示效果评审及品牌决策；继续推进安装包、启动脚本、文档和分平台验证。暂缓不等于评审通过，正式默认前端切换仍须满足完整验收与切换批准条件。

> 同日用户确认：本轮先完成 Mac 和交付脚本，Windows 稍后实机验收。Windows 脚本照常交付，Windows 实测项目保留待验收，不计入本轮 Mac 完成结论。

### 文件

- 新建：`scripts/blender/run_wfrl_macos.sh`
- 新建：`scripts/blender/run_wfrl_windows.ps1`
- 新建：`scripts/blender/verify_macos.sh`
- 新建：`scripts/blender/verify_windows.ps1`
- 新建：`docs/blender/INSTALL.md`
- 新建：`docs/blender/USER_GUIDE.md`
- 新建：`docs/blender/TROUBLESHOOTING.md`
- 新建：`docs/blender/ACCEPTANCE.md`
- 更新：`docs/blender/feature-parity.md`
- 全部门槛通过后才更新：`README.md`

### 实施步骤

- [ ] 进入本层前，对齐 Part 6.5 的计划勾选、`evidence/part6.5/progress.md` 和功能矩阵：逐项核对验收证据，清理与最终结果冲突的旧进度描述（如 `UI integration pending`），并关联 Part 6 录制问题的修复与复验记录。缺少证据的项目保持待验收，不能仅凭测试总数或“已完成”标题认定门槛通过。
- [ ] macOS launcher 使用参数数组启动 Blender 和 Bridge，不拼接 shell 命令字符串。
- [ ] Windows launcher 使用安全的 PowerShell 参数传递，不写死开发者路径。
- [ ] 在全新 Blender 用户配置中安装同一个 Extension ZIP。
- [ ] 在全新安装环境中回归 Part 6.5 新增能力：暂停态手动 yaw/pitch/RPM 锁定、释放当前/全部及 Snapshot 恢复接管，按模式显示的训练控制，真实来源训练统计及 WAITING/STALE/unsupported 状态，以及有界历史 JSON 导出；同时复验截图和录制（含 1 FPS / 0.1 s 短录制）。记录 UI、状态与输出文件证据，确认安装包包含所需模块和资源，不依赖开发者配置。
- [ ] 在当前 Apple Silicon Mac 上验证 Extension、Demo、FLORIS、重连、截图和退出。
- [ ] 将 macOS FAST.Farm 验收单独列出；只有配置兼容原生 executable 后才执行。
- [ ] 在目标 Windows 机器上配置 Blender、Conda Python、Microsoft MPI 和 FAST.Farm。
- [ ] 在 Windows 运行 `turb3_ctrl3.yaml`，记录启动、运行、draining 和停止前后的进程证据。
- [ ] 在 `docs/blender/ACCEPTANCE.md` 中按 macOS/Windows、前端/后端和运行模式分别记录环境版本、安装包版本或哈希、验收项、结果及证据路径。先完成当前 Mac 可执行项目；Windows 与各平台 FAST.Farm 项目必须在对应目标环境实测，未执行项标为“待验收”并注明缺少的机器或依赖，不以另一平台结果代替，也不据此宣布整体交付完成。
- [ ] 对比两个系统的协议、对象命名、操作结果、Snapshot 数值和输出产物。
- [ ] 不要求不同 GPU 的像素完全一致，但要求功能和数值含义一致。
- [ ] 编写安装、四种模式、fidelity、安全停止、日志和常见错误说明。
- [ ] 【用户要求暂缓】与老板评审标准 Blender + Extension + App Template 的展示效果。
- [ ] 只有在确有外部分发需求时，另立 branded launcher/installer 项目。
- [ ] 完整验收后把 Blender 设为默认前端。
- [ ] 保留一个带旧 Studio/RViz 的可恢复版本；删除旧前端需要单独计划和批准。

### 最终完成标准

- Part 6.5 进入门槛已逐项核实，计划、进度记录和功能矩阵的状态与证据一致；
- 同一个 Extension ZIP 能安装在 macOS 和 Windows；
- 全新安装环境中的手动锁与释放、训练控制与统计、历史导出和截图/录制回归通过，具备可复核产物；
- 四种模式、关键 3D、曲线、安全和相机功能可用；
- FAST.Farm 验收与 Blender 前端验收边界清楚；
- 分平台验收表明确列出通过、失败和待验收项；最终切换所需项目全部通过，不把待验收项计为完成；
- 关闭后没有本次会话遗留的仿真进程；
- 文档足够让非开发者完成安装和演示；
- 老板认可对外展示效果（用户要求暂缓，恢复评审前不计为通过）；
- 最终切换可回退。

---

## 阶段成果总览

| Part | 本层结束时能看到什么 | 是否需要真实后端 |
|---|---|---:|
| 1 | 功能清单、视觉方向和演示流程 | 否 |
| 2 | 可给老板看的高质量 Blender 静态与 Demo 原型 | 否 |
| 3 | 稳定 Extension、配置、状态机和协议 | 否 |
| 4 | 真实数据驱动 Blender 的最小闭环 | FLORIS 后 FAST.Farm |
| 5 | 完整动画、尾流、Lidar、相机和多视图 | 分层接入 |
| 6 | 旧 Studio/RViz 的完整必要能力 | Replay 与真实运行 |
| 6.5 | 手动摆位、训练控制、实时功率/reward、训练统计和历史导出达到可验收对等 | Fake、Replay 与 FAST.Farm 分层验证 |
| 7 | macOS/Windows 交付和最终品牌决策 | 分平台验证 |

## 必须停下来评审的节点

- Part 1 后：确认产品范围和视觉方向。
- Part 2 后：让老板先看真正的界面与演示，不带着错误视觉继续开发。
- Part 4 后：评审进程、协议、数值对等和安全停止。
- Part 5 后：评审科学表达与视觉质量。
- Part 6 后：完整用户流程演练。
- Part 6.5 后：逐项确认手动摆位、训练按钮可发现性、实时功率/reward、训练统计和导出证据，确认没有用合成数据冒充真实后端能力。
- Part 7 后：决定是否需要独立品牌软件并批准正式切换。

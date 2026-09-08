# WFRL Blender 演示流程

> Part 1 · Deliverable 2 / 3 · 演示脚本与操作手册
>
> 状态：可用于 Part 2 静态原型和后续真实联调
>
> 依据：[feature-parity.md](./feature-parity.md) 与仓库根目录 `demo_studio.ps1`

## 1. 目标与边界

这份文档定义 WFRL Blender 前端的演示节奏、操作顺序、讲解口径和异常退路。它解决的是“如何在 60–90 秒内把产品讲清楚”，不是训练结果报告。

主舞台流程使用确定性的 **Demo 模式**：无需启动 FAST.Farm，不让观众等待 MPI spawn，视觉结果可重复。所有本地生成的功率、尾流和脚本事件必须标为 `SYNTH`，不能作为科研结论、策略输出或安全层动作改写。

真实后端和策略效果分别通过独立的 Real 与 Replay 验收流程证明：

- A1 Demo：`scenes/turb3_demo.yaml`，用于 60–90 秒老板演示。
- A2 Real：`scenes/turb3_ctrl3.yaml`，用于 FAST.Farm、三机控制、遥测和安全链路验收。
- A3 Replay：`scenes/turb3_stagger.yaml` + 当前兼容 checkpoint，用于确定性策略回放与数值对比。

三条流程不得混淆数据来源。运行时来源决定标签：

| 标签 | 含义 | 演示中允许的说法 |
|---|---|---|
| `DIRECT` | FAST.Farm 或 Bridge 直接返回 | “这是本次后端运行的直接结果。” |
| `EXPORTED` | 由后端状态或输入场导出的可视化 | “这是从当前运行状态导出的显示层。” |
| `SYNTH` | 前端确定性脚本或展示代理 | “这是用于说明交互的演示数据，不是仿真结论。” |

## 2. 对 `demo_studio.ps1` 的继承与调整

保留脚本里最有效的叙事顺序：先让观众理解场景与交互，再展示运行、功率曲线和安全事件。

| `demo_studio.ps1` 中的做法 | Blender 前端中的处理 |
|---|---|
| 不带 `--train` 启动，先进入界面 | 保留。进入 WFRL 后先展示场景摘要，再由操作者明确启动模式。 |
| 通过 `mpiexec` 启动真实训练 | 保留为 A2/A3 的 Bridge 启动方式；A1 不启动 MPI。 |
| `--fast` 用于快速证明流程可动 | 改为确定性 A1 Demo；不得把 fast preset 讲成训练收敛或性能提升。 |
| 默认使用 `turb3_stagger.yaml` | 拆分为固定场景：A1 Demo、A2 Real、A3 Replay，各自承担单一证据职责。 |
| 手动演示 yaw、pitch、RPM 和相机 | 保留为可选互动段；主流程优先使用预设动作，避免现场输入失误。 |
| 启动 FAST.Farm 约需几十秒 | 移出 60–90 秒主流程。技术验收时显示 `STARTING`，并如实等待。 |
| PowerShell 中写死 Python、MPI、项目路径 | 不进入 Blender 产品。改由 Preferences 配置并在启动前预检。 |
| 取消 Lidar 通道会停止采样 | 保留为技术验收点，不占用老板演示主线时间。 |

## 3. 演示前准备

### 3.1 画面准备

- Blender 5.2.1，WFRL 扩展已启用。
- 输出画面建议为 1920×1080；系统缩放固定，避免现场布局跳动。
- 进入 `view.world`，开启演示布局，隐藏与叙事无关的 Blender 区域。
- 预置俯视、侧视、等距三种相机位；默认从等距全场景开始。
- 面板顺序固定为 Scene、Run、Telemetry、Safety / Demo Events；Manual 折叠。
- 颜色、字号、曲线样式和镜头构图以 [visual-direction.md](./visual-direction.md) 为准。

### 3.2 数据准备

- A1、A2、A3 场景都通过 `scene.validate`，并能在 `scene.summary` 中看到机组数、入流和控制通道。
- A1 Demo 序列从相同初始状态开始，重复两次所得关键帧一致。
- A1 使用 Blender Presentation 的单次 66 秒预设：从点击“开始演示”计时，到第 66 秒进入 `STOPPED`；不继承 Studio 默认两轮约 94 秒的时长。循环次数只在 Development 模式开放。
- A2 的 Python、MPI、FAST.Farm 和 Bridge 路径通过预检；不存在遗留子进程。
- A3 checkpoint 与当前 observation/action schema 兼容；不引用历史演示中的旧性能数字。
- 所有曲线、卡片和事件都能看到 `DIRECT`、`EXPORTED` 或 `SYNTH` 来源标签。
- 顶部同时显示 `connection.status` 与 `run.status`：A1 为 `LOCAL DEMO`，A2/A3 分别显示 Bridge 是否 `CONNECTED`。

### 3.3 操作者准备

- 鼠标停在 WFRL 主面板，不在镜头开始后搜索菜单。
- 演示前把 A1 置于 `READY`，但不要提前播放动画。
- 关闭通知、自动更新弹窗和可能遮挡 Blender 的窗口。
- 准备一句降级说明：“真实后端当前不可用，我会继续用明确标注的演示数据说明交互，不把它当作仿真结果。”

## 4. 80 秒主舞台流程（A1 Demo）

目标总时长约 80 秒。操作者只执行高确定性动作；讲解和画面同时推进。

| 时间 | 操作者动作 | 观众看到 | 建议讲解 | 功能 ID / 来源 |
|---:|---|---|---|---|
| 0–6 s | 打开 WFRL Presentation 布局 | 风场全景、顶部状态条、四个主面板 | “这是 WFRL 的统一控制界面，场景、运行状态和证据在同一个视图里。” | `view.world`, `run.status`, `status.fidelity` |
| 6–14 s | 加载 A1，展开 Scene 摘要 | 三台机组按 YAML 布局出现；入流箭头与场景说明同步更新 | “场景文件同时驱动机组布局、入流和控制配置，不需要在 3D 里重复搭建。” | `scene.load`, `scene.validate`, `scene.summary`, `scene.layout`, `scene.inflow` |
| 14–25 s | 点击“开始演示” | 状态从 `READY` 到 `RUNNING`；叶片由顺桨回到工作角，转子依次起转 | “现在启动的是确定性 Demo，不启动 FAST.Farm；它用来快速验证交互和镜头。” | `mode.demo`, `run.start`, `turbine.pose.pitch`, `turbine.pose.rpm` · `SYNTH` |
| 25–35 s | 播放预设偏航动作并开启尾流层 | 三台机组形成错列 yaw；扩散流管和稀疏流线随之更新 | “偏航变化会立即进入场景状态，尾流显示帮助我们看清控制意图。” | `turbine.pose.yaw`, `wake.rings`, `wake.proxy.toggle`, `wake.proxy.update` · `SYNTH` |
| 35–45 s | 选择 T1，切到双视图和传感器相机 | 左侧保留全场，右侧显示 T1 机舱视角与视锥 | “选中任意机组后，可以把全场态势和机舱传感器视角并排检查。” | `camera.focus.select`, `view.dual`, `camera.sensor.toggle`, `camera.sensor.select`, `camera.frustum` |
| 45–56 s | 展开 Telemetry，聚焦合成功率曲线 | 三机功率卡片、总功率和 yaw/pitch/RPM/power 曲线随时间变化，来源标签持续可见 | “这里展示信息结构和联动；当前功率标为 SYNTH，不代表真实发电性能，Demo 也没有 reward 变化。” | `panel.telemetry`, `telemetry.power`, `curve.demo-filter`, `status.fidelity` · `SYNTH` |
| 56–67 s | 播放脚本阶段事件 | Panel 标题切为“演示事件”，显示启动、偏航到位或顺桨阶段 | “这些是帮助讲解的脚本事件，不是安全规则触发。真实的请求值、应用值和限制原因要在 A2 验收。” | `panel.safety`, `safety.events`, `status.fidelity` · `SYNTH` |
| 67–76 s | 展示控制动作影响卡片 | yaw 展示动作与合成功率代理的前后状态并列；卡片标为“策略效果组件预览” | “这里说明控制量与下游指标的界面关系，不是 checkpoint 输出。真正的策略效果只在 Replay 中确认。” | `telemetry.power`, `curve.demo-filter`, `status.fidelity` · `SYNTH` |
| 76–80 s | 让 Demo 序列自然结束，回到全场等距视图 | 状态返回 `STOPPED`，最终画面稳定 | “同一套界面接下来可以接入真实 FAST.Farm，也可以回放已有策略。” | `mode.demo`, `run.status`, `camera.world.iso` |

### 4.1 主流程按钮文案

四种模式必须使用不同动词，避免用户把 Demo 当作训练：

| 模式 | 主按钮 | 二次动作 | 禁止使用的模糊文案 |
|---|---|---|---|
| Demo | 开始演示 | 重新演示 | 开始训练 |
| Interactive Train | 开始交互训练 | 暂停 / 继续 | 快速演示 |
| Formal Train | 提交正式训练 | 查看任务 / 请求停止 | 开始演示 |
| Replay | 开始回放 | 暂停 / 继续 | 重新训练 |

### 4.2 可选的 15 秒互动加演

只有在观众追问“能不能手动控制”时使用，不计入 80 秒主流程：

1. 展开 `panel.manual` 并选中 T2。
2. 小幅调整 `turbine.pose.yaw`，让场景、数值和尾流代理同时更新。
3. 调整 `turbine.pose.pitch` 或 `turbine.pose.rpm`，说明 A1 只改变展示状态。
4. 点击恢复预设，回到可重复的 Demo 初始状态。

不要在主舞台上自由拖动机组位置；`layout.drag` 留给设计评审或配置编辑演示。

### 4.3 阶段口径

同一套 80 秒镜头在不同阶段使用不同证据，不得提前升级说法：

| 阶段 | 67–76 秒内容 | 允许名称 | 证据边界 |
|---|---|---|---|
| Part 2 静态原型 | 固定的前后状态卡 | 策略效果组件预览 | `SYNTH`，只评审布局和信息层级 |
| A1 可运行 Demo | yaw 展示动作与合成功率变化 | 控制动作影响 | `SYNTH`，没有 checkpoint，不称为策略效果 |
| A3 Replay 验收完成后 | 当前兼容 checkpoint 的同条件对比 | 策略效果 | `DIRECT` / `EXPORTED`，必须带 checkpoint、场景和时间窗口 |

## 5. A2 真实后端验收流程

A2 用于证明系统真的接通 FAST.Farm，不受 60–90 秒限制。预计时长应包含真实 spawn、初始化、运行与 drain；界面不得伪造一个更短的等待时间。

### 5.1 操作顺序

1. 确认当前状态为 `STOPPED`，加载 `scenes/turb3_ctrl3.yaml`。
2. 检查 `scene.validate`、`scene.controls` 和 `scene.notes`；确认三台机组控制通道与目标一致。
3. 选择 Interactive Train，点击“开始交互训练”。
4. 在 `STARTING` 阶段显示当前步骤：环境预检、MPI spawn、FAST.Farm 初始化、Bridge 握手。
5. 进入 `RUNNING` 后观察三机 yaw、pitch、RPM、功率、载荷与通道状态。
6. 开启真实可用的 `wake.disxy`；若当前配置无 DisXY 输出，显示“本次运行无导出数据”，不得退回无标签代理图冒充。
7. 取消一个 Lidar 通道并确认采样真正停止；恢复后确认数据重新进入时间线。
8. 触发或等待一次安全约束，核对请求动作、应用动作、机组 ID、step 和原因。
9. 执行暂停、单步、继续，再点击停止；等待 drain 完成并确认没有遗留进程。

### 5.2 A2 证据清单

| 证据 | 期望来源 | 通过条件 |
|---|---|---|
| yaw / pitch 指令与实测 | `DIRECT` | UI 数值、3D 姿态与 Bridge step 对齐。 |
| RPM | `EXPORTED` | 显示计算或测量来源；不得与直接回传的控制量共用一个来源标签。 |
| 功率 | `DIRECT` | 面板显示本次运行值，并带单位、时间和来源。 |
| 载荷及其派生显示 | `DIRECT` / `EXPORTED` | 叶根载荷与由载荷计算的挠度、净空分别标注。 |
| DisXY | `EXPORTED` | 仅在后端实际导出时显示；时间索引跟随当前 step。 |
| Lidar / 湍流采样 | `EXPORTED` | 关闭通道后采样停止，而不只是隐藏图层。 |
| 安全事件 | `EXPORTED` | SafetyLimiter 输出的请求动作和最终动作可追溯到同一 turbine ID 与 step。 |
| 停止结果 | 生命周期证据 | 状态到达 `STOPPED`，Bridge 与子进程完成清理。 |

### 5.3 A2 讲解口径

- 可以说：“这些值来自本次 FAST.Farm 运行。”
- 可以说：“DisXY 是由本次后端输出导出的显示层。”
- 不可以说：“地形改变了 FAST.Farm 的物理结果”，除非后端实现和本次证据明确支持。
- 不可以把显示缩放后的叶片弯曲、尾流尺寸或颜色当作真实量纲。
- 不可以因为 fast preset 跑通就声称策略收敛或性能提升。

## 6. A3 策略回放验收流程

A3 用于把舞台上的“策略效果组件预览”替换为可复核的策略证据。

### 6.1 操作顺序

1. 在 `STOPPED` 状态加载 `scenes/turb3_stagger.yaml`。
2. 选择兼容当前 schema 的 checkpoint；若不兼容，阻止开始并指出维度或版本差异。
3. 选择 Replay，点击“开始回放”。
4. 记录初始状态、checkpoint 标识、随机种子、场景摘要和运行配置。
5. 在关键 step 暂停，核对 turbine ID、yaw、pitch、RPM、功率、reward 与安全事件。
6. 继续到终态，导出本次回放摘要。
7. 使用相同输入再次回放，比较关键 step 与终态；差异必须被解释或判为失败。

### 6.2 A3 展示方式

- “策略前 / 策略后”必须使用同一场景、同一输入条件和明确的比较窗口。
- 卡片同时显示绝对值与差值，不只显示百分比。
- 图表标出 checkpoint、时间范围和来源。
- 历史演示文档中的百分比或 MW 数值不能直接作为本次验收阈值。
- 若当前 checkpoint 只能控制 yaw，界面不要暗示它同时优化 pitch 或 RPM。

## 7. 异常与降级流程

| 情况 | 界面行为 | 操作者说法 | 后续动作 |
|---|---|---|---|
| 场景校验失败 | 保留旧场景，定位字段和行号，不进入 `READY` | “场景配置未通过校验，我不会带着未知配置启动。” | 修复后重新 `scene.validate`。 |
| MPI / FAST.Farm 不可用 | A2/A3 按钮禁用；A1 仍可使用 | “真实后端未通过预检，下面只展示明确标注的 Demo。” | 保存诊断信息，不伪造连接成功。 |
| checkpoint 不兼容 | Replay 阻止启动并显示 schema 差异 | “这个 checkpoint 与当前接口版本不匹配。” | 更换兼容 checkpoint。 |
| DisXY 缺失 | 显示空状态和原因 | “本次运行没有导出 DisXY。” | 可切到 `SYNTH` 代理，但必须重新标注。 |
| 传感器相机未配置 | 保持主相机，提示缺失 turbine/camera | “这个场景没有可用的机舱相机。” | 返回全场视图，不临时乱改场景。 |
| 安全事件未自然出现 | 不等待、不编造 | “本段没有触发安全约束；可在验收预设中验证链路。” | 使用已定义的安全验收条件重跑。 |
| 停止 drain 较慢 | 保持 `DRAINING`，显示阶段和耗时 | “系统正在安全排空后端进程。” | 等待完成；禁止强装成已停止。 |
| 渲染性能下降 | 关闭高成本体积效果，保留状态和来源标签 | “我降低显示质量，数据链路不受影响。” | 记录性能配置供后续复现。 |

## 8. 镜头与界面状态清单

| Shot | 画面目的 | 必须出现 | 不应出现 |
|---|---|---|---|
| S1 全场建立 | 一眼理解三机风场 | 三台机组、入流、状态条、WFRL 标识 | Blender 默认欢迎页、杂乱 Outliner |
| S2 启动 | 看清叶片与转子响应 | pitch 变化、RPM 变化、`RUNNING` | 无来源的功率结论 |
| S3 尾流 | 看清 yaw 与下游关系 | yaw 错列、尾流层、来源标签 | 把代理尾流称为 FAST.Farm 真值 |
| S4 机组聚焦 | 建立全场与局部关系 | T1 高亮、双视图、传感器视锥 | 丢失当前机组标识 |
| S5 证据面板 | A1 看合成功率；A2/A3 看功率与 reward | 单位、时间轴、图例、来源 | A1 暗示 reward 变化；截断轴且无说明 |
| S6 事件与收尾 | A1 看脚本阶段；A2/A3 看约束和安全停止 | 事件类型、来源、原因、最终状态 | 把 A1 脚本事件称为安全层改写 |

## 9. 演示验收标准

### 9.1 60–90 秒主流程

- [ ] 从 `READY` 开始，到 `STOPPED` 结束，全程无需终端操作。
- [ ] 总时长在 60–90 秒内；默认脚本目标为 80 秒。
- [ ] 顺序覆盖：进入 WFRL、加载场景、启动风机、显示尾流、选择机组、切换相机、查看功率、看到脚本事件，并明确策略效果要由 A3 验证。
- [ ] A1 中所有生成数据都标为 `SYNTH`，讲解不暗示真实仿真或训练结论。
- [ ] 关键控制仅需单击或使用预置动作，不依赖现场精确拖拽。
- [ ] 最后一帧稳定、状态明确，可停留用于问答。

### 9.2 真实与回放验收

- [ ] A2 能从 `STARTING` 进入 `RUNNING`，并用 `DIRECT` / `EXPORTED` 展示真实来源。
- [ ] A2 的通道关闭会停止采样，而不只是隐藏图层。
- [ ] A2 的暂停、单步、继续、停止和安全清理都可复现。
- [ ] A3 能阻止不兼容 checkpoint，兼容回放可重复。
- [ ] A3 的策略比较使用同条件数据，不沿用未经重跑的历史数字。
- [ ] 后端失败时可安全降级到 A1，且来源标签同步改变。

## 10. Part 2 实现输入

静态 Blender 原型至少需要为主舞台流程提供以下可点击状态：

1. Presentation 初始全场景。
2. A1 场景加载完成。
3. Demo 启动与三机转动。
4. yaw 错列与尾流代理。
5. T1 选中、双视图与传感器相机。
6. Telemetry 合成功率曲线展开；不制作变化的 Demo reward。
7. “演示事件”视觉夹具出现；不模拟 SafetyLimiter 已改写动作。
8. “策略效果组件预览”视觉夹具出现；不模拟已加载 checkpoint。
9. 停止完成状态夹具；不模拟真实进程 drain 或清理证据。

原型可以使用固定数据，但每一个科学数据组件都要携带 `SYNTH` 标签。这里的事件、策略卡片和后端状态都是**静态视觉夹具**，不属于 Part 2 的真实功能。真实后端联调在后续阶段替换数据源，不改变这条演示信息架构。

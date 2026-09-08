# Studio 回放功能使用指南

## 功能说明

Studio 回放模式可以加载已训练的 checkpoint，用**确定性策略**（`dist.mean`，无探索噪声）跑一个完整回合，并实时 3D 可视化：

- **3D 场景**：机组实时偏航角、桨距角、转子转速
- **FLORIS-proxy 尾流**：稳态尾流平面跟随偏航角实时更新，看到 wake steering 的尾流偏转
- **数据通道面板**：yaw/pitch/torque/power 曲线实时绘制
- **安全约束监控**：占空比/转速/桨距限制实时检查

这与 `rollout_ckpt.py` 的口径完全一致（都用 `dist.mean` 确定性动作），但多了可视化。

---

## 快速开始

### 1. 运行回放（推荐）

直接运行 PowerShell 脚本：

```powershell
cd "D:\project\wind farm RL"
.\scripts\demo\replay_studio.ps1
```

**运行时间**：约 12 分钟（400 步 FAST.Farm 前向 + warmup 8 步）

**预期结果**：
- T1（上游）偏航角逐步增大到 **33-36°**
- T2/T3（下游）保持 **0°** 接受偏转后的来流
- 尾流平面（白色网格）随 T1 偏航**向左偏转**
- 全场功率稳定在 **3.28 MW**（比零偏航基线 3.12 MW 高 **+5.3%**）

---

### 2. 测试导入（可选）

如果担心环境问题，先运行测试脚本：

```powershell
cd "D:\project\wind farm RL"
& "C:\Users\s1155\miniconda3\envs\wfcrl\python.exe" scripts/demo/test_replay_import.py
```

应看到：
```
✓ 导入成功
✓ 场景加载成功: turb3_stagger, 3 台机组
✓ Trainer 初始化成功 (replay=True, replay_steps=10)

所有导入和初始化测试通过！可以运行 replay_studio.ps1
```

---

## 命令行参数

手动调用时可用参数：

```powershell
python -m wfrl.studio.app \
  --scene scenes/turb3_stagger.yaml \
  --replay \
  --ckpt results/checkpoints/mappo_fastfarm_..._stagE400ref.pt \
  --replay-steps 400 \
  --warmup 8
```

**参数说明**：

- `--replay`：启用回放模式（必需，否则是训练模式）
- `--ckpt PATH`：checkpoint 路径（必需）
- `--replay-steps N`：回放步数（默认 400）
- `--warmup N`：热身步数（默认 4，建议 8 让转子稳定）
- `--no-wake`：关闭 FLORIS-proxy 尾流可视化（加快启动）

---

## 界面操作

启动后界面会**自动开始回放**（~400 步，约 12 分钟）。

### 控制按钮

- **⏸ 暂停**：停在当前帧观察（可随时恢复）
- **⏹ 停止**：终止回放并关闭 FAST.Farm（需要 ~30 秒排空迭代预算）

### 视角操作

- **左键拖动**：旋转视角
- **滚轮**：缩放
- **Shift + 左键**：平移

### 面板说明

**左侧面板**：
- **场景**：机组数量、布局、风况
- **数据通道**：勾选要绘制的曲线（yaw/pitch/torque/power/...）
- **手动摆位**：回放模式下不可用

**右侧面板**：
- **训练**：显示回放进度（"sampling" 阶段 = 正在回放）
- **物理与安全约束**：实时监控占空比/转速/桨距限制

---

## 观察要点

### 1. Wake Steering 行为

- **T1（上游）**：偏航角从 0° 逐步增大到 **33-36°**（理论最优 ~20-30°）
- **T2/T3（下游）**：保持 **0°**，接受偏转后的来流（功率增益来源）

### 2. 尾流偏转

- **白色网格平面**：FLORIS-proxy 尾流，跟随 T1 偏航角实时更新
- **偏转方向**：T1 偏航为正（逆时针看从上方）时，尾流向**左**偏

### 3. 功率增益

- **全场功率**：右侧"训练"面板显示，稳定在 **3.28 MW**
- **基线对比**：零偏航基线 3.12 MW → 增益 **+5.3%**（+178 kW）

### 4. 占空比约束

- **右侧"物理与安全约束"面板**：
  - **占空比（滑动窗口）**：最近 20 步内偏航动作占比 ≤ 30%
  - 策略学会了"偏几步 → 停几步 → 再偏"的节奏（散热恢复）

---

## 与 rollout_ckpt.py 的区别

| 特性 | `rollout_ckpt.py` | Studio 回放 |
|------|-------------------|-------------|
| **动作采样** | `dist.mean`（确定性） | `dist.mean`（确定性） |
| **观测归一化** | checkpoint 存的 obs_mean/var | checkpoint 存的 obs_mean/var |
| **约束层** | 启用（与训练一致） | 启用（与训练一致） |
| **输出** | npz 文件（yaw/power 轨迹） | 3D 实时可视化 + 尾流 + 曲线 |
| **用途** | 验证策略、提取数据 | 现场演示、教授汇报 |

两者的**策略执行完全一致**，只是输出形式不同。

---

## 常见问题

### Q1: 界面卡顿怎么办？

**A**: 正常现象。FAST.Farm 每步 2-3 秒墙钟，界面每 3 秒更新一次位姿。可以：
- 关闭尾流可视化（`--no-wake`），节省 ~10% 时间
- 减少回放步数（`--replay-steps 170`），看前半段即可

### Q2: 尾流平面不更新？

**A**: 检查：
1. 是否加了 `--no-wake`（这会关掉尾流）
2. 数据通道面板是否勾选了"wake"相关项

### Q3: 停止按钮无响应？

**A**: FAST.Farm 在排空迭代预算（~30 秒），状态栏会显示"停止中：排空 FAST.Farm 迭代预算…"。**不要强制关闭**，否则会留下卡在 MPI_RECV 的孤儿进程。

### Q4: 回放功率与训练历史不一致？

**A**: **正常**！训练历史用 `dist.sample()`（探索噪声 std=0.57），回放用 `dist.mean`（确定性）。判断策略好坏**必须看回放**，不能看训练曲线。

### Q5: 能否回放其他 checkpoint？

**A**: 可以，只要是 MAPPO 的 checkpoint（包含 `actor`/`obs_mean`/`obs_var`）。修改 `replay_studio.ps1` 里的 `$ckpt` 路径即可。

---

## 技术细节

### Checkpoint 兼容性

回放代码自动识别两种 checkpoint 格式：

1. **Studio 训练**：有 `controls` 键，无 `obs_duty`/`recurrent`
2. **CLI 训练**（`train_fastfarm.py`）：无 `controls` 键，有 `obs_duty`/`recurrent`

当 `controls` 缺失时，从 `act_dim` 推断：
- `act_dim=1` → `controls=['yaw']`（旧口径，单控偏航）
- `act_dim=3` → `controls=['yaw','pitch','torque']`（新口径，多控）

### 动作缩放

- **单控（yaw）**：网络输出直接 `clip(a, act_low, act_high)`（与 `rollout_ckpt.py` 同口径）
- **多控**：网络输出 `tanh(a) * act_high`（与 `train_fastfarm.py` 同口径）

这保证了回放与训练时的动作尺度完全一致。

### FLORIS-proxy 尾流

- **不是真实 FAST.Farm 尾流数据**（FAST.Farm 的涡格几何 API 不公开）
- 是**稳态近似**：用当前偏航角调 FLORIS 求解器，合成一个瞬时尾流平面
- 足够展示 wake steering 的偏转效果（教授汇报够用）

---

## 相关文件

- **回放脚本**：`scripts/demo/replay_studio.ps1`
- **测试脚本**：`scripts/demo/test_replay_import.py`
- **实现代码**：`wfrl/studio/trainer.py:_replay_policy()`
- **命令行入口**：`wfrl/studio/app.py:main()`
- **Checkpoint**：`results/checkpoints/mappo_fastfarm_..._stagE400ref.pt`

---

## 下一步

回放完成后，可以：

1. **截图/录屏**：用于 PPT/论文（OBS Studio 或 Windows Game Bar）
2. **提取数据**：运行 `rollout_ckpt.py` 输出 npz 文件，绘制更精细的图表
3. **复现训练**：运行 `train_reference_s1.ps1` 用不同随机种子验证可复现性

# Studio checkpoint 评估与可视化

Studio 的 `--replay` 加载 checkpoint，以确定性动作 `dist.mean` 推进新的物理回合并实时显示。它需要完整源码环境与后端依赖；Blender 内置保存结果的离线回放见[演示指令单](演示指令单.md)。

## 入口与输入条件

实现为 `wfrl/studio/trainer.py:_replay_policy()`，命令行入口为 `wfrl/studio/app.py`。运行前核对场景、机组顺序、checkpoint 的 `obs_keys`、`obs_dim`、`act_dim`、控制量及训练归一化统计。相同确定性动作选择不自动保证与另一脚本的场景、控制器、预热、约束、采样和统计窗口完全一致。

`stagE400ref` checkpoint、原 rollout 和训练历史当前缺失。保留的 level/E128 权重属于另一配置；不能用它补作原实验的收益证据。[固定来流实验](../training/WAKE_STEERING_BREAKTHROUGH.md)说明报告值与复现限制。新运行的耗时、偏航角和功率以实际输出为准。

## 命令模板

从完整源码根目录运行，使用与 checkpoint 匹配的场景和已配置的环境。macOS 通过桌面启动器使用 `pythonw` 与 OpenMPI：

```bash
scripts/macos/run_desktop.sh studio --scene scenes/turb3_stagger.yaml \
  --replay --ckpt /absolute/path/matching-checkpoint.pt --replay-steps 400 --warmup 8
```

Windows 替换 Python、MPI 和 checkpoint 路径。PowerShell 的调用运算符为 `&`，续行符为反引号：

```powershell
$PY = "C:\path\to\python.exe"
$MPI = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$CKPT = "C:\path\to\matching-checkpoint.pt"
& $MPI -n 1 $PY -m wfrl.studio.app `
  --scene scenes/turb3_stagger.yaml --replay `
  --ckpt $CKPT --replay-steps 400 --warmup 8
```

`--replay` 启用 checkpoint 评估；`--replay-steps` 默认 400，`--warmup` 默认 4。`--no-wake` 关闭 FLORIS proxy 尾流显示。环境与平台要求见[本地配置](../setup/setup_local.md#4-旧-studio--rviz-桌面入口)；命令模板不证明目标环境已经通过后端、策略效果或可见窗口验收。

## 画面与操作

界面显示场景、偏航/桨距/转矩/功率等通道、策略运行进度与约束状态。左键拖动旋转，滚轮缩放，Shift＋左键平移。暂停用于观察，停止结束回合；外部求解器排空迭代预算的耗时取决于实际环境，不承诺固定秒数。

FLORIS proxy 是用当前偏航求得的稳态示意尾流，不是 FAST.Farm 动态尾流真值。显示方向和功率应按坐标与实际输出解释，不以白色网格证明控制收益。占空比窗口和阈值取实际环境配置，不从旧说明固定推定为 20 步 / 30%。

## 动作与模型合同

回放从 checkpoint 读取 actor、观测键与归一化统计。存在 `controls` 时使用其通道列表；缺失时实现按 `act_dim=1` 推断 yaw、`act_dim=3` 推断 yaw/pitch/torque，其他维度回退到场景控制量。此回退是实现状态，不能替代模型兼容性确认。

单通道动作按 driver 边界裁剪，多通道使用 `tanh` 与逐通道上界缩放；应与产生该 checkpoint 的训练入口匹配。训练分布采样与确定性评估行为不同，评价策略需统一部署方式并报告匹配基线、完整轨迹和重复运行。

数值轨迹入口为 `scripts/experiments/rollout_ckpt.py`，也会重新推进物理；它不是读取历史 NPZ 的播放器。当前缺失的旧权重/轨迹不能用其他 level/E128 checkpoint 或 Blender 内置柔性片段替代。

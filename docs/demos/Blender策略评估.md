# Blender checkpoint 策略评估

Blender 的后端 Replay 模式加载 checkpoint，以确定性动作 `dist.mean` 推进新的物理回合并实时显示，不更新权重。执行由 `wfrl/studio/trainer.py:_replay_policy()` 提供，Bridge 将纯值快照传给 Blender。该模式需要完整 Python、MPI、FAST.Farm、控制器和算例环境；保存结果的离线播放见[演示指令单](演示指令单.md)。

## 场景与输入

运行前核对场景、机组顺序、checkpoint 的观测键、观测／动作维度、控制量和归一化统计。动作合同校验运行时边界；保存了场景的 checkpoint 还校验后端、机型、时间步长与布局。相同确定性动作选择不自动保证另一脚本的预热、约束、采样和统计窗口一致。

`stagE400ref` 的 seed 0 最终 checkpoint、迭代权重、原 rollout 和训练历史可从本地提交 `d6979f49` 恢复；seed 1 权重仍未找到。文件可恢复不表示运行目录已具备它们，也不补足匹配零偏航基线或跨种子验证。level/E128 权重属于另一配置，不能补作原实验的收益证据。[固定来流实验](../training/WAKE_STEERING_BREAKTHROUGH.md)提供恢复入口、报告值与复现限制。

## Blender 操作

1. 按[本地配置](../setup/setup_local.md#3-fastfarm-动态后端与实时-bridge)准备完整环境，通过项目启动器或手动连接 Bridge。
2. 在 **Item → WFRL / CONNECTION → 后端连接（高级）** 选择 **Replay**。手动连接时在环境与路径中设置 Default scene，并执行 **SCENE CONFIGURATION → Load & Validate Scene**；修改 YAML 或覆盖参数后重新校验。
3. 在 **Item → WFRL / 后端运行** 设置 Checkpoint、Replay steps、Warmup steps 和 Seed。checkpoint 使用匹配场景的完整路径。
4. 点击 **开始策略评估（不更新权重）**，等待后端进入 RUNNING。Pause／Resume／Step 受当前状态约束；Stop 后核对 STOPPED／FAILED 结果并等待独立清理确认，不能仅凭状态名判断进程已退出，见[停止结果与清理确认](../development/BlenderBridge协议.md#停止结果与清理确认)。

项目启动器入口为 `scripts/blender/wfrl_launcher.py launch`；macOS 的 `scripts/blender/run_wfrl_macos.sh` 已包含该子命令。启动器使用实际配置的后端解释器与已安装扩展，修改源码不会自动更新安装载荷。完整操作及平台边界见[用户手册](../blender/用户使用手册.md#62-已有权重推理replay)。

## 动作、显示与结果

Replay 从 checkpoint 读取版本化动作合同。`tanh_scale` 与 `clip` 按训练入口确定，不按动作维度猜测；无版本模型仅接受来源可确认的元数据。规则见[动作合同](../training/MAPPO_SETUP.md)。

画面消费 Bridge 的姿态、功率、测量和运行状态，各通道保留来源、单位、时间及有效性。保存柔性片段、合成示意、稳态 FLORIS 参考和实时 FAST.Farm 输出各按自身来源解释；显示本身不能证明控制收益。占空比窗口与阈值取实际环境配置。

数值轨迹入口 `scripts/experiments/rollout_ckpt.py` 同样会重新推进物理仿真。策略效果需使用匹配基线、完整轨迹和重复运行评价；缺失权重或轨迹不能由另一配置的 checkpoint 或内置柔性片段代替。

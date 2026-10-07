# 固定来流尾流控制：Reference Reward 历史实验记录

对齐日期：2026-10-08。实验原记录日期为 2026-08-20，负责人 Jchenhan。本文保留当时三机错列、固定来流实验的配置和报告值；本次没有重新训练、求解或评估，不将它作为当前 0.3.17.1、三机柔性回放、跨风况或实机控制收益的验收。

## 1. 结果与证据状态

原记录报告 seed 0 的确定性策略：T1 末 50 步平均偏航约 33.86°，T2/T3 约 0.03° / 0.06°；末 50 步全场功率 3.281 MW，对照零偏航基线 3.116 MW，按这些四舍五入数字计算约 +5.3%。

**数字需复核：**这两项功率相减是 +0.165 MW，即 +165 kW；原记录写的“+178 kW”与之不一致。没有原始匹配对照数据时，不将 +178 kW 或更精确的增益作为已核验值。

| 项目 | 2026-10-06 工作区核对 |
|---|---|
| `scenes/turb3_stagger.yaml` | 存在；三机错列，4D 纵向间距、逐机横向偏移 31.5 m，8 m/s、270°，controls 为 yaw |
| `scripts/train/train_fastfarm.py`、`wfrl/rewards.py` | 存在；集中式 MAPPO 与 reference 奖励实现可静态检查 |
| `scripts/demo/train_reference_s1.ps1`、`rollout_reference_s1.ps1`、`replay_studio.ps1` | 存在；历史 Windows 绝对路径，未重跑 |
| `results/runs/stagE400ref_rollout.npz` | 当前缺失，不能重新计算报告的末 50 步统计 |
| `results/runs/fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_hist.json` | 当前缺失 |
| `results/checkpoints/..._stagE400ref.pt`、`..._stagE400ref_s1.pt` | 当前没有找到；历史脚本引用不等于权重仍保留 |
| seed 1 / seed 2、跨风速、跨风向、不同湍流 | 当前未确认完成；不能沿用“正在验证”描述现行运行状态 |

当前保留的 `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt` 是另一配置；内置 MAPPO 柔性包也记录该 level/E128 策略。它不能补作 stagE400ref 的效果证据。

## 2. 当时的实验配置

```text
scene: scenes/turb3_stagger.yaml
seed: 0（原结果）；seed 1 为当时计划的复核
iters: 40
episode_steps: 400
reward: reference
reward_ref: 1.7017
duty_penalty: 0.0
```

原记录还报告：训练末轮均值功率约 3.064 MW，确定性 rollout 末 50 步约 3.281 MW；探索标准差约 0.57。当前原始历史与轨迹缺失，上述数字保留为历史报告值，未重新验证。

对照必须匹配初始化、来流、预热、控制步与统计窗口。脚本当前的 `--policy zero` 使用同一采样/统计路径；不能仅拿另一脚本、另一窗口的 3.116 MW 与本次新运行直接配对。

## 3. 奖励公式与可保留的算法解释

当前 [FixedReference](../../wfrl/rewards.py) 实现为：

```python
shaped = (raw_reward - reference) / reference
```

`raw_reward` 来自 WFCRL 的按来流立方归一化平均功率及可用载荷惩罚，不是直接将 MW 总功率代入 `(P-P0)/P0`。`1.7017` 是原配置的基线奖励值，不能直接沿用于新来流、载荷系数或新控制器；须先取得匹配零动作基线。

原记录将性能变化归因于 reference 奖励、回合长度与偏航占空比滑动窗口。这是待检验的机制解释；没有保留的匹配消融证据，不能分别宣称三者的因果增益。`--duty-penalty 0` 只关闭额外惩罚，不关闭环境约束；占空比的窗口与阈值应以实际驱动配置为准，不能固定复用旧文中的 20 步 / 30%。

训练使用分布采样，确定性评估使用 `Normal.mean`，两者测量的策略行为不同。应按部署策略做匹配评估，并报告统计窗口与重复运行；仅有一条确定性 rollout 不能证明跨种子复现、鲁棒性或统计显著性。

## 4. 当前复现与演示入口

历史 PowerShell 脚本硬编码 `D:\project\wind farm RL`、用户 `s1155` 的 Python 路径和 MS-MPI。它们调用现有训练/rollout/Studio 模块，但权重与结果当前缺失；脚本中的“8 小时”“12 分钟”“预期 +5%”不是现行保证。`train_reference_s1.ps1` 的命令使用 seed 1，脚本打印和 rollout 引用的文件名仍含 `s0`；运行前应按训练入口实际命名核对，本文不修改脚本或启动复现。

- [动态训练入口](../../scripts/train/train_fastfarm.py)：训练与同路径零动作基线，支持回合、预热、奖励、种子和场景配置。
- [确定性评估](../../scripts/experiments/rollout_ckpt.py)：从匹配 checkpoint 重新推进后端，输出数值轨迹。
- [Studio Trainer](../../wfrl/studio/trainer.py)：`_replay_policy` 加载权重并重新执行物理仿真；不读取历史 NPZ。界面的 FLORIS proxy 尾流不能当作 FAST.Farm 动态尾流真值。
- [算法与依赖分流](MAPPO_SETUP.md)、[本地配置](../setup/setup_local.md)：当前源码入口与平台要求。
- [当前 Blender 演示](../demos/演示指令单.md)：0.3.17.1 内置保存结果，适合离线展示；它与本实验效果分别判定。

恢复完整复现需要找到或重新生成匹配的权重、基线、策略 rollout、训练历史及配置来源，随后再重新评分。本文整理不授权或启动上述新计算。

## 5. 尚未完成的研究问题

| 问题 | 需要的证据 |
|---|---|
| 跨种子复现 | seed 1 / 2 的匹配基线与策略 rollout，统一窗口 |
| reference / level、回合长度、占空比模型的效果 | 控制其他因素的消融及重复运行 |
| 扩展到 6 台或更多机组 | 新布局及动作/观测兼容检查、重新评估 |
| 湍流、风速、风向鲁棒性 | 冻结测试工况与保留完整时序 |
| 与固定 20°、传统优化器比较 | 同布局、同来流、同控制约束的直接对照；文献百分比不能替代 |
| 真实风场部署 | 独立的硬件、控制语义、响应与安全验证；当前没有完成证据 |

原文“RL 更优”“所有脚本、checkpoint、数据已归档”“当前仿真验证完成、可直接用到真实风场”均超出本次可核验范围，不再作为当前结论。历史 +5.3% 只保留为该特定算例的报告。

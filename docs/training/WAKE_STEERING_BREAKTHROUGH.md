# 固定来流尾流控制：Reference Reward 历史实验记录

本文保存 **2026-08-20、三机错列、固定来流、seed 0** 实验的配置、报告值与现有证据缺口。该结果不构成当前 Blender 安装包、三机柔性回放、跨风况或实机控制收益的验收。

## 1. 结果与证据状态

提交 `d6979f49` 保存的 seed 0 确定性 rollout 中，`yaw` 和 `power` 的形状均为 `(400, 3)`，第 0/1/2 列依次对应 T1/T2/T3；预热 8 步不计入数组。末 50 步为切片 `[350:400]`，即预热后第 351–400 个控制步：平均偏航为 **[33.863931°, 1.860488°, −1.631387°]**，全场平均功率为 **3.281383785 MW**；400 步全程的全场平均功率为 **3.148101952 MW**。

同一提交的 `results/runs/stagE400ref_rollout.log` 第 109 行打印 `[33.86, 1.86, -1.63]`，与保存的 rollout 一致。

**原记录报告值：**T1 末 50 步平均偏航约 33.86°、末 50 步全场功率 3.281 MW（均与保存结果按相应精度四舍五入后一致）；T2/T3 约 0.03° / 0.06°（均与保存结果不一致）。现有材料无法判断 T2/T3 的差异是抄录错误，还是引用了另一份未保存的结果。原记录的零偏航基线为 3.116 MW，按原记录的四舍五入功率计算增益约 +5.3%；该基线与增益缺少原始匹配对照数据，尚未核验。

**数字需复核：**按原记录的四舍五入功率计算，`3.281 − 3.116 = 0.165 MW`，即 +165 kW；原记录写的“+178 kW”与之不一致。没有原始匹配对照数据时，不将 +178 kW 或更精确的增益作为已核验值。

| 项目 | 工作区证据状态 |
|---|---|
| `scenes/turb3_stagger.yaml` | 存在；三机错列，4D 纵向间距、逐机横向偏移 31.5 m，8 m/s、270°，controls 为 yaw |
| `scripts/train/train_fastfarm.py`、`wfrl/rewards.py` | 存在；集中式 MAPPO 与 reference 奖励实现可静态检查 |
| 实验启动配置 | [v0.3.41 脚本快照](https://github.com/snode11/wind-farm-blender/tree/v0.3.41/scripts/demo)保留原命令；它们引用的权重与结果未分发 |
| `results/runs/stagE400ref_rollout.npz` | 本地缺失；同一文件见提交 `d6979f49` 的同路径 |
| `results/runs/fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_hist.json` | 本地缺失；同一文件见提交 `d6979f49` 的同路径 |
| `results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref.pt` | 本地缺失；seed 0 最终权重及同名前缀的 `_iter10.pt`、`_iter20.pt`、`_iter30.pt`、`_iter40.pt` 均保存在提交 `d6979f49`，可用 `git show` 恢复 |
| seed 1 权重（`_s1.pt`） | 当前仍未找到；历史脚本引用不等于权重仍保留 |
| seed 1 / seed 2、跨风速、跨风向、不同湍流 | 当前未确认完成；不能沿用“正在验证”描述现行运行状态 |

从仓库根目录取回文件，目标目录 `results/runs/` 和 `results/checkpoints/` 需已存在。以下命令恢复 seed 0 的 rollout、训练历史、最终权重和 4 个迭代权重。

```sh
git show d6979f49:results/runs/stagE400ref_rollout.npz > results/runs/stagE400ref_rollout.npz
git show d6979f49:results/runs/fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_hist.json > results/runs/fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_hist.json
git show d6979f49:results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref.pt > results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref.pt
git show d6979f49:results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter10.pt > results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter10.pt
git show d6979f49:results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter20.pt > results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter20.pt
git show d6979f49:results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter30.pt > results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter30.pt
git show d6979f49:results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter40.pt > results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_iter40.pt
```

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

提交 `d6979f49` 的训练历史中，`hist["power_mw"][39] = 3.0641117 MW`，表示第 40 轮全部 400 个采样步的平均全场功率；这些步的动作由 `dist.sample()` 在该轮参数更新之前采样。原记录报告值“训练末轮均值功率约 3.064 MW”与该保存值按相应精度四舍五入后一致。

确定性 rollout 使用 `Normal.mean`；其末 50 步统计与训练末轮的 400 步统计在采样方式、统计窗口和参数版本上均不相同，两项功率不能作为同一口径的直接比较。

最终 checkpoint 的 `exp(actor.log_std[0]) = 0.573466`，单位为 **°/控制步**，表示裁剪和占空比约束之前的偏航增量采样标准差。原记录报告值“探索标准差约 0.57”与该最终值四舍五入后一致。训练历史未记录这个参数，不能将最终 checkpoint 的数值描述为训练全程恒定的标准差。

对照必须匹配初始化、来流、预热、控制步与统计窗口。脚本当前的 `--policy zero` 使用同一采样/统计路径；不能仅拿另一脚本、另一窗口的 3.116 MW 与不同运行直接配对。

## 3. 奖励公式与可保留的算法解释

当前 [FixedReference](../../wfrl/rewards.py) 实现为：

```python
shaped = (raw_reward - reference) / reference
```

`raw_reward` 来自 WFCRL 的按来流立方归一化平均功率及可用载荷惩罚，不是直接将 MW 总功率代入 `(P-P0)/P0`。`1.7017` 是原配置的基线奖励值，不能直接沿用于新来流、载荷系数或新控制器；须先取得匹配零动作基线。

原记录将性能变化归因于 reference 奖励、回合长度与偏航占空比滑动窗口。这是待检验的机制解释；没有保留的匹配消融证据，不能分别宣称三者的因果增益。`--duty-penalty 0` 只关闭额外惩罚，不关闭环境约束；占空比的窗口与阈值应以实际驱动配置为准，不能固定复用旧文中的 20 步 / 30%。

训练使用分布采样，确定性评估使用 `Normal.mean`，两者测量的策略行为不同。应按部署策略做匹配评估，并报告统计窗口与重复运行；仅有一条确定性 rollout 不能证明跨种子复现、鲁棒性或统计显著性。

## 4. 当前复现与演示入口

训练与评估使用下列模块，并显式指定匹配的场景、权重和输出路径。`stagE400ref` 的 seed 0 匹配最终权重及四个迭代权重保存在提交 `d6979f49`，可按上面的命令恢复；seed 1 权重（`_s1.pt`）当前仍未找到。报告中的耗时与收益不能作为新运行的保证。checkpoint 名称与实验身份以训练入口的实际输出为准。

- [动态训练入口](../../scripts/train/train_fastfarm.py)：训练与同路径零动作基线，支持回合、预热、奖励、种子和场景配置。
- [确定性评估](../../scripts/experiments/rollout_ckpt.py)：从匹配 checkpoint 重新推进后端，输出数值轨迹。
- [后端 Trainer](../../wfrl/studio/trainer.py)：`_replay_policy` 加载权重并重新执行物理仿真；不读取历史 NPZ。稳态 FLORIS 参考不能当作 FAST.Farm 动态尾流真值。
- [算法与依赖分流](MAPPO_SETUP.md)、[本地配置](../setup/setup_local.md)：当前源码入口与平台要求。
- [Blender 策略评估](../demos/Blender策略评估.md)：Replay 面板、模型兼容条件及实时后端边界。
- 当前 Blender [演示](../demos/演示指令单.md)：使用扩展内置保存结果离线观看；它与本实验效果分别判定。

完整复现需要匹配的权重、基线、策略 rollout、训练历史及配置来源，并进行独立评价。

## 5. 尚未完成的研究问题

| 问题 | 需要的证据 |
|---|---|
| 跨种子复现 | seed 1 / 2 的匹配基线与策略 rollout，统一窗口 |
| reference / level、回合长度、占空比模型的效果 | 控制其他因素的消融及重复运行 |
| 扩展到 6 台或更多机组 | 新布局及动作/观测兼容检查、重新评估 |
| 湍流、风速、风向鲁棒性 | 冻结测试工况与保留完整时序 |
| 与固定 20°、传统优化器比较 | 同布局、同来流、同控制约束的直接对照；文献百分比不能替代 |
| 真实风场部署 | 独立的硬件、控制语义、响应与安全验证；当前没有完成证据 |

该特定算例的 +5.3% 只保留为历史报告，不证明通用增发电、减载、跨种子复现或真实风场部署。

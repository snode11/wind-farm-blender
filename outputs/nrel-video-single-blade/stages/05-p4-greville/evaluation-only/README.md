# P4：冻结后三组独立三维评分

状态：`ALL_THREE_FROZEN_BRANCHES_SCORED / COMPLETE_SIGNATURES_UNCHANGED`。三组评分全部完成；原验收均为 `NOT_YET_ESTABLISHED`，用途验收保持 `PENDING_USE_CASE_TARGETS`。具体结果与解释见[总报告](../README.md)，机器可读对照见 [cross_branch_summary.json](cross_branch_summary.json)。

评分前确认 `fit/model_freeze.json` 存在、三组全部完成，核对冻结模型、源码及拟合输入共 134 个签名检查项，通过后才读取真值与历史评分。加入冻结文件本身、原评分配置、真值 PLY/元数据、历史正式 v2 评分及本次评分脚本后，**141 个保护文件运行前后签名全部相同**；三组各四个元数据对象共 **12/12 contract 通过**。记录在 [evaluation_record.json](evaluation_record.json)。

未经修改的 `wfrl/nrel_reconstruction/evaluate.py:evaluate_run` 分别评价三组的 initial/prior-only/final。真值采用原首轮正式 `formal/evaluation-only/truth-at-tref/T1_B1_truth.ply`，保持原第二轮配置：8192 个全局样本、2048 个分区样本、seed=20261002，以及 z=9.225、30.75、52.275 m 三个截面。无配准、无尺度调整，没有修改拟合器或模型，也没有据真值调参或选择检查点。

距离是按源表面面积抽样到目标三角形表面的 Monte Carlo 统计。截面长短边来自固定 z 截面的 xy 最小面积包围矩形。评分中的根/中/尖分区是 [0,20.5]、[20.5,41]、[41,61.5] m，**不是 P3 的 [6.15,12.30] m 诊断根带**。图像目标降低不等于几何改善；数值等价、闭合表面和有限误差也不能替代工程精度与覆盖验收。

输出：

- [original_raw/scores.json](original_raw/scores.json)、[physical_reference_center/scores.json](physical_reference_center/scores.json)、[greville_normalized/scores.json](greville_normalized/scores.json)：原评分器完整结果。
- [global_distance.csv](global_distance.csv)：18行双向距离；[section_errors.csv](section_errors.csv)：27行截面尺寸/绝对误差/闭合与自交状态。
- [cross_branch_summary.json](cross_branch_summary.json)：全部分支、初始/无图像对照、历史正式 v2 和跨分支差异。
- [几何对照 PNG](cross_branch_geometry.png) / [PDF](cross_branch_geometry.pdf)：已打开检查布局。
- [evaluate_p4.py](evaluate_p4.py)：签名门槛、原评分器调用和摘要生成。

实际运行命令：

```bash
PYTHONPATH=. /opt/anaconda3/envs/wfrl-mac/bin/python outputs/nrel-video-single-blade/stages/05-p4-greville/evaluation-only/evaluate_p4.py
```

退出码 0，三组并行评分，各约46秒。后续核验再次通过141个保护签名、18行距离、27行截面及三个成功评分状态。脚本拒绝覆盖已有评分记录；直接重复运行不会覆盖本次证据。没有改变任何历史输出。

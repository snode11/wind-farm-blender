# P5：冻结后的独立三维评分

评分已完成：`BOTH_FROZEN_BRANCHES_SCORED / COMPLETE_SIGNATURES_UNCHANGED`。两组原综合判据均为 `NOT_YET_ESTABLISHED`，用途验收保持 `PENDING_USE_CASE_TARGETS`。`original_raw` 与 `revised_boundary` 全部冻结、源码和入口输入签名通过后，才读取原真值；原真值和视频继续引用历史路径。

8/8 元数据对象核验通过，162 个评分保护文件前后签名不变。原观测组的三份 PLY 和全部评分精确复现正式 v2；新边界组的四项全局 mean/P95 全部变差，根固定截面长/短边误差分别增加约 13.58/7.62 cm。完整结果见 [总报告](../README.md) 和 [跨组摘要](cross_branch_summary.json)。退出码 0，两组评分各约 37 s；没有据评分修改模型或标注。

`evaluate_p5.py` 使用未经修改的 `wfrl.nrel_reconstruction.evaluate.evaluate_run` 和历史固定采样配置。评分前要求 `original_raw` 的 initial、prior-only、final 三个 PLY 与第二轮正式 v2 逐字节一致；评分后要求其完整网格统计与历史评分精确一致。每组都保留同配置无图像对照、双向全局及 root/middle/tip mean/P95、三个固定截面的长短边误差、交叉检测和覆盖限制。

两组使用不同观测目标，各自的拟合图像损失和总目标值只能按各自目标解释。跨目标残差在模型几何冻结后计算，并纳入最终模型签名，仅用于明确目标变化的影响；不能据各自目标的下降宣称同一目标改善。44/45 帧保持非盲诊断身份。

图像贡献判据沿用原评分器；用途验收保持 `PENDING_USE_CASE_TARGETS`。只有评分器实际得出的状态可写入结果。评分成功、全局距离改善或封闭网格都不证明完整表面恢复和用途精度。

从仓库根目录运行（拟合和全分支冻结完成后）：

```sh
PYTHONPATH=. /opt/anaconda3/envs/wfrl-mac/bin/python outputs/nrel-video-single-blade/stages/06-p5-observation/evaluation-only/evaluate_p5.py --root '/Users/eason/Desktop/wfcrl/wind farm RL' --workers 2
```

入口在首次真值读取前保存 `evaluator_at_launch.json`，记录评分源码、配置及所有已验证文件签名。已存在启动记录、评分记录或分支评分时拒绝覆盖；失败或中断必须保留记录并调查。

输出：两组 `scores.json`、`cross_branch_summary.json`、`global_distance.csv`、`regional_distance.csv`、`section_errors.csv` 和 `evaluation_record.json`。所有差值为 B−A，负值表示误差减小；固定截面位置必须完全一致。

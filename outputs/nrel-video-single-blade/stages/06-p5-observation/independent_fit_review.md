# P5 独立拟合审查

审查时间：2026-10-03T02:30:16.459619+00:00。结论：**PASS**。本审查只读取拟合、RGB 注释和历史 v2 算法结果；没有运行优化、没有读取真值或评分文件、没有进行真值评分。

## 原始基线复现

original_raw 的三份 PLY 与 20261002-second/reconstruction 对应文件逐字节相同：

| 模型 | 字节相同 | SHA256 |
|---|---|---|
| initial_template | True | `27811c1a3f4c2f0195b3413787a7759ad03015175b41b1a2345633132c3edd81` |
| prior_only | True | `27811c1a3f4c2f0195b3413787a7759ad03015175b41b1a2345633132c3edd81` |
| T1_B1 | True | `89cee4964b9d943b3965f2014f57795a9277d3a1c9dfec919c3f677005634c7a` |

原始分支 prior_only 与 video 的全部 100 步 stage、iteration、resolution、image_loss、regularization_loss、total_loss 也与历史 v2 逐项相等；elapsed_s 是本轮运行时间，未用于数值复现比较。

## 对照与预算

两臂均使用原始 `optimize._fit`、BladeModel B-spline 原始 raw/tanh 坐标、零初始控制、Adam、学习率 0.012、seed 20261002、CPU/4 threads、batch_size=1。完整 frozen_config 与两臂 optimization.config 相同。每臂图像拟合为 40/40/20 步，分辨率为 160×90、320×180、640×360，共 100 步、600 次图像前向/反向。

prior_only 只计算一次，image_weight=0，同样 100 步；其完整历史复制到两臂并明确披露 shared_prior_only。两臂 initial_template/prior_only exact NPZ 的 vertices、faces、center_raw、chord_raw 逐数组相同；初始及 prior 的 raw 控制全部为零。

## 注释与输入

12 条观察逐字段比较，仅 C1#42 的 contour_points_path 与 contour_valid_path 改变。所有 F/B/U 路径、标量不确定度、相机、根部姿态、时间和 split 保持一致，并且原始观察与输入文件当前 SHA 与入口冻结签名一致。

原始 1104 点按原顺序和数值完全保留，追加 710 点；修订 valid 等于原始 valid 与 reviewed support 的严格并集。新开域 11360 native pixels。注释的实际 MP4 RGB 像素签名在入口预验证中匹配；完整解码 C1/C2/C3 各 100 帧，selected RGB SHA 验证通过。

max_observed=600 未变。三个分辨率下 C1#42 的原始版本为 600 个原始点；修订版本为 365 个原始点与 235 个新增点。因此本次是观察集合及其固定采样空间权重的消融，710 个密集 RGB 点不应解释成 710 条独立 3D 信息。

RGB 注释 provenance 披露了检查 schema 时意外看到校准/姿态字段；它声明边界选择和提取未使用其数值，也未使用投影、模型、损失或评分。文件一致性审查可确认本轮冻结未变；它不额外证明历史人工选择过程。

## 冻结与共同目标残差

| 冻结阶段 | UTC |
|---|---|
| RGB annotation | 2026-10-03T02:21:08.105235+00:00 |
| Fit launch | 2026-10-03T02:26:30.959385+00:00 |
| Both geometry/controls | 2026-10-03T02:27:26.154075+00:00 |
| Complete fit artifacts | 2026-10-03T02:27:29.524404+00:00 |

入口 111 项、实现源码 24 项、launch 6 项、geometry 20 项、最终结果 37 项签名均通过当前复核。geometry_freeze 的时间与最终 manifest.geometry_freeze_utc 一致；固定实现先写完并签名两臂几何，再计算 cross residuals，最后冻结包括残差的完整结果。

cross_observation_residuals.json 共 48 行：两个观察版本 × original_raw/revised_boundary/shared_prior_only/initial_template 四个固定模型 × 六条 fit 观察。每行的 fitted_model、observation_version、split=fit 与实际归属一致，没有把不同训练目标的 own-target loss 作为共同目标比较。

已存在的 nonblind_residuals.json 在最终模型冻结之后由单独命令生成；44/45 属非盲诊断。本审查结论仅覆盖拟合执行和对照合同，未建立独立几何验证、完整表面恢复或正式替换验收。

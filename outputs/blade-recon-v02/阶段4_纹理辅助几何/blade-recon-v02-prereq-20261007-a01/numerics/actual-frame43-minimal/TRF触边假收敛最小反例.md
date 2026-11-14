# TRF 触边假收敛最小反例

旧 `source-release` 在 actual frame43 返回 nfev2 的 ftol success，但状态基本没有更新，成本2361.2106、梯度∞9935.95、optimality24378.78。该结果应标为 FAILED_NUMERICAL_GATE；terminal success 不应替代可用几何。新常数坐标原点候选在完全相同物理起点、输入、先验、边界、残差和150预算内，用17 nfev 将成本降到191.9428，optimality .009592。实际 Blender 的独立几何/对应是否通过仍须另验，不能从此单帧修复外推。

## 复现范围

`../actual_frame43_minimal.py` 只导入被冻结的 `source-release` 与 partial-contour-alpha 输入，不读取源 truth。重放旧 frame42 的物理估计状态，重新运行第二有效图像初始化：相位范围48.32777–72.32777°，步长0.1°、局部0.01°，240+20次真实残差调用，约5.80秒，选择67.50776822°。该选择与旧日志完全相同。frame42留下的 pitch2 距下界 -5° 仅8.88e-16°（一浮点 ulp），不是需要真值才能发现的问题。

SciPy TRF 先将边界起点移到严格可行域。在全零增量 q0 中，pitch2 的分量变成约1e-10，其他分量仍零。TRF 的初始半径为 `norm(q0 / x_scale / sqrt(v))`，此时为1.02597e-11；因为半径非零，未触发原本全零起点的 `Delta=1`。第一步状态范数约1.03e-10，微小成本下降便满足 ftol，相位和其他自由方向的大梯度没有被消除。

## 有限差分、方向与同预算对照

| 中央绝对步长 | nfev | 成本 | 梯度∞ | 初始半径 |
| --- | --- | --- | --- | --- |
| 1e-7 | 2 | 2361.21063056 | 9935.95384 | 1.02597e-11 |
| 1e-6 | 2 | 2361.21063056 | 9935.95382 | 1.02597e-11 |
| 1e-5 | 2 | 2361.21063056 | 9935.94664 | 1.02597e-11 |
| 1e-6 + 常数相位坐标原点 | 17 | 191.94280388 | .424258 | 1 |

初始 `Jᵀr` 的相位约-7835.8，edge0约-9935.95，flap0约+3305.53；三个步长方向和幅值一致。独立前向半成本差分（1e-5）相位约-7805.27、flap0约+3306.01、flap2_0约-1437.52，与中央导数符号相同。局部像素/最近折线分支存在有限差异，但不能解释这次完全相同的两评估假终止；改变差分步长没有修复。

修复只做等价变量平移：第一个 active 分量的 `q_ref=x_scale`，其余为零；物理状态 `p=x0+q-q_ref`，边界同移，输出 `res.x` 仍为物理 active state。CLI 相位一直 active，这里的归一化坐标起点范数为1。预算、质量门槛和残差不变。saved13D方向、explicit10D调用以及 active 子集均保留。没有强置valid。

## 独立新候选与验证

候选源码位于 `../trf-bound-origin-candidate/source/recon.py`，SHA256 `e6266871f6669d96d94a5701e9693b799c211449ad55e8c0b591431ea6d91156`，自身严格依赖 sibling 已保存。旧 release、full13、原production冻结输出未被本反例脚本修改。

`../trf-bound-origin-candidate/check_regression.py` 的5项验证通过：两个真实触边数值最小二乘反例（首个 active 为 bounded pitch/legacy10、bounded flap/full13）以及三个真实 P2 首观测/前导空帧/不等间隔回归。候选直接 Fitter frame43 replay 与手动等价坐标对照一致：17nfev、成本191.9428。修复后的 frame43 blade1 pitch imageσ约36.79°、flap2 imageσ约1.927m，仍然弱辨识；成本改善不意味着几何已通过。

root 接手新最终源码及四组完整默认预算重跑，本目录仅保留旧假终止与候选最小证据。最终新结果须按自己的源码/结果哈希独立记录，不能写回本目录的旧 frame43 记录。证据为 `freeze.json`、`comparison.json`，以及候选 `frame43-replay.json`、`regression.log`。

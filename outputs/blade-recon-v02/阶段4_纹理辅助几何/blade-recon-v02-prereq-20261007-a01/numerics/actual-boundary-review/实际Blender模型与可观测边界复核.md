# 实际 Blender 模型与可观测边界复核

本次只读复核针对修复 TRF 触边问题之前的 `source-release` actual 结果（recon SHA256 `4edf3ec61cbf0ac63729749dcf5b04515dfcb4179116b0ed20f06d7c875e5d59`）。它保留数值假收敛、局部未观测方向及模型族差异，不能用 terminal success 宣称实际几何可用。root 的新 v2 默认预算完整重跑需以新哈希另作验收，本报告不覆盖新结果。

## 数值与局部图像约束

| 帧 | nfev | cost | image rank | 梯度∞ | observed sections（各叶片/40） |
| --- | --- | --- | --- | --- | --- |
| 42 | 22 | 197.52 | 9 | .135 | 36/0/37 |
| 43 | 2 | 2361.21 | 13 | 9935.95 | 36/0/37 |
| 45 | 26 | 204.95 | 13 | .378 | 35/23/36 |
| 47 | 28 | 207.00 | 13 | .442 | 35/31/37 |

frame42 的 blade1（0-based）pitch、flap1、edge、flap2 图像σ均为 null。对应 posteriorσ 25°/4m/1m/1m 只来自先验，不能把模型推断当图像观察。frame43 虽局部 image J 满 rank，blade1 observed_sections 仍0/40；两者并非同一合同。rank只表示选定状态附近残差对方向有非零灵敏度，不证明轮廓贴合、未遮挡或几何准确。partial mask 的显式未知域仍需独立维持。

frame43 的大梯度与两评估终止已经单独复现为 FAILED_NUMERICAL_GATE，证据在 `../actual-frame43-minimal/`。三个步长1e-7/1e-6/1e-5均失败，根因是近边界增量坐标起点的 TRF 初始半径约1e-11。等价常数坐标原点在同150预算内解决这次假终止；这不能消除实际几何合同问题。

旧各帧可识别的 pitch 图像σ约2.65–6.13°、flap1约.108–.721m、edge约.106–.302m、flap2约.062–.953m。修复后单帧43 replay 的 blade1 pitchσ36.79°、flap2σ1.927m，说明原假终止点的小σ不能当高质量的估计。所有σ均以既定13D模型和局部 image Jacobian为条件，不包含 Blender模板差异及系统性边界偏差。

## 13D 模型族并不严格覆盖 Blender source

实际 Blender 的 `turbine_geometry.blade_mesh` 使用8组打包翼型坐标、19个 AeroDyn 原站、reference对齐、形状保持 Hermite插值；FarmFlex 显示 loft 为4 subdivisions、48 ring samples，保存 replay span 形状再变形。最后1.3666m有显示用途的 tapered tip，reference prebend也由另一个资产保持。

重建 `model.airfoil_ring` 使用解析 NACA厚度式及近似 camber、40×32截面库、线性弦长/扭角插值、固定二次prebend，再加每叶两项flap及一项edge全局振型。启用13D修复了旧合成真值含flap2而拟合固定0的错误，但不会把解析翼型变成 source 坐标翼型，也不会给所有截面独立的真实 replay 姿态或形状。

一个只读健康横截面对照，使用相同归一化弦向点及上下表面，没有平移/旋转/ICP或变形拟合。以下只是直接模板差，不是经过任意参数最优拟合后的误差下界，也不是actual solve的同r/c几何验收：

| 径向站 | source翼型 | 弦长 | 两模板周线点距 median/P95/max |
| --- | --- | --- | --- |
| 40.45m | DU21_A17 | 3.256m | .0323/.1129/.1252m |
| 44.55m | NACA64_A17 | 3.010m | .0404/.0725/.0768m |
| 56.1667m | NACA64_A17 | 2.313m | .0311/.0557/.0590m |

其数值与源码/资产hash在 `summary.json`。pitch及弯曲全局方向可改变周线的姿态与位置，不能直接把截面内固定的翼型坐标重新塑形。严格同模型族成立的原合成第三视角改善不能证明真实 Blender loft 已被13D精确覆盖。要分解 actual 的模型误差、观测误差与姿态估计误差，必须保留独立健康模板合同及 heldout证据；source形状只能用于评价，不送入求解。

## 独立对应与可接受边界

当前冻结 actual RGB v3 评分中，Patch/Crack 各7个非锚独立观察都保留分母，均0/7。Patch P95约1.84–9.09px，Crack约2.57–11.42px，固定1原像素门槛失败。锚帧拟合相机约.001–.002px误差是绑定构造结果；同帧独立 HoldoutPatch约1.84px、Holdout约4.46px 已失败，不能只用跨帧或灰度轮廓解释。

actual eval 的 `points_world` 与源健康三角面参数化没有被直接等同为算法 Atlas 的r/c。两模板存在结构性差异，若缺少明确不可重置的跨模板材质对应，不可制造一个“same r/c actual米误差”或用最近点替代。这里将几何准确性保留为 FAILED/未建立，用可复查的源模板差与独立像素误差定位边界，不以终止、满rank、先验posterior或新合成PASS消除失败。

复核未改生产、被冻结source/input/评分、mask、texture、camera/model，未运行额外实际数值拟合。源eval JSON只读用于合同核对，未进入solver输入；新TRF最小反例另用声明输入完成。阶段4仍未开启。

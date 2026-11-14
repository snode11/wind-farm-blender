# 固定小绝对 FD 的残差连续性只读复核

2026-10-06；reviewer：capture_contract。范围为冻结 `recon.py` 的 `Obs.sample_dt`、`point_to_polylines`、`silhouettes`，以及冻结 `window_surface_fit.py` 组装与已保存 A1 Jacobian 数组。**没有运行优化、残差再评价、几何成绩计算、h扫描或源码修改。** 本文件不能确定 correction01 首个 sparse A1 的根因，也不建议依据本复核追加参数搜索。

结论：输入距离图使用双线性插值，不能将其描述为整数像素 nearest-neighbor 残差。代码存在分段取样、轮廓候选选择、最近线段与有效性切换；它们可能使局部 Jacobian 随 FD 策略改变，但本次实际触发分支与退出机制尚未定位。已存 correction Jacobian 与同一点的固定h分块诊断吻合；旧相对h Jacobian与同一点固定h诊断明显不同，支持“旧导数/梯度对FD策略敏感”，不支持某一个策略已被证实为正确根因。

## 静态代码能确认的机制

| 路径/冻结行 | 能确认的行为 | 对连续性与小h的限制 |
|---|---|---|
| `recon.py:100-112` Obs构造 | morphological edge、DT、整数轮廓点和最多200点的整数索引在Obs初始化时一次生成；DT存float32，点坐标转float64 | 掩码/点列表是固定输入，不是每次state残差里重新阈值。不能把该整数输入定位误说成state每次变化被整数取整。 |
| `recon.py:114-127` sample_dt | `floor`只查找所在格点，随后以真实亚像素 `(ax,ay)` 做双线性插值；output为float64数组 | 固定图上的插值在格内为双线性，共享格边值通常连续；格线处梯度可改变。DT零值带内若四邻点均零，残差对更小扰动确实可保持零平台。float32格值并不等于输出以float32或整数坐标取整。 |
| `recon.py:118-127` sample_dt有效域 | 出画/非有限坐标返回NaN | 直接调用跨画框可改变有限性。但正式white/robust路径先clip，再加outside penalty，因此不能直接拿raw出画NaN推定正式残差零成本。 |
| `recon.py:149-165` silhouettes | 周向cross-product变号布尔筛选、`argmax/argmin`选左右候选，`has`失败时切换到离散最外顶点fallback；候选比例clip到0–1 | 存在hard候选/分支身份切换。一般相邻包络候选可能在切换处连续，不能说每次索引变化都跳值；退化/多个极值/不同纵向位置的候选切换有导数kink或值跳变风险。 |
| `recon.py:150,154` silhouettes退化量 | `cr-cr1==0`时替换分母1e-12，法向归一化除以`norm+1e-9` | 小分母/很小投影展向梯度能放大局部敏感性；是否本次接近退化不能仅凭代码确定。 |
| `camera.py:30-39` 与 `recon.py:166-171` | camera在z≤0.05把uv置NaN；silhouette另要求全部ring点有限、axis z>0.5，然后整截面/端环变NaN | 硬有效性阈值可导致截面和可用segment集合切换；本次是否接近这些阈值没有分支日志证据。 |
| `recon.py:187-198` point_to_polylines | 只保留有限endpoint segment；每点先投影到segment、clip t，再取所有segment的最小欧氏距离 | 连续非退化segment的单段距离通常连续；clip引入分段活跃区域，但不必然在每个endpoint切换产生一阶导数跳跃。最近segment并列/更换可造成梯度kink，零距离范数作为残差不可导；有限segment消失可能跳值。 |
| `window_surface_fit.py:249-251` | lateral polyline与root/tip rings两个候选距离再`np.fmin`；NaN距离替换固定`2*hypot(W,H)` | 再增加一个最近分支；finite↔invalid切换可能跳到固定大残差，不能静默当零。 |
| `window_surface_fit.py:241-247` | 正式轮廓取clip后DT+到画框的outside norm；非有限点转固定大代价；最后应用固定support | clip/outside是分段映射，画框/角点/零outside处可有kink。无效点的大代价可不连续。support在A0一次固定，六次方法显式复用，support本身不是随迭代增删行的机制。 |
| `recon.py:202-204` robust | `sign(r)*sqrt(2f²*(sqrt(1+(r/f)²)-1))` | 数学soft_l1等价变换在普通有限r区间平滑；实现的`sqrt(1+tiny)-1`在极小r存在浮点相消/归零风险。不能据此断言本次1e-4*scale造成相消：需实际残差变化量/分支证据。 |

## 静态代码能排除或必须区分的说法

- `densify` 的 `floor`索引来自固定section采样网格t，不随state改变；它是相邻截面固定线性插值，不是投影点整数吸附。
- 模板 `model.Rotor.blade/forward` 对本轮活动state主要使用三角函数、固定shape系数、poly和cumsum；模板截面/翼型/弦长插值在建模板时冻结。A1没有q，不能把此次A1终止归因于q的triangle chart切换。
- fitter初始化的unwrap/clip只处理初值；没有在每次residual调用中逐帧取模或反复wrap psi。
- 白化physical prior是线性state残差；actual-dt acceleration也是固定dt上的线性二阶state残差。它们本身不存在轮廓/像素branch。A1材料track与domain_penalty行为空；这不排除silhouette的有限性/出画罚项。
- 代码能确认“存在机制”，不能确认某次固定扰动实际跨了格线、哪个`chg/i_hi/i_lo/has`改变、最近segment是否换支、是否发生depth/finite切换，或哪项触发SciPy ftol。已保存call_z仅记录参数，没有逐调用branch identity或全部probe残差。
- 旧相对h覆盖更宽邻域可能给出某種平均/跨支导数，固定小h可能给出另一局部斜率。当前不能仅根据更低总代价或更小某种gradient判定哪一个是可信真实导数。

## 只读已保存结果与分块目标

引用：`diagnostics_old_attempt02/numerics/sparse_lsmr_A1` 与 `.../correction01/sparse_lsmr_A1` 的 `result.json`、`instrumented_solver.npz`、`residual_blocks.npz/summary.json`。只做已有数组的线性代数/目标拆分，没有调用fitter、forward或几何评价。

| 已存量 | 原 sparse A1 | correction01 sparse A1 |
|---|---:|---:|
| stop | xtol；nfev11/njev6 | ftol；nfev2/njev2 |
| cost | 6852.09523587929 | 6886.675164371183 |
| silhouette robust cost | 6845.972088132462 | 6845.738348615311 |
| physical-prior cost | 1.432819443442405 | 1.403487161768325 |
| actual-dt acceleration cost | 4.690328303384870 | 39.533328594103050 |
| silhouette rows / effective | 9136 / 7865 | 9136 / 7865 |
| material domain rows | 0 | 0 |

总代价差 +34.57992849 主要来自 acceleration 项差 +34.84300029；silhouette 项反而减少约0.23373952，physical prior减少约0.02933228。这是两个已保存终点的目标分解，不能解释为何correction只迈出一步，也不能代替几何评价。

correction日志有2次Jac，58列central、6列forward、0列backward；真实执行物理扰动范围约 `[9.99999999998e-6,1.000000000033e-4]`。call_role计数：role0=2、role2=64、role3=58，总124，与已保存真实residual计数一致。未执行侧记录0。

## 只读同一终点的已存 Jacobian 对照

将分块`robust_J`按silhouette/physical-prior/acceleration原残差行顺序合并，统一乘冻结physical column scale为J_z，再与各自终点的solver已存J比较：

| 已存同点对照 | 原 sparse A1 | correction01 sparse A1 |
|---|---:|---:|
| solver J_z范数 | 438.052402420487 | 464.085414565329 |
| 固定绝对h分块诊断 J_z范数 | 464.033773301639 | 464.085414588376 |
| J元素最大绝对差 | 35.788319546104 | 4.7253863e-9 |
| solver gradient_z∞ | 1.633157858495 | 172.278451729211 |
| 固定h分块诊断 gradient_z∞ | 85.453490652427 | 172.278451689149 |

correction的合并分块残差与solver残差完全相同，physical parameter向量完全相同；gradient元素最大差4.5113e-8。故其固定h增量Jac与独立同h分块实现的数值记录吻合，没有发现记录错位或漏加先验行。

原solver采用相对2-point FD（psi约0.054–0.099度）及稀疏差分；保存的分块诊断采用固定physical h=1e-4*scale（psi0.0001度）。同一点的明显Jac/gradient差确认FD策略敏感，但也混合了步长、单边/中心以及稀疏差分方式，不能把差值直接确定为某个残差branch、选定新h或宣称固定h已收敛。

数组身份：原A1 instrumented SHA-256 `e5c0fef0261d349eaea74739e1aefc383e43c2b7a14eb0f2f4ca33b6fabc9399`；correction首A1 instrumented SHA-256 `36114288033a76d6e60c6c057a5fab336f108b3155ed890e3e90de815c5204ad`。原result SHA `0b5dfd782051e1e018e7894f6076e570979b8ae4ad109a18fb1e2cec971eabc2`；correction result SHA `b92733617b91f2d457ec0542a8275aca1a40074e5ca02419b2005bf7d87e748e`。

## 尚未确定

没有branch日志，不能证明本次小h是否跨cell/nearest-segment/silhouette候选，不能确认相消程度，也不能区分ftol退出主要来自局部残差非光滑、受限信赖域/LSMR的步骤机制或其他数值交互。cost更高且大optimality与“几何mean稳定”不是同一证据；本记录只能要求保持NUMERICAL_INCONCLUSIVE直到根侧门槛审查。不得为凑正结果额外扫h或无限追加修正。最小后续如另获范围内授权，应优先记录已有失败点的branch与solver步骤证据，不通过新几何成绩调参。

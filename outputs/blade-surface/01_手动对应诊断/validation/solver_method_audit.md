# 窗口求解方法收尾审计

日期：2026-10-05。范围：本次新增的 solver 输入加载、模板表面坐标、窗口优化与专用测试。状态：**软件方法已实际运行；几何精度与用途验收保持 REVIEW_ONLY**。

本次审计仅读取求解源码、测试日志、允许输入的 manifest，以及此前 A0/A1 的求解结果和数值诊断；没有读取 `evaluation/`。没有再次修改 solver 源码、legacy 源码、vendor 或已安装扩展。

## 实际源码与输入尺寸

实际算法目录为 Git 根目录内的 `blade_recon/`，不是历史脚本中已经失效的 `WORKSPACE_ROOT/blade_recon`。本次新增文件如下，legacy `recon.py`、`model.py`、`camera.py` 保持原行为。

| 文件 | 实际职责 | 本次审计 SHA-256 |
|---|---|---|
| `blade_recon/input_bundle.py` | 显式白名单输入、尺寸与标定校验、RGB 解码、二维标注读取及访问哈希 | `f0f40a0a5417b8844ca1fa3f5cb40de6cbe735d5e6443e5d341dbeb214555f41` |
| `blade_recon/surface_geometry.py` | 健康模板 chart、三角形重心绑定、首次像素射线、估计网格可见性诊断 | `bcc1fb4a51663ee1368547df04e286dee45f1434a159d9e8870edefb68ef0902` |
| `blade_recon/window_surface_fit.py` | A1/A3 短窗口、共享 q、固定几何 q-only、稀疏最小二乘与局部敏感性 | `0201934504993bd38cef21cc220f5f9592c938817a306965e32f787ca8d7372c` |
| `blade_recon/tests/test_window_surface_fit.py` | 独立理想数学与数值边界测试 | `d4a80f34469073e013a54820ddd0c0a927932989c9f1615a576d2789bba921ab` |

现场读取 `input/solver_bundle/input_bundle.json`：8 帧（150–157），相机为 `WideInput`，每帧原图 **3840×2160**。用 `Camera.scaled(0.25)` 实际得到 **960×540**，两轴实际倍率均为 0.25。此前讨论的 1920×1080 已过时。

原图像素中心到轮廓处理像素中心的变换为：

```text
[u_fit, v_fit, 1]^T =
[[0.25, 0, -0.375], [0, 0.25, -0.375], [0, 0, 1]]
@ [u_raw, v_raw, 1]^T
```

表面轨迹始终用原始 3840×2160 像素中心和原始 K；仅轮廓 masks/相机使用 960×540。当前数据没有裁剪。裁剪和像素中心缩放的组合仿射正逆变换在独立测试中覆盖，不表示正式数据经过了裁剪。

根任务后续会从输入 RGB 组件审查产生独立 `solver_bundle_clean`，重新运行 fresh A0 与同设置 A1/A3，并保留旧 bundle 和旧结果。本审计的旧成本数值不能与清理后的输入成本直接当成同数据组比较；最终报告应按输入哈希、配置和 attempt 区分。

## 实际方法

### 输入边界

`load_bundle()` 要求 `blade-surface-input.v1`，`extrinsic_from_frame="model"`，`pixel_coordinates="original_image_pixel_centers"`，`color_order="RGB"`。仅打开 manifest 显式列出的 RGB、RGB 派生 mask 和人工二维标注。OpenCV BGR 解码后立即转换为 RGB。文件路径必须留在 bundle 根内；路径中的 `evaluation`、`truth*`、含 `object_id`、`geometry.npz`、`source-surface*` 等明确禁用名称被拒绝。它不是通用数据来源识别器；输入分离与驱动的实际访问限制仍需根任务独立核查。加载器不会搜索相邻 truth，也没有显示读取器依赖。

相机使用显式 `T_cv_from_model` 直接从 M 投影到 C，不再乘 `T_world_from_model`。标定须有有限 K、正确图像尺寸及 proper rigid transform。传入健康模板参数，加载器不接收源补漆 UV、源动态网格或真实状态。窗口 fitter 自身不读文件；主驱动传入的 A0 初值来自 fresh RGB legacy 路径。

### 规范态表面与共享材料坐标

材料点 `q=[span_norm, perimeter_turns]`，其中 span 是真实半径归一化 `(r-hub_radius)/(tip_radius-hub_radius)`，按 `Rotor.tpl.xi` 的非均匀截面插值。周向变量允许展开，几何使用 modulo 1；UV 为 `(perimeter_turns modulo 1, span_norm)`。

chart 派生固定模板 `triangle_id + vertex_indices + barycentric_weights`，三角形对角线与 `BladeTemplate.faces()` 完全一致。顶端封口没有此 lateral chart 表示，不会暗中转为侧面答案。法线根据模型截面轴向外定向。

每条轨迹只在首次经人工确认可见的输入像素上，对**当前健康模板和 A0 初态**射线求交以初始化 q。射线未命中为显式 `INITIAL_RAY_MISS`；不以最近顶点、源绑定或逐帧重新落点替代。随后同一个 q 跨所有输入时刻共享，位置由相同重心绑定和各帧状态共同解释。q-only 可复用相同 q 初值。

### A0、A1、A2 定位侧车与 A3

- **A0**：保留 legacy 逐帧轮廓、先验与活动参数行为；由独立通用驱动读取 fresh RGB/mask/逐帧标定。
- **A1**：窗口内逐帧状态联合优化，仅轮廓数据、相同物理先验与时间先验。
- **A2 几何**：由根任务逐值保存 A1 状态。`fit_material_only()` 只优化共享 q，状态逐值固定；它是固定几何下的材料定位侧车，不能算额外几何估计收益。
- **A3**：与 A1 使用同一 A0 初值、活动参数、状态边界、物理/时间先验、终止规则和预算，增加人工二维轨迹与共享 q。当前主活动集合由驱动明确冻结为 `psi + pitch_b + flap_b + edge_b`；其他状态保持 A0 值。

A1/A3 必须以同一初值构造 fitter，因为模型轮廓的初始入画支持槽位在构造时冻结。不得把 A1 已优化状态当作主 A3 初值。多起点、对应扰动、错误身份另报诊断组，不改写主组定义。

### 残差与可见性

轮廓复用 legacy `silhouettes/densify/point_to_polylines`，但 A1/A3 对初始支持的模型轮廓槽位采用固定维度，并对出画或失效投影保留明确成本。观测轮廓到模型折线一项也保持固定观测数。没有观测的轮廓项不产生图像支持。

每个已确认可见的轨迹观测具有两个原图像素残差，并具有负深度、出画、背面一致性的固定二次惩罚槽位。人工 `image_visible=false` 只在求解开始前排除，并记为输入未支持；**当前错误模型的可见性从不删除人工确认可见的点**。

图像残差只做一次手工 soft-L1 变换，`least_squares(loss="linear")`；物理/时间先验与有效域罚项保持二次。轨迹权重按声明 group 的观测数平方根归一化，避免一个局部补漆的重复点无界压倒轮廓；默认 `track_sigma_px=2`、`track_weight=12`。

优化后，针对全部三片**估计模板叶片**做射线遮挡检查。背面、负深度、出画或被另一估计表面遮挡均保留逐观测矛盾，状态标为 `VISIBILITY_CONTRADICTION`，不是通过。这里的遮挡检查不是源场景 object-ID 真值。该模块没有连续遮挡求交优化，也没有隐藏源网格筛选。

### 时间口径、预算与数值修复

psi/pitch 仅对活动角度作 360° 展开，用原数值加整数圈，避免改变固定角度的浮点值；这不处理 120° 叶片身份交换。相邻速度使用实际 dt 秒，角度为度、形变为米。加速度为 `(v_next-v_previous)/mean_adjacent_dt`，对应单位为 deg/s² 或 m/s²。匀速正常转动的加速度残差为零。默认速度先验关闭；若另行启用，它惩罚相对初始速度差，不惩罚正常转速。

主窗口预算为 `max_nfev=60`，固定几何 q-only 为 30；报告 nfev、njev、成本、optimality、原始 `||J^T r||∞`、触界数和固定残差维度。jac_sparsity 明确对应每帧状态、共享材料 q 与相邻时间项。有限差分评估的调用量不能把 nfev 简单当成总残差调用量。

本次有两项明确数值修复，A1/A3 一起使用并冻结：

1. `azimuth_unbounded=True`：psi 是展开相位，稀疏 TRF 实际使用 ±∞，其他状态边界不变。JSON 中保存此语义，有限的 legacy ±10⁹ 默认表项不会作为 psi 的 operational 边界。
2. `finite_difference_relative_step=1e-3`：显式传给 SciPy；也支持 15 个 canonical 步长（13 状态 + span/perimeter）。这是相对步长，零值参数仍采用 SciPy 的非零机器步长回退。

旧 A1 对 32 列的 initial/final、sparse/dense Jacobian 检查一致：最大 |J| 为 100，最大 |Jᵀr| 约 1231/1185；没有找到 10⁸ 级真实差分梯度或大量轮廓分支跳变。旧 10⁸ 级 optimality 是 bound-scaled 指标，不能解释为真实像素 Jacobian 爆炸。

同一旧输入、同一初值、同一 LSMR、同一 1e-3 步长与 30 nfev 预算的诊断中，±10⁹ psi 组最大 pitch/flap/edge 更新约 10⁻¹⁰；无界 psi 组更新约 0.01725° / 0.10641 m / 0.04194 m，目标从 24776.78 降至 24718.93。±180° 局部边界组也有类似更新。这支持“远伪边界导致稀疏反射尺度失衡”的数值解释，**不证明几何更准确**。

证据文件：`jacobian_diagnostic_attempt01.json`、`finite_difference_diagnostic_attempt01.json`、`bound_scaling_diagnostic_attempt01.json`。旧失败/停滞结果保留；清理 RGB mask 后的 fresh 实验应单独判定。

### 可辨识性诊断与局限

局部敏感性只用标准化、未鲁棒化的像素轮廓与二维点数据行。排除先验、时间平滑及深度/出画/背面罚项，避免它们给数据补秩。按明确参数单位尺度有限差分，再消去材料 q：`J_eff = J_state - J_q @ pinv(J_q) @ J_state`。报告消去前后的奇异值、秩阈值和逐帧状态列范数。

此检查具有下列限制：

- 一个相机、8 帧、同一局部补漆的少量点高度相关；点数增加不等于独立视差或全状态可辨识。
- 单目深度、形变、局部材料位置仍可能耦合。数据 Jacobian 局部满秩不证明全局唯一，也不是经过校准的物理置信区间。
- 使用声明健康设计模板；源展示网格的模板偏差、真实标定误差不能由求解成功自动排除，亦不能把吸收偏差的 flap/edge 称为准确恢复。
- 初始模型轮廓支持槽位冻结会依赖 A0 初值。图像中有轮廓但初始预测未入画的情况需独立诊断，不能把该机制当成完整可见表面证据。
- 叶片身份是显式候选；首次 ray miss 或错误身份拒绝仅是诊断，不是身份全局唯一性证明。
- solver.success 只表示数值终止规则满足。ftol 终止且梯度非零、触界、留出误差未改善或可见性矛盾均应报告，不能写成几何验收通过。
- q 的固定绑定与纹理随形变不滑动，只证明显示/绑定一致性；不能用来证明三维精度。
- 本轮没有确认用途精度阈值，也没有在本审计中进行独立源几何比较；不承诺毫米或厘米精度，不把结构叶尖、模板末端轴点或展示叶尖混为同一点。

## 17 项实际测试

日志：`solver_tests_attempt02.log`，实际结果 **17 passed in 0.70s**。它们是独立理想数学与软件回归，不是正式补漆 RGB 成绩。

| 测试范围 | 检验内容 |
|---|---|
| 1. chart | 非均匀物理 span、seam、q↔重心 round-trip、faces 与模板一致 |
| 2. 射线/法线/遮挡 | 前表面精确初始命中、向外法线、后表面遮挡矛盾 |
| 3. 时间 | 非均匀 dt、跨 360° 展开、正常匀速加速度残差为零 |
| 4. 固定维度 | 错误状态/背面轨迹仍保持维度和非零成本 |
| 5. 共享 q/q-only | 整条轨迹只有一个 q；固定几何 states 逐值保留并下降定位成本 |
| 6. 数据敏感性 | 排除 prior/domain/visibility 行，消去共享 q 后报告状态 SVD |
| 7. 白名单/颜色 | RGB/BGR 色卡、邻近坏 truth 未读取、禁用 truth 与越界路径 |
| 8. 未命中 | 首次像素 ray miss 显式失败，不用隐含最近点补齐 |
| 9. 坐标变换 | M→C 直接投影与 M→W→C 一致；重复乘 M→W 有显著错误 |
| 10. 像素中心 | resize 与 crop+resize 仿射及逆变换一致 |
| 11. 负深度 | 确认可见点在错误模型负深度时仍有有限、非零大代价 |
| 12. 失效轮廓 | 已支持槽位预测 invalid 时维度固定、成本不归零 |
| 13. 求解配置 | 实际 SciPy 调用使用 psi 无界、其他原边界、状态/q 向量差分步长；理想定位成本下降 |
| 14–17. 拒绝无效步长 | 0、负值、错误长度和 NaN 分别显式拒绝 |

复现命令（从真实 Git 根执行）：

```sh
env PYTHONPATH=outputs/blade-surface-20261005-manual-a01/runtime_pytest:blade_recon:blender_frontend:. \
  blade_recon/.venv-trial/bin/python -m pytest blade_recon/tests/test_window_surface_fit.py -q
```

软件闭环、外观恢复、人工对应可靠性、留出投影误差、几何改善和最终用途验收必须分别下结论。本审计仅支持前述方法与软件测试，不替代 root 的新输入对照和独立评价。

# P2b 捕获与独立评价合同只读复核

2026-10-06。本文件由 capture_contract 子代理只读核对；主采集、标记制作、求解与新评价均未执行。本文件的候选方案不等于冻结合同。P2b 主采集须由统筹代理在 P2b-0 诊断门槛通过后启动；数值门槛失败时本文件只是准备证据。适用 `AGENTS.md` 与 `.agents/skills/windfarm-blender-dev/SKILL.md`；不修改源 .blend、保存回放、安装扩展或已有输出。

## 已核对的来源与运行环境

- 源场景绝对路径：`REPOSITORY_ROOT/outputs/blade-recon-mappo-two-camera-20261004-a02/MAPPO_original.blend`。当前文件存在，12,116,819 bytes，当前 SHA-256 `1f1ff1d5211c54bd513eb272e4c1d3d99dffe6df5d1a4e511d5cff4566cf1328`，与旧正式 `evaluation/formal_window/source_identity.json` 和 `capture/formal_window/capture_report.json` 一致。
- Blender：`/Applications/Blender.app/Contents/MacOS/Blender`，本轮实际只运行 `--version`，返回 5.2.1 LTS，build `9e2066aef7ef`，Darwin；没有打开新捕获或运行渲染。
- Python：`REPOSITORY_ROOT/blade_recon/.venv-trial/bin/python`；当前实际 import 版本 NumPy 2.5.3、SciPy 1.18.1、OpenCV 5.0.0。不需要 torch、CoTracker 或环境重装。
- 旧源场景记录的回放包是 `USER_HOME/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender/assets/mappo`。该绝对路径及工作区 `REPOSITORY_ROOT/blender_frontend/wfrl_blender/assets/mappo` 均在本轮逐文件只读验证：两份 `manifest.json` SHA-256 均为 `d3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde`，manifest 的全部 14 个文件哈希均匹配。`geometry.npz` SHA-256 `289abea277d8a4954836b53bc06a1afcc41d8098a6fabe99f74c7f2ebe4ebac1`。
- 保存源数据时间为 117.0–177.0 s，共 2401 样本、40 Hz；`times (2401,)`、`transforms (2401,3,3,19,3,4)`、`poses (2401,3,6)`。这些源数组属于捕获/评价权限，不能进入求解或初始化。
- 新 run 的根侧 `provenance/source_identity_before.json` 已记录 dirty 工作区、Git HEAD `e504ed66548c4b774664233341b70c87512561ef`、分支 `wind-farm-rl-all-work` 和本次源码身份；本子代理未重置、回滚、覆盖他人文件。旧捕获读工作区 `blender_frontend/wfrl_blender` 源码，并读安装资产，不能称为安装版算法验证。

## 可复用的捕获通路

实际依据是旧 `capture/capture_wide.py`、`capture/capture_rgb.py`、`capture/README.md`、`capture/formal_window/split_frozen.json` 及正式逐帧提交记录。保持这一路线：

1. 独立 `BLENDER_USER_CONFIG`、factory 启动，导入工作区模块并开源场景；只保存新 run 下的副本。
2. 逐帧在 `readonly_frame_handlers()` 内执行 `scene.frame_set`、显式 `FarmFlex.update(sim_time_s=...)`、depsgraph update 后渲染；核对 `wfrl_clearance_time_s`，保留请求/已提交时刻、Blender frame 和叶片顶点字节哈希。
3. 保持 WideInput MODEL 中心 `(100,0,90)` m、WideEval `(100,-20,90)` m，瞄准 `(5,0,90)` m、水平 FoV 75°，原图 3840×2160，near 10 m/far 1000 m，真实结构遮挡保留。相机每帧按实际 `Mmodel` 放到世界；保存副本的最后世界姿态不能当连续回放绑定证据。
4. 实际已验证过的颜色通路是 `BLENDER_WORKBENCH`、`shading.light='FLAT'`、`shading.color_type='MATERIAL'`、`Standard`、RGB PNG、关闭 shadows/specular/cavity。该通路显示 `material.diffuse_color`，旧 repair shader 的 noise/bump 不自动构成输入图案。
5. 只把 WideInput RGB 与单路 MODEL→CV 标定写入 input；完整两路标定、WideEval RGB、世界变换、源状态/mesh、源 ID 与标记绑定写 evaluation/capture。MODEL→CV 应用一次，不能再乘 MODEL→WORLD。

旧回放导入时 `FarmFlex.__init__` 会重新生成 `blade_mesh(subdiv=4, ring_points=48)` 并重新绑定 cosmetic repair；所以新颜色必须在开源场景并完成 FarmFlex 激活后添加，不能依赖开文件前加到旧展示 mesh 的 face ID。

## 源 blade0 映射与侧面：需要明确反向对应

已证实的身份链为：源 `WFRL.Turbine.T1.Blade1` → `FarmFlex.blades` 的 `turbine=0,b=0` → 正式 `source_geometry.npz` blade order `T1/B1,T1/B2,T1/B3` 的索引 0。旧四点 `evaluation/attempt02/source_material_bindings.json` 的 `blade_id` 均为 0。模型活动目标预定 blade0，但最终新源映射要作为评价合同单独冻结，不回传源绑定或姿态给求解。

侧面不能仅按 upper/lower 名称直连。当前代码及本轮纯 NumPy 静态法向检查得到：

- 源 `turbine_geometry._resample_ring` 为 TE→upper→LE→lower，48 点。源 upper 内部面局部法向 x 为正（抽样范围 0.624–0.998），lower 内部面局部法向 x 为负（-1.000–-0.258）。原局部轴是 x downstream、y chordwise、z radial。
- 捕获的 MODEL 转换含 `diag(-1,-1,+1)`。因此源 lower（局部上风面，normal x<0）变为 MODEL 上风侧。
- 模板 `airfoil_ring` 的 upper 半圈（q perimeter 0–0.5）在零状态 MODEL 法向 x 为正（抽样 0.756–0.998），lower 半圈为负。
- 所以候选对应是 **源 lower 半圈 ↔ 模板 upper 半圈**；材料周向候选关系为 `q_template_perimeter = 1 - q_source_perimeter`，这只是评价侧的静态区域对应，不是拟合 q 的真值。旧四点源顶点 `%48` 在 34–38，确实落源 lower；旧 repair footprint 还显式筛 `normal.x < -.15`。

建议冻结名称使用 `source_local_upstream/lower_arc` 与 `template_model_upstream/upper_arc`，同时保存两侧 face ID 清单、参考拓扑哈希和方向检查，避免混称几何相同。新源 rest mesh 的 ring 结构为 92×48，另有 cosmetic apex；FarmFlex 的 comparison 还插入一个 loose structural-tip vertex，正式动态顶点共 4418。根/尖 caps 与 loose vertex 不属于 lateral 材料侧面。当前只读检查未读取任何新测试成绩。

## 12 点颜色标记的最小候选实现

无需修改叶片顶点或外形即可在**现有源 blade1 polygons** 上追加 `diffuse_color` 材料槽、改 `polygon.material_index`。现有拓扑 face 边界就是固定材料边界，随 `FarmFlex` 的顶点写入运动；可在三处候选展向约 .25/.55/.85，各使用四个颜色/形状 ID 明确、弦向与展向都有间隔的角点。真实 point = source triangle ID + barycentric（或明确现有 vertex）与颜色面集合只保留 capture/evaluation。必须核对标记前后源顶点、polygon 顶点索引、动态运动数组、轮廓投影完全相同。

不建议复用旧 repair overlay 复制出 12 个带 offset 的实体小片；直接面着色免除深度冲突及外形改动。不要为 sharp pattern 增加实体突起，也不要靠 known marker size 求姿态。若当前面分辨率不能形成 ≥8 原图 px 的清晰定位结构，记录 OBSERVATION_GATE_FAILED，而不是通过增加几何自由度绕过。

**掩码风险已定位：**旧 `prepare_input.py` 只接受 `neutral=(RGB spread<10)&(max>35)` 或 `red=(R>45)&(R>1.4G)&(R>1.4B)`。任意蓝/绿/黄高饱和标记会产生内部 mask 孔洞；黑线 max≤35 也会被挖空。最小候选是全部标记颜色均落于冻结的中性/红色占据类，用不同灰度/红色组合及固定图形角点表达 12 个独特 ID。必须从开发 WideInput 原图验证每个实际输出颜色落入同一规则、每个标记内部均属于同一 RGB mask，不依赖填洞或源 ID。必要改变 RGB 占据规则时四组统一、开发后一次冻结，并说明不能与旧 RGB 数据直接比较。现在尚未制作图案或证实12点可见。

Workbench 材质面颜色已被旧实际 PNG 验证；shader texture 路线仍需单独 runtime 小验证，不能从 nodes 存在推定 Workbench 输出。最小路线优先材料槽面着色。

## 已用片段排除与候选时序规则

只读实际 `capture/*/capture_report.json` 的已用时间：

| 原标签 | 已用时间 s |
|---|---|
| scout（Nacelle/Down） | 131.8,132.0,132.2,133.0,135.0,137.0 |
| wide_scout | 132.0,133.0,135.0,137.0 |
| flat_scout_back/front、flat_clean_front、flat_clean_depth_front | 132.0 |
| formal_window / attempt02 | 132.0,132.1,132.2,132.3,132.4,132.5,132.6,132.7 |

精确并集为 `{131.8} ∪ [132.0,132.7]@10Hz ∪ {133.0,135.0,137.0}`。建议预声明保守开发排除区间 `[131.8,137.0]`，而不是仅避开同一采样整数；该建议尚未冻结。正式可用 start 在 `[117.0,176.3]`、步长 .1 s；一窗 start+[0,.1,…,.7]，frame ID=`round((sim_t-117)*10)`，Blender frame=`round((sim_t-117)*60)+1`。

候选确定规则：先在剩余时间轴预声明四个相隔的时间 strata 与每个固定算术 anchor/备选顺序；第一个为开发，后三个为盲测。若要不同转动相位，可在捕获侧保存回放的 T1 azimuth 记录里预声明 phase bins，然后按固定时间顺序找对应 anchor；其源相位不传求解，不根据 A1/A3 成绩筛选。开发允许用 WideInput 图确认特征/结构遮挡，最终将三窗清单、所有点 ID、标记、颜色、采样与失败处理一起冻结。任何预声明测试窗的观察失败保留分母，不能看成绩后换窗。当前实际 four frame lists 保持 null；本子代理没有看新测试成绩、没有选赢家或启动新 scout。

若仅允许总64 RGB初始规模，不可把密集整段 RGB scout 当免费前处理。可用保存回放时序设计一次固定小清单；开发验证与正式64规模若有额外小渲染应明确数量和用途。同回放三片段仍是相关非重叠片段，不是统计独立现场样本。

## 主表面指标的最小复用方法

直接复用已有 `wfrl/nrel_reconstruction/evaluate.py` 的 `Mesh`、`triangle_areas`、`sample_surface` 和 `point_to_surface_distances`。后者是连续三角面的精确最近距离，计算面内垂足及三条边；有 `tests/test_nrel_evaluate.py` 的非最近顶点、面积概率、退化null等测试证据。不能拿旧 `evaluate_material.py` 的 point-to-point 误差当主面距离。

只需在新评价驱动添加材料区域适配与聚合：

1. 源 evaluation-only 导出**不可变 rest vertex 的 physical span scalar** `xi=(rest_z-HubRad)/(TipRad-HubRad)`、polygon/triangle side tags、actual loop triangles 及同刻 MODEL 动态顶点。模板采用已有 `rotor.tpl.xi`。源/估计分别在自己的 rest chart 上固定内/中/外范围；候选中心 .25/.55/.85 尚不是区域边界。
2. 在静态材料 scalar 上裁剪每个 triangle，保存裁剪新增顶点的 parent triangle + barycentric，使每帧从当时顶点得到同一材料区域。**不得对旋转后的 MODEL z 调 `clip_mesh_z`**，否则区域随组/时刻变化。可复用其 scalar clipping思想，但裁剪变量必须是immutable material xi。先根据上述反向侧面关系限制正确目标叶片/侧面，再做面积与最近面；不要从当前最接近相机或最近反面选区域。
3. 在每一时刻/区域，为源 clipped Mesh 用 `sample_surface` 固定 seed/count；源样本数组对四组复用。每个估计 Mesh 以自身当时区域三角面面积概率采样。该函数采样概率∝face area，返回等面积权重；若改固定face quadrature，必须使用各自face area/样本数且保存权重。
4. 分别 source→estimate、estimate→source 连续点到面距离；每个方向内部归一化为1，两方向各 .5。mean 为两方向mean平均。P95 从带 .5 方向权重的合并距离分布求；不得平均两个P95。所有方向样本数相同则直接合并等权，但仍保存方向/权重/区域/时刻/clip身份。
5. 每窗8时刻等权，三个测试窗等权；总体P95应从时间×片段×方向的等权混合分布求。内/中/外分表。全区mean若合并不同area区域应按声明区域面积权重，不把三带未经声明直接等权冒充整体面积mean。
6. 冻结 seed/count，开发做一次两倍density对照，比较成对改善量变化并进入 δ_dev。退化面、缺帧、错叶片/侧面明确null/failure并保留失败分母；零面积不能记零距离。无ICP、姿态、尺度再对齐。

旧源动态 NPZ 已有 MODEL顶点与三角面，但没有 rest material chart/side tags；新捕获评价侧需要补这份静态区域映射。模板族与真实展示loft并不一致，不能把最近面下界或侧面对应当语义材料点绑定。几何主指标和材料/留出RGB必须并列。

## q_eval 与源材料参考

最小复用 `surface_geometry.intersect_pixel`、`binding_to_chart` 与 `chart_binding`：所有组几何冻结后，输入同一首帧12个RGB查询，对各自冻结mesh只读定位；不得更新状态，不读WideEval或源绑定。可冻结 nearest model blade0 lateral ray hit 作为初值；若采用仅首帧q-only定位，所有方法同一损失/side候选/预算，并保存从首帧RGB产生的初值、最终q_eval与状态哈希不变。miss、cap hit、错blade/side须按冻结规则拒绝或失败，不静默丢点。q_eval与正式拟合q分文件；首帧/后7帧分表。

旧独立 `evaluate_material.py` 的 `ray`、two-sided `intersect`、`nearest_hit` 和固定 source barycentric路径可复用来构建**历史首帧射线参考**；新合成图案的真实source绑定另作第二参考。两表身份、构造性首帧零误差和覆盖定义分开，不能混成现场地标或回灌正式标注。

## 实际隔离需要补足

旧 `solver_read_policy.sb` 是默认allow+少量deny，只禁旧evaluation、工作区mappo、旧reconstruction及单个原blend；它**没有**禁止安装资产、capture副本、本轮evaluation或其他source路径。本轮不可直接复用其“probe passed”结论。

新求解/纹理系统沙箱至少拒读：

- `USER_HOME/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender/assets/mappo`（更稳妥可拒整个 installed assets，因dual-beam sidecar能相对回到mappo）。
- 工作区 `blender_frontend/wfrl_blender/assets/mappo`、源/回放原目录、各轮capture、evaluation、所有.blend、旧已解结果与源标记/ID/状态。
- input白名单中只声明WideInput RGB、RGB masks、单路标定、2D像素/ID/定位不确定度与健康模板配置；禁止source identity/location/known marker size字段。

同时用现有 `input_bundle.load_bundle` 的路径约束/允许键/access_log，每个实际打开文件记录hash；它无法阻止任意Python从外部偷读，因此不能代替macOS kernel sandbox。捕获/评价进程使用另一权限环境；执行前正例bundle可读与上列反例真实读probe均应记录。新评价可读已冻结测试结果；测试重建全部哈希冻结前不能开其评分。

## 尚未冻结/未执行

four windows、C4/D4/D12 IDs、标记尺寸与绑定、声明侧面face清单、区域边界、sample seed/density、q_eval失败处理、输入定位sigma/重复性、δ_dev/像素保护带等仍未冻结。上述源/环境/旧时序身份已现读核对，颜色可见性12×8、身份漂移≤1px、负例、密度稳定性、新求解与独立评价均未执行。P2b-0若判NUMERICAL_INCONCLUSIVE，应保留这份准备记录并停主实验。

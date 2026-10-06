# 叶片表面小实验：正式 attempt02

日期：2026-10-05。最终解释级别：**REVIEW_ONLY，几何精度未验收**。本次完成重新捕获的 RGB、A0/A1/A2/A3、公平窗口求解、独立评价和表面 sidecar。RGB 外观可以按固定材料坐标显示；本片段没有给出 A3 相比 A1 的有意义几何改善证据。

## 实验范围与真实基线

实际 Git 根是 `/Users/eason/Desktop/wfcrl/wind farm RL`，算法现在位于该根下 `blade_recon/`。HEAD 为 `e504ed66548c4b774664233341b70c87512561ef`，实际磁盘还包含大量既有未提交工作；起始状态 28,610 项保存在 `baseline_git_status.txt`。没有重跑 FAST.Farm/MAPPO、训练、提交、推送、发布、安装或清理。

原 `model.py/recon.py/camera.py`、既有 Blade Recon 数据加载器、源补漆模块和 MAPPO manifest 的 SHA-256 与基线一致。原 `MAPPO_original.blend` 仍为 `1f1ff1d5211c54bd513eb272e4c1d3d99dffe6df5d1a4e511d5cff4566cf1328`。日常安装的 manifest 与 repair 模块身份未变，详见 `preservation_audit.json`。本次实际 Blender 验证使用工作区源码和独立配置，不等于日常安装扩展验证。

模型仍是声明的健康 NREL 近似模板：3 片叶片、13 维状态、40 个非均匀物理展向截面、每圈 32 点、1 m 设计预弯。没有读取源动态状态来初始化，也没有修改模板去适配源补漆。补漆外观不等于局部缺陷几何；本次未添加单独自由叶片或未知相机估计。

## 捕获、像素与输入隔离

原 NacelleT1 6 个 scout 中，叶根及近处结构遮挡了可用信号。后续已知宽视角、背面、STUDIO 阴影、教学 Ghost 叠加及浅 near 深度闪烁的配置全部保留，见 `capture/README.md`。不能把这些失败图里的灰面当作已识别补漆。

正式片段为 **132.0–132.7 s，8 帧，10 Hz，3840×2160**。WideInput 是唯一求解与纹理输入；WideEval 是独立留出。新已知相机在 MODEL 中分别位于 (100,0,90) m 和 (100,-20,90) m，指向 (5,0,90) m，水平视场 75°。Workbench FLAT/MATERIAL/Standard、near 10 m/far 1000 m，关闭教学 Ghost，保留真实塔/机舱遮挡。这是受控合成材料颜色外观，未验证自然光照鲁棒性。

每帧显式执行已有 FarmFlex 更新、依赖图提交再捕获；提交时刻和源顶点哈希保存在捕获证据。相机每帧依据 MODEL 变换显式放置，使用直接 `T_cv_from_model`，不再乘 MODEL→WORLD。该标定与 Blender 原生投影的抽样最大差为输入 0.000526 px、留出 0.000400 px，只证明实现约定一致。保存的源捕获副本仅验证最后 132.7 s 快照，不能称 Wide 相机连续播放场景。

输入原图无裁剪。轮廓拟合缩至 960×540，中心变换为 `u_fit=.25*u_raw-.375`；点与纹理均使用原图整数像素。OpenCV BGR 转 RGB 一次。RGB neutral/red 阈值、输入 ROI、掩膜和哈希显式保存。第一轮视觉掩膜审核发现约 500/295 px 的两块独立地面区域；第二轮仅保留面积 ≥10000 的组件，约 414k px 的连续转子组件完整保留。未填洞或改变补漆内部占据。旧输入 `input/solver_bundle` 和旧结果 `groups/A*` 不覆盖；正式输入为 `input/solver_bundle_clean`。

4 个圆角补漆边界转折候选 × 8 帧共 32 点，来自输入 RGB 色块提议及 Codex 逐图视觉审核；**不是用户提供的人类标注，也不是自动跟踪结果**。点不确定度设为 2 px。均匀圆角补漆缺少内部纹理，角点材料身份最初只是可检验假设；未使用质心当材料点。后续独立评价明确测量该假设的轨迹漂移。

`input_bundle.py` 只读取显式 manifest 允许的文件，并记录读取哈希。主求解和纹理进程实际使用 macOS `sandbox-exec`，拒绝读取本 run 的 evaluation、MAPPO 源资产、旧 a02 解和源 blend；拒读探针实际通过。源几何、状态、对象 ID、原补漆绑定及留出 RGB 都未进入求解。正式文件和 q 结果哈希由 `primary_results_frozen.json` 冻结，根复核后才启动评价，时间和冻结哈希见 `formal_freeze_authorization.json`。

## A0/A1/A2/A3 的严格含义

| 组 | 输入与方法 | 几何/材料关系 |
|---|---|---|
| A0 | fresh 单输入 RGB 旧逐帧轮廓算法，8 帧 | 原算法不改；作为两主组共同初值 |
| A1 | 8 帧窗口轮廓、物理先验、实际时间间隔的加速度约束 | 只活动 psi/pitch0/flap0/edge0；其余保持同一 A0 值 |
| A2 | A1 几何 + 原 RGB captured texture | recon 与 A1 逐字节相同；q-only 定位不更新状态 |
| A3 | 与 A1 相同初值、活动变量、先验、边界、预算，增加 32 点和跨帧共享 q | 从 A0 独立启动；每个点只含一个固定 q |

额外 `S1_single_frame` 只用首张 RGB 做外观闭环，固定几何取 A1 首个保存状态；它不是独立单图几何恢复。`A3_surface` 是 A3 recon 的原字节副本加外观 sidecar。

q 为 `[物理展向归一化, 周向圈数]`，UV 为 `[q1 mod1,q0]`。三角 ID/顶点/重心权重是 q 的派生缓存，读入时检查一致。首次射线只在声明的估计模板上定位一次；主组和固定几何定位复用 q 初值，不逐帧重新射线绑定。4 点初始最近模板射线均命中 blade0，无真实身份回灌。

A1/A3 主预算均 max_nfev=60；停止容差 ftol/xtol/gtol=1e-7。物理边界 pitch [-5,90]°、flap [-3,12] m、edge [-2,2] m；展开 psi 实际无界。先验 weight=2，加速度 weight=2；角量/位移按 degree/s²、m/s²，`a=(v_next-v_prev)/mean(dt)`，正常匀速转动残差为零，速度先验禁用。轮廓 sigma=.25 拟合像素、鲁棒转折 .5 拟合像素；点 sigma=2 原像素，track weight=12，整组按 sqrt(32) 归一化。q 局部边界为初值 ±.15 展向、±.125 周向。完整设置见 `frozen_configuration.json`。

像素残差仅鲁棒化一次，SciPy loss 为 linear；先验保留二次形式。轮廓支持在共同初值冻结，点残差长度固定；坏深度/出框/背面有显式罚项，已确认点不会因估计可见性而静默删掉。最终 A3 32 个确认观测均保留，拒绝 0、模型可见性矛盾 0。

## 数值失败与修正

第一轮在 SciPy 稀疏 TRF/LSMR 下几乎只更新 psi；success=true 但 optimality 约 1e8、变形量变化约 1e-10 m，不能称可信收敛。输入雅可比正向差分和稀疏/稠密对照本身正常。实际同初值短诊断定位到 psi 的 ±1e9° 占位边界引发 Coleman–Li 内部尺度失衡：相同 LSMR、步长和预算下，无界 psi 恢复约 0.106 m flap 更新并降低目标。另显式采用 legacy 对齐相对差分步长 1e-3。这两项数值变化同时应用于 A1/A3，不把步长说成已证明的主要根因。

诊断保存在 `validation/*diagnostic_attempt01.json`。第一轮、旧 mask 和失败日志全部保留，不能把清理 mask 前后的成本直接比较。

正式第二轮 A1 nfev=11、A3 nfev=14，均 xtol 停止、各 1 个边界触及；raw gradient inf norm 为 1.63 / 17.54，并非 gtol 精密最优性保证。A1 数据平方和由 13691.48 到 13691.94，A3 由 13724.60 到 13711.83；下降不能替代独立指标。不同残差定义的 A1/A3 总成本也不作几何质量排名。

| 正式运行 | 墙钟/资源 |
|---|---|
| A0 全8帧 | 8.35 s；峰值 RSS 264,142,848 B |
| A1 含本地敏感性诊断 | 7.85 s |
| A3 含本地敏感性诊断 | 9.31 s |
| A2 q-only | 1.71 s |
| 完整窗口+多起点/错配/错身份诊断进程 | 53.93 s；峰值 RSS 519,929,856 B |

主 A1/A3 最大状态差仅 psi .001645°、pitch0 .005571°、flap0 .006898 m、edge0 .002157 m，其余 9 维逐值相同。多起点额外从 psi+3°/flap0+1 m 开始，最后目标 8202.25，主 A3 6862.25；该不同初值诊断与主解最大 flap 差 .0782 m，保留了初始化依赖。后半段错配角点使成本到 7432.96；错 blade 身份首帧射线失败被严格拒绝。没有用这些诊断挑选新的正式赢家。

## 独立评价：结果与限制

评价首次将输入首帧 4 个审核像素射线绑定到实际已提交源显示表面的三角形/重心，仅在评价侧固定材料点。其后随 8 帧实际源表面运动，并投影到独立 WideEval。源参考绑定从未回传求解。这是**首帧射线定义的源显示材料点**，不是已知物理地标，不是结构叶尖、模型轴端或现场计量。

首帧 4 个输入投影的零误差是构造的，已排除主要人工轨迹漂移统计。后续 28 个观测漂移：median **0.7463 px**、P95 **1.4491 px**、max **1.6050 px**。该短窗口中角部身份在 2 px 标注尺度内相容；不能推广到长视频、复杂光照或遮挡。`heldout_color_ROI_review.png` 的 8 帧灰块和红参考点已由 Codex 审查，不能称独立人类标注。

| 独立指标，原4K像素/同材料定义 | A0 | A1（A2同几何） | A3 |
|---|---:|---:|---:|
| 留出材料点误差 median px | — | 0.4952 | 0.4985 |
| 留出材料点误差 P95 px | — | 1.0717 | 1.0358 |
| 源材料点三维距离 mean m | — | 0.070636 | 0.070299 |
| 留出全图 occupancy proxy mean IoU | 0.945584 | 0.945934 | 0.945987 |
| 留出双向边界距离 mean px | 2.65687 | 2.64319 | 2.64118 |

A3 平均三维距离差仅 **0.000337 m（0.337 mm）**，median 留出点误差还稍高，不能据此声称有效几何改善或准确恢复。点分布集中在约中段一块均匀补漆，无法建立完整三片表面的独立观测。

occupancy proxy 由同一冻结 neutral/red RGB 规则与 ≥10000 面积筛选得到，再与估计 Rotor 所有三角面投影比较；没有用源 mesh 生成观测 mask。实际塔/机舱也可能进入 RGB 前景，模型轮廓没有整场景遮挡，因此该数值不是纯叶片边界精度。逐帧 mask、两个方向的距离和坏三角形计数均保留。点投影无效计数均 0；若出现坏投影，评价使用 2×图像对角线显式罚值，不默默滤除。

数据敏感性只包含原像素轮廓/点行，去掉先验与可见性/域障碍行；A3 还以 `J_eff=J_x-J_q pinv(J_q)J_x` 消去共享 q 自由度。当地有效状态 rank 32/32（阈值约 2e-4），奇异值从约 199.89 到 1.165；这是固定模板、固定关联和本解附近的局部结果，不能当唯一性、置信区间、全局几何可辨识性或物理精度证书。本实验统计单位是一个相关的 0.7 s 片段，不计算独立像素/帧置信区间。

## 原 RGB 外观与未知表面

Atlas 为每片 1024×512，固定 q/UV，原 RGB 最近整数像素采样；多帧融合选择最接近 RGB 中值的一个真实样本，不生成平均颜色。候选需朝外且通过对三片估计叶片的精确射线可见性检查。正式修复了早期 .10 m 深度栅格容差可能误接薄背面的漏洞；当前 1e-5 m 是数值一致容差，不是测量精度。

| 包 | captured texels | unknown texels（三片总量） | conflict | 多帧支持 |
|---|---:|---:|---:|---:|
| A2 | 689 | 1,572,175 | 0 | 653 |
| A3_surface | 686 | 1,572,178 | 0 | 653 |
| S1_single_frame | 622 | 1,572,242 | 0 | 0 |

A2 对输入 polygon 候选累计剔除 4826 个背面样本，A3 4820 个；额外自身遮挡剔除为 0。灰色重复支持和冲突 0 不证明材料定位精确。A2 模板捕获面积约 .9002 m²，所选叶片未知侧面积约 491.32 m²；这些面积只对应模板，不能说是实际源补漆尺寸。三片 atlas 超过 99.95% 仍未知，叶尖封口也未知。橙色为未知、紫色为已接受多帧颜色冲突，不能把完整橙色模型说成完整重建。

正式 texture 提取 A2/A3/S1 墙钟为 7.35/7.40/3.88 s，最大 RSS 858,390,528/863,272,960/536,903,680 B，包含文件校验和压缩；不是实时 FPS 或跟踪速度。详情见 `texture/formal_summary.json` 和实际 build 日志。

A2 recon 的 SHA-256 与 A1 完全相同：`8e2b72678637ccb12166c7bd4566101a4a03e9c72c4e84259dea0ef1cf920ab3`；A3_surface 与 A3 相同：`ac23462535d0e8ff7860b5aeb9335cc2f209c45b6100209af4f9ef4ac2dde6ca`。Sidecar 记录模型/拓扑/UV/资产 hash、实际 RGB 源像素、相机、帧、时间、q、三角 ID/重心和 support；包加载逐值验证颜色证据、路径和缓存，无邻近 truth 自动读取。

## 新文件、检查与使用

新增产品源码是 `blade_recon/input_bundle.py`、`surface_geometry.py`、`window_surface_fit.py`、`surface_texture.py`、`surface_io.py` 与 `blender_frontend/wfrl_blender/blade_recon_surface.py`。现有入口和原 v1 recon 格式保留。输入、求解、捕获、评价、纹理、验证驱动及全部报告只在本日期 run 下新增。未集成到日常扩展菜单或覆盖安装包；独立新场景演示实际工作区模块。

求解测试 **17 passed**；外观/sidecar 测试 **5 passed**；原数据/时序基线 **26 passed、1 skipped、91 subtests passed**。覆盖 MODEL→CV 与双乘拒绝、RGB/像素尺度、固定 q、周向接缝、共享材料、域罚项固定维度、无效/背面、状态保持、数值边界/差分参数、文件篡改/越界路径/数量和缓存拒绝。理想夹具和真实 RGB 产物分目录；理想夹具的显示往返不能代替以上独立评价。

实际窗口验证均为 `background=false`：A2、A3、S1 各 **34 项检查、0 失败**，证据与实际截图在 `validation/A2_window`、`A3_window`、`S1_window`。检查包括 timer 已提交才采图、float32 顶点与目标状态逐值一致、UV 跳转不变、packed、显式加载工作区模块后保存重开恢复，以及坏 sidecar 在分配场景前拒绝。S1 只有一个几何/图像时刻；不能把其 seek 检查说成动态单图推断。动态 replay 需按 `open_surface_review.py` 显式注册本模块；静态 packed 画面已保存在 blend，未安装或修改日常扩展。

最终 132 s 并排文件位于 `groups/attempt02/comparison_final/A2_A3_132s_static_comparison.blend`，A2/A3 六片局部几何与各自保存首帧逐值相同，布局 object 平移仅 y±82 m。它是静态同刻比较，源独立场景各保留8个采样；不是连续源与重建同步动画。`texture/patch_evidence_triptych.png` 清楚显示首帧输入/A2/A3灰色区域，nearest4×只为查看。早期远处背视角 overview、遮挡中央的 splash、启动 context.window=None、重开过早的黑图均保留为显示失败。最后 root 经 CUA 原生控制台打开正确保存文件，核对实际 AX 文件 URL 并审查左右前向转子、灰色补漆、明确 UI crop inset、同刻图例且没有 splash；成功图片是 `comparison_final/root_native_window.png`，记录在 `validation/root_native_validation.json`。窗口保持打开，root 未把临时 editor 切换写回 blend。窗口不证明 FPS、实时性或连续源 Wide 相机回放。

CoTracker/torch 在求解环境未安装，环境审计状态 **NOT_RUN**；本轮依照人工闭环完成几何比较，未下载模型、安装共享依赖或声称自动跟踪通过。系统 Python 缺 NumPy/pytest、捕获 contact sheet 缺 Pillow 的真实错误保留；使用已有 trial/bundled runtime 和本 run 局部 pytest 完成对应工作。

完整命令见 `REPRODUCE.md`、`capture/README.md`、`texture/README.md` 和 `evaluation/attempt02/README.md`。`fairness_final.json` 逐值验证相同配置、9个非活动状态与 A0 一致、A2固定状态/字节与A1一致、A3副本与A3一致以及确认点未因模型可见性删除。源码/输入/输出哈希清单由 `artifact_hashes_final.json` 保存；坏依赖、旧配置、失败求解和负例不清理。

本次可据此确认：RGB 确有可审核补漆，固定材料坐标外观链路、公平主组、隔离和独立评价已实际运行。可据此否定：该均匀短片段已经证明 A3 显著改善几何、完整观察、物理缺陷恢复、现场精度或使用验收。下一次研究若继续，应先增加更分散且身份更稳定的材料纹理/视角及多个独立片段，再冻结新的实验；本次不自动扩大范围。

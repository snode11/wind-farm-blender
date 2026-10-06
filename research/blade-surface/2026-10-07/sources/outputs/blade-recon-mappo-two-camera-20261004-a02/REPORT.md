# MAPPO 两相机图像叶片重建实验报告

日期：2026-10-04（Asia/Shanghai）  
实验：`blade-recon-mappo-two-camera-20261004-a02`  
实际求解状态：`COMPLETED`；独立三维评估状态：`EVALUATED`；窗口验证状态：`VISIBLE_RECON_SEEK_PLAYBACK_SOURCE_RETURN_VERIFIED`；精度状态：`NOT_ACCEPTED`。

> 2026-10-05 交付补充：本实验的输入、601时刻重建结果、评估及精度状态原样保留。其同步对照现已制成[0.3.15便携发布示例](../../docs/blender/0.3.15发布核对.md)，通过 **MAPPO → MAPPO＋重建示例** 打开；原研究场景仍在[split-playback/MAPPO_demo_reconstruction_split.blend](split-playback/MAPPO_demo_reconstruction_split.blend)。便携副本规范源是 `blender_frontend/wfrl_blender/assets/examples/mappo_reconstruction_split.blend`，将四图packed并用保存的manifest摘要恢复安装内MAPPO，不重新求解、不更改实验数据、不改变 `NOT_ACCEPTED`。本次之后的布局/窗口验证按新发布记录阅读，不回写为2026-10-04的原实验验收。

## 1. 实验结论与完成范围

本次已用现有 MAPPO 保存回放的 **T1 Down 与 T1 机舱两台实际相机**，采集两路图像、逐帧标定和仅由 RGB 产生的外部叶片掩码，实际运行老板交付的 `blade_recon` 逐帧求解。601 个时刻覆盖保存场景的仿真 **117–177 秒，共 60 秒**。本次结果已导入当前安装版 Blender 的 Blade Recon，导入了同一份原 MAPPO 场景，并生成了同刻实际网格与重建网格的对照材料。

完整结果可以形成随时间转动的三叶片模型，但独立比较显示，**当前结果不能认定通过叶尖位置或三维表面精度验收**。全片段 1803 个叶片参考点对照的距离 RMSE 为 **3.4914 m**，中位数 **2.7603 m**，P95 **6.3026 m**，最大 **8.9877 m**。这项比较的源端是实际场景的结构叶尖顶点，重建端是老板模型的末端轴参考点；其差异包含参考定义、叶片设计几何、预弯约定和运动参数模型的差异，不能直接叫同一物理表面的测量误差。

绿色标记大体随画面约束出现，但它是原算法的**截面轮廓邻近判据**，前端把整圈顶点染绿的行为不等于整圈被看见。全片段 13,100 次绿色截面标记中，970 次（7.40%）在截面自身的两侧轮廓点处不满足严格的像素贴合判据，依靠截面半格内的插值点扩展而来；49 次截面自身不能在任一相机中同时两侧入画；10 次标记依赖边框轮廓。7 个代表时刻的正确叶片可见 ID 核查中，按原半格扩展口径，111/112 个标记能在至少一路相机中得到同一叶片可见像素的邻近支持。这7帧参与过RGB门限开发，结果是开发帧一致性诊断，不能作为独立留出集的泛化评估；也不覆盖其余全部时刻、不证明隐藏表面的可见性或深度可辨识性。

本次完成图像采集、求解、独立三维数值评估以及原生导入、寻址、顶点颜色和静态对照渲染核验。**实际窗口已验证跳到末帧、播放／暂停，以及通过原生UI控制台调用操作符切至同刻原场景并返回重建**；实际两相机面板可见。坐标鼠标点击返回 `noWindowsAvailable`，因此鼠标按钮点击未测；本次也没有执行持续FPS基准测试。没有重新运行 FAST.Farm 或重新训练 MAPPO。没有执行与已删除单相机实验同协议的 A/B 对照，因此不声称两相机比单相机更准确。

## 2. 输入来源与时间

实际 Blender 程序为 `/Applications/Blender.app/Contents/MacOS/Blender`，版本为 **5.2.1 LTS**。采集与原生重建导入使用的是安装目录：

`/Users/eason/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender/`

实际扩展模块为 `bl_ext.user_default.wfrl_blender`，MAPPO 结果包来自该目录下 `assets/mappo/`，由其 `load_demo_scene`、`farm_flex` 驱动保存结果回放。保存源包文件摘要记录在 [capture_report.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/input/capture_report.json>)；源包 manifest 的 SHA-256 为 `d3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde`，柔性几何 `geometry.npz` 为 `289abea277d8a4954836b53bc06a1afcc41d8098a6fabe99f74c7f2ebe4ebac1`。工作区源码与实际安装来源分开记录，没有把工作区文件存在当作安装版加载成功。

| 项目 | 本次实际值 |
| --- | --- |
| 保存场景仿真时间 | 117.0–177.0 s |
| 相对实验时间 | 0.0–60.0 s |
| 保存结果源频率 | 40 Hz |
| Blender 显示时间轴 | 60 Hz |
| 本次图像采样率 | 每路 10 Hz |
| 每路采样时刻数 | 601，含首尾端点 |
| 两路 RGB 图像数 | 1202 |
| 每路图像与实际求解分辨率 | 960×540；`fit_scale=1.0` |
| Blender 帧映射 | 第 k 个图像对应 `1 + 6k`，仿真时刻 `117 + k/10` |
| 代表帧 | 0、17、50、150、300、450、600 |
| 渲染方式 | Workbench，STUDIO 材质色，Standard 色彩；保留实际场景遮挡物 |

601 个端点样本不代表 60.1 秒片段；首尾时间差为 60.0 秒。40 Hz 源频率、60 Hz 显示时间轴与10 Hz 图像采样各有不同含义，本报告不据此宣称实际窗口稳定 60 FPS，也不把软件渲染称为实体相机采样。当前实验使用的是已跑好的 MAPPO 片段，历史“66 秒 SYNTH”与交付包内“20 秒 synth20”均没有代替本次输入。

## 3. 两台相机、光心基线与标定

两台相机都由当前安装模块已有入口创建并使用既定取景。它们是两台不同对象、不同光心的相机，没有复制单路输入冒充多视图。此次包括用户追加的 T1 Down 相机，以及原要求用于叶尖运动／净空观测的机舱相机；没有使用叶根／中段／叶尖表面缺陷三相机阵列。

| 输入名称 | 实际 Blender 对象 | YawRoot 局部位置（m） | 水平 FOV | 云台 yaw / pitch / roll（°） |
| --- | --- | --- | --- | --- |
| T1Down | `WFRL.Camera.T1.Gimbal` | (-3.5, 1.2, -37.8) | 85 | 180 / -67 / 180 |
| NacelleT1 | `WFRL.Camera.T1.NacelleGimbal` | (-1.5, -2.3, 0) | 120 | 180 / -79.0724 / 180 |

两者父对象均为 `WFRL.Turbine.T1.YawRoot`。由其实际局部光心位置得到基线 **38.01433895 m**。该基线是实际场景相机光心间距，不是共光心三摄扇形阵列的角度差；两台相机仍需要在同一时刻共同观察同一几何才能提供对应的联合约束。T1Down 大量时刻没有叶片入画，基线本身不能证明本次具有连续、完整的双目深度约束。

每个时刻分别保存两台相机的 K、分辨率和外参。相机坐标采用 OpenCV 的 x 右、y 下、z 前；像素中心坐标采用 `(W-1)/2,(H-1)/2`。K 由实际水平 FOV 和960×540图像尺寸生成。外参矩阵名 `T_cv_from_world` 沿用老板数据格式，但本次所指“world”实际上为**随 YawRoot 运动的模型局部坐标**；此语义已写在标定说明中，不可把该矩阵当作固定全球世界坐标变换。

模型局部坐标 x 指向上风、z 向上。`T_world_from_model` 使用 YawRoot 实际刚体矩阵和 `diag(-1,-1,+1)`、局部 z 平移 -87.6 m 构造；这只定义支撑坐标与相机标定，不包含叶片方位、变桨、柔性形状或结构叶尖真值。逐帧 `T_cv_from_model = inverse(T_world_from_camera_cv) @ T_world_from_model` 进入求解适配，世界刚体运动没有被静默忽略。

| 投影核查 | T1Down | NacelleT1 |
| --- | ---: | ---: |
| 代表帧抽样入画顶点数 | 4 | 51 |
| 与 Blender 原生投影的入画最大差（px） | 0.0001692 | 0.0010606 |
| 含远离画面／近平面的抽样最大差（px） | 0.0115223 | 2.7239348 |
| 局部外参相对首帧的最大元素变化 | 8.10×10⁻⁶ | 1.07×10⁻⁵ |

这些结果验证本次像素与标定的几何一致性；入画检查只有上表数量的抽样点，不是全部图像像素验收。画面外或近平面的投影误差单独保留，没有删除它们以宣称全部通过。逐帧标定仍传入求解，没有以局部外参近似不变为由省掉更新。完整矩阵与时钟见 [calibration.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/input/calibration.json>)。

## 4. RGB 分割及可见 ID 核查

本次使用 [prepare_masks.py](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/scripts/prepare_masks.py>) 从每路 RGB 图像独立生成掩码。它没有读取原场景柔性网格、叶片状态、对象 ID 或结构叶尖。

每路以第0、10、20、…、600帧共 **61 帧 RGB** 的时间中位数构建背景。中性漆面候选要求 `max(R,G,B)-min(R,G,B) < 10` 且最高通道 >25；为保留现有叶尖红条纹，红色候选要求 R>45、R>1.4G、R>1.4B。两者都要求与中位背景的最大通道差 >18，随后保留面积 >100 像素的连通域；没有手画空间 ROI，也没有用目标“只绿叶尖”裁剪画面。

中位背景使用整个保存片段，因此这是离线分割流程，不能称为未经调整的实时算法。上述阈值利用了当前渲染场景的材质和背景，不能据本实验推定现场图像泛化能力。红条纹也满足同一变化门限，没有借用 ID 掩码恢复被漏掉的叶尖。

**数据复用边界**：0、17、50、150、300、450、600这7个代表帧曾用于RGB门限开发和ID核查，随后仍在这些帧上报告分割IoU和绿色ID一致性。它们不是独立留出集；这里的数值说明当前开发片段上的处理与渲染标签一致到何种程度，不能作为未经调参样本或新场景的泛化成绩。ID渲染参与了开发时的人工核查，但没有被分割脚本直接读取，也没有成为求解初值、目标三维坐标或优化参数真值。

全601帧中，T1Down有 **418 帧空掩码、183 帧非空**，像素数范围0–41,859；NacelleT1没有空掩码，像素数范围21,520–67,245。两路同时空的求解时刻为0。空掩码表示本次算法输入没有叶片轮廓，不能填成虚假观测。

先行核查记录在 [probe/segmentation_check.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/probe/segmentation_check.json>)。这7个时刻的 T1Down 只有第150帧含叶片，RGB 掩码 IoU=0.980644；其余6帧参考与预测均空，IoU 为 `null`，按 `TRUE_NEGATIVE_EMPTY` 记录。NacelleT1各帧 IoU 如下；该先行核查是代表帧证据，不是601帧人工标注。

| 帧 | 相对 t（s） | NacelleT1 RGB 掩码 IoU |
| ---: | ---: | ---: |
| 0 | 0.0 | 0.983029 |
| 17 | 1.7 | 0.976130 |
| 50 | 5.0 | 0.938981 |
| 150 | 15.0 | 0.988441 |
| 300 | 30.0 | 0.951848 |
| 450 | 45.0 | 0.939889 |
| 600 | 60.0 | 0.959430 |

求解后核查使用实际场景的可见 ID 渲染：红=B1、绿=B2、蓝=B3，保留场景遮挡，背景黑色。取最高 RGB 通道>32 并归属最高通道以包含抗锯齿边缘。ID 渲染保存在 `evaluation/`，分割和求解脚本均不直接读取它。7个开发代表时刻的汇总是累计像素 TP/FP/FN 计算的结果，不是逐帧 IoU 的简单平均，也不是独立留出泛化分数。

| 相机与分割方法 | Precision | Recall | 累计像素 IoU |
| --- | ---: | ---: | ---: |
| T1Down，RGB 外部掩码 | 1.000000 | 0.980644 | 0.980644 |
| T1Down，老板原自动 dark | 0.002624 | 0.018112 | 0.002297 |
| T1Down，老板原自动 bright | 1.000000 | 0.935676 | 0.935676 |
| NacelleT1，RGB 外部掩码 | 0.997964 | 0.968468 | 0.966558 |
| NacelleT1，老板原自动 dark | 0.103338 | 0.473485 | 0.092687 |
| NacelleT1，老板原自动 bright | 0.096943 | 0.571931 | 0.090385 |

原自动阈值假设的天空背景不适合当前机舱取景中复杂的场景物体；上述结果支持使用本次外部 RGB 掩码适配。bright 在 T1Down 唯一非空代表帧表现尚可，但不能替代机舱相机分割核查，也不能将其推广成全片段性能结论。

## 5. 原算法与本次输入适配

老板交付目录 `/Users/eason/Desktop/wfcrl/blade_recon` 里的三份原文件保持原样，求解使用独立 Python 环境 `.venv-trial/bin/python`。实际文件摘要如下，完整运行身份见 [solver_manifest.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/reconstruction/solver_manifest.json>)。

| 原文件 | SHA-256 |
| --- | --- |
| recon.py | `e5aeeb8b5cfe9f69507a07e925e7a85ca763a3204757bb1bb57de5dc07e6bf88` |
| model.py | `9253fdfb7bb1e76790129511a24b98311c08c7c14a2a54b0c012d260842b888c` |
| camera.py | `3c18384c5bb549a80945a766412c5c0ae14cd890f4900b5b94e6022cc123d644` |

本次新增 [solve.py](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/scripts/solve.py>)，SHA-256 为 `899a845803491e55df50217c03cd810f519781431412a5fa7cdf5b382490f0f1`。输入标定 SHA-256 为 `cccafecfe906fdd1680e9192c89b3c110202b60d4cdcbef6daa5fed10637b0d8`。与老板默认 `main()` 的差别明确为：

1. 强制读取两路对应 PNG 与已存在的外部掩码，缺失时报错，不自动回退到另一个分割方法。
2. 每帧重新实例化对应的两台 Camera，读取该帧模型局部坐标到相机坐标的标定；没有把单台相机复制两次。
3. `dt` 使用连续采样时间戳差，首帧使用1/fps；记录相对 t、仿真 t 和 Blender 帧号。
4. 保留原最小二乘、方位初搜、速度外推、`PhysPrior`、`last_obs`、参数边界与不可观参数门控；增加求解退出状态、活动参数、观测量、摘要、日志与中间结果。

最大转速默认20 rpm，首两帧 `max_nfev=150`，随后60；时序先验权重首帧0、第二帧0.2、随后0.5。优化的10个参数为方位角、三片变桨、三片一阶挥舞、三片一阶摆振；第二阶挥舞固定0。三片叶片各40截面、每圈32顶点，由原前向模型展开。未静默改写模型、不利用真值初值、不从真值反推参数、不以原网格代替重建输出。

以下评估信息明确禁止成为求解答案输入：MAPPO叶片方位、变桨、柔性网格、结构叶尖与ID图像。源端网格和 `Blade.data.vertices[-2]` 由采集脚本只写入 `evaluation/`，由独立三维评估及可选实际网格对照读取；[recon.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/reconstruction/recon.json>) 的状态来自图像优化与原先验。上节披露的开发帧ID人工核查不应被误写成留出集测试。

## 6. 求解运行结果及可信度限制

601帧全部输出，实际求解耗时 **123.385 s**。591帧得到优化器 `success=True`，10帧到达最大函数评估次数；全片段有 **335帧至少一个参数触及边界**。参数边界触及计数与完整状态范围见 [solver_statistics.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/evaluation/solver_statistics.json>)。

成功退出表示优化器满足停止条件，不等于已恢复正确三维状态。边界触及和少数未收敛帧表明当前图像约束、参数模型或先验存在张力，不能把每帧都生成模型当作精度通过。完整诊断保存在 `reconstruction/progress.jsonl` 和 `solve.log`，不以单个平均数掩盖失败帧或无观测参数。

原 MAPPO 展示网格从保存的具体设计翼型坐标、设计站位、参考点、弦长和扭角放样；其最末段包含呈现用圆滑叶尖封口，独立结构叶尖为额外的 `vertices[-2]`。老板模型依据 NREL 公共弦长/扭角/厚度族参数，用近似翼型周线生成40×32网格，并用一阶结构振型参数化变形。两者不是同一几何网格或同一翼型坐标采样。

本次给老板配置的预弯末端幅值为1 m，并采用相同的63 m叶尖半径、1.5 m轮毂半径、5°轴倾和2.5°预锥静态参数；这属于模型设计配置，不是逐帧真值姿态。源场景的预弯先应用在叶片参考几何上，再随加载后的结构变换与变桨运动。老板原模型的预弯项是 `prebend * n_b`，`n_b` 不依赖变桨；其挥舞/摆振方向和截面扭转另随变桨变化。该预弯约定差异是代码可确认的模型差异，但本次没有把3.49 m RMSE定量分解到单独某个原因，也没有修改原模型来强行贴合源几何。

## 7. 独立三维与图像对照评估

### 7.1 叶片身份与参考叶尖

三片相似叶片的图像轮廓允许120°循环身份歧义。评估在求解完成后枚举三个**全片段固定**循环置换，选择总参考叶尖RMSE最小者；没有逐帧重排来隐藏身份跳变，也没有用这个映射影响求解。最优映射为重建B1→源B3、重建B2→源B1、重建B3→源B2；601帧逐帧最优循环均为同一映射。

| 对照集合 | 样本数 | Mean（m） | Median（m） | RMSE（m） | P95（m） | Max（m） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 三片、全时刻合计 | 1803 | 2.8380 | 2.7603 | 3.4914 | 6.3026 | 8.9877 |
| 重建B1→源B3 | 601 | 2.6038 | 2.4939 | 3.1923 | 5.6656 | 7.1178 |
| 重建B2→源B1 | 601 | 2.8201 | 2.7171 | 3.4993 | 6.5493 | 8.3933 |
| 重建B3→源B2 | 601 | 3.0899 | 3.1304 | 3.7596 | 6.8517 | 8.9877 |

距离在已有标定定义的模型坐标中直接计算，没有拟合额外刚体对齐。源端为独立保存的实际 `vertices[-2]` 结构叶尖；重建端为 `Rotor.forward` 的轴 `A[:,-1]`。因此报告使用“参考叶尖差异”这个明确口径，不称为已经标定通过的净空误差或深度测量精度。

### 7.2 实际网格的双向最近顶点

7代表帧×3叶片共21个网格对，使用同一固定身份映射、原标定坐标和 cKDTree，分别计算重建顶点到源网格最近顶点、源顶点到重建网格最近顶点。21个“每帧每片平均距离”的平均值分别为 **0.9026 m** 与 **1.3478 m**。详见 `evaluation/mesh_nearest_vertices.csv`。

这是离散顶点距离，不是精确三角面表面距离。原网格与老板模型的采样密度、翼型、参考轴和叶尖封口差异都会贡献误差；本次没有用网格简化、真值姿态或拟合对齐削小这项指标。

### 7.3 原分辨率双向轮廓误差

评估在原960×540分辨率将老板原算法的截面包络两边及根/尖端环投影为像素线，计算无符号双向距离。模型→掩码使用入画模型线像素；掩码→模型排除3像素边框，另保留含边框诊断。模型投影线没有重新渲染场景遮挡，所以这一结果是图像拟合诊断，不能当作可见表面或深度精度。

| 相机 | 有定义的帧数 | 模型→掩码：各帧平均距离的平均（px） | 掩码内部边缘→模型：各帧平均距离的平均（px） |
| --- | ---: | ---: | ---: |
| T1Down | 183 | 6.6232 | 36.3316 |
| NacelleT1 | 601 | 5.7168 | 17.4349 |

T1Down空掩码对应的距离无定义，保存为 `null`，没有按零误差平均。两方向差异较大说明可见边缘与参数模型投影并非全部一致；“平均模型线距离较小”不足以证明所有观察边界被解释。完整逐帧统计、P95和最大值见 [metrics.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/evaluation/metrics.json>) 与 `evaluation/per_frame.csv`。

## 8. 绿色截面标记审核

原 `observed_sections` 规则是在至少一路相机中，同一插值展向位置的左右两侧轮廓点都入画且到掩码轮廓的距离 <3求解像素；每截面两侧半格内任一点通过即可标记整截面。本次 `fit_scale=1`，所以3求解像素就是3原图像素。多相机的“至少一路”按每路完整双侧配对判定，没有把一台的左侧和另一台的右侧拼成通过。

| 审核项 | 数量 | 占13,100次绿色标记 |
| --- | ---: | ---: |
| 保存的绿色截面标记 | 13,100 | 100% |
| 截面自身两侧未同时满足3 px，依靠邻域扩展 | 970，涉及461帧 | 7.4046% |
| 截面自身两侧无法在任一相机同时入画 | 49，涉及49帧 | 0.3740% |
| 去掉边框轮廓后失去所有半格支持 | 10，涉及9帧 | 0.0763% |
| 按原规则重算与输出标记不一致 | 0 | 0% |

13,100次为601×3×40=72,120个截面时刻中的标记计数，不能解释成13,100个独立三维表面测量点。T1Down可支持845次、NacelleT1可支持13,083次原半格标记，两路支持有重叠；这只说明本次哪路图像提供了轮廓判据，不能据此量化两相机相对单相机的精度收益。

7代表帧共有112次保存标记。用正确源叶片的可见ID像素、原图3 px邻域核查，截面自身两侧都对应正确ID的为 **107/112（95.54%）**；沿用原半格扩展规则的为 **111/112（99.11%）**。剩余1次未得到这项正确ID扩展支持；即使其邻近通用掩码轮廓，也不能把它认定为正确叶片观察。单路T1Down只支持12次代表帧ID核查，其余标记主要由机舱相机支持；逐帧结果保存在 `evaluation/green_id_audit.csv`。这些帧复用了RGB门限开发样本，因此上述比例仅为开发帧一致性诊断。

前端将通过截面的所有32个周向顶点染成绿色，橙色表示模型推断。绿色只能说明**图像约束相关截面**，不证明周向每个表面点都被相机看见、不证明没有遮挡、不证明物理深度唯一，也不证明重建坐标正确。没有为了得到“只绿叶尖”的预期外观而手工裁剪绿色范围。

## 9. Blender 原生对照与窗口核验

本次 [open_comparison.py](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/scripts/open_comparison.py>) 用当前安装版 `blade_recon_review.py` 导入新结果与两台标定相机，来源标签为：

`MAPPO T1 Down + NACELLE · two cameras · sim 117–177 s · 10 Hz`

已完成0、17、50、150、300、450、600帧以及回退17帧的原生寻址检查。Blender保存的重建顶点与老板相同前向模型的对应坐标最大往返差约 **7.61×10⁻⁶ m**，绿色/橙色顶点颜色与保存的 `observed_sections` 一致。它验证导入、帧寻址和显示没有额外扭曲，不验证原重建对真实几何的精度；前向模型文件hash也与求解一致。

原 MAPPO 场景从本次 `MAPPO_original.blend` 加入对照文件。实际网格线框来自 `evaluation/representative_meshes.npz`，共7时刻×3叶片；仅在对应核查帧显示，其他时刻隐藏，避免用插值真值补出未保存对照。视口线框颜色受Blender主题和显示方式影响，不能以是否呈青色来辨认数据身份；对象的对照角色与源帧属性才是其来源标识。另已渲染0、17、150、300、600帧的同刻重建与实际网格并排图，图中右侧实际网格使用青色。

证据：[blender_validation.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/visualization/blender_validation.json>)，状态 `NATIVE_IMPORT_SEEK_COLORS_AND_STILLS_VERIFIED`；[MAPPO_two_camera_reconstruction.blend](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/MAPPO_two_camera_reconstruction.blend>) 保存了重建、原场景和同刻并排场景。

后台原生操作符调用还验证了从重建第151帧（相对15.0 s）切到原MAPPO的Blender第901帧（仿真132.0 s），使用 `WFRL.Camera.T1.NacelleGimbal`，再返回重建第151帧。记录见 [source_switch_validation.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/visualization/source_switch_validation.json>)，状态 `NATIVE_OPERATORS_SOURCE_AND_RETURN_VERIFIED`。该文件明确标注方法为后台Blender操作符执行；这验证功能调用和时间映射，不是实际窗口鼠标点击测试。

实际窗口验证状态：**`VISIBLE_RECON_SEEK_PLAYBACK_SOURCE_RETURN_VERIFIED`**，记录见 [window_validation.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/visualization/window_validation.json>)。CUA读取实际Blender窗口截图并通过键盘输入，确认以下可观察变化；截图保存在本次聊天的CUA工具输出，JSON保存对应观察记录。

| 实际窗口操作 | 观察结果 |
| --- | --- |
| 重建第151帧→`Shift-Right`跳到第601帧 | 从相对15.0 s到60.0 s，几何与颜色改变 |
| `Shift-Left`回起点，空格开始播放 | 观察到第9帧→第90帧，几何与颜色随回放改变 |
| 空格暂停 | 停在第93帧 |
| 在原生UI控制台调用现有source操作符 | 切到原MAPPO场景第901帧、仿真132.0 s，实际机舱相机视图可见 |
| 在原生UI控制台调用现有return操作符 | 返回两相机重建第151帧 |
| 检查专用面板 | “本次 MAPPO · T1 + 机舱 · 两相机”面板可见，显示两输入、960×540、10 Hz、601帧、132.0 s及绿色截面0/0/27，精度“未验收” |

原通用Blade Recon面板经重新注册poll后在本次重建场景隐藏，以专用两相机面板呈现本次来源。坐标鼠标点击返回 `noWindowsAvailable`，所以source/return通过**实际UI内的原生控制台操作符调用**执行；不能声称它们的鼠标按钮点击已通过。播放只在两个不同推进帧及暂停状态核验，没有持续FPS基准测试。窗口里的完整模型、几何变化和绿色着色仅补足显示与操作证据，不改变三维精度未验收的结论。

用户随后确认其所说的demo指MAPPO保存结果回放，并要求查看当前对比方式。已通过 [show_comparison_frame.py](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/scripts/show_comparison_frame.py>) 在实际Blender窗口显示仿真132.0秒的同刻并排核查：左为重建样本151，右为原场景帧901的实际网格。右侧仅为展示平移155m，数值评估没有这个平移。该场景明确标为静态核查，并锁定当前帧；不是完整60秒连续并排动画。窗口截图已确认两网格与对应说明可见，记录见 `visualization/static_view_validation.json`；完整601样本的动画仍在重建场景内。本次保存文件的默认打开场景已改为该同刻并排核查。

## 10. 产物、复查入口与清理

| 产物 | 用途 |
| --- | --- |
| `input/T1Down/`、`input/NacelleT1/` 与对应MP4 | 实际两路采样RGB；求解读取对应PNG |
| `input/calibration.json`、`cameras.json`、`capture_report.json` | 逐帧标定、时钟、实际对象和来源 |
| `input/masks/<相机>/`、各路mask manifest、中位背景 | RGB分割输入与处理参数 |
| `reconstruction/recon.json`、`solver_manifest.json`、`progress.jsonl`、`solve.log` | 实际新求解结果、原算法身份与逐帧诊断 |
| `reconstruction/overlay_<相机>.mp4` | 原RGB上重建轮廓的完整片段叠加 |
| `evaluation/full_tips.npz`、`representative_meshes.npz`、`id_masks/` | 独立源端评估信息，禁止成为求解答案输入 |
| `evaluation/metrics.json` 与各CSV | 全片段及代表帧对照、绿色诊断 |
| `evaluation/overview_T1Down.png`、`overview_NacelleT1.png` | RGB、RGB掩码与ID边缘、重建轮廓对照拼图 |
| `visualization/comparison_*.png`、`blender_validation.json` | Blender同刻三维对照图与原生检查 |
| `visualization/source_switch_validation.json`、`window_validation.json` | 后台操作符与实际可见窗口证据，分别记录验证范围 |
| `MAPPO_original.blend`、`MAPPO_two_camera_reconstruction.blend` | 本次原场景及重建对照场景 |

评估环境未安装 matplotlib，`metrics.json` 明确记录 `plots.status=UNAVAILABLE`，因此没有宣称已生成 matplotlib summary图。蒙太奇与CSV已生成，可用于图像和时间序列复查。

复现主流程的命令参数为：

```sh
# 从本实验目录执行；Blender需载入同一安装扩展与源MAPPO包。
/Applications/Blender.app/Contents/MacOS/Blender --background --python scripts/capture.py -- --full
/Users/eason/Desktop/wfcrl/blade_recon/.venv-trial/bin/python scripts/prepare_masks.py
/Users/eason/Desktop/wfcrl/blade_recon/.venv-trial/bin/python scripts/solve.py --input input --out reconstruction --fit-scale 1 --overlay
/Users/eason/Desktop/wfcrl/blade_recon/.venv-trial/bin/python scripts/evaluate_run.py
/Applications/Blender.app/Contents/MacOS/Blender --python scripts/open_comparison.py
```

这些命令说明所运行的实验入口与关键参数；重新执行采集会重新写本实验输入，不应据此覆盖作为基线保留的结果。需要复现实验时应使用新独立输出目录。

用户追加两相机并明确要求清除刚产生的单相机实验后，旧目录 `blade-recon-mappo-nacelle-20261004-a01` 已按明确文件清单移至 macOS 废纸篓，清单合计 **1256个文件、307,489,380字节**，详见 [cleanup_inventory.json](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-recon-mappo-two-camera-20261004-a02/cleanup_inventory.json>)。此次没有删除老板原交付、历史synth20基线或既有MAPPO保存结果；新a02是独立实验来源，synth20默认结果没有代替它。

## 11. 已验证与尚未建立的结论

**已验证**：实际两台相机及其光心、60秒保存片段的1202张RGB与601个同步标定、RGB外部分割的开发代表帧核查、601帧实际原算法求解输出、原文件摘要保持、独立参考叶尖及网格顶点距离、原分辨率轮廓误差、原绿色判据与可见ID诊断、Blender原生导入与寻址颜色一致性、同刻静态对照；实际窗口里的跳帧、播放推进、暂停、专用两相机面板，以及原生UI控制台操作符切换同刻原MAPPO机舱视图并返回重建。

**未建立或未测试**：当前三维精度通过、净空误差通过、完整周向或隐藏表面被观察、连续双目深度约束、独立留出图像或现场泛化性能、硬件相机性能、两相机相对单相机的准确率收益、source/return鼠标按钮点击、持续FPS基准性能。窗口验证只补足已执行操作的显示证据，不能把3.4914 m参考叶尖RMSE改写为精度验收通过。

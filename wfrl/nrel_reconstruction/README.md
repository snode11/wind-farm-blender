# NREL 三摄视频到单叶片的研究实现

本模块读取带签名的 MP4、理想针孔标定和声明的叶根刚体位姿，研究目标为NREL T1/B1。生成端复用工作区Capture；评分由独立进程读取evaluation-only表面。静态表面研究与动态状态研究分别保存输入、模型、协议和结果；本模块与相邻 `blade_recon0.2` 材料点候选是独立研究。

自动生成的 RGB 阈值注释与调用者选择的参考帧使用 `NOT_REVIEWED`，`reviewer=null`；图像摘要标识输入，不代替人工可见性与叶片身份审核。

## 当前研究边界

静态正式结果保留v2。视频约束模型在对应片段的全局距离有改善，部分截面退化；可逆坐标和RGB边界候选未采用，完整几何验收仍待建立。动态原型为 `REVIEW_ONLY`、`DY1 NOT PASSED`：仅保存原视频索引42–45（场景帧43–46）四个时刻的估计，其余96帧为 `UNAVAILABLE; no interpolation`。显示同步检查不能替代动态计量验收，四时刻结果不能推广为完整序列或未见时刻精度。

| 证据对象 | 入口 |
| --- | --- |
| 静态正式v2及比较范围 | 单叶片研究索引（本地文件：`outputs/nrel-video-single-blade/README.md`）、v2实验报告（本地文件：`outputs/nrel-video-single-blade/stages/02-second-run/README.md`） |
| 输入、面积和根部诊断 | 诊断报告（本地文件：`outputs/nrel-video-single-blade/stages/03-diagnostics/README.md`） |
| 中心/尺度与未采用候选 | 受控诊断（本地文件：`outputs/nrel-video-single-blade/stages/04-p3-center-scale/README.md`）、可逆坐标比较（本地文件：`outputs/nrel-video-single-blade/stages/05-p4-greville/README.md`）、RGB边界消融（本地文件：`outputs/nrel-video-single-blade/stages/06-p5-observation/README.md`） |
| 动态原型与计量门 | 动态研究总报告（本地文件：`outputs/nrel-video-dynamic/runs/20261004-integrated/README.md`） |

## 模块职责

| 入口 | 职责 |
| --- | --- |
| `scripts/blender/capture_nrel_three_camera.py` | 可见 Blender 窗口中按显式中心时刻采三路 PNG、相机与刚体状态；独立导出同态 evaluated truth |
| `pack.py` | 生成端白名单交接、复用 PNG 目录 symlink 编码、空间实测 |
| `dataset.py` | 内容哈希、严格字段/路径/时间/刚体核验，MP4 流式解码 |
| `observations.py` | 对调用者选择的帧做 RGB 对比度分割，F/B/U 与排除框留档；人工审核状态独立标记 |
| `model.py` / `renderer.py` / `optimize.py` | 独立粗模板、CPU 可微三角形轮廓、Adam、小批次与同配置无图像对照 |
| `contour.py` | 可选的分段可微外轮廓目标，过滤内部与自遮挡边，独立有效域和像素不确定带 |
| `boundary_metrics.py` / `compare_rgb.py` | 冻结模型后的可信 RGB 边界、F 覆盖和 B 溢出对照；缺项记 null |
| `evaluate.py` | 单独读取实际 evaluated surface；面积采样到连续三角面的双向距离、分区和固定截面 |
| `diagnostics.py` / `preview.py` | RGB 投影复核、自包含可旋转模型预览 |

主机依赖可通过 `nrel-reconstruction` extra 表达；FFmpeg/FFprobe 和可见 Blender 5.2+ 单独提供。冻结静态实验使用 `/opt/anaconda3/envs/wfrl-mac/bin/python`，依赖版本和代码签名保存在实验 `implementation_manifest.json`。未要求 CUDA、PyTorch3D 或分割模型权重。

## 可复用命令

以下命令从实际源码根执行，所有输出使用新目录。路径工具可识别 Git checkout，或含 `pyproject.toml`、`wfrl/` 和 `scripts/` 的源码归档；嵌入使用时可传入已有的 `root=` 参数。外部绝对路径保持原位置，旧 NREL 产物路径仍按映射定位。`pack` 会拒绝已有 algorithm-input；`fit` 会拒绝覆盖已有 T1_B1.ply。当前采样/编码支持 15/20/25 fps。

```bash
PY=/opt/anaconda3/envs/wfrl-mac/bin/python
$PY scripts/nrel_single_blade.py pack --capture <完整采集目录> --output <新运行目录> --start 120 --duration 5 --fps 20
$PY scripts/nrel_single_blade.py inspect --input <运行目录>/algorithm-input --output <运行目录>/inspection --frames 42 43 44 45
$PY scripts/nrel_single_blade.py observe --input <运行目录>/algorithm-input --output <运行目录>/observations --fit-frames 42 43 --heldout-frames 44 45 --reference-frame 43
$PY scripts/nrel_single_blade.py fit --input <运行目录>/algorithm-input --observations <运行目录>/observations --output <新的模型结果目录> --config <冻结配置.json>
$PY scripts/nrel_single_blade.py score --reconstruction <模型结果目录> --truth <评分网格.ply> --truth-metadata <评分元数据.json> --config <评分配置.json> --output <独立评分目录>
```

`observe` 只适用于该输入的成像与已确认的 B1 过境。其颜色阈值、C1 遮挡排除框、连通域和不确定边界都必须在新的 RGB 中重新审核。自动生成的 annotation/reference 默认 `NOT_REVIEWED`，annotation 绑定当前 RGB 摘要；历史审查证据只适用于其原对象。不能把它当通用分割器或自动身份识别器。该静态链路使用轮廓，不提供通用身份跟踪。

v2使用 `observe --with-contours`，从 RGB 连通域在 F/B/U 不确定环生成之前提取可信轮廓。另存的 `contour_valid_path` 仅排除裁切和已审核遮挡；不能用 F/B/U 的 U 环删除真实边界。点采用原生图像像素中心坐标；缩放时使用 `(xy+0.5)*scale-0.5`，原生 3 px 不确定度同步缩放。`fit` 核验三摄与冻结帧的完整笛卡尔积、原生标定尺寸、轮廓坐标/有效域、RGB 来源与非真值分割声明。

可选配置 `model_interpolation: "bspline"` 和 `image_objective: "projected_contour"` 保留原始默认值。前者只平滑中心线偏移和弦长比例，独立初始模板不变；后者用双向稳健边界距离，正式拟合缺少有效轮廓时报错。硬可见性选择在反向传播时固定，已验证范围为闭合凸体和轻弯叶片，不保证任意强折叠网格的全局可微性。外轮廓仍不能单独确定厚度、背面与扭转。

研究输出以叶根局部米制坐标保存：x 轴向/厚度、y 弦向、z 展向；叶根为 Blender 原轮毂局部 z=1.5 m。输出是 t_ref 附近的受载近似表面；允许刚体位姿不含精确柔性形变。

三组结果从同一独立模板出发。厚度比例与扭转保持先验，中心线与弦长按图像优化。初始和无图像结果完全相同是有效对照，不人为制造差别。F 和 B 分别归一；U 和画外没有负约束。低分辨率使用半像素 K 缩放。评分不做自由缩放或刚体配准；空模型不填零。

冻结macOS正式拟合通过 `sandbox-exec` 拒绝读取生成端素材、精确源数据和 evaluation-only 文件内容，且禁用网络。策略及实际拒绝读取证明在 `formal/reconstruction/isolation.sb` 和 `isolation_probe.json`；复现时应使用自己的绝对路径生成策略。纯路径分目录不能替代进程隔离。

## 验证

```bash
/opt/anaconda3/envs/wfrl-mac/bin/python -m pytest -q tests/test_nrel_dataset.py tests/test_nrel_optimizer.py tests/test_nrel_evaluate.py
```

v2对应验证还包括 `test_nrel_model_v2.py`、`test_nrel_contour.py`、`test_nrel_renderer_audit.py`、`test_nrel_observations_v2.py`、`test_nrel_optimize_v2.py` 和 `test_nrel_boundary_metrics.py`；完整本机记录见v2的 `tests.txt`。

包括真实小 MP4 全解码、白名单/隔离/时间错误拒绝、实际梯度与有限差分、U 零梯度、半像素 K、近面剪裁、合成图像优化与无图像对照、连续三角面距离和空模型规则。实际 NREL 视频结果另在实验报告中给出，单元测试不能代替它。

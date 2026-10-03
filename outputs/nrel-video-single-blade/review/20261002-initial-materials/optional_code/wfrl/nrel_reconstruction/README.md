# NREL 三摄视频到单叶片的最小研究实现

本模块实现执行方案 v1.0 的首轮链路。生成端复用工作区 NREL `Capture`，研究算法读取带签名的 MP4、理想针孔标定和声明的叶根刚体位姿。评分是单独的进程入口。

2026-10-02 的实际运行与限制见 [首轮实验报告](../../outputs/nrel-video-single-blade/20261002-first/README.md)。这次运行得到视频约束模型与独立评分；全局距离改善，但部分截面退化，不能视为完整表面精度验收。

同日的 [第二轮实验报告](../../outputs/nrel-video-single-blade/20261002-second/README.md) 保留首轮，复用相同 MP4，增加可选 B-spline 参数插值与独立 RGB 可信轮廓目标。帧 44/45 已在首轮检查过，第二轮只称非盲时序验证。三组第二轮模型在评分前冻结；等预算对照与较长预算正式结果分别保存。

## 模块职责

| 入口 | 职责 |
| --- | --- |
| `scripts/blender/capture_nrel_three_camera.py` | 可见 Blender 窗口中按显式中心时刻采三路 PNG、相机与刚体状态；独立导出同态 evaluated truth |
| `pack.py` | 生成端白名单交接、复用 PNG 目录 symlink 编码、空间实测 |
| `dataset.py` | 内容哈希、严格字段/路径/时间/刚体核验，MP4 流式解码 |
| `observations.py` | 针对本次已审核过境的 RGB 对比度分割，F/B/U 和人工式排除框留档 |
| `model.py` / `renderer.py` / `optimize.py` | 独立粗模板、CPU 可微三角形轮廓、Adam、小批次与同配置无图像对照 |
| `contour.py` | 可选的分段可微外轮廓目标，过滤内部与自遮挡边，独立有效域和像素不确定带 |
| `boundary_metrics.py` / `compare_rgb.py` | 冻结模型后的可信 RGB 边界、F 覆盖和 B 溢出对照；缺项记 null |
| `evaluate.py` | 单独读取实际 evaluated surface；面积采样到连续三角面的双向距离、分区和固定截面 |
| `diagnostics.py` / `preview.py` | RGB 投影复核、自包含可旋转模型预览 |

主机依赖可通过 `nrel-reconstruction` extra 表达；FFmpeg/FFprobe 和可见 Blender 5.2+ 单独提供。本次实际使用 `/opt/anaconda3/envs/wfrl-mac/bin/python`，依赖版本和代码签名保存在实验 `implementation_manifest.json`。未要求 CUDA、PyTorch3D 或分割模型权重。

## 可复用命令

以下命令从实际 Git 根执行，所有输出使用新目录。`pack` 会拒绝已有 algorithm-input；`fit` 会拒绝覆盖已有 T1_B1.ply。当前采样/编码支持 15/20/25 fps。

```bash
PY=/opt/anaconda3/envs/wfrl-mac/bin/python
$PY scripts/nrel_single_blade.py pack --capture <完整采集目录> --output <新运行目录> --start 120 --duration 5 --fps 20
$PY scripts/nrel_single_blade.py inspect --input <运行目录>/algorithm-input --output <运行目录>/inspection --frames 42 43 44 45
$PY scripts/nrel_single_blade.py observe --input <运行目录>/algorithm-input --output <运行目录>/observations --fit-frames 42 43 --heldout-frames 44 45 --reference-frame 43
$PY scripts/nrel_single_blade.py fit --input <运行目录>/algorithm-input --observations <运行目录>/observations --output <新的模型结果目录> --config <冻结配置.json>
$PY scripts/nrel_single_blade.py score --reconstruction <模型结果目录> --truth <评分网格.ply> --truth-metadata <评分元数据.json> --config <评分配置.json> --output <独立评分目录>
```

`observe` 只适用于本次成像与已确认的 B1 过境。其颜色阈值、C1 遮挡排除框、连通域和不确定边界都必须在新的 RGB 中重新审核；不能把它当通用分割器或自动身份识别器。当前没有可靠点对应跟踪；第一版使用轮廓。

第二轮使用 `observe --with-contours`，从 RGB 连通域在 F/B/U 不确定环生成之前提取可信轮廓。另存的 `contour_valid_path` 仅排除裁切和已审核遮挡；不能用 F/B/U 的 U 环删除真实边界。点采用原生图像像素中心坐标；缩放时使用 `(xy+0.5)*scale-0.5`，原生 3 px 不确定度同步缩放。`fit` 核验三摄与冻结帧的完整笛卡尔积、原生标定尺寸、轮廓坐标/有效域、RGB 来源与非真值分割声明。

可选配置 `model_interpolation: "bspline"` 和 `image_objective: "projected_contour"` 保留原始默认值。前者只平滑中心线偏移和弦长比例，独立初始模板不变；后者用双向稳健边界距离，正式拟合缺少有效轮廓时报错。硬可见性选择在反向传播时固定，已验证范围为闭合凸体和轻弯叶片，不保证任意强折叠网格的全局可微性。外轮廓仍不能单独确定厚度、背面与扭转。

研究输出以叶根局部米制坐标保存：x 轴向/厚度、y 弦向、z 展向；叶根为 Blender 原轮毂局部 z=1.5 m。输出是 t_ref 附近的受载近似表面；允许刚体位姿不含精确柔性形变。

三组结果从同一独立模板出发。厚度比例与扭转保持先验，中心线与弦长按图像优化。初始和无图像结果完全相同是有效对照，不人为制造差别。F 和 B 分别归一；U 和画外没有负约束。低分辨率使用半像素 K 缩放。评分不做自由缩放或刚体配准；空模型不填零。

本次 macOS 正式拟合通过 `sandbox-exec` 拒绝读取生成端素材、精确源数据和 evaluation-only 文件内容，且禁用网络。策略及实际拒绝读取证明在 `formal/reconstruction/isolation.sb` 和 `isolation_probe.json`；复现时应使用自己的绝对路径生成策略。纯路径分目录不能替代进程隔离。

## 验证

```bash
/opt/anaconda3/envs/wfrl-mac/bin/python -m pytest -q tests/test_nrel_dataset.py tests/test_nrel_optimizer.py tests/test_nrel_evaluate.py
```

第二轮扩展验证还包括 `test_nrel_model_v2.py`、`test_nrel_contour.py`、`test_nrel_renderer_audit.py`、`test_nrel_observations_v2.py`、`test_nrel_optimize_v2.py` 和 `test_nrel_boundary_metrics.py`；完整本机记录见第二轮 `tests.txt`。

包括真实小 MP4 全解码、白名单/隔离/时间错误拒绝、实际梯度与有限差分、U 零梯度、半像素 K、近面剪裁、合成图像优化与无图像对照、连续三角面距离和空模型规则。实际 NREL 视频结果另在实验报告中给出，单元测试不能代替它。

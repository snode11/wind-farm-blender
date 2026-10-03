# 冻结模型表面—RGB证据审计

状态：**NONBLIND_DIAGNOSTIC_ONLY**。未读取 truth，未拟合、未修改冻结模型、未运行 Blender。

模型为 initial_template、26 次可信轮廓分支、正式 reconstruction；12 个观测固定为 C1/C2/C3 × 帧 42–45。42–43 为 fit，44–45 只作已看过数据的非盲诊断。

## 面积口径

三个模型共用 300 个顶点、596 个三角面及相同 face ID。每面使用 (2/3,1/6,1/6) 的三个排列作面积积分采样，权重为该模型面面积/3。相机射线对本模型所有三角面做双面 Möller–Trumbore 求交；严格更近的交点标自遮挡。F 仅表示可见模型点投影与审核前景一致，不表示该点三维坐标被测得。

| 模型 | 全表面面积 m² | fit 可见F % | fit 可见但无任何F % | fit 从未模型可见 % | eligible邻接面 / 3px内一致邻接面 |
|---|---:|---:|---:|---:|---:|
| initial_template | 498.869 | 34.166 | 14.333 | 51.501 | 97 / 2 |
| trusted_contour_26 | 496.604 | 42.342 | 4.737 | 52.921 | 105 / 71 |
| formal_reconstruction | 509.933 | 42.339 | 4.713 | 52.948 | 107 / 81 |

表格三种面积状态互斥且合计100%。其他 `visible_B_any`、`visible_U_any` 等 any-view 统计重叠，不能相加。轮廓“邻接面数”只是拓扑邻接，不能转换为已测表面积。

## 根部与固定截面

根局部带为 z∈[6.15,12.30] m；以落入此带的积分点累计权重，带边界未作精确三角形裁切。

| 模型 | 根带采样面积分母 m² | 根带fit可见F % | 根带fit从未模型可见 % |
|---|---:|---:|---:|
| initial_template | 63.279 | 34.991 | 53.904 |
| trusted_contour_26 | 63.802 | 44.404 | 54.950 |
| formal_reconstruction | 66.816 | 43.568 | 54.978 |

固定截面使用精确三角形—平面交线，每段3个线积分采样，以下分母是截面周长而非面积。

| 模型 | z m | 周长 m | fit可见F % | fit可见但无F % | fit从未模型可见 % |
|---|---:|---:|---:|---:|---:|
| initial_template | 9.225 | 11.131 | 37.560 | 9.026 | 53.415 |
| initial_template | 30.75 | 7.975 | 39.736 | 10.264 | 50.000 |
| initial_template | 52.275 | 5.101 | 35.508 | 14.492 | 50.000 |
| trusted_contour_26 | 9.225 | 11.158 | 43.614 | 0.000 | 56.386 |
| trusted_contour_26 | 30.75 | 7.712 | 48.551 | 1.449 | 50.000 |
| trusted_contour_26 | 52.275 | 4.640 | 49.181 | 0.819 | 50.000 |
| formal_reconstruction | 9.225 | 11.692 | 43.619 | 0.000 | 56.381 |
| formal_reconstruction | 30.75 | 7.731 | 48.551 | 1.449 | 50.000 |
| formal_reconstruction | 52.275 | 4.852 | 48.676 | 1.324 | 50.000 |

截面 individual-state 字段按任一所选视图聚合，非互斥。例如 C3 看不到根截面，所以 `outside_image=1` 可与 C1/C2 的可见F同时成立；请使用 `visible_any` / `never_model_visible` 互补对，或表格中的互斥三分法。

## 轮廓约束与模型分歧

eligible 要求：本模型真实前后面交界轮廓、精确射线未被本模型遮挡、落入独立审核 contour valid 域、存在可信观测轮廓。eligible 且距离≤3原图像素为不确定带内一致；距离>3仍是有效约束，标为 active residual。二者分开记录，未把3px内匹配当成唯一有效约束。审计在原图以≤1px间距采样，不复现优化器低分辨率和300点上限。

按初始模板 498.869 m² 为共同分母，fit任一相机/帧中三模型状态分歧涉及 34.213% 的加权采样面积。12视图合计为 53.136%。这只是同 face/sample ID 的模型条件状态分歧，标 uncertain，不是v2未恢复比例。共同先验也会让三模型一致但仍不可识别。

厚度比例、扭转、隐藏表面参数化、61.5m展长与椭圆截面结构仍属于固定先验；绝对厚度随弦长耦合变化。JSON/CSV 保留原模型历史 `fixed_priors` 字符串，其中 `section_thickness` 应按固定厚度比例理解。外部场景几何未加载，真实外遮挡只由审核 F/B/U 与独立轮廓域表达。三点积分存在边界和薄小区域近似误差。侧面标签是模板未扭转厚度轴的正/负半侧，不是经认证的压力面/吸力面。

## 输出与验证

- `surface_samples.csv.gz`：64,368 行，面/积分点/相机帧、像素状态、遮挡面、轮廓资格与固定先验。
- `contour_edge_samples.csv.gz`：98,523 行，轮廓边采样/排除原因/eligible/deadband/active residual；完全画外边用单行原因标记。
- `section_samples.csv.gz`：6,480 行，3个固定截面的局部像素与自遮挡证据。
- `model_disagreement.csv.gz`：21,456 行，共同face/sample ID的状态与轮廓资格分歧。
- `surface_audit.json`：分组统计、解释与全部实际输入SHA-256。
- `validation.json`：7个合成测试通过、面积/周长分母与互斥统计一致、资格/deadband分区一致，运行后输入签名再核验。

运行命令：

```sh
/opt/anaconda3/envs/wfrl-mac/bin/python -m pytest -q tests/test_nrel_surface_audit.py
/opt/anaconda3/envs/wfrl-mac/bin/python -m wfrl.nrel_reconstruction.surface_audit --root outputs/nrel-video-single-blade/20261002-second --output outputs/nrel-video-single-blade/20261003-diagnostics/surface/final
```

其他 MP4 帧候选筛查由主任务处理，本审计没有扩展审核掩膜。

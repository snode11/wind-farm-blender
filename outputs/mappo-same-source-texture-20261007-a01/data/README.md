> 2026-10-08 清理后说明：本次隔离安装、测试副本、派生输入与旧开发 ZIP 已移入废纸篓，历史证据保留；当前 0.3.17.1 安装包从 [Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.17.1) 获取。恢复位置与最终场景状态见[清理记录](../../../docs/maintenance/post-v0.3.17.1-cleanup-20261008/清理记录.md)。下文按其验证日期阅读。

# MAPPO 同源纹理数据交付

日期：2026-10-07（Asia/Shanghai）。这是对原两相机 MAPPO 实验已保存图像和重建状态的离线纹理后处理。未重跑 FAST.Farm、MAPPO 或几何求解。原精度状态继续为 **NOT_ACCEPTED**；本文件只报告数据适配和纹理生成，实际 Blender 窗口验收另见本次交付根目录。

## 数据来源与派生合同

原输入是 `outputs/blade-recon-mappo-two-camera-20261004-a02/input/` 的 T1Down、NacelleT1 两路各 601 张 960×540 RGB PNG、对应 1202 张 RGB 派生二值掩码、601 时刻动态标定。几何复用该实验 `reconstruction/recon.json` 的全部 601 个 13 维保存状态。源文件 5 项和 RGB/掩码 2404 项已在生成结束后重新核对 SHA-256，全部与历史保存记录一致。

`input/input_bundle.json` 是显式 `blade-surface-input.v1` 合同。相机顺序固定为 T1Down、NacelleT1，每个时刻均声明原 PNG、掩码、K 和 MODEL→CV 外参。历史标定字段名 `T_cv_from_world` 实际指向随 YawRoot 移动的模型局部坐标，这一语义来自原 `calibration.json` 和 `REPORT.md`；适配只改为 `T_cv_from_model`，1202 组数值原样保留，没有乘入另一个变换、静态化或正交修补。外参旋转正交性最大元素误差 1.7311388822438545×10⁻⁷，满足现有严格加载器的 10⁻⁶ 容差。

`recon.json` 保留原状态、标准差、观测截面、时刻、模型变换和求解诊断。原 `cameras` 哈希与掩码统计移至 `source_camera_diagnostics`，`cameras` 使用对应动态标定的规范字段。完整 `input_access_log` 绑定当前派生清单以及原始 RGB/掩码哈希，使纹理器的既有相机和输入哈希校验正常执行；没有删除或放松校验。

原图通过硬链接以原文件名放入独立派生输入目录，仅供读取，没有写入图像。适配脚本拒绝已有不同字节的准备文件。原来源记录在 [source_integrity.json](source_integrity.json)，输出资产清单在 [asset_manifest.json](asset_manifest.json)。

## 模型与时间

原求解模型历史外层路径已经不存在，仓库 `blade_recon/model.py` 的 SHA-256 与原求解清单严格相同：`9253fdfb7bb1e76790129511a24b98311c08c7c14a2a54b0c012d260842b888c`。Blender vendor 模型具有同一哈希。纹理器 v0.2 模型是 `427e6073d21b8c68f8ceaea44f7177ce3a761bbe5faa3fa9c65ad4643b74967c`，其可选固定健康设计没有被声明。

三个模型在全部 601 个保存状态上，顶点和轴线都逐元素 bit-identical，最大差 0；截面半径、翼型、弦长、扭角、三角面顺序、float32 逐面角 UV 也 bit-identical。详见 [model_compatibility.json](model_compatibility.json)。模型仍为 40 截面×32 周向点，半径 1.5–63 m。

第 k 个保存样本为 `frame=k`、`t=k/10`、`sim_t=117+k/10`、`blender_frame=1+6k`，k=0…600。首尾仿真时刻 117–177 s，相对 0–60 s；源 MAPPO 40 Hz、图像与几何 10 Hz、显示时间轴 60 Hz 分开记录。包身份仍绑定 capture 中的 MAPPO manifest SHA-256 `d3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde`。

## 纹理结果与实际质量

使用冻结的 v0.2 纹理器，`--atlas-dr 0.05 --atlas-cols 384 --fusion best`，无第二遍配准、无曝光假设、未填充掩码或未知表面。每片叶片产生一张固定 1230×384 的 8-bit RGBA 灰度图集；动态的是 601 时刻保存几何，图集不会每帧重新求解。首遍生成耗时 101 s；带诊断的重复采样耗时 126 s。三张 RGBA 和 `texture_data.npz` 在两遍间字节相同。

601 时刻输入全部匹配与验证，572 帧尝试外观采样，534 帧产生有效贡献；共 711 个相机×叶片视角，其中 T1Down=178、NacelleT1=533，B1/B2/B3=238/236/237。未贡献的时刻没有伪造纹理观测。

| 叶片 | 非零 alpha 纹素 / 总纹素 | 全图集纹素占比 | 归一化灰度范围 | 灰度中位数 |
| --- | ---: | ---: | ---: | ---: |
| B1 | 182713 / 472320 | 38.6842% | 55–255 | 150 |
| B2 | 183421 / 472320 | 38.8341% | 54–255 | 150 |
| B3 | 182589 / 472320 | 38.6579% | 52–255 | 151 |

覆盖率分母是 1230×384 全部展向×闭合周向纹素，未按物理表面积加权，不能与吸力面覆盖率混用。从未采样的纹素保持 alpha=0，没有补成虚构细节。`texture/texture.json` 另外保存逐 5 m 的吸力面覆盖与分辨率口径。

已查看实际 B3 图集与 NacelleT1 原 RGB。原图是 Workbench 材质色渲染，纹理器转换灰度并归一化明暗；图集可见展向条带、拼接接缝以及叶尖 collage 式错贴/拖影。灰度亮暗块不代表真实裂纹、修补或现场反射率。模型与视角不足可能造成外观位置偏差，当前外观准确性也未验收。

默认观察点建议 B2 半径 47.225 m、tau=0.8216145833、范围 2 m；其 19×19 图集局部 alpha=100%，灰度标准差约 14.964。资产 manifest 保存三个稳定中段点和三个叶尖拼接点，全部是已支持图集的观看位置，没有缺陷识别含义。

## 警告与验证

既有 `sample_view` 在筛选前对非有限 UV 做 round→int，会产生 RuntimeWarning，日志原样保留。仪器诊断 751 次调用中累计 36,563,535 个非有限投影纹素出现次数，具有正融合权重者为 **0**；这是各视角的累计投影次数，不是独立的物理表面点数量。返回灰度、权重和最终 `val/raw/wmax` 均有限；未知区域的分辨率数组仍允许 NaN，不能将其解释成有效分辨率。

实际前端 `ReconSequence` 和 `TexturePackage` 已读入新资源，核对所有 601 状态/观测截面/时刻和 601 组动态双相机元数据，以及 9 项资源哈希和大小。负向检查拒绝相机名称变化、外参矩阵变化、RGB 哈希变化、时间戳变化。数据验证见 [data_validation.json](data_validation.json)，采样明细见 [sampling_diagnostics.json](sampling_diagnostics.json)，负向验证见 [negative_validation.json](negative_validation.json)。这些属于宿主机与数据证明，不代替实际 Blender 窗口证明。

复现命令（从实际 Git 根目录运行）：

```sh
blade_recon/.venv-trial/bin/python scripts/reconstruction/prepare_mappo_same_source_texture.py
```

该命令准备独立派生输入、冻结纹理源码、检查模型数值等价，调用同一纹理算法并记录不改变采样返回值的诊断，最后形成 `blender_frontend/wfrl_blender/assets/blade_recon_mappo_tex/`。原始首遍日志位于上一级 `data-generation.log`，本次诊断重复日志为 `data-diagnostics.log`。

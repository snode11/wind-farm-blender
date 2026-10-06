# 叶片表面三维重建：网页版 GPT 本地交接

公开研究交接更新时间：2026-10-07（Asia/Shanghai）。当前入口为 `research/blade-surface/2026-10-07/`。本文整理文字与证据索引，没有重跑实验或修改算法；选取的 71 个研究源码/报告原字节文件位于 `sources/<原项目相对路径>`，来源与 SHA 见同目录 `snapshot_manifest.json`。

## 1. 目标与已确认边界

老板希望重建不仅使用叶片轮廓，还考虑表面特征，并在 Blender 中展示二维图像对应的近似三维叶片。当前重点包括可见补漆外观、表面材料点与整体几何的关系，以及后续裂纹、凹坑等缺陷和健康监测。

已有 `blade_recon` 与 Blender 接入，后来也实际加入表面输入、固定材料绑定、共享 q 的窗口求解和 RGB 外观。下一步应复用已完成模块，从已有实验暴露的问题继续；不能把已有算法当作从零开发任务。

“一张图片”是目标描述。严格单张输入、是否允许已知模板与标定、是否允许多图或视频，尚需明确。要区分以下输出：

| 输出 | 本项目中的含义 | 尚不能据此推出 |
| --- | --- | --- |
| 近似三维解释 | 在声明的模板/尺度/相机先验下估计姿态和整体形变 | 隐藏面与真实深度已经测量 |
| 可见外观恢复 | 将原 RGB 样本绑定在估计表面的固定材料坐标 | 表面定位与几何已达到计量精度 |
| 缺陷检测与定位 | 识别图像中疑似异常并关联到材料位置 | 深度、内部损伤或物理原因已确定 |
| 局部几何恢复 | 估计开口、凹坑等局部形状参数 | 当前 RGB、光照和视角足够辨识这些参数 |
| 健康监测/工程计量 | 定义独立基线、身份、重复性、物理量与验收 | 跟踪或显示通过即可诊断健康 |

## 2. 项目 GitHub、公开研究快照与产品源码

项目唯一 GitHub 地址：[snode11/wind-farm-blender](https://github.com/snode11/wind-farm-blender)。网页 GPT 现在可以直接读取 [本次研究材料入口](https://github.com/snode11/wind-farm-blender/tree/main/research/blade-surface/2026-10-07)，其中包含交接说明、带原路径/行号的源码文本、选取的 71 个原字节源码/报告、ZIP 与清单。

本次把材料放在研究目录，**没有把本地研究模块合并到产品目录**。根 `blade_recon/`、`wfrl/nrel_reconstruction/` 仍未同步，产品 main manifest 保持 `0.3.13`；安装 ZIP、公开产品、研究快照、本地磁盘源码与实验冻结实现需要分别判断。公开可读取研究材料不意味着新算法已成为可安装产品，也不代表工程或健康监测验收。

### 2026-10-07 推送前 GitHub 基准

同目录 [github_alignment.json](github_alignment.json) 是本次发布准备之前的认证只读核对记录，不能把其中的旧 main SHA 或“研究目录缺失”当作发布后的 main 状态。

| 项目 | 推送前基准 |
| --- | --- |
| 仓库 | public，默认分支 `main` |
| main SHA | `1caafb9edb7593a96813b8edfddf5d70d040717f` |
| main 产品 manifest | `0.3.13`，本次研究资料发布不改变该字段 |
| 递归 tree | `truncated=false` |
| 根 `blade_recon/`、`wfrl/nrel_reconstruction/` | 基准缺失；本次不合并产品路径，选定模块可在研究 `sources/` 阅读 |
| 研究交接 | 基准中缺失；本次新增 `research/blade-surface/2026-10-07/` |
| 文档迁移 | 基准 docs 为旧平铺；本次研究快照含选定新路径的副本，没有将全部产品文档迁移到 main |
| `blade-surface-experiments-20261006` | 推送前正确项目仓库 tag/release 查询均 HTTP 404；本次提交研究目录，不创建该 Release 或编造历史恢复附件 |

### 2026-10-05 历史核对身份

以下是最初建包时的历史证据，不是本轮重新核验的当前本地 HEAD 或最新 Release：

- 当时公开 main 是 `1caafb9e…`，提交时间为 2026-10-02 19:32:32（Asia/Shanghai）。
- 当时本地 HEAD 为 `e504ed66548c4b774664233341b70c87512561ef`，分支 `wind-farm-rl-all-work`，磁盘含未提交工作；HEAD 不代表全部磁盘源码。
- 当时本地 manifest 为 `0.3.16`，公开 main manifest 为 `0.3.13`。工作区字段不证明日常安装已更新。
- 当时 GitHub Latest Release 元数据为 `v0.3.16`，2026-10-05 19:50:09（Asia/Shanghai），附件 `wfrl_blender-0.3.16.zip`，95,604,329 bytes。
- 历史 `docs/blender/releases/0.3.16发布核对.md` 记录该次只发布 ZIP，未推送新版源码。该记录的原字节副本见 [研究 sources](sources/docs/blender/releases/0.3.16发布核对.md)；没有下载远端安装附件核对载荷。

本次研究快照的补齐范围：

| 原项目材料/模块 | 研究快照可读范围 | 产品路径状态 |
| --- | --- | --- |
| `blade_recon/` legacy 与表面六模块 | 选定核心源码在 `sources/blade_recon/` 及 `02`，非完整运行环境 | 根目录不合并 |
| Blender Blade Recon 与表面 viewer | 选定关键入口在 `sources/blender_frontend/` 及 `02` | 未作为安装版更新 |
| `repair_marks.py` 与缺陷编辑器 | 选定说明/参考源码；不能据此推定全部编辑器文件都在快照 | 产品目录不据研究副本更新 |
| 两相机 a02 与后三轮表面实验 | 精选报告/协议/判定在 `sources/outputs/` | 研究资料已可从当前 GitHub 阅读，原图/大数组未包含 |
| 整理后的 docs | `sources/docs/` 中选定文件保留原项目相对路径 | 没有同步全部 docs 迁移 |

下面的原路径说明模块在原项目中的身份；在 GitHub 阅读时请到 `sources/<原路径>` 或 `02` 中查找。缺少的依赖、真实输入、权重、环境和场景需要另行提供，不能把这个目录当作完整可运行工程。

## 3. 当前实现：legacy 与表面扩展共同存在

### 保留的 legacy 基线

`blade_recon/model.py` 的 13 维状态表达方位角、三片变桨/挥舞/摆振等整体量；由公开 NREL 弦长、扭角与近似翼型构造固定拓扑健康模板。`recon.py` 的旧逐帧 `Obs(mask)` 与轮廓、时序、物理先验求解仍保留。旧算法不使用 RGB 内部纹理目标，也没有裂缝/凹坑局部形状参数。

旧两相机 a02 实验使用 601 时刻、117–177 s、10 Hz、960×540，RGB 主要用来分割。其 `NOT_ACCEPTED` 与参考叶尖差异 RMSE 3.4914 m 保留历史含义：参考点/模型约定不同，不能称同一物理表面的测量精度。绿色轮廓截面标记不能证明整圈表面已观察。旧 wrapper 中算法位于 Git 根外的绝对路径已失效，当前算法位于根内 `blade_recon/`。

### 已实施的六个表面模块

模块职责依据选定源码 header 与 10 月 5 日正式报告；实现见 `02_本地关键源码.md`、`sources/` 和 ZIP 中的原字节源文件。

| 文件 | 已有职责 | 限制 |
| --- | --- | --- |
| `blade_recon/input_bundle.py` | 严格读取显式 manifest 中 RGB、mask、逐帧标定和 2D 观测，记录 SHA；保留原图像素中心坐标 | 不自动搜索相邻真值 |
| `blade_recon/surface_geometry.py` | 模板材料坐标 `q=(物理归一化展向, 周向圈数)`、三角 ID/重心/UV 派生缓存和投影/可见性 | 当前健康模板绑定，不是局部缺陷模型 |
| `blade_recon/window_surface_fit.py` | 同一实现配对 A1 无点与 A3 有点，跨帧共享 q，联合整体状态窗口优化 | legacy `Fitter` 保留；收益仍需独立评价 |
| `blade_recon/surface_texture.py` | 从原 RGB 采样 atlas，固定材料 q 随保存状态变形，保留未知/冲突/样本来源 | 不是完整表面观测，也不自动分离光照与材质 |
| `blade_recon/surface_io.py` | versioned、校验后的外观 sidecar，保留 recon v1 字节，限定资产路径和哈希 | 不是现场几何或健康证书 |
| `blender_frontend/wfrl_blender/blade_recon_surface.py` | 独立离线 viewer、packed atlas、网格变形与固定 UV、保存重开 | 不代表日用入口直接兼容；最新展示需显式 unknown adapter |

10 月 6 日 exec-a01 的 TRF 初始半径修复和 CoTracker/局部对照驱动属于该独立 run 的冻结实现，不能写成上述共享产品模块或 SciPy 安装已被更新。

已经建立外观链路、固定材料绑定与公平窗口求解，**尚未建立受控的有意义几何收益**。仅仅存在模块、代价下降、像素重投影变好或软件 PASS，均不能替代独立几何门槛。

当前不是任意照片到任意叶片的完整接口。S1 只是首张 RGB 在已保存几何上的外观闭环；若目标严格单图或自由单叶片，还需要论证相机/尺度/姿态、目标模式、活动参数与隐藏面条件。

## 4. 补漆与缺陷编辑器的身份

`repair_marks.py` 在源 `WFRL.Turbine.T1.Blade1` 上建立合成补漆，约 1.4×0.7 m、距根约 34 m，主要体现颜色/粗糙度/shader bump，1 mm 展示偏移不表示真实修补厚度。`physics_coupled=False`，不改写刚度、气动或保存的物理运动。三角 ID 与重心绑定可作动态材料位置的实现参考，但**源补漆绑定不能直接作为重建答案**。

`blender_frontend/wfrl_blender/nrel_defects/` 与 `docs/blender/NREL缺陷编辑器.md` 支持制作细裂纹、开口裂缝、凹坑、侵蚀、涂层剥落、雷击等合成对象。这是实验场景/显示工具；能创建缺陷不等于能从图像检测、恢复或诊断缺陷。

当前局部 RGB 实验已经验证一个具体停止原因：Workbench FLAT MATERIAL 的固定相机条件下，健康与 50 mm 凹坑的 decoded RGB 完全相同。此条件下局部恢复未运行，depth=null；不能推广成所有视角/照明下均不可恢复，也不能将未运行写成恢复深度 0。

## 5. 实验阅读入口与当前状态

三轮正式进展、数值/定位/自动身份/长窗/局部/显示边界见 `03_实验进展与下一轮讨论依据.md`。该文依次索引：

1. `outputs/blade-surface-20261005-manual-a01/`：正式 attempt02。
2. `outputs/blade-surface-20261006-p2b-a01/`：P2b-0 与数值门槛停止。
3. `outputs/blade-surface-20261006-exec-a01/`：最新实际执行，最终 `COMPLETED_WITH_NEGATIVE_OR_GATED_RESEARCH_RESULTS / REVIEW_ONLY`。

原项目 `docs/proposal/叶片表面重建_2026-10-06/README.md` 为方案/后续执行入口，更早诊断方案位于 `docs/proposal/reconstruction/`；研究快照只含清单列出的选定材料，未收录文件需另行索取。旧方案的 `DOCUMENT_UPDATE_ONLY`、CoTracker `NOT_RUN`、新 RGB=0 与“未生成比较”均只属于各自阶段，不代表最新 exec-a01。

实际 RGB/mask/calibration/轨迹/recon/eval、最终报告/场景和失败 JSON/log 仍在本地。原 REPORT/REPRODUCE 的“全部保留”与环境命令需结合已收录的 `LOCAL_STORAGE_STATUS_20261006.md` 阅读：临时环境、缓存、部分逐帧衍生图、草稿、打包副本和大 patch 后来已清理。清理记录、历史上传回执与修改前备份不在此公开快照，其原项目路径仅供需要时索取；本交接不把历史文件清单视为当前全部存在，也不编造恢复附件。

## 6. 附件与阅读顺序

1. `00_发给网页版GPT的提问.md`：可直接粘贴的任务。
2. 本文：目标、实现、项目 GitHub/本地差异。
3. `03_实验进展与下一轮讨论依据.md`：三轮矩阵和最新门槛。
4. `02_本地关键源码.md`：带原路径/行号的核心源码，按需查实现。
5. `本地源码参考包.zip`：精选源文件、三轮报告/协议/判定与存储状态；是讨论快照，不是完整可运行工程或安装插件。
6. `snapshot_manifest.json`、`packaging_check.json`、`github_alignment.json`：快照来源、附件字节检查与推送前 GitHub 基准。

**真实补漆/标记/局部 RGB 已经生成，不是尚待采集。** 最新 exec-a01 共 197 张实际实验 capture RGB；本小包不包含全部 4K 原图、视频、完整逐帧数组或 `.blend`。需要判断具体图像信号时，应按 run/相机/时间索取已保留的 RGB 与配对标定，不能默认附件包含未提供的图像证据。

网页 GPT 可以读取当前 GitHub 研究目录，也可使用 MD 和 ZIP 附件。`sources/` 的正式报告保留原文，其中一些历史链接指向未收录的图像、场景或其它附件；这些链接不保证在当前公开快照中可打开。完整复现仍需核对保留输入、冻结环境、资源与读权限，不能仅凭这份快照宣称可直接复现。

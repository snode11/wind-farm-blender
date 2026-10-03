# NREL Single-Blade Video Reconstruction · 2026-10-03

本发布归档了已跑通的 NREL 5MW 三摄视频 → 单叶片模型 → 独立评分研究链路。**正式结果保留第二轮 v2；P4/P5 候选未采用，完整几何验收仍待建立。**

- [GitHub Release](https://github.com/snode11/wind-farm-blender/releases/tag/nrel-video-single-blade-20261003)
- 分支：[`experiments/nrel-video-single-blade`](https://github.com/snode11/wind-farm-blender/tree/experiments/nrel-video-single-blade)
- 固定标签：`nrel-video-single-blade-20261003`
- [实验总入口](../../../outputs/nrel-video-single-blade/README.md) · [实现说明](../../../wfrl/nrel_reconstruction/README.md)

这是已有研究结果的归档发布。当前工作属于研究路线 B 的单叶片几何重建验证与改进，状态为 `PIPELINE_COMPLETE_PARTIAL_GEOMETRY`；根部精度尚未解决，背面、厚度和扭转仍依赖先验。完整表面恢复、工程用途精度与覆盖验收尚未建立，C 纹理与缺陷地图、D 局部损伤计量及现场验证未完成。

## 直接审阅

| 材料 | 入口 |
| --- | --- |
| 正式 v2 模型 | [T1_B1.ply](../../../outputs/nrel-video-single-blade/stages/02-second-run/reconstruction/T1_B1.ply) |
| 自包含模型预览 | [model_preview.html](../../../outputs/nrel-video-single-blade/stages/02-second-run/reconstruction/model_preview.html)，下载后用本地浏览器打开 |
| 正式结果与失败区域 | [第二轮报告](../../../outputs/nrel-video-single-blade/stages/02-second-run/README.md) |
| 最后一轮及收尾结论 | [P5 报告](../../../outputs/nrel-video-single-blade/stages/06-p5-observation/README.md) |
| 三路输入视频 | [C1](../../../outputs/nrel-video-single-blade/stages/01-first-run/formal/algorithm-input/C1.mp4)、[C2](../../../outputs/nrel-video-single-blade/stages/01-first-run/formal/algorithm-input/C2.mp4)、[C3](../../../outputs/nrel-video-single-blade/stages/01-first-run/formal/algorithm-input/C3.mp4) |
| 正式独立评分 | [scores.json](../../../outputs/nrel-video-single-blade/stages/02-second-run/evaluation-only/reconstruction/scores.json) |

44/45 帧在先前阶段已被查看，报告中的时序检查属于非盲验证。图像吻合、闭合网格、测试通过和归档完整性不能代替三维几何验收；历史报告按各自原判据阅读。

## Git 内容与完整附件

Git 分支包含实验源码、测试、依赖配置、方案、各阶段报告、冻结配置、评分数据、正式与候选模型、关键图表和历史源码快照。保留在报告中的原始链接和记录没有因发布重新计算。

为控制 Git 仓库体积，下列材料从 Git 中省略，保存在完整 Release 附件：

| 省略范围（相对于 `outputs/nrel-video-single-blade/`） | 内容 |
| --- | --- |
| `stages/01-first-run/internal-capture/`、`internal-capture-formal/` | 生成端主采集 PNG 与内部记录 |
| `stages/01-first-run/rgb-scout/`、`rgb-scout-next-b1/` | 预采集素材 |
| `stages/01-first-run/trial/encoding-adapter/`、`formal/encoding-adapter/` | 编码适配记录及指向原 PNG 的相对符号链接 |
| `review/` 中五份历史审阅 ZIP | 初始审阅、diagnostics、P3、P4、P5 审阅包 |

主机缓存也不进入 Git。精确省略路径与原因见 [git-exclusions.json](git-exclusions.json)。因此仅克隆 Git 时，历史报告中指向这些材料的链接需要在恢复完整附件后查看。

Release 提供以下附件：

- `nrel-video-single-blade-20261003-full.tar.gz`：完整实验归档，保留 `outputs/nrel-video-single-blade/` 路径。
- `nrel-video-single-blade-20261003-manifest.json`：归档文件与符号链接清单及签名。
- `nrel-video-single-blade-20261003-validation.json`：归档核验记录。
- `SHA256SUMS`：上述三个附件的 SHA-256 校验值。

完整归档为 **1,267,871,054 bytes（约 1.18 GiB）**。发布前逐一核验了 **1,483 个文件和 6 个相对符号链接**，未发现漏项或重复，源文件签名前后一致；这是归档核验结果。

## 获取并恢复完整材料

从实际 Git 仓库根工作：

```bash
git clone --branch experiments/nrel-video-single-blade https://github.com/snode11/wind-farm-blender.git
cd wind-farm-blender
git checkout nrel-video-single-blade-20261003
```

从 Release 下载上述四个附件，放到该仓库根目录，再执行：

```bash
shasum -a 256 -c SHA256SUMS
tar -xzf nrel-video-single-blade-20261003-full.tar.gz
```

校验应在解压前完成。归档会原样恢复 `outputs/nrel-video-single-blade/` 下已有的同内容报告及全部实验素材；若你已修改这些文件，先保留个人修改，再解压。归档中的相对符号链接指向同一归档内的采集目录。

GitHub 自动生成的 Source code ZIP 和历史审阅 ZIP 不能充当实际 Git 仓库根：路径解析器会寻找 `.git` 与 `wfrl/`，复核命令应在克隆的仓库中运行。

## 依赖与安全的复核命令

使用 Python 3.11+，安装研究依赖与 pytest：

```bash
python -m pip install -e '.[nrel-reconstruction]' pytest
ffmpeg -version
ffprobe -version
```

FFmpeg/FFprobe 需单独安装并放入 `PATH`。下面的检查读取冻结输入，所有新检查结果写入新的临时目录；不会自动重新采集、拟合或重跑全部实验。

```bash
CHECK_DIR=$(mktemp -d)
python scripts/nrel_single_blade.py inspect \
  --input outputs/nrel-video-single-blade/stages/01-first-run/formal/algorithm-input \
  --output "$CHECK_DIR/inspection" --frames 42 43 44 45

python scripts/nrel_single_blade.py score \
  --reconstruction outputs/nrel-video-single-blade/stages/02-second-run/reconstruction \
  --truth outputs/nrel-video-single-blade/stages/01-first-run/formal/evaluation-only/truth-at-tref/T1_B1_truth.ply \
  --truth-metadata outputs/nrel-video-single-blade/stages/01-first-run/formal/evaluation-only/truth-at-tref/truth_manifest.json \
  --config outputs/nrel-video-single-blade/stages/02-second-run/scoring_config.json \
  --output "$CHECK_DIR/evaluation"

python -m pytest -q tests/test_nrel_*.py
```

模型预览直接打开已保存的 `model_preview.html`；该页面内嵌三份模型与显示代码，无远端脚本。不要在冻结模型目录上调用预览生成入口，因为它会写入 HTML/PNG。

本次发布前在发布工作树运行全部 `tests/test_nrel_*.py`，最终结果为 **209 passed in 4.28 s**，包括新增的 5 项跨 checkout 路径与签名测试；CLI `--help` 成功。该次测试使用 Python 3.11.16、NumPy 1.26.4、SciPy 1.17.1、Torch 2.13.0、Pillow 12.3.0、pytest 9.1.1。历史 P5 报告中的 92 项测试与第二轮报告中的 105 项测试保留原运行记录，不替换为本次数字。当前拟合实现依赖 Unix/macOS 的 `resource` 模块，Windows 拟合未验证。

## 路径、签名与新实验

冻结 JSON、日志和源码快照保留当时的绝对路径和原内容。当前 [artifact_paths.py](../../../wfrl/nrel_reconstruction/artifact_paths.py) 将历史 NREL 输出路径映射到当前阶段目录；`frozen_path` 核对预期 SHA-256，必要时使用可见的迁移原文或历史源码快照。历史绝对源码路径查找仅在发布分支适配，优先使用本 checkout 或其冻结快照，不回读原机器上的日常工作目录；历史 JSON 字节与完整归档保持原样。返回历史快照说明它核验的是当时字节，不能据此声称当前源码与历史运行完全相同。[目录整理记录](../../../outputs/nrel-video-single-blade/review/layout-migration-20261003/README.md)

独立审核了 74 条历史 `implementation_at_launch` 源码签名：71 条在本 checkout 内匹配，0 条回读原机器；其中 P4/P5 的 46 条全部匹配。另有 3 条历史启动签名没有同哈希快照：第二轮 `contour-ablation` 的 `optimize.py`、`boundary_metrics.py`，以及正式 `reconstruction` 的 `boundary_metrics.py`（均属于 `wfrl/nrel_reconstruction/`）。原日常工作区也没有匹配字节；这是保留的历史证据缺口，不能宣称所有历史启动源码均可逐字节恢复。归档内容完整性核验不消除这一缺口。

新运行必须使用新的输出目录，并为当前机器重新建立协议、冻结清单和隔离策略。旧 `isolation.sb` 仅作为历史隔离证据保留：它采用 `allow default` 与原机器绝对目录拒读规则，直接用于新 checkout 不能拦截新位置的真值目录。新拟合必须重建隔离规则并运行实际拒读探针；仅将真值放在不同目录不能替代进程隔离。新 RGB 的阈值、遮挡排除与边界归属需要重新审核，现有观测提取不是通用分割器。

生成端采集还需要兼容的可见 Blender 环境、NREL 前端与原仿真结果。本发布保存既有输入和结果，没有重新采集、渲染、编码视频或重跑物理仿真。需要开展新实验时，按 [实现说明](../../../wfrl/nrel_reconstruction/README.md)另建运行，不覆盖本发布的正式 v2、历史基线或 P4/P5 反例证据。

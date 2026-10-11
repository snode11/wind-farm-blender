# NREL 动态叶片研究：合并交付

2026-10-04：原来的五个运行目录已合并为唯一主目录 **`20261004-integrated`**，原目录已移除。输入诊断、估计、评分、查看器和验证证据现在按功能集中存放；A01–A05 只作为历史轮次标识保留。

**打开研究查看器.command（本地路径：`outputs/nrel-video-dynamic/runs/20261004-integrated/打开研究查看器.command`）** 是当前启动入口，使用 A05 冻结查看器、A03 冻结研究包和三路原始视频。查看数值与精度依据可读 [结果报告](reports/a03.md)，查看播放验证可读 [查看器报告](reports/a05.md)。

当前交付为 **`REVIEW_ONLY` 条件研究原型**。软件播放同步检查已通过；动态计量门仍为 **`DY1 NOT PASSED`**，工程用途为 `PENDING_USE_CASE_TARGETS`，接受的测量样本仍为 0。

## 文件在哪里

| 目录 | 内容与常用入口 |
| --- | --- |
| `inputs/` | [视频审查](inputs/video-audit/summary.json)、[理想刚性平面](inputs/baseline/plane/rigid_plane.json)、[静态映射审计](inputs/static-mapping/static-mapping/mapping_audit.json)、[结构点先验](inputs/structural-prior/static_prior.json) |
| `diagnostics/` | 粗、细网格图像约束诊断；[细网格反例](diagnostics/refined/sensitivity/counterexamples.md) |
| `estimation/` | [估计协议](estimation/protocol.json)、[冻结记录](estimation/attempt01/result/estimator_freeze.json)、[研究包清单](estimation/attempt01/result/review-package/manifest.json)、[四时刻序列](estimation/attempt01/result/review-package/sequence.json) |
| `scoring/` | 冻结估计之后的独立评分；[评分结果](scoring/result/score.json) |
| `viewer/` | 当前冻结源码清单（本地路径：`outputs/nrel-video-dynamic/runs/20261004-integrated/viewer/frozen-source/manifest.json`）、历史场景与启动器、各次独立配置 |
| `validation/` | 单元测试、后台检查、原生窗口、回放及[本次合并检查](validation/merge/checks.json) |
| `sources/` | 输入诊断和估计阶段的源码快照 |
| `reports/` | 五轮报告的可读版本、[播放检查协议](reports/protocol-playback.md) |
| `provenance/` | [旧路径到新路径及哈希映射](provenance/migration.json)、历史状态与清单、原始文档 |

研究包保留固定拓扑、G1/G2/G3 候选点、平面、垂足和有符号距离。三路视频各 100 帧、20 fps，场景帧 1–100 对应原视频索引 0–99。保存估计仅覆盖原索引 **42–45（场景帧 43–46）**，其余 96 帧显示 `UNAVAILABLE; no interpolation` 并隐藏估计几何。

## 使用查看器

双击根目录的 `.command`，然后用 Space 播放／暂停、左右方向键逐帧，或在研究面板切换 G1/G2/G3 及保存时刻。到末帧停止，再次播放回到起点。历史 `.blend` 保存的是当时的场景状态，当前交互查看请使用统一启动入口。

界面的 `d` 是候选结构点到同一时刻理想辅助平面的有符号距离。点身份为 `PRIOR_DEFINED_STRUCTURAL_POINT`，材料对应尚未验证，因此读数不能直接作为已验收的物理净空。

## 已有结果与验证

独立评分只覆盖四个已查看的历史开发时刻。G1 固定 q=0；G2 使用运动学先验并关闭当前序列图像成本；G3 开启同一条件下的图像成本。三组共用的历史 v2 参考已来自 RGB。

| 组 | 结构点位置误差 RMSE | 有符号距离误差 RMSE |
| --- | ---: | ---: |
| G1 | 128.539 mm | 80.215 mm |
| G2 | 128.539 mm | 80.215 mm |
| G3 | 211.988 mm | 119.021 mm |

该轮图像项没有改善结构精度，误差包含尚未验证的结构点映射偏差。有限网格候选范围不是置信区间；四时刻结果不能推广为未见时刻或现场精度。定义、隔离过程和逐帧数值见 [估计与独立评分报告](reports/a03.md)。

以下是合并前 A05 已完成的检查，作为历史验证证据保留：

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 读取器与播放证据合同 | 33 项测试通过 | [单元检查](validation/playback/unit_tests.json) |
| 后台与原生结构操作 | 各 27 次分阶段操作通过 | [后台](validation/playback/background/attempt02/window_check.json)、[原生](validation/playback-native/attempt02/window_check.json) |
| 原生播放 | 30 阶段、219 次提交、2237 次 POST_PIXEL，0 失败；两次完整 1→100 回放 | [播放检查](validation/playback-native/attempt02/playback_check.json) |
| 实际窗口 | Space 播放／暂停；46→47→46 的读数、隐藏和恢复通过 | [窗口观察](validation/playback-native/attempt02/visible_observation.json) |

两轮各推进 4.95 s 的源时间，实际耗时 5.790913／5.868868 s，约为原速的 **0.85 倍**。这是含观察器和绘制等待的有限测量；POST_PIXEL 回调不等于屏幕 FPS，所选窗口观察也不覆盖每帧像素。

## 历史报告与迁移说明

| 报告 | 内容 |
| --- | --- |
| [A01](reports/a01.md) | 三路输入、时间映射、理想平面与粗网格诊断 |
| [A02](reports/a02.md) | 细网格、相机子集与静态结构映射；[历史复现说明](reports/a02-reproduce.md) |
| [A03](reports/a03.md) | 条件候选序列、冻结研究包、独立评分和初版查看器 |
| [A04](reports/a04.md) | 稳定帧显示、布局、读数及实际窗口检查 |
| [A05](reports/a05.md) | 播放同步、重载保护和有限性能检查 |

历史报告的轮次结论按当时状态阅读。A03 后续开展了保留歧义的条件研究原型，A04/A05 完成显示和播放验证，均未将 DY1 改判为通过。

本次物理合并迁移了 713 个文件，迁移时逐文件 SHA256 核对通过，映射保存在 [migration.json](provenance/migration.json)。历史 JSON、签名研究包和源码快照中的旧路径继续记录原运行位置；使用迁移表可查到当前位置。报告可读版本修复导航，原始文档另存于 `provenance/original-documents/`。

本次核验针对合并后的路径、入口与文件完整性，没有重新运行估计或独立评分。具体检查状态以 [合并检查记录](validation/merge/checks.json) 为准；历史启动器及沙箱策略用于追溯原运行，重新开展实验时应建立新的路径绑定与输出目录。

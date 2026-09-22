# evidence 目录盘点与保留建议

盘点日期：2026-09-23。仅检查本地文件、相关源码引用和已有报告；未运行回归，未删除、移动或修改 evidence 内容。

目录：[evidence](/Users/eason/Desktop/wfcrl/wind farm RL/evidence)。现存 34 个一级子目录、1,903 个文件（含根 README 和 Finder 元数据），逻辑大小 803.16 MiB，约 0.84 GB。文件内容修改时间主要覆盖 2026-09-08 至 2026-09-22；目录自身 9 月 20 日的修改时间受到清理操作影响，不能用于推断记录创建日期。下表以现存文件 mtime 范围为准，结合目录名、报告内日期辨认；复制的旧快照可能早于该任务。

## 全部目录与时间

| 日期（2026 年） | 目录 | 文件数 | MiB | 记录内容 | 建议 |
|---|---|---:|---:|---|---|
| 09-08 | [blade_revision](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/blade_revision) | 3 | 0.01 | 叶片连接、运动及圆滑叶尖审查 | 保留小型审查记录 |
| 09-11 | [cinematic-fix](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/cinematic-fix) | 5 | 35.59 | 电影视角、流线与播放时钟修复 | 保留；场景有依赖 |
| 09-11 | [landscape-flow](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/landscape-flow) | 15 | 0.02 | 地形与来流驱动流线改进 | 归档保留 |
| 09-13～09-15 | [gimbal-inner-20260915](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/gimbal-inner-20260915) | 23 | 0.12 | 叶片内侧云台、叶尖红条及修复前快照 | 归档保留 |
| 09-15 | [blade-flex-20260915](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/blade-flex-20260915) | 5 | 0.04 | 叶片柔性开发前的源码与文档基线 | 低优先级归档 |
| 09-15 | [tip-code-fix-20260915](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/tip-code-fix-20260915) | 7 | 0.06 | 叶尖实现修复及原始代码快照 | 低优先级归档 |
| 09-15 | [blade-flex-short](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/blade-flex-short) | 16 | 3.49 | 单机 18 秒柔性回放与验收 | 保留数据和验收 |
| 09-15～09-16 | [down-gust-preview](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/down-gust-preview) | 9 | 4.68 | 单机随机湍流、阵风短片及三机续接 | 保留数据和说明 |
| 09-16 | [flex-mappo](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/flex-mappo) | 89 | 25.69 | 三机 MAPPO 柔性叶片、控制器迭代与首版结果包 | 保留科学来源；可整理探针 |
| 09-16 | [storage-cleanup-20260916](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/storage-cleanup-20260916) | 1 | 0.00 | 历史存储清理清单 | 保留 |
| 09-16 | [deflection-t1-20260916](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/deflection-t1-20260916) | 17 | 0.65 | T1 叶尖挠度与独立刚性参考对照 | 保留 |
| 09-16 | [panel-layout-20260916](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/panel-layout-20260916) | 10 | 0.01 | 三页布局与共享播放控制整理 | 低优先级归档 |
| 09-16 | [remove-dual-20260916](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/remove-dual-20260916) | 5 | 0.00 | 旧双视图移除检查 | 低优先级归档 |
| 09-16 | [blade-labels-20260916](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/blade-labels-20260916) | 5 | 0.00 | 叶片 1/2/3 标签与安装核验 | 低优先级归档 |
| 09-16 | [tower-flex-20260916](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/tower-flex-20260916) | 136 | 39.92 | 塔架柔性重算、v2 回放包及数值敏感性自检 | 重点保留 |
| 09-17 | [radar-feedback-20260917](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/radar-feedback-20260917) | 79 | 38.27 | 雷达状态灯与光束反馈、安装回归 | 保留报告；精简运行时副本 |
| 09-17 | [tower-fittings-20260917](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/tower-fittings-20260917) | 10 | 0.01 | 塔顶密封圈及焊缝柔性随动修复 | 保留 |
| 09-17 | [camera-switch-20260917](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/camera-switch-20260917) | 10 | 0.01 | 侧前方切回 Down 的状态遗留缺陷 | 保留 |
| 09-18 | [prebend-20260918](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/prebend-20260918) | 104 | 4.50 | 预弯物理输入、三机求解、雷达覆盖率失败及扫描候选 | 重点保留 |
| 09-18 | [release-0.3.4](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/release-0.3.4) | 6 | 0.00 | 0.3.4 发布说明与验证 | 保留 |
| 09-19 | [nacelle-camera-20260919](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/nacelle-camera-20260919) | 3 | 0.01 | 新增机舱相机方案与交付 | 归档保留 |
| 09-19 | [camera-video-20260919](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/camera-video-20260919) | 149 | 45.26 | 固定机舱视频渲染、色彩、曝光与推流检查 | 保留最终验证；精简大日志 |
| 09-19～09-20 | [single-camera-20260919](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/single-camera-20260919) | 213 | 229.60 | 单相机渲染性能、循环视频与 RTSP 耐久测试 | 优先精简 |
| 09-20 | [single-camera-review-20260920](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/single-camera-review-20260920) | 7 | 0.01 | 单相机修复后复核 | 保留 |
| 09-20 | [release-0.3.5](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/release-0.3.5) | 9 | 0.02 | 0.3.5 发布与安装检查 | 保留 |
| 09-20 | [windows-delivery-20260920](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/windows-delivery-20260920) | 3 | 0.37 | 独立 Windows 推流检查与清理清单 | 保留清单；其余历史归档 |
| 09-20 | [blender-video-output-20260920](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/blender-video-output-20260920) | 1 | 0.00 | 视频输出集成到 Blender 的检查 | 保留 |
| 09-20 | [custom-cameras-20260920](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/custom-cameras-20260920) | 6 | 0.01 | 自定义相机原生、窗口与安装检查 | 保留 |
| 09-20 | [local-install-0.3.6](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/local-install-0.3.6) | 2 | 0.02 | 首版 0.3.6 本机安装核对 | 归档保留 |
| 09-22 | [four-cameras-20260922](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/four-cameras-20260922) | 155 | 150.73 | 四相机初版、四格布局、GPU 标定与退出修复 | 保留最终结果与标定；图片去重 |
| 09-22 | [camera-v3-20260922](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/camera-v3-20260922) | 637 | 209.31 | v3 事务/历史、预览与交互修复的多轮测试 | 保留最终版本证据；精简迭代图片和轨迹 |
| 09-22 | [camera-v3-final-review-20260922-5fynuhko](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/camera-v3-final-review-20260922-5fynuhko) | 107 | 12.36 | v3 源码最终审查、补测与实际 UI 核查 | 重点保留完整目录 |
| 09-22 | [local-install-0.3.6-v3-attempt-hj_mzvbl](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/local-install-0.3.6-v3-attempt-hj_mzvbl) | 16 | 0.30 | v3 安装失败、RestrictData 注册缺陷与回滚 | 保留 |
| 09-22 | [local-install-0.3.6-v3-20260922-if19yins](/Users/eason/Desktop/wfcrl/wind farm RL/evidence/local-install-0.3.6-v3-20260922-if19yins) | 38 | 2.09 | 注册时机修复、实际安装版回归与哈希核对 | 重点保留完整目录 |

## 逐目录判断依据

- **blade_revision**：说明展示几何的已知限制，只有约 10 KiB。
- **cinematic-fix**：preview.blend 约 35.58 MiB，cinematic_nacelle_regression.py 的说明仍引用它。
- **landscape-flow**：仅剩小型日志、脚本、README，旧图片与场景已清理。
- **gimbal-inner-20260915**：主体在 9 月 15 日，before 快照含 9 月 13–14 日文件。
- **blade-flex-20260915**：仅 5 个小文件；若要删除，先确认对应 Git 提交可还原，不能假定未提交快照都在 Git 中。
- **tip-code-fix-20260915**：小型检查与源码快照，不是主要空间来源。
- **blade-flex-short**：close-flex.npz 仍被 open_blade_flex_preview.py 和 open_blade_flex_down.py 读取。
- **down-gust-preview**：open_down_gust_preview.py 默认读取 v3-random；历史进度不能当作现状。
- **flex-mappo**：package-v1 是旧物理基线，不能仅因有新版本就视为重复；保留 controller-v6、来源、验收及必要版本对比。
- **storage-cleanup-20260916**：记录之前删了什么，便于解释旧文档的缺失链接。
- **deflection-t1-20260916**：参考系、数值定义和误差校验有追溯价值。
- **panel-layout-20260916**：体积很小；若只想简化目录，可归档报告和最终结果。
- **remove-dual-20260916**：少量脚本、JSON 和日志，已属历史界面工作。
- **blade-labels-20260916**：小型验证记录，保留成本极低。
- **tower-flex-20260916**：self-audit、验收、来源、package-v2-r2 值得保留；runtime-smoke 可在留存历史版本后精简。
- **radar-feedback-20260917**：runtime 约 38.26 MiB；它不是当前源码的全量相同副本。其旧物理数据与 tower-flex/package-v2-r2 有逐字节重复，可去重归档。
- **tower-fittings-20260917**：记录真实几何错位和修复误差，且前端 README 仍引用。
- **camera-switch-20260917**：有失败基线、修复后和安装版对照，仅约 10 KiB。
- **prebend-20260918**：reference 与 acceptance-budget.json 仍被脚本读取；覆盖率失败是有效结论，不应删成只剩成功结果。
- **release-0.3.4**：小型发布追溯记录。
- **nacelle-camera-20260919**：说明新机舱相机和旧 Down 的区别，只有几个小文件。
- **camera-video-20260919**：35 个 JSONL 共 42.14 MiB，可先保留统计摘要和代表样本再压缩归档；保留 render-validation.md 和 native-render-regression-production。
- **single-camera-20260919**：13 个 JSONL 共 227.94 MiB，几乎占满目录；保留性能/完整渲染摘要、失败与修复结论，逐帧原始日志宜压缩归档。
- **single-camera-review-20260920**：最终复核结果很小，不能与前面的原始日志一起整目录清理。
- **release-0.3.5**：小型发布追溯记录。
- **windows-delivery-20260920**：evidence/README.md 明确独立 Windows 交付方案已撤销。cleanup.json 记录 1505 个已删文件，不是当前现存文件清单。
- **blender-video-output-20260920**：只有一个小型 JSON。
- **custom-cameras-20260920**：早期功能基线，仅 6 个小型 JSON。
- **local-install-0.3.6**：后续已有 9 月 22 日安装记录，但只有两个小文件，删掉收益极小。
- **four-cameras-20260922**：100 张 PNG 共 150.33 MiB；保留 calibration、verified/result.json、layout-fix-final/result.json、exit-switch 及代表图片，旧轮次可精简。
- **camera-v3-20260922**：532 张 PNG 共 153.16 MiB；v3-window 约 150.79 MiB，含 23.03/15.31/15.31 MiB 的轨迹 JSON。保留各最终结果、来源指纹和必要失败对照。
- **camera-v3-final-review-20260922-5fynuhko**：当日 20:17–20:27；报告、源码指纹、原生/GPU/CUA 证据链完整，约 12.36 MiB。它是该次源码审查结论，不是永久有效的现状认证。
- **local-install-0.3.6-v3-attempt-hj_mzvbl**：当日 21:35–21:38，仅 0.30 MiB；真实安装入口缺陷的失败证据，后续已修复。不要按 attempt 名称直接删除。
- **local-install-0.3.6-v3-20260922-if19yins**：当日 21:42–21:47；报告记录 96 个安装文件与新包一致，是失败安装之后的成功闭环，约 2.09 MiB。

## 清理优先级

1. **先压缩归档旧推流逐帧日志。** single-camera 和 camera-video 两个目录的 JSONL 合计约 270.08 MiB。它们仍是详细测试证据，所以建议先保留摘要、测试配置及失败/成功对应关系，再压缩归档或精简，不直接删除全部 JSON/JSONL。
2. **其次处理重复相机图片。** evidence 内 PNG 按 SHA-256 比对，相同内容多余副本合计约 179.42 MiB；所有大于等于 100,000 字节的文件中，重复内容多余副本约 217.80 MiB。后一个数字包含前一个，不能相加。保留代表图、标定图、最终验收图及路径映射；同图可能记录不同测试执行，不能只按哈希删除后留下失效证据链接。
3. **整理历史运行时副本。** radar-feedback/runtime 和 tower-flex/runtime-smoke 合计约 44.52 MiB。与当前源码相比只有部分字节相同，不能直接宣称两者全是冗余。旧数据与 package-v2-r2 重复部分可归档去重，历史代码快照保留版本或压缩包。
4. **小型报告、源码指纹、发布记录、清理清单和有结论的失败日志继续保留。** 它们占用少，通常比重新追查一次问题更有价值。旧界面报告若显得杂乱，可统一归档，但需要更新文档链接。
5. **保留脚本依赖的数据。** close-flex.npz、down-gust-preview/v3-random、prebend/reference、prebend/acceptance-budget.json 以及被回归说明引用的 cinematic-fix/preview.blend，不能在未调整入口前直接删除。

## 已有清理历史与解释限制

- 2026-09-19 的外部清单 `/Users/eason/Desktop/wfcrl/outputs/evidence-cleanup-20260919.json` 记录删除 448 个文件、319,392,188 字节（约 304.60 MiB），保留过若干关键数据和场景。
- 2026-09-20 的 `evidence/windows-delivery-20260920/cleanup.json` 记录一次更广范围清理：1505 个文件、3,192,821,456 字节（约 2.97 GiB）；范围包含证据媒体、缓存、测试归档和求解器临时文件，不能全部算成 evidence 目录的空间。
- evidence/README.md 已明确旧报告里的截图、录像及运行时副本可能不存在。历史报告中的“已完成”“已安装”只代表当时环境与版本，不等于当前版本重新验收通过。
- 工作区已有大量未提交修改、未跟踪证据和既有删除。本次未更改这些状态；不能假设所有证据删后均可通过 Git 恢复。

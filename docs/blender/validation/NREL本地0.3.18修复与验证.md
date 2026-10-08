# NREL / WFRL 0.3.18 发布前本地修复与验证

验证日期：2026-10-08。**软件修复已验证，叶尖精度仍为 FAILED_GATE。** 本页记录先于发布完成的本地验证，原始 JSON 中的 `LOCAL_ONLY_UNPUBLISHED` 保留其验证时身份。随后 0.3.18 已发布安装 ZIP 与对应源码，见 [0.3.18 发布核对](../releases/0.3.18发布核对.md)及[发布回执](../../../evidence/nrel-local-0.3.18-20261008/publication.json)。本次发布另做隔离源码复建和相关宿主回归，没有重跑 FAST.Farm 或重做全部原生窗口验收。

当前安装使用 [0.3.18 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18) 的 `wfrl_blender-0.3.18.zip`，源码标签指向 `6dfa0eefe410ef3e38d3cbd9ee7d017aafa2fc02`，包含下面的扩展修复及对应构建输入。0.3.17.1 当次 ZIP 单独发布的历史范围见 [原发布核对](../releases/0.3.17.1发布核对.md)。

## 本地软件修复

| 问题 | 本地 0.3.18 行为与证据 |
| --- | --- |
| 场景恢复互相干扰 | 运行实例按场景绑定。FULL_COPY 缺少必要对象时明确未就绪，原场景仍回放；共享网格的 linked scene 拒绝第二个时钟。[后台场景检查](../../../evidence/nrel-local-0.3.18-20261008/scene-lifecycle.json) |
| 单步、复位、停止误影响另一场景 | 通过原生播放 PRE/POST 回调识别 owner scene；针对当前场景的操作不取消另一场景的原生播放，末帧停止也按所属场景处理，冲突明确拒绝。[隔离安装窗口](../../../evidence/nrel-local-0.3.18-20261008/playback-window.json)、[日常安装窗口](../../../evidence/nrel-local-0.3.18-20261008/daily-window.json) |
| C2 缺失导致槽位错位 | C1/C2/C3 保留实际身份；缺失槽位明确报错，不把 C3 当 C2，也不执行不完整三路分析。[槽位检查](../../../evidence/nrel-local-0.3.18-20261008/camera-slots.json) |
| 原生相机与采集记录不一致 | 采集前及采集中核对原生／评估姿态、父级逆矩阵、约束、镜头和裁剪，拒绝非有限值。中断恢复时间、读数、图表、着色与可见性，同时保留用户原生修改。[相机检查](../../../evidence/nrel-local-0.3.18-20261008/camera-guard.json)、[GPU 窗口](../../../evidence/nrel-local-0.3.18-20261008/gpu-window.json) |

Blender 同一进程使用一个全局原生播放器。以上检查证明另一场景的播放器不会被当前场景操作误停，不能表述为两个独立原生 player 同时播放。共享网格也不能由两个场景时钟各自写入。

GPU target 按采集事务及输出尺寸复用。实际 Metal 窗口中，三尺寸、三样本的分配／释放由 **9/9 降至 3/3**，9 张 PNG 字节和状态哈希分别一致；完成、取消及异常后活动目标为零。这是资源复用与输出一致性证据，不能推导播放 FPS 提高或整套 Blender 无内存泄漏。

本地 Tools 诊断显示实际模块、版本、构建和数据来源，绘制采样默认关闭；近 2 秒 POST_PIXEL 统计仅用于排障，暂停时 FPS 不可用。60 Hz 时间轴、短窗口绘制样本与显示器扫描输出不是同一口径，不构成完整片段稳定 FPS 验收。

## 验证来源与安装身份

| 验证层 | 已保存结果与边界 |
| --- | --- |
| 宿主机相关回归 | **117 passed、1 skipped、6 subtests passed**。跳过项是原始运行 VTP 缺失；保留现有 Pillow API 弃用提示。[宿主结果](../../../evidence/nrel-local-0.3.18-20261008/host-final.json) |
| 最终 ZIP 隔离安装 | **PASS**，172 文件，实际模块为 `bl_ext.wfrl_frontend.wfrl_blender`；默认三相机及原生投影后台回归通过。报告自身 `visible_ui` 与 `actual_presentation` 为 `NOT_RUN`，可见窗口由另外两份报告证明。[包校验](../../../evidence/nrel-local-0.3.18-20261008/package-validation.json) |
| 安装版后台行为 | 场景保存重开、原生相机状态与缺失槽位通过；叶尖软件行为先完成，再以原 0.2 mm 断言失败结束。[叶尖结果](../../../evidence/nrel-local-0.3.18-20261008/tip-installed.json)、[失败日志](../../../evidence/nrel-local-0.3.18-20261008/tip-installed.log) |
| 隔离安装真实窗口 | Metal GPU 采集、取消／异常恢复及原生多窗口播放归属检查 **PASS**，实际来源为隔离已安装扩展；不是工作区裸源码导入。 |
| 日常安装 | 实际模块 `bl_ext.user_default.wfrl_blender`，安装根为 Blender 5.2 的 `extensions/user_default/wfrl_blender`，版本 **0.3.18**；172 文件与最终 ZIP 一致，实际窗口检查通过，用户偏好摘要不变。[安装记录](../../../evidence/nrel-local-0.3.18-20261008/daily-installation.json) |
| 正式资产保留 | 本轮前后核对 65 个正式资产摘要，变化列表为空。[最终结果](../../../evidence/nrel-local-0.3.18-20261008/result.json) |

最终 ZIP SHA-256 为 `f5fb69e6e8cdc58edf1d1f76fd4eacc1e108b688768635cc3a5a05c04bdfc400`，该已验证包随后作为 0.3.18 安装附件上传。程序退出日志仍有少量未释放内存诊断；GPU 事务已释放不能替代整体内存验收。Windows/Linux、长期稳定性、完整片段 FPS 和现场效果没有在本轮验收。

## 叶尖姿态读取修复与失败门槛

读取器改用已核对摘要和时间轴的 `data.json` 原始 float64 motion，消除先降为 float32 的姿态量化；独立刚性参考使用同一原始姿态，并核对形状、有限值和一致性。没有改写正式数据、用 BeamDyn 位移驱动实际网格、补差或放宽阈值。Blender 实际网格及世界矩阵仍受其 float32 表示限制。

| 同一安装版后台抽样口径 | x / mm | y / mm | z / mm |
| --- | ---: | ---: | ---: |
| 保存帧真实网格对结构通道误差 | 0.203520 | 1.061285 | 0.029086 |
| 同样本独立源变换运输对结构通道误差 | 0.205065 | 1.066503 | 0.017678 |
| 真实网格与独立源运输的世界坐标差 | 0.004751 | 0.017200 | 0.028986 |
| 插值帧真实网格对结构通道误差 | 0.028258 | 0.135656 | 0.022984 |

保存帧阈值仍为 **0.2 mm**，插值帧阈值仍为 **5 mm**；结果为 `software_checks=PASS`、`physical_gate=FAILED_GATE`，原断言导致退出码 1。真实网格与独立源运输差最多约 **0.0290 mm**，两者对结构通道却约有 **1.06 mm** 残差，主要差异来自正式源包 AeroDyn 表面与 BeamDyn 结构点运输近似，尚不能证明该映射满足原门槛。

另一个[独立源诊断](../../../evidence/nrel-local-0.3.18-20261008/tip-source-diagnostic.json)以 SciPy 构造刚性参考，覆盖全部 **7,203 个保存结构点**，不是上述安装版抽样或像素验证。旧 sidecar 姿态与原始 motion 姿态的最大 y 残差分别约 1.6154 mm 与 1.3515 mm，仍超过 0.2 mm；原始运行 VTP 与 `Case.T1.out` 缺失，源验证为 `SKIPPED_SOURCE_ABSENT`。

历史相同抽样约 **0.8945 mm**，本次为 **1.0613 mm**：去除姿态量化也去除了局部偶然抵消。不能将不同口径合并，也不能称本轮精度提升或科学接受。后续须恢复原始源文件，查清并修正表面／结构点运输，重新导出并独立回归；本轮没有重跑 FAST.Farm 或替换正式结果包。

Blade Recon 的 `NOT_ACCEPTED / FAILED_GATE`、MAPPO 同源纹理的 `SAME_SOURCE_SIMULATION` 及几何／纹理 `NOT_ACCEPTED`、TLS 与旧法的 `REVIEW_ONLY / PENDING_ACCEPTANCE` 均保持。软件/UI 通过不能升级这些研究结论。

## 证据如何阅读

本页依据本地 `outputs/nrel-all-fixes-20261008-a01/修复记录.md`、最终 `result.json`、`daily-installation.json`、含 `final` 的验证目录和 `validation/tip-source/tip-findings.md` 整理。早期失败或废弃 mock 不作为最终结论。公开[证据目录](../../../evidence/nrel-local-0.3.18-20261008/README.md)保留原数值、门槛、模块身份与摘要；[来源清单](../../../evidence/nrel-local-0.3.18-20261008/provenance.json)记录逐文件转换和原文 SHA-256。本机证据绝对路径用说明过的占位符替换，profile、备份、完整原图和未筛选 outputs 不随发布上传；必要随包资产在对应源码中保持原字节，安装 ZIP 位于 Release。

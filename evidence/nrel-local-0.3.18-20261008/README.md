# 本地 0.3.18 最终验证的精选证据

2026-10-08。本目录公开轻量验证与发布记录，**软件修复已验证，科学精度仍 FAILED_GATE**。原验证 JSON 的 `LOCAL_ONLY_UNPUBLISHED` 表示当时尚未发布，随后 [0.3.18 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18) 已提供安装 ZIP，对应扩展源码已推送；[发布回执](publication.json)记录此次状态变化。本目录不存安装包，发布前验证说明见 [本地修复与验证](../../docs/blender/validation/NREL本地0.3.18修复与验证.md)。

- [最终结果](result.json)、[宿主回归](host-final.json)、[日常安装清单核对](daily-installation.json)。
- [隔离 ZIP 安装及后台校验](package-validation.json)：报告自己的窗口项保持 `NOT_RUN`。
- [实际 Metal GPU 窗口](gpu-window.json)、[隔离安装原生播放窗口](playback-window.json)、[日常安装原生窗口](daily-window.json)：与后台检查分别记录。
- [场景生命周期](scene-lifecycle.json)、[相机槽位](camera-slots.json)、[原生与评估相机状态](camera-guard.json)。
- [安装版叶尖抽样](tip-installed.json)、[保留原断言失败的日志](tip-installed.log)、[独立 7,203 点源运输诊断](tip-source-diagnostic.json)。
- [来源及转换清单](provenance.json)：原记录摘要、公开文件摘要和被省略字段。
- [发布源码复建及回归](source-publication-validation.json)、[源码宿主日志](source-host-final.log)：与发布前 117 项回归分别记录，不累加。

发布前记录与本次源码复建／相关宿主回归分别保留，没有重新做全部原生窗口验收。数值、状态、阈值、来源哈希及 PNG／状态比较哈希原样保留。绝对路径被替换为 `<ISOLATED_ADDON>`、`<DAILY_ADDON>`、`<LOCAL_REPO>`、`<LOCAL_EVIDENCE>`、`<TEMP_SCENE>`，定义见来源清单；新增宿主日志使用 `<RELEASE_CHECKOUT>` 表示发布隔离目录。它们是实际运行来源的说明，既不是仓库链接，也不是可直接执行的命令。

已验证 ZIP 随后作为 0.3.18 Release 附件上传，对应源码包含构建所需的正式资产；本证据目录不含 Python、测试代码、ZIP、`.blend`、profile、原安装备份、完整脏工作区清单、正式资产或原始采集图。GPU 9/9 到 3/3 不能推导 FPS 提高；退出内存诊断保留，不能据此称整套程序无泄漏。单进程原生播放器仍为全局一个，原生场景归属检查不是两个 player 同时播放。

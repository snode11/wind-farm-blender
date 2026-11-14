# A05 历史保全审计

审计时间：2026-10-04T06:01:36.954876+00:00。状态：`PASS_WITH_APPEND_ONLY_EXCEPTION`。本次只读取历史文件并写入本审计 JSON 与说明；没有运行 Blender、拟合或评分。

A05 的 [启动前保护清单](preservation_before.json) 登记 634 个文件。本次逐项重新计算 SHA256：**633 / 634 字节相同**，变化 1，其中已证明仅追加的日志例外 1；未解释变化 0、缺失 0、读取失败 0。逐文件期望值、当前值及状态保存在 [preservation_after.json](preservation_after.json)。

唯一变化是 A04 原生窗口日志 `outputs/nrel-video-dynamic/runs/20261004-a04/window-check/attempt02/blender.log`。当前 1588 字节中，前 1099 字节在完整行边界处的 SHA256 与 A05 启动前记录完全相同，后续新增 489 字节。因此该日志是**原字节完整保留后的追加**，不能归入未改变。原长度由当前日志的行边界前缀哈希唯一匹配确定，未从时间戳推测。

新增尾部原文：

```text
2026-10-04 13:47:47.180 Blender[79614:13956713] Warning: Window move completed without beginning
2026-10-04 13:48:06.848 Blender[79614:13956713] Warning: Window move completed without beginning
2026-10-04 13:49:09.290 Blender[79614:13956713] Warning: Window move completed without beginning
40:21.583  operator         | Saved "A04_window_before_A05.blend"
40:29.315  blend            | Saved session recovery to "TEMPORARY_PATH"

Blender quit
```

A01–A04 运行目录按同一排除口径重新枚举：新增历史文件 0，移除历史文件 0。误建空目录 `New Folder` 当前不存在；其原先为空与 targeted rmdir 来自 root 的操作记录，本审计只核验当前不存在。

三路原始 MP4 全部与启动前哈希一致。原 A04 `.blend` 仍与原保护哈希一致；另外保存的 A04 当前窗口与 Blender 自动 `.blend1` 备份位于 A05 `validation/`，不覆盖原 A04 `.blend`，它们各自的哈希及大小记录在 JSON。

634 清单对正式 v2 仅登记报告，因此本审计另用未改动的 A03 历史基线核对正式 v2 的 `T1_B1.ply`、`model_state.json`、`frozen_config.json`：**3 / 3 字节相同**。该补充核对不并入 634 分母。

A05 新产物在本次审计时枚举为 71 个文件，列于 JSON；这是当前文件索引，不是冻结交付清单，其他代理可能继续添加 A05 文档或审计产物。没有根据当前文件重写历史期望哈希。

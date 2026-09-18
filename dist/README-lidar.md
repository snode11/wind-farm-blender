# WFRL Blender 0.3.4 交付说明

要求 Blender 5.2+。从 [GitHub Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.4) 下载 `wfrl_blender-0.3.4.zip`，从磁盘安装并启用，重启后加载 MAPPO · 60 秒。Windows 与 macOS 共用此包，离线回放无需后端 Python、MPI 或 FAST.Farm。

内置预弯 v3 三机结果包，包含独立未受载参考、BeamDyn 根系 xyz 挠度、轨迹渐隐和 Down 视角修复。固定三束覆盖率失败，数据仍为 REVIEW_ONLY；二维扫描候选未接入，不能作为现场精度或全项验收通过的证明。

SHA-256：`3f2c718e3ae438a49786df4de2f92a1153fd405ea026521d8c19f3e22434a9b0`。

[校验文件](wfrl_blender-0.3.4.zip.sha256) · [逐文件清单](wfrl_blender-0.3.4.inventory.json) · [交付清单](lidar-delivery.json) · [发布核对](../docs/blender/0.3.4发布核对.md)。

已验证独立 ZIP 的 macOS Blender 原生回放与本机安装一致性。Windows 实机、稳定 60 FPS、长期稳定性和现场精度未验收。

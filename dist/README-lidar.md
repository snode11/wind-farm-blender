# WFRL Blender 0.3.1 交付说明

正式版本为 **0.3.1**，汇总本地 0.3.1–0.3.5 开发阶段。上一 GitHub 正式版为 0.3.0。

## 给 Windows 用户的文件

只需下载 [wfrl_blender-0.3.1.zip](wfrl_blender-0.3.1.zip)，在 Blender 5.2+ 从磁盘安装并启用，重启后到 **N → MAPPO → 加载 MAPPO · 60 秒**。不要用 GitHub 的 Source code ZIP 安装扩展；不需要 Mac `.command`、Python、MPI 或 FAST.Farm。

[用户手册](../docs/blender/用户使用手册.md) · [快速演示](../docs/blender/MAPPO演示.md) · [正式发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.1)

ZIP 内置最新三机柔性塔架 v2 数据、九片叶片形变、T1 挠度对照和同源遥测。默认资源包括 `geometry.npz`、`tower-motion.npz`、`deflection-t1.json`、`data.json`、`telemetry.json` 与来源清单；播放不依赖原始 VTP 或本机路径。

## 版本与校验

- 扩展/项目版本：0.3.1；数据 schema：`wfrl.farm-flex-review.v2`；状态：`REVIEW_ONLY`。
- ZIP SHA-256：`4b947a3e9565374e2b1da46387176ccb7940e659f1836e9a073f83939c2f149c`。
- [SHA-256 文件](wfrl_blender-0.3.1.zip.sha256) · [逐文件清单](wfrl_blender-0.3.1.inventory.json) · [机器可读交付清单](lidar-delivery.json)。
- 内置物理包 manifest SHA-256：`b765b9791b95fcfd7a39079b949131554e905ba4e12af60979a85d254628ae5e`。

在 PowerShell 中执行 `Get-FileHash .\wfrl_blender-0.3.1.zip -Algorithm SHA256` 比对。旧正式包保留，本地中间版本不作为新的 GitHub 版本发布；从中间版迁移请先移除旧扩展后安装正式版。

## 兼容与范围

读取器继续兼容旧三机 v1 包。两个历史独立雷达包 `normal-v1.1` / `close-v1.1` 仍需项目外部目录，仅在使用手册第 12 节流程中需要，默认三机演示无需它们。本轮不重复上传这些未变数据，不上传原始求解几何、截图序列、缓存和本机备份。

实时后端需要完整项目和独立环境；只发扩展不能替代 Windows 的后端安装。当前 ZIP 以隔离解包运行验证；本机原已安装扩展和旧便携目录不会仅因生成 ZIP 自动升级。

本轮检查见 [0.3.1 发布核对](../docs/blender/0.3.1发布核对.md)。物理数据仍为 REVIEW_ONLY，固定 B2 估计未补偿塔架弯曲；Windows 实机、完整风场收敛、稳定 60 FPS 和现场精度尚未验收。

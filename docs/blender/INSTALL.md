# WFRL Blender 0.3.4 安装

要求 Blender 5.2+，Windows/macOS 使用同一 ZIP。

1. 从 [0.3.4 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.4) 下载 **wfrl_blender-0.3.4.zip**，不要下载自动生成的 Source code。
2. Blender → Edit → Preferences → Get Extensions → Install from Disk，选择 ZIP 并启用。
3. 重启 Blender，打开新场景，按 N → MAPPO → 加载 MAPPO · 60 秒。

包内含完整预弯 v3 回放数据，无需仓库、外部 Python 或 FAST.Farm。可将 ZIP 的 SHA-256 与发布页 `.sha256` 文件比较。

源码构建：`python scripts/blender/build_extension.py`。更新源码不会更新已安装扩展，需要重新安装 ZIP。

固定三束覆盖率未通过，Windows 实机、稳定 60 FPS 和现场精度未验收。详见 [发布核对](0.3.4发布核对.md)。

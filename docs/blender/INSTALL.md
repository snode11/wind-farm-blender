# WFRL Blender 0.3.1 installation

当前发布版本为 **0.3.3**：[下载](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.3)，含雷达反馈与塔顶密封圈/焊缝随动修复。详细验证见发布核对文档；以下早期版本说明保留作为历史。

The same extension ZIP is intended for Windows and macOS. Blender 5.2+ is required. Native release checks run on macOS with Blender 5.2.1; Windows has not yet been tested on a Windows host.

1. Download **wfrl_blender-0.3.1.zip** from the [release page](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.1). Do not use the automatically generated Source code archive.
2. In Blender, open **Edit → Preferences → Get Extensions**, open the menu and choose **Install from Disk**.
3. Select the ZIP without extracting it, enable **WFRL Blender**, then restart Blender.
4. Start a new General scene. In the 3D View press **N → MAPPO → 三机 MAPPO 回放 → 加载 MAPPO · 60 秒**, then play.

The ZIP includes the complete three-turbine 60-second recording, flexible towers, nine flexible blades, T1 tip comparison and telemetry. Offline playback needs no repository checkout, external Python, MPI, FAST.Farm, checkpoint or network. The `.command` portable launcher is for macOS; Windows users should install the ZIP.

If migrating from a local development build labelled 0.3.2–0.3.5, save any scenes first, remove that extension in Preferences, and install the official 0.3.1 ZIP. Those higher numbers were local development labels consolidated into this release.

Optional PowerShell checksum check:

```powershell
Get-FileHash .\wfrl_blender-0.3.1.zip -Algorithm SHA256
```

Compare it to the release `.sha256` file. Updating source files does not update an installed extension. To rebuild from this repository, run `python scripts/blender/build_extension.py`, then install the new ZIP and restart Blender.

Real backend Replay, Interactive and Formal Training require the repository and separate backend dependencies. Configure project, Python, MPI and FAST.Farm paths for the destination computer. See the [complete user manual](用户使用手册.md#6-需要时连接真实后端) for launch commands, connection and shutdown steps. The default offline recording does not require these steps.

[Chinese quick start](MAPPO演示.md) · [Complete manual](用户使用手册.md) · [Release verification](0.3.1发布核对.md)

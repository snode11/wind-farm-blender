# WFRL Blender 0.2.1 安装与使用

只看风场演示：下载前端扩展 ZIP，安装到 **Blender 5.2.0 或更新版本**即可。真实仿真与训练还需要单独配置项目后端环境。

## 下载安装

1. 下载本仓库的 [`dist/wfrl_blender-0.2.1.zip`](../dist/wfrl_blender-0.2.1.zip)。在 GitHub 文件页面选择 **Download raw file**；不要把整个仓库的 **Download ZIP** 当作扩展安装包。
2. 打开 Blender，进入 **Edit → Preferences → Extensions → Install from Disk**，选择这个 ZIP，不要解压。
3. 安装仓库选择 **User Default（user_default）**，启用 **WFRL Blender**。启动脚本使用这个安装位置。
4. 若已安装旧版，更新同一个扩展，确认显示版本 **0.2.1**；保存当前工作，完全关闭并重新启动 Blender，让新代码生效。

## 先验证离线演示

从 Finder 或开始菜单打开 Blender，使用新文件。在三维视窗按 **N** 打开侧栏：

1. **Item → WFRL / CONNECTION → Demo**，确认显示 **LOCAL DEMO**，无需连接后端。
2. **WFRL / PRESENTATION → Scene → Load Demo Scene**。
3. **WFRL / PRESENTATION → Run → Start Demo**。

三台风机运动、时间推进且显示 `SYNTH / No physical backend`，表示离线演示已启动。这是预设动画，不是真实仿真或训练结果；不需要 Conda、MPI 或 FAST.Farm。

## 视角与云台

在 **Item → WFRL / Views & Capture** 统一切换：

| 按钮 | 用途 |
| --- | --- |
| World | 风场全景 |
| Top | 顶视图 |
| Side | 侧视图 |
| T1 / T2 / T3 Gimbal | 对应风机的云台视角 |

选择云台后，点击 **World** 即可返回全景，不需要另一个返回按钮。这个面板还提供尾流显示和截图、录制功能。离线演示的 **PRESENTATION → Views & Layers** 也保留相同的视角入口。

需要调整云台时，进入 **Camera → WFRL / Gimbal Camera 云台相机**，选择对应机组并使用 **Camera Mode**：拖动改变朝向、滚轮调整 FOV，按 **Esc** 退出控制。云台朝向不会改变风机偏航或后端策略。

## 连接真实后端

前端 ZIP 不包含训练环境或 FAST.Farm 可执行程序。需要完整项目、已安装 WFRL 及相应依赖的 Python 环境；FAST.Farm 场景还需要兼容的 MPI 和本机 FAST.Farm。配置方法见 [后端安装说明](blender/INSTALL.md) 与 [项目环境部署](setup_local.md)。

macOS 启动参数没有因这次前端更新改变。在完整项目根目录执行，路径必须替换为使用者电脑上的实际位置：

```bash
scripts/blender/run_wfrl_macos.sh \
  --blender "/Applications/Blender.app/Contents/MacOS/Blender" \
  --python "/absolute/path/to/environment/bin/python" \
  --scene "scenes/turb3_row.yaml" \
  --mpi "/absolute/path/to/mpiexec" \
  --fastfarm "/absolute/path/to/FAST.Farm"
```

启动器会启动本地 Bridge 并打开 Blender；它不会替你安装扩展或后端依赖。也可在扩展设置中填写项目目录、Backend Python、MPI、FAST.Farm、Default scene 与端口，再用 **Check Environment** 检查各项配置。只观看离线演示时不必运行此命令。

Windows 启动器和更多操作参见 [用户使用手册](blender/用户使用手册.md)。Windows 的实际安装与运行仍需在目标机器上验证。

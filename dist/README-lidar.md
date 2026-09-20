# WFRL Blender 0.3.5 交付说明

要求 Blender 5.2+。从 [GitHub Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.5) 下载 `wfrl_blender-0.3.5.zip`，从磁盘安装并启用，重启后加载 MAPPO · 60 秒。Windows 与 macOS 共用此包，离线回放无需后端 Python、MPI 或 FAST.Farm。

新增独立机舱云台相机，默认 120° 广角，保留原 Down 视角；支持摇杆转向与相机模式下滚轮变焦。叶尖轨迹改为红绿蓝三叶片相邻两圈定格 2 秒，再用 0.75 秒淡出，按仿真时钟播放。

内置物理数据与 0.3.4 完全一致，固定三束覆盖率失败，仍为 REVIEW_ONLY。

SHA-256：`709aa8151263b97bd1c89fb74f61f72c3becb0432a11677fe35643a40763aaa9`。

[校验文件](wfrl_blender-0.3.5.zip.sha256) · [逐文件清单](wfrl_blender-0.3.5.inventory.json) · [交付清单](lidar-delivery.json) · [发布核对](../docs/blender/0.3.5发布核对.md)。

验证环境为 macOS Blender 5.2.1 LTS。Windows 实机、稳定 60 FPS、长期稳定性和现场精度未验收。

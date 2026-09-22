# WFRL Blender 0.3.7 交付说明

要求 Blender 5.2+。安装 [wfrl_blender-0.3.7.zip](wfrl_blender-0.3.7.zip)，启用后重启 Blender，加载 MAPPO · 60 秒。包内含现有预弯 v3 回放，离线播放无需后端 Python、MPI 或 FAST.Farm。

本版包含四相机安装与独立双 FOV、原生单路/四路观察、同刻原图与序列采集、布局导入导出和相机专用撤销，以及已有 MP4 导出与 RTSP 推流。

[安装说明](../docs/blender/INSTALL.md) · [四相机使用说明](../docs/blender/T1四相机研发使用说明.md) · [四段布局与使用方法](../outputs/camera-layout-same-blade-v2/README.md) · [视频输出](../docs/blender/Blender视频输出.md)

四路仍可能明显掉帧；四段布局不持续跟踪叶片，也不保证完整叶片表面无盲区。物理数据仍为 REVIEW_ONLY，固定三束覆盖率未通过。

SHA-256：`c087776e1b5a9b0269523177b9c4d52bedd163e87813f0a6de1118204dfec74e`。

[校验文件](wfrl_blender-0.3.7.zip.sha256) · [逐文件清单](wfrl_blender-0.3.7.inventory.json) · [交付清单](lidar-delivery.json) · [发布核对](../docs/blender/0.3.7发布核对.md)。

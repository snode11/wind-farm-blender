# WFRL Blender 0.3.9

> **历史 0.3.9 交付说明。** 本页摘要、校验文件与清单仅标识该轮交付，不是当前安装包或完整功能清单。当前公开 NREL / WFRL 使用 [0.3.18 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.18) 的 ZIP，对应扩展源码也已发布；安装和范围见[安装指南](../docs/blender/INSTALL.md)、[0.3.18 发布核对](../docs/blender/releases/0.3.18发布核对.md)。旧独立 normal/close 与当前 MAPPO 柔性回放分开阅读。

要求 Blender 5.2+。安装 ZIP 后重启 Blender，重新加载 MAPPO · 60 秒，自动创建带推荐连续重叠取景的三相机盒体。支架安装端贴住机舱，盒体外伸；支持同一步选点与转向，再分别调整 C1–C3。默认关闭轨迹与挠度辅助物。

[0.3.9 Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.9) · [历史安装包](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.9/wfrl_blender-0.3.9.zip) · [三相机说明](../docs/blender/T1三相机使用说明.md) · [历史发布核对](../docs/blender/releases/0.3.9发布核对.md)

支持三路原生预览、同刻/序列原图、内外参、布局导入导出及整盒/相机撤销。盒体尺寸和镜头参数为仿真假设；未实现图像拼接，保留实际几何遮挡。内置物理数据仍为 REVIEW_ONLY。

SHA-256：`0cabd8f1c95739d585b72ba638be3026db9488757a037b524b50fd8d362b5343`。

历史清单文件名为 `wfrl_blender-0.3.9.zip.sha256`、`wfrl_blender-0.3.9.inventory.json` 与 `lidar-delivery.json`；当前仓库未提供这三份文件，不作为可打开的现行附件。

该轮 Release 附件仅提供扩展安装 ZIP；上方保留该包 SHA-256，逐文件清单的当前缺失状态如上。GitHub 自动生成的 Source code 归档用于查看版本源码，不是扩展安装包。

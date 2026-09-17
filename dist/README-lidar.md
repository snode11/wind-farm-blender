# WFRL Blender 0.3.3 交付说明

当前版本 **0.3.3**，要求 Blender 5.2+。下载 [安装 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.3.3/wfrl_blender-0.3.3.zip)，从磁盘安装并启用，重启 Blender 后加载 MAPPO · 60 秒。Windows 与 macOS 共用此包，无需后端 Python、MPI 或 FAST.Farm。

本次增加净空状态灯、逐束测量提示，修复塔顶密封圈和塔筒焊缝随动，消除密封圈穿过雷达的外观问题。内置物理数据与正式 0.3.1 一致，仍为 REVIEW_ONLY。

SHA-256：`eef7356b30afab4a0c7fac10d4d4567051005599316e49775ead3c31d15ece82`。

[校验文件](wfrl_blender-0.3.3.zip.sha256) · [逐文件清单](wfrl_blender-0.3.3.inventory.json) · [交付清单](lidar-delivery.json) · [发布核对](../docs/blender/0.3.3发布核对.md)。旧正式包保留。

已通过 macOS 原生后台回归和独立 ZIP 检查；Windows 实机、新窗口鼠标交互、稳定 60 FPS 与现场精度尚未验收。短服务电缆末端悬空问题仍待修整。

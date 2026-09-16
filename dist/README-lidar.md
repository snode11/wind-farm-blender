# 0.3.0 安装与交付

当前安装包：[wfrl_blender-0.3.0.zip](wfrl_blender-0.3.0.zip) · [GitHub Release](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.0)

在 Blender 5.2 或更新版本中选择“从磁盘安装”，安装上述 ZIP 后重启 Blender。GitHub 自动生成的 Source code ZIP 不是扩展安装包。N 侧栏 → MAPPO →“加载 MAPPO · 60 秒”可直接播放内置三机结果，无需后端环境。

SHA-256：`e1f5a5ccb67832eabc95302b2c1e551e35b2bf92f47b3b59cc11db6a68f3e441`

[逐文件清单](wfrl_blender-0.3.0.inventory.json) · [校验文件](wfrl_blender-0.3.0.zip.sha256) · [机器可读交付清单](lidar-delivery.json)

## 本版交付内容

- ZIP 内置三机 60 秒 MAPPO 形变、测量、运动及功率/载荷遥测数据，含九片独立形变、彩色叶尖轨迹、视角/云台、统一回放面板与 B2 测距。
- 项目仓库包含原命令启动与 Bridge 暂停/单步/重连修复。只安装 ZIP 不会更新项目脚本；使用 `run_wfrl_macos.sh` 前需同步本版仓库并具备本机后端环境。
- 原命令默认加载已安装扩展并暂停在 T1 侧前方首帧，Bridge 待用；主动连接后自动加载指定 YAML。操作见 [MAPPO 演示说明](../docs/blender/MAPPO演示.md)。
- 独立雷达包继续使用 `results/lidar/packages/normal-v1.1` 和 `close-v1.1`。这些外部包与原始 VTP 不在扩展 ZIP 中；三机 MAPPO 数据则已内置。
- 0.2.x 安装资源保留为历史版本。本次复用已保存物理结果，未重新求解或训练。

## 校验

下载 ZIP 与校验文件到同一目录后运行 `shasum -a 256 -c wfrl_blender-0.3.0.zip.sha256`。发布源码可通过 `scripts/blender/build_extension.py` 重建同一安装包。

需要核对独立雷达包时，在项目根运行（输出文件需为新路径）：

```sh
python3 scripts/blender/verify_lidar_delivery.py dist/wfrl_blender-0.3.0.zip results/lidar/packages/normal-v1.1 results/lidar/packages/close-v1.1 --output /tmp/wfrl-delivery-030-check.json
```

此命令校验隔离 ZIP 读取，不代表 Blender GUI 或现场设备验收。

## 验证范围

发送前已核对正式 ZIP、便携 ZIP 和本机安装文件一致，并完成九片回放、遥测、保存重开、缺包恢复、启动模式与真实后端短测。详见 [发送前检查](../docs/blender/0.3.0发送前检查.md) 和 [Demo 替换验证](../evidence/flex-mappo/demo-replacement-20260916/验证记录.md)。

三机片段保持 REVIEW_ONLY；T1 雷达有效率、数值收敛、稳定 60 FPS、长期稳定性、控制收益、Windows 与现场设备尚未验收。旧记录中的“ZIP 不含三机数据”已由当前完整 MAPPO ZIP 替代；独立 normal/close 包仍为外部数据。

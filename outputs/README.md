# 输出与研究资源

本目录保存研究输入、实验结果、软件验证证据及部分本地安装包，不是可整体删除的缓存目录。正式运行资产位于 [`blender_frontend/wfrl_blender/assets/`](../blender_frontend/wfrl_blender/assets/)；资源分发范围见 [分发说明](../docs/PUBLICATION.md)与 [文件清单](../publication/resources.json)。

## 按用途查找

| 用途 | 入口与内容 |
| --- | --- |
| 表面与材料点研究 | [`blade-surface/`](blade-surface/)：原始观测、对应关系、重建、独立评分及正负结果 |
| 动态与纹理研究 | [`blade-recon-v02/`](blade-recon-v02/)：输入、冻结实现、纹理、几何候选及验收证据 |
| MAPPO 同源重建 | [`blade-recon-mappo-two-camera-20261004-a02/`](blade-recon-mappo-two-camera-20261004-a02/) 与 `mappo-same-source-texture-20261007-a01/`（资源包目录）：保存的采集输入、重建及纹理再生成数据 |
| NREL 单叶片与动态研究 | [`nrel-video-single-blade/`](nrel-video-single-blade/) 与 [`nrel-video-dynamic/`](nrel-video-dynamic/)：原始采集、标注、冻结代码、数值结果及研究查看器 |
| 雷达研究 | [`dual-beam-tls-20261002/`](dual-beam-tls-20261002/)：保存源、候选算法结果、原始数值证据及独立验证 |
| 机舱相机 | `nrel-nacelle-camera/`（本地证据目录，使用入口见[机舱相机](../docs/blender/NREL机舱相机.md)）：相机取景、原始渲染、诊断、对照页面与隔离安装包 |
| GW184 试片 | `gw184-single/review/index.html`（本地查看页，来源及使用见[项目说明](../projects/gw184-single/README.md)）：试片、参数、参考帧与几何检查；下载入口使用 `delivery/GW184-trials.zip` |
| 前端验证 | [`frontend-reliability-20261011-a01/`](frontend-reliability-20261011-a01/)、`reconstruction-*`、`blender-validation/`、`release-*` 等：按包身份解释的软件检查、截图和日志 |
| 当前本地扩展 | [`frontend-stability-fixes/validation/package/`](frontend-stability-fixes/validation/package/)：0.3.28 ZIP、逐文件清单及摘要；[安装版回归](frontend-stability-fixes/installed-regressions.json)与[日常安装身份](../publication/daily-installation.json)分别标识验证载荷和实际模块 |

目录名中的编号和日期属于现有资源定位符，不代表当前软件开发顺序。根目录的部分别名是符号链接，不是另一份数据。

## 保留边界

- 原始 RGB、掩码、标定、轨迹、重建、独立评分、冻结源码、配置和摘要共同构成实验依据；`FAILED_GATE`、`NOT_ACCEPTED` 或试采集名称不能作为删除理由。
- `nrel-video-single-blade/` 中的标注脚本、协议、观测数组和冻结实现被测试及研究工具读取。MAPPO 同源重建目录也是后续处理的输入；移动或删除前必须检查代码引用。
- 截图、日志和软件验证 JSON 按其记录的输入、包身份及平台解释；一次运行的结果不能替代另一包或另一运行的验证。
- 内容相同的原始文件可能服务于独立数据包。单个文件的摘要相同不意味着其路径、包清单和来源关系可以删除。
- Python `__pycache__` 可重新生成。安装包的解压副本只有在逐文件匹配保留 ZIP、且没有当前入口依赖时才可移除。

## 机舱相机安装资源

`nrel-nacelle-camera/` 保留当前对照页、原始图像、场景、诊断及对应验证证据。页面和图像可直接查看；`review.py` 打开或重新生成场景需要与证据匹配的扩展 ZIP 和隔离安装配置。

`nrel-nacelle-camera/final-package/` 当前保留 inventory、环境声明和验证记录，扩展 ZIP 与 `profile/` 缺失。`final-render.log` 与 `artifact-sha256.json` 声明的摘要也不一致，原因待核实。原始日志、声明摘要及检查结果均保留；复核启动器当前不能通过完整性检查，保存的运行结果不代表现有交付目录完整。

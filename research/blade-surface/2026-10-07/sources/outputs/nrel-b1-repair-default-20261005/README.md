# T1 / B1 默认修补痕迹 · 本机覆盖记录

2026-10-05，状态 **COMPLETE**。用户要求将一处补漆外观直接加入原有 NREL 5MW T1/B1，覆盖原场景，并保留原启动入口。此次操作不要求打开独立修补场景。

用户确认的入口为 `scripts/blender/run_wfrl_macos.sh --scene scenes/turb3_row.yaml`，其余 Blender、Python、MPI、FAST.Farm 参数沿用原命令。当前入口先加载 MAPPO 60 秒回放，显式后端连接后建立 YAML 场景；两条场景建立路径均包含默认修补。此次仅启动同一前端 bootstrap 做显示检查，没有运行 Bridge、求解器或训练。

默认修补约 1.4 × 0.7 m，距叶根约 34 m，使用浅灰色补漆材质。原生 Geometry Nodes 从原叶片网格采样三角形及重心坐标，随转子、变桨和柔性形变运动。仅改变合成外观，不参与物理计算。实现为 `blender_frontend/wfrl_blender/repair_marks.py`；场景建立、柔性参考网格替换及文件重开均已接入。

## 原场景与安装

四个仓库原文件已在原路径覆盖保存并重新打开，逐个保留原时间线、当前帧和相机；路径及记录见 [originals-updated.json](originals-updated.json)。本机已安装扩展及其便携示例也已更新，详情见 [installation.json](installation.json)。四份重复 `.blend1` 均与 `before/scenes/` 的实际更新前备份逐字节一致，列入本次中间产物清理；构建 ZIP 不包含保存备份。

原扩展位于 `/Users/eason/Library/Application Support/Blender/5.2/extensions/user_default/wfrl_blender`；完整更新前副本保留在 `before/installed/`。四个原场景的字节备份保留在 `before/scenes/`，校验值见 [scene-hashes.json](scene-hashes.json)。`scenes/turb3_row.yaml` 未修改。历史 GitHub 0.3.15 Release ZIP 和 `dist/` 未覆盖；`build/` 是本机更新包。

旧 66 秒历史文件仅作为调查副本检查，没有恢复到旧入口。用户已提供现用启动命令，交付以该入口为准。旧场景提取副本、已取消恢复的重复副本和原生测试 `.blend` 夹具均列入本次中间产物清理，保留 [旧场景检查报告](legacy-inspect/inspection.json) 及源版、安装版验证 JSON。报告中的夹具路径是历史生成位置，当前回放不依赖这些文件。

## 已验证范围

- Blender 5.2.1 LTS 已安装扩展原生检查通过，22 个姿态及生命周期检查见 [installed-validation/validation.json](installed-validation/validation.json)。覆盖默认柔性回放、多次重载、保存重开、带 Bevel 的刚性测试、66 秒测试时间线保留，以及用户 YAML 经 `live_scene` 建立场景。
- 最大几何附着误差约 0.000012 m；这是对绑定计算的数值一致性检查，不是实际缺陷尺寸测量或相机识别准确率。
- 同一前端 bootstrap 在原生 GUI 中加载已安装版本，确认默认修补存在、辅助网格隐藏、YAML 设置正确，记录见 [ui-bootstrap-validation.json](ui-bootstrap-validation.json)。视口已放大到 B1 修补以供检查，没有另存展示文件。
- 扩展 ZIP 的全部 135 个文件逐字节核对安装结果；源脚本语法检查及相关文件 `git diff --check` 通过。

本次未渲染视频，未运行 FAST.Farm，未验证缺陷识别、实时性能或物理损伤响应。

## 2026-10-05 复核与中间产物清理

清理前再次逐字节核对本机扩展全部 135 个打包文件、最终 ZIP、四个覆盖后的原场景及实际更新前备份；启动 YAML 未修改。使用已安装扩展重新打开四个真实原场景，12 个首帧、原保存帧及末帧的附着检查通过，时间线、当前帧及相机设置保持一致，详见 [originals-recheck.json](originals-recheck.json)。复核未保存原文件，也未运行求解器。

[cleanup-plan.json](cleanup-plan.json) 列出本次可回收的 28 个文件、原路径、大小、SHA-256 及原因，总计 325,089,943 bytes。范围仅为本次修补的试作场景、测试夹具、旧场景调查副本、一次性脚本、缓存与重复保存备份。源码、永久回归测试、最终 ZIP／校验和／清单、验证记录、完整更新前插件及四个实际场景备份均保留。

执行结果见 [cleanup-record.json](cleanup-record.json)。清理通过 macOS 废纸篓完成，保留恢复能力，不清空废纸篓；清理后核对原场景、安装版、备份和其余受保护文件的 SHA-256，并比较已有跟踪文件的 Git 状态。

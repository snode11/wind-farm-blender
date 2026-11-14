# MAPPO／重建分屏优化交付

> 后续发布状态（2026-10-05）：本报告保留优化开发阶段的版本、测试与哈希。现在0.3.15已发布便携示例，日常安装及当前窗口来源见[发布核对](../../../../docs/blender/0.3.15发布核对.md)；下面“当前Blender”“未创建Release”仅指原优化结束时。旧0.3.13候选包与临时预览副本由[维护清单](../../../maintenance-20261005-docs-cleanup/REPORT.md)回收，最终研究场景、baseline、v5关键JSON/日志及复验脚本保留。

日期：2026-10-05。范围：用户批准的第 1、3、4 项；纯界面、布局和回放控制。

已更新已安装扩展，并保存 `../MAPPO_demo_reconstruction_split.blend`。当前 Blender 打开该最终文件，停在第 52 帧。`../打开同步分屏.command` 只打开文件，不再注入临时 Python 恢复脚本。

## 已实现

- 第 1 项：按有效画布平衡左右宽度。完整取景聚焦左侧 T1、保留风场背景和观察方向；右侧依据全部保存姿态适配。侧栏或窗口尺寸改变后，等待重绘稳定再完成取景；支持一键完整取景及恢复布局。
- 第 3 项：分开显示播放时间、真实重建样本时间和保持时长，新增上一／下一实际样本。步进先暂停同场景播放；首尾钳制。整段 1–3601 帧时间轴可见，保留每 6 帧一个样本的 10 Hz 状态保持。
- 第 4 项：扩展原生管理进入、退出、恢复和文件重开；相机按钮在执行时排除右侧。保留自由浏览、天空和工具侧栏。退出保留当前时间及左侧视角、显示样式。

## 验证结果

| 验证 | 结果与证据 |
| --- | --- |
| 生命周期／所有权／透视取景逻辑 | 11 项通过，`blender_frontend/tests/test_split_reconstruction.py` |
| 时间映射与样本步进 | 8 项通过，`blender_frontend/tests/test_split_reconstruction_timing.py` |
| 安装版真实窗口 | PASS：`window-v5/window_validation.json`。六个时刻核对左右几何都在可用画布内，相机四条调用路径即时保持右侧独立，样本首尾、播放后步进、工作区往返、退出再进入均通过 |
| 实际按钮与拖动 | PASS：`manual_ui_validation.json`；下一／上一样本、窄右窗和恢复布局均可见验证 |
| 可见窗口保存与原生重开 | PASS：`window-v5/visible_reopen.json`。重开无需旧恢复脚本，仍为第 52 帧，左右隔离与六时刻取景通过 |
| 独立进程读取最终文件 | PASS：`reopen_final.json`。每片 601 个绝对形态键、CONSTANT 保持、材质动画、源动态恢复、前后寻址和几何核对通过 |
| ZIP／安装一致性 | PASS：`installed_final.json`，实际安装模块来自用户扩展目录；全部 ZIP 文件与安装文件一致，全部 assets 与修改前逐文件一致 |

实际环境为 macOS、Blender 5.2.1 LTS，模块 `bl_ext.user_default.wfrl_blender`。验证未测量 FPS；其他系统未验证。重建精度仍为原实验的 `NOT_ACCEPTED`。

## 包与来源

原优化阶段最终开发包（历史路径，已由后续0.3.15替代，按维护清单回收）：`delivery-v5/wfrl_blender-0.3.13.zip`。下面哈希属于该历史开发包。

当前发布最终包：[wfrl_blender-0.3.15.zip](../../../release-0.3.15-20261005/validation-v2/package/wfrl_blender-0.3.15.zip)，SHA-256 `eb8319ee5de39c65471527893065f74ad5a5541fceaed87697a0d527c349e324`。

SHA-256：`313a5b42a097b83d877e227eb9c1ca35ab70d56b9573466d80da00b5d42a1601`。

最终 blend SHA-256：`76aaa3a70eb7d657579af4a6f150f41c085e96f0c7affa0803b830ef117dd578`。

修改 5 个原有 Python 文件，新增 3 个分屏模块；没有修改安装包中的仿真资源。未运行 FAST.Farm、Bridge 或重建求解器，未创建 GitHub Release，未改根发布记录。

## 可重复验证命令

从仓库根目录执行；可见窗口复测使用新的输出目录和新的保存路径，避免覆盖本次证据。

```sh
PYTHONPATH=blender_frontend:. ../blade_recon/.venv-trial/bin/python blender_frontend/tests/test_split_reconstruction.py
PYTHONPATH=blender_frontend:. ../blade_recon/.venv-trial/bin/python -m unittest discover -s blender_frontend/tests -p test_split_reconstruction_timing.py -v
```

窗口入口为 `blender_frontend/tests/blender/split_reconstruction_window.py`：`WFRL_TEST_OUTPUT` 指向新增证据目录，`WFRL_SPLIT_SAVE` 指向新增 blend 路径，`WFRL_SPLIT_REOPEN=1` 启用保存后原生重开；打开本目录 `baseline/current_window_before.blend` 副本再执行脚本。独立读取入口为 `verify_optimized_reopen.py`，`WFRL_REOPEN_REPORT` 可指定新的 JSON 证据路径。

## 基线与修复记录

修改前磁盘文件保存在 `baseline/MAPPO_demo_reconstruction_split.blend`；操作前未保存窗口副本为 `baseline/current_window_before.blend`。启动器、旧恢复脚本、说明和修改前源码也保留在 baseline。

开发中发现并修复：UI 分类标签加载初期只读、缩放矩阵需等待重绘、左侧同一回调连续投影导致错误缩放、退出后保留右窗时显示样式继承，以及完成状态缺少重绘。早期失败证据保留在 `window-v1`／`window-v2`；v3 几何验证通过但左侧取景不足，v4 补上源取景断言和解析算法，最终 v5 补状态刷新并完成保存重开验证。最终结论以 v5 及 `reopen_final.json` 为准。

2026-10-05清理补充：`delivery`至`delivery-v5`、两份`optimized_preview*.blend`、`window-v3/window_validation.json`与`window-v4/window_validation.json`等已移入废纸篓；原路径/文件哈希见[文件级清单](../../../maintenance-20261005-docs-cleanup/cleanup-plan.json)。v1/v2失败记录、v5最终窗口与保存重开、`reopen_final.json`、`reopen-v3.json`、`verify_optimized_reopen.py`及baseline保留。旧复验命令中的目录是历史环境；如需复验须新建隔离安装环境，不复用已回收profile。

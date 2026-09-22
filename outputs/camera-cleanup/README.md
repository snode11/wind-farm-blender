# 四相机旧预览清理与本机更新

**观察目标更正（2026-09-23）：用户要求多台相机分段观察同一片叶片。旧 T1-four-cameras-v1.json 的上、右、下、左布局不符合目标，仅保留作历史测试输入。观看工具的改造与清理通过，不代表布局目标已经完成；后续已交付 [同一叶片 v2 试用布局](../camera-layout-same-blade-v2/README.md)，本目录旧测试结果仍按原输入解释。** 见 [旧布局状态说明](../camera-layout-test-v1/README.md) 和 [更新后的阶段报告](../../docs/blender/T1四相机研发_v3实施记录.md#completion-20260923)。

2026-09-23。本次删除旧离屏四格入口、四格卡片/点击区域、自动四幅刷新分支与残留 QUAD 状态。保留原生单路/四路、单路精确编辑、布局导入导出、四相机原图/序列采集。清空 WATCH 中的相机回到外部布局，故障回滚和撤销仍有效。

本机扩展已更新。重启 Blender 后重新加载演示、导入 v2 试用布局，再使用 View → 自定义相机 → 四路对照。正在运行的 Blender 不会自动重新加载磁盘上的 Python 文件。

## 谨慎清理

- 91 个本次中间文件、旧测试截图、重复包和新验证产生的 PNG，合计 153,361,450 字节（约 153 MB），移到仓库外的可恢复隔离目录。没有永久删除，因此不宣称释放磁盘空间。
- 隔离位置：`/Users/eason/.codex/quarantine/wfrl-camera-20260922-235649`。
- [manifest.json](manifest.json) 逐文件记录原位置、备份位置和 SHA-256。所有移动文件与修改前备份均已校验；需要恢复某项时按清单复制对应文件，避免覆盖后续工作。
- 保留桌面与仓库里的 T1-four-cameras-v1.json，两份内容一致；正式 MAPPO 数据、模型、用户场景与既有无关修改保留。未清空整个 outputs 或 evidence。
- 四相机旧窗口测试保留有价值的安装与导出验证，改为外部布局入口；原生四路由专用测试验证。历史文档不重写为新的通过结果。

## 已运行验证

- Python：173 项通过。命令：`PYTHONPATH=blender_frontend:. /opt/anaconda3/envs/wfrl-mac/bin/python -m pytest blender_frontend/tests/test_custom_camera_preview.py blender_frontend/tests/test_custom_camera_v3.py blender_frontend/tests/test_custom_camera_capture.py blender_frontend/tests/test_camera_projection.py blender_frontend/tests/test_custom_cameras.py -q`。
- Blender 5.2.1 LTS 实际独立窗口：单路退出与视角切换；单路编辑、清空、撤销和提交失败回滚；1920/2560 原图、四路同刻采集、序列与取消、布局恢复及回放统计保留，均通过。结果在 validation/single-exit、editor-export-final、four-export。
- 安装版原生四路/单路、播放、退出、系统关闭窗口、临时观察相机释放、原布局与主渲染设置保留，均通过，见 [安装版结果](validation/installed-native/check.json) 和 [四路截图](validation/installed-native/quad.png)。原生观看不启动旧四幅离屏缓存。
- 验证用导出的 PNG 已移至隔离目录，验证脚本、结果与采集元数据仍在原位置，文件恢复映射见清单。
- [本机安装核对](install.json)：ZIP 内全部文件与安装目录一致，仅更新五个本次修改的 Python 文件；已备份安装前文件。

## 包与性能边界

本地包：[wfrl_blender-0.3.6.zip](package/wfrl_blender-0.3.6.zip)。SHA-256：`7bd4aa5dd926c049a33c0a84c0ca46ec222682d3ca9aa9da11cf65862240712c`。仅本地更新，未发布 GitHub。

清理后仍保留原材质、贴图、灯光与阴影质量。卡顿的主要已验证因素是动态场景的阴影成本，原生四路原设置短测约 2.2 FPS，阴影分辨率减半的独立对照约 8.0 FPS。没有把降低阴影质量写入默认配置。此次安装后的生命周期短测约 2.75 FPS，同样不能称为流畅播放。

详细对照与测量边界见 [卡顿诊断](../camera-lag-diagnosis/REPORT.md)。未验证长时间内存增长或 Windows/Linux。

# 原生相机布局回归

`frontend_layout_regression.py` 在独立 Blender 窗口内检查三列、2×2、单路放大及返回，复用正式演示结果包。它不运行 FAST.Farm，不更新用户配置，不构建或安装扩展。

## 产品入口与状态

- `wfrl.native_camera_view(mode='TRIPLE', layout='GRID')`：C1 / C2 / C3 与静态安装示意；`layout='STRIP'` 保留三列对照。
- `wfrl.native_camera_focus(slot=2)`：同一窗口内放大 C2；`native_camera_view(mode='TRIPLE', layout=...)` 返回三路。相机参数、场景帧与播放状态不由布局操作重设。
- 三个相机区域仅拟合既有标定画幅，保持完整视场与比例。窗口尺寸不足时会保留边缘空白，不能通过裁掉视场填充区域。
- 第四格是当前场景机舱、盒体和支架网格的静态几何投影，有总览及局部图；不创建第四路相机或持续离屏渲染。动画不使其失效；安装参数、安装对象局部变换、实际布局导入及恢复后先显示失效提示，再刷新。相同配置的导入沿用核心模块已有的无变化跳过行为。
- 观察窗口中的播放按钮与 Space 绑定 `wfrl.native_camera_play`，由原主窗口维护场景播放；原图采集恢复也返回该播放上下文。程序退出在活窗口内读取实际播放状态，必要时交接到幸存主窗口。OS 关窗不使用过期播放缓存自动恢复，避免“刚暂停后立即关闭”被误恢复为播放。

## 执行

从仓库根目录运行，使用独立且尚无 `result.json` 的输出目录。以下是 macOS 示例；实际 Blender 路径按本机调整。

```sh
BLENDER_USER_CONFIG=/tmp/wfrl-layout-config \
WFRL_TEST_OUTPUT=/tmp/wfrl-layout-check \
/Applications/Blender.app/Contents/MacOS/Blender \
  --factory-startup --python-exit-code 1 \
  --python blender_frontend/tests/blender/frontend_layout_regression.py
```

`WFRL_ADDON_ROOT` 可指定源码或扩展导入根目录，`WFRL_ADDON_MODULE` 可指定安装命名空间，例如 `bl_ext.user_default.wfrl_blender`。脚本仅改变导入来源，不执行安装动作。默认参考帧为 1，可用 `WFRL_LAYOUT_REFERENCE_FRAME` 指定其它暂停参考帧。三列与 2×2 始终使用同一个观察窗口和同一暂停时刻。

本机 Retina 环境中，不加 `--window-geometry` 的工厂启动得到 820×473 逻辑观察窗口及约 1640×946 像素截图。命令行 `--window-geometry` 的数值不可直接当作逻辑像素；应以 `result.json` 中实际 `window`、`area`、`region` 和 `gate` 值核验比较条件。

宿主机补充检查：

```sh
PYTHONPATH=blender_frontend:. python -m pytest \
  blender_frontend/tests/test_installation_schematic.py \
  blender_frontend/tests/test_custom_camera_capture.py -q
```

## 输出与判定

`result.json` 记录完成状态、实际检查、模块、Blender 版本、窗口及每路有效画幅尺寸、画幅比例和同条件面积比。四张截图为 `strip-paused.png`、`grid-paused.png`、`focus-c2-paused.png`、`grid-playing.png`。正常原图采集子目录包含三路 1920×1080 PNG、来源时间、同一场景状态、内外参与清单；取消与故意渲染失败分别保留未完成清单。

回归覆盖配置编辑/导入/恢复后的静态示意失效、动画期间静态缓存稳定、暂停与播放中的放大/返回、正常/取消/故意失败的采集恢复，以及主窗口/观察窗口播放后的程序退出、产品播放后的 OS 关窗、主动暂停后不等监视定时器立即 OS 关窗。Space 检查包括绑定及对应产品 operator，不等同于物理键盘验收。

播放推进断言使用 `time.monotonic()` 确保真实等待。不能把 Blender timer 回调返回的间隔视为一定已经过去的墙钟时长：当同一回调包含较长的 PNG 采集时，下次回调可能早于这一直觉。`animation_cancel()` 可能返回 `PASS_THROUGH` 同时成功停止；判定应检查实际播放状态与帧，不能只解释返回值。

本脚本不测整段播放 FPS，不证明显示器实际呈现或实物安装可行性。任意外部脚本若直接从子窗口调用 Blender 原始 `screen.animation_play()`，会绕过产品播放上下文；其 OS 关窗行为需另测。程序退出路径包含这种原始播放调用的恢复检查。旧短跑中的提前断言不得单独归因为产品停播，恢复逻辑必要性需使用同一修正后驱动做对照。

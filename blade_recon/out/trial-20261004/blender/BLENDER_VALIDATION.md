# Blender 5.2.1 后台验证（2026-10-04）

本次仅使用 `/Applications/Blender.app/Contents/MacOS/Blender` 的独立 `--background --factory-startup` 进程。没有渲染、编码视频、打开或修改用户前台 Blender；没有修改 `blade_recon` 根目录交付源码。

## 输入与状态

- `compatibility_fixture.json`：仅兼容性 fixture，复制 `data/synth20/truth.json` 中原始帧 0、3、7、13、19 的状态；每片叶片设置人工 40 长度交替 0/1 的 observed_sections。**不是重建结果或评分证据**。
- 真实结果：`out/trial-20261004/synth20/recon.json`，共 200 帧。
- 原版 `blender_view.py` 本身成功运行，只有 `Material.use_nodes` 的未来 Blender 6.0 弃用警告，未抛异常。

## 原版验证

- fixture 创建三片重建叶片、三片真值虚影、塔筒/机舱/轮毂、三台相机。
- 每片重建叶片 1280 顶点、2526 三角面；5 个 fixture 帧的回调可更新顶点。
- fixture 顶点相对 `Rotor.forward` 最大绝对误差 `7.62494e-6 m`；真值虚影按原始 frame 编号正确选帧。
- `evidence` 是 `POINT / BYTE_COLOR`。人工可见/不可见分段分别映射绿色/橙色；BYTE_COLOR 的量化误差约 0.003，属于颜色存储精度。
- 真实结果抽检 Blender 时间轴 frame 1、51、101、200（原始 frame 0、50、100、199）：几何、真值虚影和 observed_sections 颜色均通过；最大顶点误差 `7.62692e-6 m`。
- 这些抽检帧中，Blade1 全橙和实际 JSON 一致；这不是全部 200 帧一直无观测，完整 JSON 中三片叶片都有 observed_sections 非零的帧。

## 原版相机问题与隔离修正

1. `blender_view.py:103` 的 `ob.matrix_world = [list(r) for r in M]` 在当前 Blender 中导致旋转转置、相机 location 为零。最小复现对两种赋值均显式执行 `bpy.context.view_layer.update()`，排除了未求值缓存原因。原版反导出 T 最大绝对误差 `65.7939769`。
2. 同一最小复现改用 `mathutils.Matrix(M.tolist())` 后，保留正确相机中心，T 最大误差降至 `6.85870e-6`。
3. 原版未设置场景输出分辨率，factory-startup 默认为 1920×1080，而本次 cameras.json 为 816×636。
4. 原版 `add_cameras` 仅从 fx 设置视角，忽略 fy；本次 fx=`874.95882357`、fy=`873.69781939`。改正分辨率后有效 fy 仍约 `874.95867920`，差 `1.26086 px`。原版 `blender_export_cameras.py:44-48` 同样将 fy=fx，反导出差 `1.26100 px`。这是内参近似限制，与外参问题分开。

`compat/blender_view_compat.py` 是输出目录内的 wrapper，读取根目录原版脚本，在运行时只把相机矩阵赋值替换为 `Matrix(M.tolist())`，并设置本次相机 JSON 的 816×636 输出分辨率；选择 C2 作为活动相机。根目录原版文件不变，fy 近似限制保留。

兼容版使用真实 200 帧重新验证 frame 1、51、101、200。几何/颜色仍通过，相机 T 在 `frame_set` 与显式 depsgraph 更新后通过，原版导出器反导出 T 也通过：三台相机最大绝对误差 `6.85870e-6`，相机中心最大误差 `1.83498e-6 m`。K 精确匹配检查仍为 false，原因是上述 fy 近似。

## 保存文件与播放

- `synth20_original_view_validation.blend`：原版导入的 frame1 快照，保留原版相机问题，用于复核。
- `compat/synth20_compat_view_validation.blend`：兼容版 frame1 快照，活动相机 C2，可用于检查几何和相机位置。
- **保存 .blend 不会保存本次 Python frame_change_pre 回调**。兼容版 .blend 的独立后台重开检查确认 handler 数量=0，frame1→51 的网格变化=0。因此单独打开 .blend 是静态快照；动画播放需要重新运行 wrapper。

从工程目录重新加载播放（该命令会在它启动的 Blender 场景里按原版流程清空并重建对象；本次仅提供命令，没有启动前台）：

```sh
cd 'WORKSPACE_ROOT/blade_recon'
'/Applications/Blender.app/Contents/MacOS/Blender' --factory-startup \
  --python out/trial-20261004/blender/compat/blender_view_compat.py -- \
  --recon out/trial-20261004/synth20/recon.json \
  --truth data/synth20/truth.json --cameras data/synth20/cameras.json
```

后台重复验证：

```sh
cd 'WORKSPACE_ROOT/blade_recon'
'/Applications/Blender.app/Contents/MacOS/Blender' --background --factory-startup \
  --python-exit-code 10 --python out/trial-20261004/blender/compat/validate_real_compat.py
```

## 证据

- `original_view.log`：原版 fixture 直接启动日志。
- `validate_blender.py`、`compatibility_harness.log`、`compatibility_results.json`：fixture 几何/颜色/原版相机检查，以及独立 K 诊断。
- `matrix_assignment_diagnosis.json/.log`：嵌套列表与 Matrix 赋值最小复现。
- `real_reconstruction_results.json`、`real_reconstruction_harness.log`：原版真实结果抽检。
- `compat/real_reconstruction_compat_results.json`、`compat/real_reconstruction_compat_harness.log`：兼容版真实结果抽检与相机比较。
- `compat/cameras_roundtrip_compat.json`：原版相机导出器对兼容版场景的反导出。
- `compat/saved_scene_reopen_probe.json/.log`：保存场景重开与回调不持久化验证。

本次结果验证脚本兼容性与 JSON→Blender 几何一致性，未测实时 FPS、显示器输出、渲染外观或真实风机重建精度。

# Part 2 · 离线 Blender 原型

> 状态：`ACCEPTED`。用户已在任务 `01a07219-343d-7152-a302-250fe6077104` 中确认视觉可接受；2026-09-06 记录于此。冻结资产见 [part2-baseline.json](part2-baseline.json)。

本阶段交付为 Blender 5.2.1 的离线场景与 66 秒可操作 Demo。所有功率、动作、尾流和事件均为 `SYNTH`；没有启动 FAST.Farm、FLORIS、MPI、训练或 checkpoint。后端集成留在后续阶段。

## 打开

macOS 双击项目根目录 **Open WFRL.command**。它用 `/Applications/Blender.app` 打开已保存的 WFRL 工作区，且不修改全局 Blender 偏好。可以用 `WFRL_BLENDER` 环境变量指定其他 Blender 可执行文件。若从 Codex 的 `seatbelt` 沙盒运行，入口会在启动前停止并提示改从 Finder 或普通 Terminal 打开；这是为了避开 Blender 5.2.1 的 Metal 探测崩溃，不是对 Blender 本体的修改。

也可以在 Blender 的 Preferences → Add-ons → Install from Disk 安装当前构建的 `dist/wfrl_blender-0.2.0.zip`，启用后打开 `evidence/part2/wfrl_part2_static.blend`。只双击 `.blend` 而未启用扩展时，烘焙动画和场景仍在，但交互控件和自绘图表需要扩展。独立安装后，在 3D 视图按 N，展开 Item/条目中的 WFRL / PRESENTATION，点击 Load Demo Scene。

## 演示

1. 点击 **Start Demo**：从 READY 开始，4 秒完成启动，之后偏航展示，最后 10 秒顺桨减速，66 秒停在 STOPPED。
2. **Pause → Resume** 保留帧位置；暂停时 **Step** 前进 0.04 秒。
3. **Stop & Feather** 跳到安全的展示终态；**Reset to Ready** 返回起点。
4. 展开 **Views & Layers**，切换 World、Top、Side、T1 Rotor Close-up、T1 Nacelle Sensor；World + Nacelle View 创建原生双视图。
5. 切换 Wake proxy 和 Lidar rays 会真的改变对应对象可见性。Channels 中关闭遥测后，采样时间和图表冻结。
6. 暂停后在 Manual Pose 中调整选中机组的 yaw/pitch；手动姿态没有对应功率模型，因此面板显示功率不可用。Resume 恢复确定性脚本。

启动前可花 10–15 秒说明场景、来源和入流，再播放 66 秒序列，总演示约 80 秒。

## 几何与视觉

- 复用本地 FAST.Farm 模板的 19 个叶片截面、8 个翼型和 12 站塔筒数据；不是上一版的矩形叶片或手绘后掠轮廓。
- 直径 126 m，塔顶 87.6 m；上风向悬伸、轴倾角、预锥角和独立变桨轴保留。机舱尺寸取本地 `wfrl/turbines/nrel5mw.yaml`。
- 导出资产随 ZIP 打包，包含源文件 SHA256；扩展运行时不导入后端、NumPy 或 PyVista。
- 按用户反馈移除“铁环”式尾流，改用边缘渐隐的展示体积和细流线。这是说明性的代理，既没有运行 FLORIS，也不代表 DisXY。
- 机舱传感器镜头是真正挂在 YawRoot 下的向下几何视野；外部轮毂近景使用另一个相机，名称不混用。
- Overview 镜头按三机完整转子包络构图，避免裁掉 T3；渲染图片持续带 SYNTH 注释。
- 工业石墨底色与白灰实体；自绘状态、场景摘要和功率曲线配合原生操作面板。原生字体与控件沿用用户 Blender 主题。

## 验证与边界

运行 `python3 scripts/blender/verify_part2.py` 可串行复验纯 Python、真实 Blender 场景、构图、几何、Demo 工作流、打包、清单以及干净配置的安装/禁用/启用/打开工程。Blender 路径可用 `WFRL_BLENDER` 指定。

实际 GUI 的连续播放与双视图另见 `evidence/part2/gui_playback_check.json` 和 `gui_dual_check.json`。当前保留的界面截图为 `workspace.png` 和 `workspace_dual.png`；旧版 running/stopped 截图已清理，历史 GUI 记录不作为刷新后截图的替代。

本次在 Apple Silicon macOS + Blender 5.2.1 验证。目标支持 macOS 和 Windows；由于当前没有 Windows 设备，Windows 安装、运行和后端联调仍标记为未完成，不能用 macOS 结果替代。Review Fixtures 只是等待、过期、失败、checkpoint 不兼容等静态文案，不宣称真实连接或安全层已实现。训练/Replay 双轴图与真实科学图层将在相应后续阶段补齐。用户已认可本阶段视觉；该认可不代表 Part 3 或真实后端已验收。

## 本次崩溃

三份报告（2026-09-06 17:30、17:35、17:40）都显示同一个启动期 SIGSEGV：`supports_barycentric_whitelist → MTLBackend::metal_is_supported → GPU_backend_type_selection_detect → WM_init`，均发生在 Python 脚本和 `.blend` 文件之前。项目入口现在会在 `CODEX_SANDBOX=seatbelt` 时先退出，不再盲目启动；分析和逐份对比保存在 `evidence/part2/CRASH_DIAGNOSIS.md`。这项保护不能修复 Blender/Apple Metal 本体，正常权限下仍需由用户从 Finder 或普通 Terminal 启动。

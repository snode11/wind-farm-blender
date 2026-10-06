# 独立原生显示回归

已完成本run两包各77项软件检查：8采样frame42–49对应121.2–121.9 s均由真实viewer `_tick` 提交，三个实际float32 mesh逐值等于冻结Rotor.forward，UV固定；packed atlas、保存重开、坏model SHA sidecar在场景分配前拒绝通过。状态为REVIEW_ONLY，不是几何精度验收。

实际运行来源为 `frozen_source/wfrl_blender/blade_recon_surface.py`/`blade_recon_data.py` byte-identical工作区副本，独立 factory 配置，未注册或覆盖日常扩展。原reader会拒绝新recon缺失旧schema的observed_sections；仅本目录driver加载 `scripts/legacy_observation_adapter.py`，在内存深复制中将缺失元数据补全0unknown，原recon字节、所有state、UV、几何不变。保存重开继续同driver/adapter；不能声称未改产品reader直接兼容或日常安装版PASS。

原局部保存文件 [A1/A3 同刻静态比较](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-surface-20261006-exec-a01/validation/display/comparison/A1_A3_1212s_static_comparison.blend>)，独立CUA核对了真实AX标题/文件URL及像素。[窗口证据](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-surface-20261006-exec-a01/validation/display/comparison/independent_visible_window.png>) 显示两个同121.2 s输入标定视线、RGB点与橙unknown/紫conflict，无启动弹窗。显示平移仅y±82 m，重开后六个对象局部顶点仍逐值等于各自冻结首state。场景为1–1静态；原input画面裁切范围保留，下方叶尖原本出框的部分未追加成新观测。

初始自动window截图的Quick Setup遮挡、重开早截图灰屏、原readerschema拒绝、adapter import错误、open_mainfile context瞬态None及第一次放大后图例重叠/裁切全部保留，不算最终可见性通过。完整数据、资源、边界、失败记录在 [外观报告](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-surface-20261006-exec-a01/appearance/REPORT.md>)。


完整取景交付为 [comparison_full_frame_v2.blend](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-surface-20261006-exec-a01/validation/display/comparison/comparison_full_frame_v2.blend>) 和 [v2原生窗口截图](</Users/eason/Desktop/wfcrl/wind farm RL/outputs/blade-surface-20261006-exec-a01/validation/display/comparison/v2_independent_visible_window.png>)。root独立复看确认两侧三片全部端点在frame内，RGB/unknown/conflict及同121.2 STATIC/REVIEW_ONLY标签可读，复核PASS；已查看截图SHA记录于 `comparison/v2_visible_ui_review.json`。v2仅改显示camera距离/lens/shift与文字布局，原比较file、六mesh、UV和纹理不变；原154项不重跑。显示取景不是数值评价camera，也不是geometry alignment。

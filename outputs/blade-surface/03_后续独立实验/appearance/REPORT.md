# 本 run 外观与显示交付

状态：COMPLETED_SOFTWARE_APPEARANCE_AND_DISPLAY / REVIEW_ONLY。两包原像素证据与冻结几何校验通过；两包各77项原生检查通过；最终保存重开的静态比较已由独立 CUA AX/像素检查确认可见。现有产品 reader 对新recon的直接兼容失败，需本run明确的内存 unknown adapter，未改产品。

本实验只把开发8帧的 WideInput 原始 RGB 绑定到已冻结几何，A2_new 继承 A1_new 的 recon 原字节，A3_D12 外观包继承其自己的冻结 recon 原字节。纹理不参与优化、不会改变状态，不据此宣称几何收益、完整表面观测或健康诊断。几何数值结果属于 root 独立评价。

输入为121.2–121.9 s、frame42–49、3840×2160；逐帧显式内参/模型到相机变换与 RGB-derived mask 已复制到本目录。该 mask 是 RGB 连通占据 proxy，可包含相连 hub/tower，并非完美 instance segmentation。提取只用其非零区域与原图整幅 polygon 的交集；模型自身朝向与最近射线校验进一步限制候选支持，这种模型可见性也不等同真实材质可见性真值。

冻结小 atlas 为每叶片256×128，三片叶片；材料坐标 q、三角形及重心坐标来自公开健康模板。采样直接读取原图整数像素，无缩放。多帧支持采用固定 q 的观察并集，选择最接近 RGB 中位数的一个真实原像素，不创造平均颜色。RGB 欧氏冲突阈值60，超过阈值的多帧支持为紫色 conflict；无接受输入支持为橙色 unknown，source index/xy 保留-1。端盖没有 chart，保持 unknown。灰色重复并不证明材料身份或绑定准确。

复用 surface_texture.py/surface_io.py；本 run 副本仅添加8行显式 mask hash/shape/交集读取。产品代码和旧包没有改动。source/tree、private、WideEval 都在 OS sandbox 的默认 wfcrl 拒读区；Python 审计同时使用显式路径白名单。provenance 保留复制清单、模块 SHA、OS 真路径1 byte拒读探针与实际读取审计。

原生验证从 byte-identical 的 workspace viewer 源码副本加载，独立 factory 配置，不是日常安装版验证。检查全8采样时刻真实 timer 调用后的实际 float32 mesh 与冻结 Rotor.forward 输出逐值相等、逐帧固定 UV、packed atlas、保存重开恢复、坏 sidecar 在场景创建前拒绝。同刻静态比较使用121.2 s；对象布局的 y±82 m 纯显示平移，局部顶点不变，不用于评价。渲染图不替代可见原生窗口截图。


## 实际结果与资源

每包的98304个侧面 atlas texels（3×256×128）分别统计如下；这是 chart 离散采样状态，既非完整表面覆盖率，也非几何误差。未绘制 chart 的端盖继续 unknown。

| 包 | captured RGB | conflict | unknown | 继承 recon SHA-256 |
|---|---:|---:|---:|---|
| A2_new | 44406 | 2090 | 51808 | ae35c7dffa68b38982008dc9ce45a2eaa632ef95a343c2ca58755b6c8ba09257 |
| A3_D12 | 44663 | 1863 | 51778 | af33d2fde18e2836447b43f6d2f54b3501c27700842c7eab32fe8ec6f2ef6eec |

两组共享的 UV 数组 SHA 为 `7ef365683bdfa7189f127f360709fb70d81060ce6baab5fc1fd44af51106a344`。`package_checks.json` 已逐项验证原RGB取值、所有已选证据像素落于对应非零mask、unknown的source index/xy为-1、两份recon原字节相等。`package_asset_sha256.json` 的提取资产在显示之后再次核对，变化0。

A2/A3 CPU提取分别124.6524/125.1130 s，峰值RSS236896256/224198656 bytes；两任务并发，包含读图、模型射线、压缩、完整包校验，不能当单线程标准性能或实时FPS。原生8时刻脚本加载后的总时长约6.3574/6.0244 s，单步后由实际 `_tick` 提交frame1–8，保存重开后又提交frame1。每包77检查全部通过。独立静态重开峰值RSS约520.6 MB，wall53.55 s含review等候；GPU峰值、显示刷新率和连续实时播放性能未测。

11份已保存实际读取审计共1404 events、禁止读取0；OS sandbox分别拒绝真实 source .blend、副本绑定 JSON、新heldout WideEval PNG 的1 byte open探针，全部EPERM。两次早期启动异常未进入audit flush，只有stderr记录；不把它们算作完整Python审计，OS白名单仍在该实际启动命令中生效。允许输入的复制是准备阶段明确逐文件操作，并单列 `input_copy_audit.json`。

## 原生交付与失败保留

原局部版本文件：`../validation/display/comparison/A1_A3_1212s_static_comparison.blend`；独立保存重开校验 `comparison/independent_reopen_checks.json`，独立 CUA 可见性记录 `comparison/visible_ui_review.json` 和截图 `comparison/independent_visible_window.png`。同一个原始标定视线分别平移到两个显示对象组，固定121.2 s；它保留原WideInput的画面裁切范围，下方叶尖超出原画面的部分仍在camera frame外。场景帧范围为1–1，源包各自仍有完整8采样。

显示代码使用 byte-identical workspace `blade_recon_surface.py`/`blade_recon_data.py` 与同SHA健康model副本，实际module path记录于各结果。`scripts/legacy_observation_adapter.py` 只在 `ReconSequence.from_data` 的内存深复制中为缺失字段填全0unknown，保存嵌入的recon文本仍是原字节，状态/mesh/UV不变。带adapter的source模块验证并非日常已安装扩展验证，也不能称未改产品reader对新schema直接PASS。

所有失败保留：`A2_window_failed_schema01/native.log` 是原reader拒绝缺失observed_sections；`A2_window_failed_driver_import02/native.log` 是首次独立adapter目录未加入sys.path；`comparison/independent_reopen_failed_context01.log` 是open_mainfile同调用期context.window=None，已分到下一timer阶段；`comparison/failed_layout01/` 保存放大后裁切/图例重叠的截图与失败decision。两组初始window截图有Quick Setup遮挡，重开早截图呈灰色未刷新，均保留，不能替代最终CUA可见性通过。

外观包的RGB和紫色conflict已显现，但色纹变化也可能来自估计几何绑定偏移/图像外观变化；这个展示不判定修补、缺陷几何、全局材料重识别或现场健康。几何收益只由root固定协议的独立数值评价判断。


## 完整取景 v2 与 root 独立复核

root 独立复看指出原输入camera frame裁切底部叶片，因此原 `A1_A3_1212s_static_comparison.blend` 和 `independent_visible_window.png` 仅记可读局部图，不称完整构图。另存 `comparison_full_frame_v2.blend`：仅显示相机沿原朝向后退65 m、修改lens/shift按冻结mesh取景（各自90%frame内），调整显示文字；模型/UV/纹理及原比较文件都未改。重复核对六个局部mesh逐值等于冻结121.2 s状态、UV hashes不变，并保存重开。

v2输出为 `../validation/display/comparison/comparison_full_frame_v2.blend`，截图 `v2_independent_visible_window.png`，两份1920×1080静态render `v2_A2_full_render.png`/`v2_A3_full_render.png`。child CUA 核对AX精确文件标题/URL和像素，root随后独立查看同一截图，确认两组每组三片叶片全部端点在frame内，RGB/unknown/conflict及同121.2 s STATIC/REVIEW_ONLY标签可读，记录PASS_READABLE_FULL_FRAME_REVIEW_ONLY。该相机是display only，不用于原像素误差、几何评分或对齐。

仅后退相机的距离草稿会露出邻组片段，保留于 `v2_distance_only_draft/`；正式v2通过lens/shift完整取景，没有隐藏任何模型或更改状态。v2脚本wall约59.976 s、峰值RSS645513216 bytes，包含等待CUA复核，并非性能benchmark。没有重跑原两包154项八帧检查。

root已独立查看的v2截图SHA-256：`c96b163ff9d4d7dbb8a18996280fd7d43c7e7b32e6ee2410978bc60d712ee691`。

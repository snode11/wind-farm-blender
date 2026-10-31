# 相机、资源与重建

用于 Down、机舱相机、C1/C2/C3、预览与采集、资源释放、重建纹理及合成缺陷任务。模块路径以 `blender_frontend/wfrl_blender/` 为基准，“仓库”路径从 Git 根目录解析。

## 模块与合同入口

| 功能 | 主要模块／仓库入口 | 专题 |
| --- | --- | --- |
| 重建对照与纹理 | `split_reconstruction*.py`、`split_turbine_context.py`、`blade_recon_*.py` | [操作说明](../../../../前端readme.md)、[Blade Recon](../../../../docs/blender/BladeRecon可视化使用说明.md) |
| Down、机舱与三相机 | `cameras.py`、`nacelle_camera.py`、`stacked_camera_rig.py`、`custom_camera*.py` | [Down](../../../../docs/blender/T1Down机舱下视.md)、[机舱](../../../../docs/blender/NREL机舱相机.md)、[三相机](../../../../docs/blender/T1三相机使用说明.md) |
| 缺陷编辑与分析 | `nrel_defects/`、`repair_marks.py` | [NREL 缺陷编辑器](../../../../docs/blender/NREL缺陷编辑器.md) |

## 光学、预览与资源生命周期

- Down 随各自机舱／塔顶运动，不随叶轮或锁定世界重力；手动 Down 绑定条目 `yaw_object`。
- Down、独立机舱相机与 C1/C2/C3 分别维护光学和复位规则；三相机按真实槽位路由、共用时钟，支架端贴合，不追踪叶片或强制三等分。
- 观察视口关闭画幅外遮罩和额外画幅边界；文件加载、扩展注册及切换视图均不得重新启用。相机真实 FOV、内参与导出画幅分别保留。
- 相机编辑预览的播放画质与 HUD 读取 `playback.is_playing(context.scene)`，不借另一场景的播放状态。
- 宽／高画幅预览按与正式分辨率相同的整数舍入保证短边至少 16 像素，仍受 GPU 上限约束；不裁画幅，不改 FOV、正式采集分辨率或原画质。
- 替换相机须事务重绑场景、局部视口与退出快照，保留忙时保护、撤销及失败回滚；复位保留时刻、播放和自由视图。
- 原生观察代理通过约束跟随姿态；姿态、输出尺寸和记录焦距不触发代理重建或取景复位。来源、FOV、裁剪距离和场景画幅比例变化重新校准代理，保持相机／自由视角模式；会话释放后停止巡检，资源清理失败仍保留重试。
- 相机 Object 的外部 Scene 直接引用、对象成员与外部／无归属局部视口在发布前拒绝；独立 Object 可共享 Camera DATA，保留其名称与参数。
- 预览失效回调须跨文件加载保留、重载不重复、卸载无残留。
- 保存写真实槽位与原画质，保留局部镜头，失败也恢复观察状态；采集中异常或原生修改须中断并保留用户修改。
- 关闭只释放自有资源，容忍删除的 RNA，失败可重试；保护共享、fake-user、shape-key 及无关数据。

## 外形、重建与合成缺陷

- rootkink 为合成展示外形，沿用 NREL 运动、载荷与测量；切换结束冲突会话，网格与共享引用共同提交或回滚。
- 重建右侧只有叶片是估计，塔筒、机舱和轮毂是共享仿真结构，随源姿态更新并核验绑定。
- 同源纹理绑定 manifest、模型、摘要与保存样本时钟；独立 `synth_tex` 来源和游标分开。
- 播放、切模式及恢复保留手动视角，增强不生成未观测细节或检测结果。
- 便携场景保留 packed 图片、无 linked libraries，以 `__WFRL_BUNDLED_MAPPO__` 和 manifest SHA-256 识别内置源；只恢复同哈希包，不匹配清空报错，不任意回退或使用不支持的 `//` 路径。
- 缺陷是 `physics_coupled=False` 的 NREL 合成几何，不直接套用 GW184；预览不写提交配置，删除辅助轮廓不删缺陷。
- G 分析冻结完整三相机配置，变更后重新冻结。
- 保留完整画幅与真实遮挡；入框、射线可见、全周期无遮挡、图像检测、拼接及实体标定分别判断。

[动态研究查看器](../../../../wfrl/nrel_reconstruction/README.md)可能使用冻结源码，不随工作区修改更新；[GW184](../../../../projects/gw184-single/README.md)使用独立模型与配置，不混用 NREL 身份。

## 可观察的验收

涉及取景、绑定或运动的修改，按相关用例选择多个关键姿态，记录场景／对象／相机槽位、时刻、FOV、画幅和观察结果，配合结构化状态与所需视觉证据。后台投影与几何检查、可见窗口、GPU 原图采集分别验证；发现显示或遮挡问题时保留输入及实际失败，不依据截图推定实体标定或图像识别效果。

## 按行为选检查

宿主文件位于 `blender_frontend/tests/`，原生脚本位于 `blender_frontend/tests/blender/`；标为“仓库”的路径从 Git 根目录解析。表格用于按行为定位；脚本模式、输入、输出、超时及完成条件以[验证入口](verification.md)和脚本本身为准。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 重建、纹理与共享结构 | `test_split_reconstruction.py`、`test_split_mappo_texture.py`、`test_mappo_texture_package.py` | 后台 `split_turbine_context_regression.py`；窗口 `reconstruction_workflow_window.py`。 |
| 相机预览与宽／高画幅 | `test_camera_projection.py`、`test_custom_camera_preview.py` | 宿主检查场景播放归属、短边下限及 GPU 舍入边界；后台 `live_contracts_regression.py` 核对预览尺寸、FOV 与正式输出保持，不测 GPU 像素或 FPS。 |
| 机舱相机与 Down | `test_camera_projection.py`、`test_nacelle_visibility.py` | 后台 `nacelle_camera_intrinsics_regression.py`、`farm_down_mount_regression.py`、`t1_down_mount_regression.py`、`nacelle_camera_regression.py`；仓库 `scripts/blender/check_nrel_nacelle_camera.py` 做投影、射线与渲染检查。 |
| 三相机布局与绑定 | `test_stacked_camera_rig.py`、`test_custom_cameras.py`、`test_custom_camera_v3.py` | 后台 `three_camera_default_regression.py`、`custom_camera_edit_binding_regression.py`；按钮交互另验。 |
| 相机窗口、释放与采集 | `test_native_camera_lifecycle.py`、`test_custom_camera_retirement.py`、`test_custom_camera_capture.py` | 后台 `custom_camera_retirement_regression.py`、`custom_camera_capture_regression.py`；窗口 `native_camera_enabled_regression.py`、`frontend_layout_regression.py`、`custom_camera_capture_window.py`、`native_camera_save_regression.py`。 |
| 非手动 FarmFlex 序列事务 | `test_custom_camera_capture.py` | 后台 `farm_capture_transaction_regression.py` 使用真实 Scene／Window／ViewLayer 与回放、图像、播放适配器检查上下文、时间映射、清单和恢复保护，不证明 GPU 像素或原生播放。 |
| 同源纹理失败资源归属 | `test_split_mappo_texture.py`、`test_split_reconstruction_ui.py` | 后台 `split_mappo_texture_rollback_regression.py` 检查分配与激活失败、共享／用户保留资源、Scene 生命周期及完整样本重试；不证明渲染像素或性能。 |
| 原生观察代理与姿态 | `test_native_camera_lifecycle.py` | 后台 `native_camera_proxy_regression.py` 使用真实相机和约束、视口适配器核对身份、姿态、光学失效和释放；窗口 `native_camera_proxy_window.py` 核对定时更新、取景、光学变化、短播放与清理重试。两者支持模块及精确目录校验，均不证明完整片段 FPS。 |
| 相机加载与场景共享 | `test_custom_camera_retirement.py`、`test_custom_camera_preview.py` | 后台 `custom_camera_load_invalidation_regression.py` 检查真实文件加载、通知、几何指纹与适配渲染请求；`custom_camera_shared_object_regression.py` 检查跨场景 Object 拒绝及独立 Object 共享 Camera DATA。两者只接受新输出目录，不验证 GPU 像素或窗口操作。 |
| 缺陷编辑与播放 | `test_nrel_defects.py`、`test_nrel_editor_playback.py` | 后台 `nrel_editor_resource_regression.py`、`nrel_defect_editor_regression.py`；窗口 `nrel_defect_window_regression.py`、`nrel_editor_playback_regression.py`。 |

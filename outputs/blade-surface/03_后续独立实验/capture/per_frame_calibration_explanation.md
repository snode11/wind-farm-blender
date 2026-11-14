# 已知相机为何具有逐帧 MODEL→CV 外参

本次捕获的 MODEL 光心与瞄准点是固定设计值，每帧仍按这些值放置相机；完整 `T_cv_from_model` 是从该帧实际 Blender `matrix_world` 计算的已知标定，允许随帧变化。`capture_configuration_freeze.json` 中 `camera_MODEL_centers_m` 与 `aim_MODEL_m` 描述光心/瞄准设计，没有声明 MODEL→CV 完整旋转矩阵静态。这些值也不是仅创建时使用：`capture_distributed.py:99` 在每一帧重新使用它们。

这份说明只读代码、许可公开标定与既有 source 副本的相机层级；没有读取几何评分、拟合结果、材料绑定或源顶点，没有渲染、回放更新或保存场景，没有修改 schema、公开标定或冻结算法。父 agent 已接受：每帧 T 必须与该窗 capture-approved public calibration 精确匹配，不以开发首帧 T 代替其他时刻。

## 实际转换

本 run 没有独立命名为 `_calibration` 的函数；标定逻辑在 `capture_distributed.py:86–101` 内联实现，延续旧 `capture_wide.py:95–100` 的转换。

用列向量齐次坐标表示：

```text
F_model = diag(-1,-1,+1,+1)，平移列为 (0,0,-TowerHt)
M = T_world_from_model = YawRoot.matrix_world @ F_model
world_C   = M @ (100,0,90,1)                 # WideInput
world_aim = M @ (5,0,90,1)
camera.rotation = (world_aim-world_C).to_track_quat('-Z','Y')
M_cam = camera.matrix_world                  # Blender camera local → World
F_cv = diag(+1,-1,-1,+1)                    # CV 的 +Y 向下、+Z 向前
T_cv_from_model = inverse(M_cam @ F_cv) @ M
p_cv = T_cv_from_model @ p_model             # 使用一次
```

`YawRoot.matrix_world` 包含父 `WFRL.Turbine.T1` 的世界布局及当前支撑/机舱姿态。`F_model` 将机舱局部的 +X downwind 转为 MODEL 的 +X upwind，保持右手系，并以 tower-height 平移形成本 run 的 moving MODEL 原点。`F_cv` 将 Blender 相机的 local −Z 看向前、+Y 向上转成 OpenCV 相机约定。public 已经导出组合后的 MODEL→CV；求解不再乘源 rig、World 或机舱变换。

K 由固定 lens/sensor、3840×2160、单位像素纵横比计算；主点为 `((W-1)/2,(H-1)/2)`，原图 crop/resize 为单位变换，因此本开发窗 K 完全相同。

## 父子关系与动态 roll 的来源

实际保存副本（development_heldout_pair，Blender frame295）只读检查得到：

| 对象 | 实际 parent | 捕获用途 |
|---|---|---|
| `Experiment.WideInput` | null | 本 run 的 WideInput；无 constraint/animation data |
| `Experiment.WideEval` | null | 本 run 留出相机；无 constraint/animation data |
| `WFRL.Camera.T1.NacelleGimbal` | `WFRL.Turbine.T1.YawRoot` | 原场景机舱相机对象；本 run WideInput 使用上面的 Experiment 对象 |
| `WFRL.Turbine.T1.YawRoot` | `WFRL.Turbine.T1` | MODEL 所依据的动态机舱根 |
| `WFRL.Turbine.T1.Rotor` | `WFRL.Turbine.T1.YawRoot` | 源场景既有转子层级 |

新 Experiment camera 在 `capture_distributed.py:80–82` 创建并链接到 scene collection，没有 parent。它的世界光心和世界看向方向由 M 每帧显式设置，故平移与看向方向跟随机舱；朝向使用 `to_track_quat('-Z','Y')` 在 World 中重新构造。这个构造让 camera local +Y 沿 World +Z 在视线正交平面内的投影，而不是把固定 MODEL 相机完整旋转矩阵直接左乘 M。

`farm_flex.py:441–456` 先更新偏航/转子/桨距；当 tower_motion 存在时，再将插值的 nacelle 支撑完整旋转与位置写入 YawRoot。实际保存副本的 YawRoot pitch/roll 均非零。随着 moving MODEL 的支撑倾斜变化，World-up 定义相对于 MODEL-up 改变，所以相机相对 MODEL 的 roll 可以变化；光心和瞄准点的 MODEL 设计值仍保持。

保存副本中，camera local +Y 与投影 World +Z 的点积为 `0.9999999773`；从当前世界视线重建同一 quaternion，与保存 camera 旋转矩阵的最大元素差为 `1.38e-7`。该检查验证实际采用上述 World-up 朝向规则，未运行 addon 注册或 FarmFlex 更新。私有检查记录在 `private_evaluation/calibration_explanation/saved_camera_hierarchy.json`，不进入 solver/tracker 输入。

## 公开开发窗的实际差异

以下结果只由 `development_window01/public_input/calibration.json` 计算，公开原文件 SHA256 为 `b4330db07dd9350d30c6fbf668e5cf56de5e3c9c0cbac9950c3b38f8295003b9`。完整 machine diff：`per_frame_public_calibration_diff.json`。

| frame / sim_t(s) | K 与首帧完全相同 | T 最大元素差 | 相对首帧光轴 roll(°) |
|---|---|---:|---:|
| 42 / 121.2 | true | 0 | 0 |
| 43 / 121.3 | true | 0.004633169927 | 0.002957664 |
| 44 / 121.4 | true | 0.011549357215 | 0.007327723 |
| 45 / 121.5 | true | 0.020971967233 | 0.013339099 |
| 46 / 121.6 | true | 0.027486456231 | 0.017490495 |
| 47 / 121.7 | true | 0.035515804946 | 0.022595107 |
| 48 / 121.8 | true | 0.047980461162 | 0.030534644 |
| 49 / 121.9 | true | 0.059501538759 | 0.037872296 |

由 public T 的逆矩阵反算，光心与 `(100,0,90)` 的最大坐标差仅 `3.7967e-6 m`；MODEL 中光轴与 `(-1,0,0)` 的最大分量差 `2.9439e-7`，属于 Blender float 变换舍入量级。相对 roll 最大 `0.0378723°`。T 最大变化位于 `[0,3]` 即 CV x 的平移分量：齐次外参平移为 `t=-R*C`，同一个 MODEL 光心 C 在变化的相机基下会产生变化的 t。因此 `max_abs(T-T_first)=0.0595015` 不能解释为相机 MODEL 光心移动 5.95 cm，也不是叶片几何误差。

`cameras.json` 由 `records[0]['cameras']` 导出首帧摘要（driver line116）；多帧消费者应取 `calibration.json.frames[i].cameras[0]` 的该帧 K/T，不能把首帧摘要当全窗静态外参。

## 复算与身份

```bash
cd 'REPOSITORY_ROOT'
blade_recon/.venv-trial/bin/python outputs/blade-surface-20261006-exec-a01/capture/explain_public_calibration_diff.py
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python outputs/blade-surface-20261006-exec-a01/capture/inspect_saved_camera_hierarchy.py
```

第一条只读 public calibration；第二条只读私有保存副本并输出相机层级/朝向摘要，不读取 source mesh，不回放、不渲染、不保存。副本 SHA 前后均 `dce7398c2fcb612f93dcf3fa68d47a3a6f88af7406e98f7ac5310779f64a64b9`。

代码与记录 SHA256：capture driver `df81009ada5d008f47afbb80b858a3bbd8bfae7c6d30c4a20d0d980b0bbf5291`；capture 配置 `50927e5454d1f4f3c21b614beef7a3940eab73e723c19ed6ad6c8fe3e34ab5d6`；FarmFlex `3bc192697eddcd5707cb575d30296e32d28a4cb6b694388c8dd1fe2a89275d2a`；public diff `ed8ac354b017d205b8f4a6a406014b67c31db100552be240cbf0c962e7ef59bf`；private hierarchy summary `395a8878a0616980d7d92217fac4d9284bfb785671e3efc832c4453117b4d847`。

相关原码：`capture_distributed.py:80–116`；`blender_frontend/wfrl_blender/farm_flex.py:408–476`；场景 parent 建立 `scene_builder.py:176–234`；原 NacelleGimbal parent 建立 `cameras.py:492–510`。后两模块只读核对当前原码，不属于此次冻结捕获六模块依赖新增项。

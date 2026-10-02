# GW184 叶片缺陷编辑器 · 0.4.0

更新日期：2026-10-02（更新 NREL 关联入口）。按照 v1.1 开发规格实现的独立刚性场景，已随 [GW184 0.4.0 运行 ZIP](https://github.com/snode11/wind-farm-blender/releases/download/v0.4.0/gw184_three_camera_defects-0.4.0.zip)发布。v1.1 是规格版本，0.4.0 是运行包版本。它不安装或修改主前端扩展，不接入 NREL 柔性回放、雷达或 FAST.Farm；NREL 安装包已另行更新为 [0.3.13](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.13)，GW184 包保持不变。

当前提供 G 几何检查及 A 图像生成入口。**2026-09-30 获准的 18 张静态检查图已完成并作有限外观审核；第一阶段仍不能宣称整体验收通过。** 开发规格、原始检查图及验收记录保存在匹配工作区的 `docs/proposal/叶片缺陷编辑与固定相机成像验证开发规格.md`、`outputs/blade-defects/completion-20260930/检查图.html` 和 `outputs/blade-defects/completion-20260930/验收记录.md`，不随公开仓库文档或运行 ZIP 提供。

实际发布 ZIP 的检查为 47 项宿主测试通过、1 项因没有历史修改前源码目录跳过；macOS Blender 5.2.1 LTS 的 126 项六类型后台检查通过，36 条三相机 G 记录，真实渲染器调用为零。解压包的 macOS 启动器及独立窗口检查通过，确认六类型、固定相机、编辑面板、时间及近景切换和包内模块来源；不是已安装扩展、完整片段性能或实机损伤验证。Windows 启动器只有静态检查，Windows/Linux 实机尚未验证。发布范围见 [发布状态与验证范围](../../docs/blender/发布状态与验证范围.md)，开发阶段六类型结果保存在工作区 `outputs/blade-defects/four-types-20260930/验证记录.md`。

公开 GitHub 目录只同步文档；以下实现文件、配置、参考资源与测试随独立运行 ZIP 提供，自动生成的 Source code 归档不能替代该 ZIP。所有相对脚本命令从完整解压包根目录或匹配实现工作区根目录执行。工作区 `outputs/` 中的开发证据路径只用于已有工作区追溯，不作为公开下载链接。

## 打开及编辑

需要 Blender 5.2+，将 ZIP 整体解压并保持目录结构。macOS 双击 `GW184-Three-Camera-0.4.0` 根目录的 `打开GW184.command`；Windows 双击 `Open-GW184.bat`。其他安装位置可设置 `BLENDER_BIN`。这是独立项目，不使用 Install from Disk。启动器新开一个 Blender 进程，以 `--all-presets` 加载六种明确的合成示例，使用 SOLID 实体视口，不启动图像渲染。匹配源码工作区原启动器路径仍为 `scripts/blender/打开GW184叶片缺陷编辑器.command`。简明启动指南见 [GW184 使用说明](../../docs/blender/GW184三相机与缺陷编辑器.md)。

右侧 N 面板选择 **叶片缺陷**：

1. 选择列表序号，点击修改，或选择类型后新建。列表序号从 0 开始，配置中的实例 ID 不受列表顺序影响。
2. 数值输入叶片、侧别、展向、弦向、方向角与尺寸；或点击“点击健康参考表面”，在当前选择的叶片上选点。凹坑、侵蚀、涂层剥落和雷击可调边缘不规则度，雷击另可调坑口比例；纯材质类型不提供实体深度。编辑期间冻结时刻。支持域之外的点击被拒绝，不自动吸附。
3. “预览”准备临时整片叶片状态；“确认”统一提交配置、网格、材质和分析缓存；“取消”恢复旧状态。提交失败保留上次有效状态。
4. “正面近景”和“斜侧近景”只改变观察视图。C1/C2/C3 使用原固定布局。“显示辅助标记”控制中心标记与表面轮廓线，雷击另外显示坑口轮廓；这些辅助对象不参与渲染，可全部隐藏，开关不改变缺陷是否启用。
5. 复制产生新 ID，默认禁用，避免复制后立即与原缺陷重叠；调整位置后再启用。健康对照临时禁用目标叶片所有缺陷，再点击返回损伤状态。
6. 配置路径建议使用绝对路径。“保存配置”不渲染。“加载配置”显式恢复完整历史快照；全局分析版本仍递增。
7. 设置时刻后可运行当前 G 分析，设置新运行目录后保存资料。时间或配置变化使旧分析失效。扫描枚举指定范围的每个目标帧，Esc 可取消；已完成范围标为不完整。

固定相机被 Blender 原生工具手动修改后，分析会拒绝继续，须重新打开场景恢复冻结安装布局。原图不会为了缺陷而自动转向、缩放或放大尺寸。

**SOLID 轮廓线是定位和编辑辅助，不是缺陷外观图。** 实体凹陷可在该视口检查，但细裂纹、涂层露底和雷击烧蚀的解析材质不能凭辅助轮廓验收；2026-09-30 指定预设的材质外观已作有限 A 审核，修改参数后的新场景须独立检查。健康对照会隐藏这些辅助对象，返回损伤状态后按辅助开关恢复。

## 无渲染命令

从完整解压包根目录或匹配实现工作区根目录执行：

```sh
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
"$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python scripts/blender/gw184_defects.py -- --mode analyze --all-presets \
  --output outputs/blade-defects/my-first-check --time-s 100
```

生成配置、源码/素材摘要、相机与运动设置、G 可见性、投影统计、审核状态及 `.blend` 检查快照。新运行目录必须为空。`--mode prepare` 仅构建与保存，不分析或渲染。

`--all-presets` 为新文档加载六类样例；`--presets` 保留原来的两种裂纹样例，两参数不能同时使用。不传样例参数时新建空文档。通过 `--config` 或 `--reopen` 加载已有文档时，不会追加样例；即使传入的是 `defects: []` 的有效空配置，也按原配置恢复。空文件本身不是有效 JSON 配置，会报告错误。

恢复配置到可操作编辑器：

```sh
"$BLENDER" --factory-startup --python scripts/blender/gw184_defects.py -- \
  --mode edit --config outputs/blade-defects/my-first-check/defects.json
```

从检查快照恢复编辑会话：

```sh
"$BLENDER" --factory-startup --python scripts/blender/gw184_defects.py -- \
  --mode edit --reopen outputs/blade-defects/my-first-check/GW184-defect-editor.blend
```

单独双击 `.blend` 只打开静态检查快照；使用 `--reopen` 才会从内嵌配置重建编辑器及运动处理器。保存重开时核对参考几何摘要，改变模型后旧配置会返回 `STALE_GEOMETRY`，没有静默迁移。

短范围逐帧扫描及续跑：

```sh
"$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python scripts/blender/gw184_defects.py -- --mode scan \
  --config outputs/blade-defects/my-first-check/defects.json \
  --start-s 100 --duration-s 0.1 --max-frames 1 \
  --output outputs/blade-defects/my-scan
"$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python scripts/blender/gw184_defects.py -- --mode scan --resume \
  --output outputs/blade-defects/my-scan
```

续跑读取原运行的时间映射、配置及完成索引，并检查源码、素材、Blender 版本和历史记录完整性；不按本次起点重编号。Ctrl+C 或 `--max-frames` 返回已完成范围。半开区间排除终点：320 s、30 fps 共 9600 帧，320 s 为额外端点检查，不是第 9601 张普通视频帧。非整数 Blender 帧坐标保留子帧。

## A 模式入口与证据边界

`--mode images` 提供固定相机无叠加原图、1:1 无损裁切、目标叶片健康配对、正面/斜侧近景及检查灯光元数据。默认拒绝启动；只有获得对应图像渲染授权，才可使用 `--render-authorized`。`--camera C2` 可限定固定相机；不传则为三路同刻。

上一轮开发的 A 导出合同测试曾发生一次误渲染：原本用于拦截渲染器的动态 `bpy.ops` mock 未生效，生成一张临时原图并经过裁切路径。测试进程已中断，临时目录自动清理，图像未查看或用于审核。随后防护改为替换导出模块的整个 `bpy` 绑定，当轮只完成静态检查，**当时 A 导出合同测试尚未通过**。后续修复后的完整合同测试已通过，实际渲染器调用为零；授权静态图另行生成并审核。这是上一轮事故，不能与发布结果混记；历史事实及当轮验证范围保留在工作区 `outputs/blade-defects/development-20260930/开发验证记录.md`。

A 默认瞬时理想针孔，无曝光模糊、噪声、畸变或压缩效果。近景额外检查光照与固定相机图分开。图像生成也不会自动将审核标记为可辨认；新运行的 `review.md` 默认保留 `NOT_REVIEWED`；2026-09-30 指定图像的后续人工审核单独记录为 `REVIEWED_WITH_LIMITATIONS`。健康差分可能包含阴影和反射，不等于缺陷分割标签。

## 冻结模型与支持域

使用现有 DTU 标量分布、固定 FFA 翼型参考、3 m 静态预弯的 GW184 尺寸合成模型，不声称实机 CAD。参考几何摘要包含模型参数、源码和来源资源。

- `s_m` 沿参考变桨轴、叶根起算；`u` 沿截面弦线，前缘 0、后缘 1。
- FFA 上表面登记为 suction，下表面为 pressure；不按相机方向切换侧别。
- 首版有效域为 `18 ≤ s_m ≤ 89.4`、`0.05 ≤ u ≤ 0.95`；拒绝根部、收尖和跨前后缘路径。
- 方向角按该侧外法线右手规则。全路径和边界绑定冻结健康三角面，而不是只绑定中心点。

默认值以运行 ZIP / 匹配工作区中的 `projects/gw184-single/gw184/defect_schema.py` 为准：

| 类型 | 表示与范围 | 长度 / 最大宽度 / 最大深度（m） | 边缘不规则度 | 默认展向（m） |
| --- | --- | --- | --- | --- |
| `fine_crack` 材质细裂纹 | 原表面解析材质遮罩，无悬浮贴片、无真实深度 | 0.60 / 0.002 / 0 | 不适用 | 44 |
| `open_crack` 几何开口裂缝 | 实体开口与内壁 | 0.60 / 0.08 / 0.015 | 不适用 | 45 |
| `pit` 凹坑 | 圆钝的实际凹陷，包含坑壁与底部 | 0.30 / 0.24 / 0.015 | 0 | 46 |
| `erosion` 局部表面侵蚀 | 不规则浅层真实表面损耗；当前不跨前后缘 | 0.65 / 0.22 / 0.004 | 0.35 | 47 |
| `coating_loss` 涂层剥落 | 仅材质露底；不模拟涂层厚度、翘起或实体深度 | 0.45 / 0.28 / 0 | 0.40 | 48 |
| `lightning` 雷击损伤 | 同一事件的烧蚀环、坑口及损伤内壁 | 0.60 / 0.36 / 0.020 | 0.30 | 49 |

六类默认位于 B1 吸力面、`u = 0.4`、方向角 0°，形态种子为 17。展向分开以避免示例重叠。长度与宽度按健康参考表面支持域计量，深度按实体损伤计量；布尔操作只发生于编辑重建。

四种新增类型的 `edge_roughness` 范围为 `[0, 0.5]`，控制边缘形态，不是粗糙度的实测值。雷击的 `crater_fraction` 默认 0.4、范围 `[0.15, 0.6]`，分别乘以**整个事件的长度和宽度**得到内部坑口的名义长度与宽度；默认坑口为 0.24 × 0.144 m，深度仍为 0.020 m。该比例不是面积比例。

六种预设均为规定的合成形态，尺寸和种子是建模输入，没有实物形态参考图，未验收真实损伤形态。雷击同一事件的烧蚀环与坑口共用实例 ID，不模拟放电或内部损伤物理；当前版本不含穿孔或分叉裂纹。所有类型均不耦合结构或气动物理，暂不支持断裂及相互重叠的独立缺陷。

六类型开发保留七项冻结输入：`geometry/target.json`、`cameras/layout.json`、`motion/timeline.json`、`sources/ffa_reference.json`、`sources/DTU_10MW_RWT.htc`、`sources/DTU_10MW_RWT_ae.dat` 和 `gw184/model.py`，均相对 `projects/gw184-single/`。其基准摘要见下述工作区开发基线，比对记录见工作区六类型验证记录。

开发容差在工作区 `outputs/blade-defects/development-20260930/baseline.json` 冻结：表面绑定 1e-5 m、尺寸 1 mm、独立投影 0.05 px、遮挡 1e-4 m。扫描步长按输出采样率（默认 1/30 s）。交互目标为姿态更新 p95 ≤ 33.333333 ms、两类示例提交 ≤ 5 s；该两类开发基线实测通过。扩展六类型场景中单独修改雷击为 5.490812 s，未满足相同 5 s 目标，保留为性能缺口；实际窗口绘制帧率与后台姿态更新不是同一指标，发布检查没有新的完整片段性能结论。

## 可见性含义

材质遮罩、虚拟健康表面开口足迹和实际损伤内壁分别登记，不相加混为一个缺陷面积。遮挡来自当前实际叶片、机舱、塔筒、相机盒体及支架等。开口只检查相机到虚拟足迹之前的遮挡，不要求射线命中已经移除的健康面；背面另作拒绝。

雷击的主记录为烧蚀与坑口的事件联合支持域；烧蚀环、坑口和实际内壁另作分量记录，不将分量面积再次累加到事件主记录。G 几何结果不证明烧蚀材质在固定相机图中可辨认。

候选面积为浮点投影多边形并集，避免重复计数并保留亚像素量。可见面积使用面积加权的子像素射线积分，默认最大采样步长 0.75 px；小于单元的遮挡可能遗漏，没有连续误差上界。超过样本预算返回 `INVALID` 和原因，不悄悄降低精度。样本可见率单列，不能冒充像素面积比例。`VISIBLE` 是所声明方法下的几何状态，不等于肉眼辨认或算法检出。

## 开发文件与回归

新模块分工：`reference_surface.py` 健康表面映射；`defect_schema.py` 配置；`defect_shapes.py` 合成轮廓与深度形态；`defect_store.py` 事务；`defect_geometry.py` 材质/实体生成；`defect_visibility.py` 固定相机分析；`defect_timeline.py` 映射与扫描；`defect_editor.py` 独立界面；`defect_outputs.py` 运行清单与按需出图。现有 `scene.py` 只增加关闭旧预设缺陷的健康构建开关和 collection 返回值，默认旧三视频管线行为保留。

```sh
python3 -m unittest discover -s projects/gw184-single/tests -p 'test_*.py' -v
"$BLENDER" --background --factory-startup --python-exit-code 1 \
  --python projects/gw184-single/tests/defect_four_types_regression.py -- \
  outputs/blade-defects/new-four-types-regression-directory
```

旧两类裂纹与修改前源码的逐点一致性比较另需设置 `GW184_DEFECT_PRECHANGE_DIR`，指向保存的旧 `gw184` 模块目录；未设置时只跳过这一项比较。含该变量的开发阶段复现命令见工作区六类型验证记录；运行 ZIP 没有附带旧源码对照目录，因此发布测试按前文所述跳过这一项。

六类型开发新增 `test_defect_four_types.py`、`defect_four_types_regression.py` 和 `defect_patch_regions_regression.py`，覆盖六类配置、四种新增形态的编辑器操作、实际实体检查、UV 接缝、雷击分量分区及固定相机 G 输出。上述回归不调用图像生成、外观导出或裁切；导出模块代理仅拦截该模块的渲染入口，不声称拦截全进程所有 Blender 渲染操作。开发阶段结果、实际命令和失败边界保存在工作区 `outputs/blade-defects/four-types-20260930/验证记录.md`；0.4.0 的实际 ZIP 检查独立记录于发布状态说明，不能用历史源码通过记录替代包内结果。

上一轮编辑器回归里的 PNG 裁切使用已知像素的 3×2 编码数据，不是场景检查图。`test_defect_output_restore.py` 另以纯 Python 检查出图前的异常恢复，不加载 Blender 或生成图像。`defect_outputs_regression.py` 的 A 产物合同测试已通过：七实例覆盖六类型，代理写入测试图验证合同，真实渲染调用为零；另外完成 18 张授权静态图的文件和像素校验。测试、实际窗口操作和 A 图像审核的结论分别记录。

缺陷编辑器源码加入后，旧三视频管线的 preflight 源码签名已过期；后续若另行授权旧管线渲染，应按其原流程生成新的无渲染 preflight，不复用旧签名冒称当前验证。

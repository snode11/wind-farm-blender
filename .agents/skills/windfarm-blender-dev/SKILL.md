---
name: windfarm-blender-dev
description: 开发和排查本仓库 Blender 原生前端的面板、相机、雷达光束、净空卡片、工况切换与回放控制，识别界面、回放、数据格式及物理算法的修改边界。用于相关开发方案和验收，不作为启动训练或重跑仿真的授权。
---

# Windfarm Blender 开发

本文件的链接相对 skill 目录；命令从仓库根目录执行。先读根目录 [AGENTS.md](../../../AGENTS.md) 和改动路径下已有规则，检查 Git 状态及现有 skills，保留当前工作。

## 先分类并给出最小方案

实施前说明：需求类别、涉及模块、最小修改方案、可观察的验收标准和验证层级。混合需求逐项分类；发现跨层影响时更新方案，不以界面需求默许物理重算。仅方案或文档任务到说明与静态校验为止。

| 类别 | 判断与默认边界 |
| --- | --- |
| 纯界面 | 面板布局、文案、卡片、观察相机、光束显示；复用 Camera 相机与演示入口，不修改正式结果包，不重跑 FAST.Farm。 |
| 回放逻辑 | 加载、切换、暂停、寻址、重播、时间映射与读数保留；检查 Blender 接入和独立读取器，复用现有数据验证。 |
| 数据格式 | schema、字段、摘要、校验与累计状态合同；先评估读取器、发布端、旧包兼容性及扩展内置副本。不得为了让旧包通过而削弱校验。 |
| 物理算法 | 标定、估计公式、真值定义、几何、求解参数；明确是否需要后处理或新求解。仅在任务范围包含这些工作时执行，生成新版本包而非覆盖旧包。 |

先读 [根 README](../../../README.md) 的 Blender 入口和 [前端 README](../../../blender_frontend/README.md)；技术公式、包结构和计算流程以现有文档为引用源，不复制整份说明。

## 按需求定位

以下实现路径以 `blender_frontend/wfrl_blender/` 为基准；按需读取，不加载所有模块。

| 需求 | 实现入口 | 补充资料 |
| --- | --- | --- |
| 面板、净空卡片、工况和播放按钮 | `panels/clearance.py`、`panels/gimbal.py`、`panels/presentation.py` | [用户手册](../../../docs/blender/用户使用手册.md) 第 4、8、12 节 |
| 云台、侧视、总览及雷达示意 | `cameras.py`、`clearance_visual.py`；几何关联时再读 `scene_builder.py`、`turbine_geometry.py` | [雷达说明](../../../docs/blender/激光净空雷达使用说明.md)、[展示流程](../../../docs/blender/presentation-flow.md) |
| 回放同步、模式和生命周期 | `clearance_replay.py`、`runtime.py`、`__init__.py`；涉及演示时钟时读 `animation.py`、`cinematic.py` | [前端 README](../../../blender_frontend/README.md) 第 4 节 |
| 包读取、统计、格式和物理边界 | 仓库级 `wfrl/lidar/replay.py`、`evidence.py`；物理任务再读 `physics.py`、`sampling.py` | [结果包合同](../../../wfrl/lidar/REPLAY_FORMAT.md)、[计算流程](../../../scripts/lidar/README.md) |
| 安装、启动和交付 | `blender_manifest.toml`；仓库级 `scripts/blender/build_extension.py`、`open_clearance_demo.py`、`verify_lidar_delivery.py` | [交付说明](../../../dist/README-lidar.md)、[交付清单](../../../dist/lidar-delivery.json)、[故障排查](../../../docs/blender/TROUBLESHOOTING.md) |

旧 [安装说明](../../../docs/blender/INSTALL.md) 和 [验收矩阵](../../../docs/blender/ACCEPTANCE.md) 含历史版本与记录；版本以当前 manifest 为准，交付路径以清单为准，实际行为核对当前实现。雷达离线回放与依赖 Bridge 的后端 Replay 是不同入口，不因查看雷达而启动求解器。

## 必须保留的行为

- 主卡片及主统计使用 B2；B2 无效时不借用 B1/B3 或真值补齐。真值、估计和有符号偏差来自同一次测量，偏差为估计减真值。
- 无效、缺失、过期和空统计不转为零净空或零误差；维持现有整组读数有限保留与过期规则。缺包、损坏、机型或版本不匹配时清除旧读数并显示可排查的未就绪原因。
- 姿态、卡片和累计统计共用仿真时钟；固定帧映射到固定仿真时刻。修改 Blender FPS 只改变目标墙钟播放速度；暂停、后退、寻址与重播不重复累计统计。
- 工况切换加载对应片段并从起点开始；仅切观察视角保留工况、进度与暂停状态，不更改雷达标定。保持 Local Demo、后端运行和离线回放的来源提示及状态隔离。
- FAST.Farm 柔性仿真结果与 Blender 刚性画面示意分开描述；橙色光束是方向示意，其显示长度不参与测距计算，末端不宣称真实命中点。Local Demo/SYNTH 不作为真实训练或现场精度证据。

## 分层验证

先检查测试输入、输出路径和运行前置条件，再执行与改动有关的最小集合；必要时增加能复现问题的针对性回归。不要为文档改动运行仿真或全量前端验收。

- 宿主机：查找 `blender_frontend/tests/test_*.py`；读取器、统计和包合同查找 `tests/lidar/`。使用已具备依赖的 Python，例如 `PYTHONPATH=blender_frontend:. python -m pytest blender_frontend/tests/test_extension_package.py -q`（仅适用于打包相关任务，测试在临时目录构建 ZIP）。根据实际模块选择测试，不把单一打包测试当成回放验收。
- Blender 原生：本地环境可用且任务含实现验证时，执行匹配的最小运行检查。雷达可从 `blender_frontend/tests/blender/clearance_ux_regression.py`、`clearance_acceptance_regression.py`、`clearance_replay_smoke.py` 选择；相机查找 `camera_framing_smoke.py`、`gimbal_controls_smoke.py`。无窗口脚本可采用 `"<Blender 可执行文件>" --background --factory-startup --python-exit-code 1 --python <测试脚本>`，先确认脚本支持后台运行。
- 实际窗口：有桌面截图能力时启动独立可见窗口，检查面板可读性、测量区构图、操作和错误提示；渲染图不能代替窗口验收。`clearance_gui_acceptance.py` 需要可见窗口及 `WFRL_TEST_OUTPUT`，使用独立 `BLENDER_USER_CONFIG`。部分旧测试硬编码 `normal/close` 或写入演示 `.blend`，先核对清单和输出，使用隔离副本/临时输出，不覆盖交付场景。
- 按改动选取验收：两个工况加载；视角切换前后样本和暂停状态一致；暂停/寻址/重播统计一致；固定帧修改 FPS 后仿真时间一致；B2 无效及保留过期显示正确；缺包失败清空旧值且修复路径后可恢复。纯布局任务至少验证相关按钮和窗口显示，不机械运行全部场景。

已安装扩展不会读取仓库源码改动。验证安装版时重启 Blender 并重新加载/校验匹配场景；后端流程按现有 `Load & Validate Scene` 操作，离线雷达按专用入口或结果包加载流程。不要为刷新界面误启动在线求解。

报告逐项标记“已运行通过”“仅静态检查”“尚未验证”，给出命令/环境、结果和证据位置；缺环境时说明缺项。历史 PASS、宿主机测试、源码入口、安装 ZIP、实际窗口及不同平台的结论分别记录。

## 扩展与数据交付判断

涉及扩展交付时检查 [构建脚本](../../../scripts/blender/build_extension.py) 的实际载荷：扩展 Python/资源、规范 Bridge 协议以及内置 `_vendor/lidar` 的读取器与证据校验模块。修改这些输入需要重建 ZIP；仅 AGENTS.md、skill 或外部说明改动不需要重建。不要手改构建生成的内置副本。

在交付范围内按现有流程运行 `python3 scripts/blender/build_extension.py`，核对 manifest 版本、ZIP、`.zip.sha256`、`.inventory.json` 及交付清单是否一致；默认构建会写入同版本 dist 文件，验证构建可使用现有 `build(output_dir)` 的临时目录能力。需要发布时再按任务范围更新交付清单并验证实际安装包，重建本身不代表已发布。

ZIP 不包含正式结果包与原始 VTP。界面/播放器改动复用已有结果包；物理参数或几何变化可能需要新求解，标定/公式变化在原始输入完整兼容时可仅重新后处理、验证和发布新包。具体依据 [前端 README 第 6 节](../../../blender_frontend/README.md) 与计算流程判断，不把重打 ZIP 当成物理结果更新。

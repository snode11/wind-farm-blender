---
name: windfarm-blender-dev
description: 开发、排查和验收本仓库 Blender 原生前端的界面、相机与净空雷达回放。
---

# Windfarm Blender 开发

本文件的链接相对 skill 目录；命令从 Git 仓库根目录执行。适用规则见 [AGENTS.md](../../../AGENTS.md)。

## 先分类并给出最小方案

实施前简要说明需求类别、涉及模块、最小方案和可观察的验收标准；小改动用几句话即可，不要求另写计划或停等批准。混合需求逐项分类，跨层影响需要更新方案。正常开发应完成实现、相关验证和本次改动引入的问题修复；仅方案、基线或报告任务按其范围结束，原样报告失败。

| 类别 | 判断与默认边界 |
| --- | --- |
| 纯界面 | 面板布局、文案、卡片、观察相机、光束显示；复用 Camera 相机与演示入口，不修改正式结果包，不重跑 FAST.Farm。 |
| 回放逻辑 | 加载、切换、暂停、寻址、重播、时间映射与读数保留；检查 Blender 接入和独立读取器，复用现有数据验证。 |
| 数据格式 | schema、字段、摘要、校验与累计状态合同；先评估读取器、发布端、旧包兼容性及扩展内置副本。不得为了让旧包通过而削弱校验。 |
| 物理算法 | 标定、估计公式、真值定义、几何、求解参数；明确是否需要后处理或新求解。仅在任务范围包含这些工作时执行，生成新版本包而非覆盖旧包。 |

启动入口见 [根 README](../../../README.md)；架构、公式、包格式及物理重生成见 [前端 README](../../../blender_frontend/README.md)。仅阅读当前任务需要的章节，已有上下文足够时无需重复读取。

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

按验收标准选择现有测试，先核对输入、输出和前置条件。必要时补充针对性回归；相关检查通过且没有新改动或未解决问题时即可结束，不为凑覆盖范围反复测试。文档改动只需相应静态校验。

| 验证层 | 现有入口与边界 |
| --- | --- |
| 宿主机 | `blender_frontend/tests/test_*.py`；读取器与统计见 `tests/lidar/test_replay.py`。从根目录用 `PYTHONPATH=blender_frontend:. python -m pytest <选定测试> -q`，选择现有依赖环境。`test_extension_package.py` 会构建 ZIP，禁止打包的任务必须排除。 |
| Blender 原生 | 本地 Blender 可用时，按改动选择 `blender_frontend/tests/blender/` 下的 `clearance_ux_regression.py`、`clearance_acceptance_regression.py`、`clearance_replay_smoke.py`，相机相关选择 `camera_framing_smoke.py` 或 `gimbal_controls_smoke.py`。先确认脚本支持后台运行，再使用 `--background --factory-startup --python-exit-code 1 --python <脚本>`。 |
| 实际窗口 | 界面或视觉修改有桌面截图能力时，检查真实窗口的可读性、构图、操作和错误提示；渲染图不替代窗口。`clearance_gui_acceptance.py` 需要可见窗口、`WFRL_TEST_OUTPUT` 和独立 `BLENDER_USER_CONFIG`。部分旧脚本硬编码旧包路径或写演示 `.blend`，使用清单核对输入并隔离输出。 |

回放相关验收从下列行为选择：两工况加载、视角切换保持样本与暂停状态、暂停/寻址/重播统计一致、固定帧改 FPS 后仿真时间一致、B2 无效与过期显示、缺包清空和路径修复恢复。纯布局至少检查相关按钮与实际窗口。

报告给出实际命令、环境和结果，区分“已运行通过”“仅静态检查”“尚未验证”。缺依赖则说明缺项；历史 PASS、宿主机、源码入口、安装 ZIP、窗口和不同平台的证据分别记录。

## 扩展与数据交付判断

仅在扩展交付任务中读取 [交付说明](../../../dist/README-lidar.md) 和 [构建脚本](../../../scripts/blender/build_extension.py)。扩展 Python/资源、规范 Bridge 协议或内置 lidar 读取器/证据模块变化需要新 ZIP；仅 AGENTS.md、skill 或外部说明变化无需重建。修改源文件，不手改生成的内置副本。

现有命令为 `python3 scripts/blender/build_extension.py`，会写入同版本 dist 文件；验证构建可调用现有 `build(output_dir)` 输出到临时目录。交付时核对 manifest 版本、ZIP、SHA-256、inventory 与清单一致，并验证安装版。安装版不读取仓库源码，需重启 Blender、重新加载匹配场景或结果包；后端流程使用 `Load & Validate Scene`，离线雷达无需启动求解器。

ZIP 不含正式结果包与原始 VTP。界面/播放器修改复用结果包；标定/公式变化是否可复用原始输出、几何/物理变化是否需要新求解，按 [前端 README 第 6 节](../../../blender_frontend/README.md) 与计算流程判断。重建 ZIP 不等于更新物理结果或发布。

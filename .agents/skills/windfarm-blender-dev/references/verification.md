# 维护与验证入口

命令从 Git 根目录执行。按受影响行为读取[场景与回放](scene-playback.md#按行为选检查)、[相机与重建](cameras-reconstruction.md#按行为选检查)、[测量与后端](measurement-backend.md#按行为选检查)或[视频作业](video-jobs.md#按行为选检查)中的检查入口；无需为每个任务加载所有专题。其他用例按相邻模块查找。

## 普通发布

同步文档、打包或发布到 GitHub 的默认范围以 [AGENTS.md](../../../../AGENTS.md#普通发布的默认范围) 为准：确认内容与版本对应，需要 ZIP 时选择本地或发布运行器构建一次，确认所需推送、附件上传及公开命令成功。仅同步文档时不打包；上传成功后不下载复验或比对远端附件。

下文的测试、逐文件核对、隔离安装、窗口／GPU 和性能入口供功能修复或明确指定的验证任务按需选择，不是普通发布的必经步骤。普通发布不默认运行这些检查，不等待自动 CI，也不额外触发或重跑 CI；已有记录只按其载荷与范围引用。功能修复只做与改动直接相关的必要检查，构建或上传失败只处理相应阻塞。

## 运行来源

- 工作区、构建 ZIP、公开附件和安装分别识别。
- 兼容版本读 `blender_manifest.toml`，实际来源读 `addon.__file__`／`frontend_diagnostics.py`，载荷核对 `build-info.json`、SHA-256 与 inventory；版本号或源码同步不证明一致。
- 替换安装后重启 Blender，[日常安装身份](../../../../publication/daily-installation.json)单独核对。
- 扩展源码、资源、规范协议或内置读取器进入安装版时重建 ZIP；skill、说明及项目侧后端改动不自动要求打包。
- 构建时生成的 `protocol.py`、`_vendor/lidar/`、`_vendor/radar_x100/` 分别来自仓库 `wfrl/blender_bridge/messages.py`、`wfrl/lidar/`、`wfrl/radar_x100/`；X100 模块清单由仓库 `scripts/blender/build_extension.py` 的 `RADAR_MODULES` 定义，逐模块摘要写入 `build-info.json` 的 `radar_core_sha256`。修改规范源，不手改生成副本或替换冻结模型绕过校验。

后台、脚本窗口与真实鼠标／键盘分别报告，PASS 须匹配载荷、环境和输入。定时器注册、正常启动或单张截图不能替代脚本的最终结果。

## 执行环境与来源

使用已有依赖环境；NumPy／PyTorch 等按选定测试要求准备，不为文档任务安装依赖。

持续集成的宿主依赖、触发条件与后台选择见[自动检查](../../../../docs/development/自动检查.md)。`configs/ci-native-tests.txt` 是后台 CI 的脚本选择；运行器继续核对来源、正常退出、完成标记和最终报告。README 的身份摘要使用仓库 `scripts/ci/check_release_identity.py --release <交付记录.json>` 检查；当前记录路径以工作流参数和发布身份文档为准。显式 `--write` 从该身份记录同步摘要，不更改记录本身或验证范围。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=blender_frontend:. python -m pytest <选定测试路径> -q -p no:cacheprovider
<Blender可执行文件> --background --factory-startup --python-exit-code 1 --python <支持源码后台的脚本路径>
```

源码约定守卫随宿主测试运行：`test_text_io_encoding.py` 要求扩展源码的文本读写显式 UTF-8，`test_blender_script_registry.py` 要求 `blender_frontend/tests/blender/` 下的每个原生脚本（下划线开头的共享模块除外）已登记到 `run_regressions.py`，或在本目录专题、`docs/blender/`、`wfrl/lidar/README.md` 或 `scripts/blender/` 驱动脚本中写明手动入口。修改相应源码或新增原生脚本时一并运行。

手动视频监管用例（`blender_frontend/tests/test_manual_video_job.py`）需要解释器提供 `os.waitid`；macOS 上 Python 3.13 起才提供，较旧的解释器会跳过这些用例而不报失败，选择宿主解释器时分别报告。这些用例在临时目录启动子进程，`PYTHONPATH` 须写成绝对路径，例如 `PYTHONPATH="$PWD/blender_frontend:$PWD"`。

窗口脚本不加 `--background`；安装检查保留所需 profile，不照搬工厂启动。使用独立进程、配置、场景副本和新输出目录，保护用户脏场景及规范资产。

仓库 `scripts/blender/run_regressions.py --list` 查看已纳入脚本的模式、输入、输出与超时；用 `--tests` 选择脚本、`--output` 指定新目录、`--scratch-root` 指定临时目录父路径。它串行运行源码，不打包或安装。PASS 要求来源、稳定摘要、正常退出、完成标记及最终结果；定时器注册返回不算完成，未运行、跳过、超时及失败分别保留。

日常模块通常为 `bl_ext.user_default.wfrl_blender`，隔离模块为 `bl_ext.wfrl_frontend.wfrl_blender`。参数按脚本读取，设置未使用的变量不产生安装验证：

| 脚本 | 模块与来源校验 |
| --- | --- |
| 普通手动、共享结构、回放恢复、相机保存／内参／采集及缺陷资源／播放 | 通常用 `WFRL_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_ROOT`；逐份确认，安装模式不回退源码。 |
| `farm_down_mount_regression.py`、`t1_down_mount_regression.py`、`nacelle_camera_regression.py` | `WFRL_TEST_RUNTIME` + `WFRL_EXPECTED_ADDON_ROOT`。 |
| `blade_model_regression.py`、`blade_model_window.py` | `WFRL_TEST_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_DIR`。 |
| 相机启用、布局、缺陷编辑器及缺陷窗口脚本 | `WFRL_ADDON_MODULE`，无期望目录断言，另核对来源；布局的 `WFRL_ADDON_ROOT` 仅控制搜索路径。 |
| `manual_down_copy_regression.py`、三份源码视频脚本、`farm_flex_workload_regression.py`、`custom_camera_edit_binding_regression.py`、`nrel_defect_ui_state_regression.py` | 仅源码；安装模块变量不能改变验证层。 |
| `live_contracts_regression.py` | 仅源码，直接导入工作区扩展；使用独立工厂启动后台进程，安装模块变量不能改变验证层。未纳入 `run_regressions.py`，按上文 `--python` 入口直接运行。 |
| `radar_x100_regression.py`、`radar_x100_edges.py`、`radar_x100_history_regression.py`、`radar_x100_binding_replay.py`、`radar_x100_window.py` | `WFRL_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_ROOT`；安装时另核对实际加载的 `radar_x100.core` 及规范数据模块路径。`radar_x100_regression.py` 的安装模式断言未导入工作区 `wfrl.radar_x100`，并核对 `radar_core_sha256`。 |
| `radar_x100_mount_presets.py` | 支持 `WFRL_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_ROOT`；后台核对单台雷达的双安装位、相同源时刻、重复选择、采样失败、记录／相机互斥及保存重开。 |
| `radar_x100_output_window.py` | 支持 `WFRL_ADDON_MODULE` + `WFRL_EXPECTED_ADDON_ROOT`；核对独立点云窗口的实际绘制、消费帧与原始 RGB 隔离。 |
| `radar_x100_output_lifecycle.py` | 仅源码，直接导入工作区扩展；必须指定 `WFRL_EXPECTED_ADDON_ROOT`，安装模块变量不能改变验证层。 |
| `radar_x100_configuration.py` | 仅源码后台，必须指定 `WFRL_EXPECTED_ADDON_ROOT`；核对名义外壳／视场、真实 RNA、已应用配置与编辑差异、显示即时生效和下一新样本预算。 |
| `radar_x100_observations.py` | 仅源码后台，直接导入工作区模块；核对面积采样、材料标签隔离、同源安装导入、未知报告拒绝、回退及估计撤回。 |
| `radar_x100_estimation_window.py` | 仅源码可见进程，直接导入工作区模块；读回拟合线／叶尖区域的真实GPU像素，检查关闭、恢复及原始帧和时刻保留。脚本控制不代替外部键鼠。 |
| 可见窗口（`visible`）`mappo_full_view_default_window.py`、`mappo_same_source_navigation_window.py`、`mappo_same_source_texture_window.py`、`split_texture_sidebar_window.py`、`reconstruction_workflow_window.py`；后台（`background`）`mappo_same_source_texture_reopen.py` | 仅安装模块。使用 `python3 scripts/blender/run_installed_windows.py --output <新目录>` 建立隔离安装并串行验证。可用 `--installed-profile <隔离验证目录>` 复用仍有效的安装，或用 `--zip <路径>` 安装指定 ZIP。`--list` 列出模式与依赖，`--tests` 选择脚本。各窗口使用独立输出与场景副本；统一工作流从干净窗口调用操作符打开隔离安装中的示例。后台重开读取纹理窗口保存的场景和 PASS 报告。运行器拒绝日常配置及 `bl_ext.user_default`，以退出码、最终报告和完成标记共同判定 PASS。 |

特殊前置条件：

- `WFRL_TEST_OUTPUT` 的文件／目录合同逐份核对，已有目录可能覆盖报告。缺陷保存／重开复用专用目录，第二个进程设 `WFRL_NREL_DEFECT_PHASE=reopen`。手动撤销／恢复依赖工厂 `Cube`，`WFRL_MANUAL_EMPTY_ONLY=1` 会缩小隔离检查范围。
- `farm_flex_workload_regression.py` 要求源码、后台工厂启动与空白注册，仅输出 `FARM_FLEX_WORKLOAD_REGRESSION_PASS`，无需输出目录。
- `launcher_mode_regression.py` 只检查已安装的 `bl_ext.user_default.wfrl_blender`（经 `scripts/blender/wfrl_blender_bootstrap.py` 加载），未纳入 `run_regressions.py`。先在仓库根目录启动空闲 Bridge，例如 `python -m wfrl.blender_bridge --host 127.0.0.1 --port <端口> --scene scenes/turb3_demo.yaml --fake`，再按启动器设置 `WFRL_PROJECT_DIR`、`WFRL_BACKEND_PYTHON`、`WFRL_SCENE`、`WFRL_PORT` 并以后台进程运行；检查 `LAUNCHER_MAPPO_BACKEND_MODE_PASS` 与正常退出。脚本经真实传输完成回放／交互训练两种连接及返回 MAPPO，不启动求解或训练；`--fake` 后端不代表 FAST.Farm 场景。
- `live_contracts_regression.py` 必须指定尚不存在的 `WFRL_TEST_OUTPUT` 目录，由脚本创建并写入 `result.json`；检查正常退出、`LIVE_CONTRACTS_PASS` 与报告，不复用正式场景或已保存结果目录。
- X100 主合同、边界、历史和雷达窗口脚本均要求尚不存在的 `WFRL_TEST_OUTPUT` 目录并写入 `result.json`。主合同、边界与历史后台脚本可由 `run_regressions.py` 选择，分别检查 `X100_NATIVE_PASS`、`X100_EDGES_PASS`、`X100_HISTORY_PASS` 及正常退出；窗口脚本未纳入该运行器，使用干净可见进程，检查最终报告及 `X100_WINDOW_PASS`。它在完成后保留窗口，不把定时器注册或启动成功当作完成，也不把脚本操作当作真实键鼠验证。

- `radar_x100_binding_replay.py` 可由 `run_regressions.py` 选择，也可按后台脚本入口直接运行，使用尚不存在的 `WFRL_TEST_OUTPUT` 目录；检查对象／场景／父链绑定、合法动态姿态、真实 RNA 量化、片尾／重播、独立呈现时钟、同时间索引和倒序重录失败，要求 `result.json`、`X100_BINDING_REPLAY_PASS` 与正常退出，不证明 GPU、真实界面输入或设备通过。
- `radar_x100_mount_presets.py` 可由 `run_regressions.py` 选择或直接按后台脚本入口运行，使用尚不存在的 `WFRL_TEST_OUTPUT` 目录，检查 `result.json`、`X100_MOUNT_PRESETS_PASS` 与正常退出；不把环境变量或启动成功当作安装验证。
- X100 色标与窄窗先查宿主投影、稳定分类颜色、固定速度范围和布局边界，再按受影响范围运行可见输出窗口检查；字号／刻度的实际布局、真实鼠标操作和完整播放分别报告。材料子集优化须对照完整几何的点数组、材料身份与速度，短时 CPU 计时只报告其更新范围。
- `radar_x100_configuration.py` 可由 `run_regressions.py` 选择，使用尚不存在的 `WFRL_TEST_OUTPUT` 目录，要求 `result.json`、`X100_CONFIGURATION_PASS` 与正常退出；核对安装快捷按钮同步应用状态、单帧来源／目录来源和实时连接未热切换。宿主的 `test_radar_x100_configuration.py`、`test_radar_x100_display.py`、`test_radar_x100_analysis_runtime.py` 与 `test_radar_x100_recording_ownership.py` 分别检查配置比较、有界辅助层缓存、冻结帧分析重试和非阻塞写盘归属；它们不代替 GPU 绘制、真实输入或持续性能。
- 两份独立点云窗口脚本均要求干净可见进程及尚不存在的 `WFRL_TEST_OUTPUT` 目录，均未纳入 `run_regressions.py`。`radar_x100_output_window.py` 写入 `result.json` 与逐次绘制诊断 `draw-sync.jsonl`，检查最终报告及 `X100_OUTPUT_WINDOW_PASS`，完成后保留窗口；`radar_x100_output_lifecycle.py` 核对保存重开、撤销／重做、其他 Image Editor 保留、重复打开及卸载时 GPU 缓存释放，检查报告、`X100_OUTPUT_LIFECYCLE_PASS` 及正常退出。脚本操作不代替真实键鼠或完整片段性能验证。
- 共享结构后台检查可用 `WFRL_SPLIT_INPUT` 指定场景副本；`reconstruction_workflow_window.py` 要求干净可见进程、安装模块及精确目录。`WFRL_EXPECTED_GEOMETRY_SHA256` 从已确认资产及 inventory 取得，不能照抄失败现场摘要绕过校验。
- 统一工作流窗口覆盖两侧完整风机、可用画布宽度、即时相机隔离、几何对象与形态键身份、工作区往返、几何模式样本步进及 CURRENT／退出恢复。右侧相机退出后的枢轴距转子中心须不超过 5 m；静止检视分别检查点击时的目标入框，以及播放、寻址和侧栏变化后的视角保持。
- `scripts/blender/check_nrel_nacelle_camera.py` 支持 `WFRL_ADDON_MODULE`／`WFRL_EXPECTED_ADDON_ROOT`，参数 `-- --output <新目录>`，生成 PNG、场景及参数。双束使用匹配内置包或 `WFRL_DUAL_PACKAGE`；窗口以 `WFRL_BUILTIN_METHOD=TLS` 选候选，同时核对方法、源摘要、S1 与双束。

视频脚本的媒体、完整样片及跨进程恢复覆盖见[视频验证边界](video-jobs.md#视频验证边界)。

## 构建、安装与完整播放

下表中的宿主测试位于 `blender_frontend/tests/`。各入口独立按任务选用，普通发布仅使用所选构建路径；指定安装或性能验证时再核对相应来源和结果。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 构建与安装 | `test_frontend_package_validation.py`、`test_extension_package.py` | 见下文构建入口；后者会构建 ZIP。 |

```bash
python3 scripts/blender/build_extension.py --output <新目录>
```

明确要求隔离安装验证时使用以下入口；该入口自行构建 ZIP，不与上面的构建命令叠加执行：

```bash
python3 scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender可执行文件>
```

明确要求完整播放测量时使用匹配且仍可用的隔离 profile：

```bash
python3 scripts/blender/run_playback_benchmark.py --blender <Blender可执行文件> --output <新目录> --installed-profile <隔离验证目录>
```

- 构建默认拒绝覆盖 ZIP、校验和与 inventory，`--overwrite` 才替换；`--dual-package`／`--dual-tls-package` 分别选择匹配源的 `hub-axis.v1`／`hub-tls.v1` 包。
- 隔离验证的 `--build-only` 仍构建 ZIP；禁止打包时排除该工具及 `test_extension_package.py`。其 `--dual-package` 仅指 `hub-axis.v1` 包。窗口与性能入口继承 `environment.json` 及 profile，`BLENDER_USER_RESOURCES` 指向 profile 根、`BLENDER_USER_EXTENSIONS` 指向其中的 `extensions`，旧环境文件缺少这两项时从 profile 推导；缺依赖、脚本或 profile 如实报告。
- 性能入口的 `--installed-profile` 要求有效 `environment.json` 及仍存在的 profile，清除源码覆盖且不使用工厂启动；`--path / --layout` 取值查脚本。逐路核对初始绘制、持续推进、终段、心跳、最终状态及退出，结合错误／超时判断，寻址不算完成。
- `native_camera_reopen_regression.py` 覆盖单路末帧→关闭→三路重开／重播及延迟恢复；安装模式须传模块与期望目录，不借其他路径推定通过。`performance.py` 只计 CPU 回调，嵌套耗时不相加；平均 FPS、缓存计数或短时诊断不替代完整片段验收。
- 播放报告保留 `observation_s`、`intentional_pause_s`、`running_observation_s` 和暂停区间；停滞判断扣除已确认的静止主动暂停，FPS 分母仍是完整墙钟时长。峰值 RSS 按平台换算（Darwin 字节、Linux KiB），与峰值差分别报告，不把峰值差称为当前内存增长或无泄漏证明。

日常安装以[安装身份](../../../../publication/daily-installation.json)为准；相同 manifest 版本不代表与公开附件相同。日常构建的 ZIP、inventory 和 SHA-256 由安装记录的 `source_archive` 标识；公开 ZIP 的位置按[发布身份](../../../../docs/blender/发布状态与验证范围.md)及其交付记录定位。运行来源及配置保护见安装记录中的证据链接。后台身份／注册、可见窗口和完整播放结论分别读取，其他候选载荷的性能记录不得转记到当前安装。

保留产物与已清理范围见[安装记录的产物可用性](../../../../publication/daily-installation.json)。原始日志和 JSON 中的临时路径保留测量时来源，不改写成当前路径；留存 `environment.json` 不表示隔离 profile 仍存在。使用安装验证或性能入口前核对 profile，缺失时须建立并验证匹配的新隔离环境，不能把历史目录当作可运行配置。

## X100 同帧分析

`tests/radar_x100/test_analysis.py` 验证独立 NumPy 三角最近点、无效值、色标与经过分段；`blender_frontend/tests/blender/radar_x100_analysis.py` 用源码后台核对侧壁封口排除、BVH 基准、只读上下文、冻结 ROI、真值隔离、提交回滚、刚塔回退及同步取样配对对照，要求新 `WFRL_TEST_OUTPUT`、`X100_ANALYSIS_PASS`、最终 `result.json` 及正常退出。该脚本可由 `run_regressions.py` 选择；它与安装预设、配置、绑定回放、主合同、边界、历史及观测合同后台检查列于[CI 原生选择](../../../../configs/ci-native-tests.txt)。窗口／GPU、加载／undo／redo 和原始 RGB 分别按上述窗口及生命周期入口验收，不由后台测试推定。离线 `scripts/radar_x100/coverage.py` 的完整30 Hz报告是指定研究配置的有限目标覆盖，不证明实机净空或安全合格。当前本地证据与四项独立优化的测量边界见[工作区验证](../../../../docs/blender/发布状态与验证范围.md#x100-工作区源码验证)；报告内临时路径只标识测量来源，不要求恢复已清理的测试场景或探针。

## X100 观测研究

仓库 `tests/radar_x100/test_sampling.py`、`test_installation.py`、`test_tracking.py` 分别核对面积度量与材料速度、最差覆盖与未知项、解析曲线与因果撤回。`radar_x100_observations.py` 和 `radar_x100_estimation_window.py` 均由运行器登记。后台观测合同列于 [CI 原生选择](../../../../configs/ci-native-tests.txt)，可见画布按需独立检查。普通文档／发布任务沿用各自默认范围。

```bash
python scripts/blender/run_regressions.py --tests radar_x100_observations.py \
  --output /path/to/new-observation-check
python scripts/blender/run_regressions.py --tests radar_x100_estimation_window.py \
  --output /path/to/new-estimation-window-check
```

后台要求 `X100_OBSERVATIONS_PASS`，窗口要求 `X100_ESTIMATION_WINDOW_PASS`；两者均要求最终 `result.json` 和正常退出。原生画布检查在独立配置中读取实际像素并完成卸载退出，不是外部鼠标或键盘操作验证。完整源网格的安装覆盖与叶片误差另用[研究脚本](../../../../docs/blender/X100观测研究.md)，保留证据与当前限制见[研究验证](../../../../docs/blender/发布状态与验证范围.md#x100-观测研究工作区验证)。报告内临时路径只标识测量来源；临时场景、合成输入和隔离配置不作为复验依赖，脚本使用新输出重新生成。

## 纯文档与 skill

只核对 frontmatter、链接／锚点、表格、模块／测试路径、参数及要求一致性；无需 Blender、渲染、求解或打包，只报告实际静态检查。 同步当前状态时，按交付记录与日常安装记录核对版本、归档摘要和文件数，并用当前代码、测试及配置核对接口。已保存的测量和验证结论按原对象引用。

修改 skill 的任务分类或专题组织时，用[任务场景](../evals/scenarios.json)作只读检索与决策检查：给出相关入口、验证方式和证据边界，按其中标准判断。场景描述是评估输入，不授权实际导出、安装、训练或发布；场景判断通过不代表产品功能或科学实验通过。评估输出保存在临时目录，不进入项目说明。

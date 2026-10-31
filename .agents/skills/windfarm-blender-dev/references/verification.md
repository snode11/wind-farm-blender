# 维护与验证入口

命令从 Git 根目录执行。按受影响行为读取[场景与回放](scene-playback.md#按行为选检查)、[相机与重建](cameras-reconstruction.md#按行为选检查)、[测量与后端](measurement-backend.md#按行为选检查)或[视频作业](video-jobs.md#按行为选检查)中的检查入口；无需为每个任务加载所有专题。其他用例按相邻模块查找。

## 运行来源

- 工作区、构建 ZIP、公开附件和安装分别识别。
- 兼容版本读 `blender_manifest.toml`，实际来源读 `addon.__file__`／`frontend_diagnostics.py`，载荷核对 `build-info.json`、SHA-256 与 inventory；版本号或源码同步不证明一致。
- 替换安装后重启 Blender，[日常安装身份](../../../../publication/daily-installation.json)单独核对。
- 扩展源码、资源、规范协议或内置读取器进入安装版时重建 ZIP；skill、说明及项目侧后端改动不自动要求打包。
- 构建时生成的 `protocol.py`、`_vendor/lidar/` 分别来自仓库 `wfrl/blender_bridge/messages.py`、`wfrl/lidar/`；修改规范源，不手改生成副本或替换冻结模型绕过校验。

后台、脚本窗口与真实鼠标／键盘分别报告，PASS 须匹配载荷、环境和输入。定时器注册、正常启动或单张截图不能替代脚本的最终结果。

## 执行环境与来源

使用已有依赖环境；NumPy／PyTorch 等按选定测试要求准备，不为文档任务安装依赖。

持续集成的宿主依赖、触发条件与后台选择见[自动检查](../../../../docs/development/自动检查.md)。`configs/ci-native-tests.txt` 是后台 CI 的脚本选择；运行器继续核对来源、正常退出、完成标记和最终报告。README 的身份摘要使用仓库 `scripts/ci/check_release_identity.py --release <交付记录.json>` 检查；当前记录路径以工作流参数和发布身份文档为准。显式 `--write` 从该身份记录同步摘要，不更改记录本身或验证范围。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=blender_frontend:. python -m pytest <选定测试路径> -q -p no:cacheprovider
<Blender可执行文件> --background --factory-startup --python-exit-code 1 --python <支持源码后台的脚本路径>
```

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

特殊前置条件：

- `WFRL_TEST_OUTPUT` 的文件／目录合同逐份核对，已有目录可能覆盖报告。缺陷保存／重开复用专用目录，第二个进程设 `WFRL_NREL_DEFECT_PHASE=reopen`。手动撤销／恢复依赖工厂 `Cube`，`WFRL_MANUAL_EMPTY_ONLY=1` 会缩小隔离检查范围。
- `farm_flex_workload_regression.py` 要求源码、后台工厂启动与空白注册，仅输出 `FARM_FLEX_WORKLOAD_REGRESSION_PASS`，无需输出目录。
- `live_contracts_regression.py` 必须指定尚不存在的 `WFRL_TEST_OUTPUT` 目录，由脚本创建并写入 `result.json`；检查正常退出、`LIVE_CONTRACTS_PASS` 与报告，不复用正式场景或已保存结果目录。
- 共享结构后台检查可用 `WFRL_SPLIT_INPUT` 指定场景副本；`reconstruction_workflow_window.py` 要求干净可见进程、安装模块及精确目录。`WFRL_EXPECTED_GEOMETRY_SHA256` 从已确认资产及 inventory 取得，不能照抄失败现场摘要绕过校验。
- `scripts/blender/check_nrel_nacelle_camera.py` 支持 `WFRL_ADDON_MODULE`／`WFRL_EXPECTED_ADDON_ROOT`，参数 `-- --output <新目录>`，生成 PNG、场景及参数。双束使用匹配内置包或 `WFRL_DUAL_PACKAGE`；窗口以 `WFRL_BUILTIN_METHOD=TLS` 选候选，同时核对方法、源摘要、S1 与双束。

视频脚本的媒体、完整样片及跨进程恢复覆盖见[视频验证边界](video-jobs.md#视频验证边界)。

## 构建、安装与完整播放

下表中的宿主测试位于 `blender_frontend/tests/`。构建、隔离安装与实际安装分别核对来源和结果。

| 范围 | 宿主入口 | 原生入口 |
| --- | --- | --- |
| 构建与安装 | `test_frontend_package_validation.py`、`test_extension_package.py` | 见下文构建入口；后者会构建 ZIP。 |

```bash
python3 scripts/blender/build_extension.py --output <新目录>
python3 scripts/blender/validate_frontend_package.py --output <新目录> --blender <Blender可执行文件>
python3 scripts/blender/run_playback_benchmark.py --blender <Blender可执行文件> --output <新目录> --installed-profile <隔离验证目录>
```

- 构建默认拒绝覆盖 ZIP、校验和与 inventory，`--overwrite` 才替换；`--dual-package`／`--dual-tls-package` 分别选择匹配源的 `hub-axis.v1`／`hub-tls.v1` 包。
- 隔离验证的 `--build-only` 仍构建 ZIP；禁止打包时排除该工具及 `test_extension_package.py`。其 `--dual-package` 仅指 `hub-axis.v1` 包。窗口继承输出的 `environment.json` 及 profile；缺依赖、脚本或 profile 如实报告。
- 性能入口的 `--installed-profile` 要求有效 `environment.json` 及仍存在的 profile，清除源码覆盖且不使用工厂启动；`--path / --layout` 取值查脚本。逐路核对初始绘制、持续推进、终段、心跳、最终状态及退出，结合错误／超时判断，寻址不算完成。
- `native_camera_reopen_regression.py` 覆盖单路末帧→关闭→三路重开／重播及延迟恢复；安装模式须传模块与期望目录，不借其他路径推定通过。`performance.py` 只计 CPU 回调，嵌套耗时不相加；平均 FPS、缓存计数或短时诊断不替代完整片段验收。
- 播放报告保留 `observation_s`、`intentional_pause_s`、`running_observation_s` 和暂停区间；停滞判断扣除已确认的静止主动暂停，FPS 分母仍是完整墙钟时长。峰值 RSS 按平台换算（Darwin 字节、Linux KiB），与峰值差分别报告，不把峰值差称为当前内存增长或无泄漏证明。

日常安装以[安装身份](../../../../publication/daily-installation.json)为准；相同 manifest 版本不代表与公开附件相同。日常构建的 ZIP、inventory 和 SHA-256 由安装记录的 `source_archive` 标识；公开 ZIP 保留于 [package](../../../../publication/blender-0.3.42.1/public/validated/package/)。运行来源及配置保护见安装记录中的证据链接。后台身份／注册、可见窗口和完整播放结论分别读取，其他候选载荷的性能记录不得转记到当前安装。

保留产物与已清理范围见[清理记录](../../../../publication/daily-installation-cleanup.json)。原始日志和 JSON 中的临时路径保留测量时来源，不改写成当前路径；留存 `environment.json` 不表示隔离 profile 仍存在。使用安装验证或性能入口前核对 profile，缺失时须建立并验证匹配的新隔离环境，不能把历史目录当作可运行配置。

## 纯文档与 skill

只核对 frontmatter、链接／锚点、表格、模块／测试路径、参数及要求一致性；无需 Blender、渲染、求解或打包，只报告实际静态检查。

修改 skill 的任务分类或专题组织时，用[任务场景](../evals/scenarios.json)作只读检索与决策检查：给出相关入口、验证方式和证据边界，按其中标准判断。场景描述是评估输入，不授权实际导出、安装、训练或发布；场景判断通过不代表产品功能或科学实验通过。评估输出保存在临时目录，不进入项目说明。

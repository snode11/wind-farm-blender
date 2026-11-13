# 脚本入口

从仓库根目录执行。Python、MPI、求解器、Blender 与资源恢复按[本地配置](../docs/setup/setup_local.md)准备；公开扩展离线使用按[安装说明](../docs/blender/INSTALL.md)操作。

| 目录或入口 | 用途 |
| --- | --- |
| `blender/` | Blender 启动、扩展构建、相机与缺陷工具、视频作业及原生回归运行器 |
| `train/`、`analysis/` | 训练、匹配评估及保存数据分析；[算法与输入合同](../docs/training/MAPPO_SETUP.md) |
| `analysis/export_blade_response.py`、`analysis/blade_del.py` | 从已有BeamDyn输出生成九项响应统计或研究用根部弯矩DEL；[通道、扭转限制与DEL方法](../docs/development/叶片响应与研究DEL.md)，不启动求解器 |
| `radar_x100/` | X100 严格 PLY 导入、本地发布、记录、完整源网格导出及安装覆盖；`optimize_mount.py` 搜索有限安装集合，`extract_mount_summary.py` 由完整报告提取导入摘要，`evaluate_blades.py` 评价几何因果估计，`--detections` 分别报告单帧支持与离线经过跟踪。见[数据合同](../wfrl/radar_x100/README.md)、[离线安装覆盖](../docs/blender/X100雷达.md#离线安装覆盖研究)和[安装搜索／叶片评价命令](../docs/blender/X100观测研究.md)；读取已有运动，不启动训练或求解器 |
| `lidar/` | 雷达结果包、数值诊断与来源检查；[计算入口](lidar/README.md) |
| `reconstruction/`、`nrel_*.py` | 叶片重建与独立动态研究；[研究对象与状态](../research/README.md) |
| `experiments/` | 明确对象的物理实验、数值检查与约束探针；MPI 和输入由各脚本声明 |
| `camera_video.py` | 已有相机视频包的检查、播放与服务入口；[视频包合同实现](../wfrl/camera_video/package.py) |
| `ci/` | 发布身份摘要检查；[自动检查](../docs/development/自动检查.md) |
| `figures/` | 研究图表生成，使用各脚本指定的数据源 |

## 常用检查

以下入口用于查询参数、检查身份或列出回归脚本，不启动训练或求解：

```bash
scripts/blender/run_wfrl_macos.sh --help
python3 scripts/blender/run_regressions.py --list
python3 scripts/blender/run_installed_windows.py --list
python3 scripts/ci/check_release_identity.py --release publication/blender-0.5.0.0.1.json
```

后端 checkpoint 评估通过 Blender Replay 操作，配置与模型合同见[Blender 策略评估](../docs/demos/Blender策略评估.md)。宿主测试与原生检查按[自动检查](../docs/development/自动检查.md)选择。

## 隔离安装包验证

```bash
python3 scripts/blender/validate_frontend_package.py --blender <Blender可执行文件> --output <新目录>
```

工具构建 ZIP 并在独立 profile 中安装，以 `bl_ext.wfrl_frontend.wfrl_blender` 运行默认三相机、原生投影、X100 DETECTIONS 模式与净空四项后台检查。核对安装 inventory、模块名及加载路径；雷达检查还核对已导入的扩展模块和数据路径都位于安装包内，拒绝工作区回退。每项须正常退出并留下完成标记及 PASS 报告，汇总保存为 `package-validation.json`；窗口／GPU、实机和性能不在该入口范围内。此工具只在明确需要安装验证时运行，不是普通文档同步或发布的默认步骤。

## 隔离安装窗口验收

```bash
python3 scripts/blender/run_installed_windows.py --blender <Blender可执行文件> --output <新目录>
```

默认从工作区构建 ZIP，将隔离安装及包验证结果放在 `--output/package`。可用 `--zip <路径>` 安装指定 ZIP，或用 `--installed-profile <隔离验证目录>` 复用自包含的隔离安装。该目录须有 `environment.json`，并保留配置与扩展文件。扩展兼容性缓存也写入隔离 profile，运行器拒绝日常安装与配置。

五项窗口检查串行启动，请在运行期间保持 Blender 窗口无人工操作。独立重开在后台读取纹理窗口保存的场景与 PASS 报告。`--tests` 接受脚本文件名或不带 `.py` 的名称，单选重开会记为 `NOT_RUN`。

`result.json` 汇总脚本模式、状态和失败原因，每项的 `launch.json` 记录命令与隔离环境。全部所选项 PASS 时退出码为 `0`，有失败为 `1`，其余为 `2`。

## 安装烟测

```bash
python3 scripts/blender/verify_part7_install.py --blender <Blender可执行文件> --evidence-dir <新目录>
```

从工作区构建 ZIP，在 `--evidence-dir` 下的临时目录中用全新 profile 安装并后台启用，核对模块来自隔离扩展目录及主要面板已注册。`--evidence-dir` 默认 `evidence/part7`；`package-install.json` 记录 ZIP 文件名与 SHA-256，`package-checksum.sha256`、`package-inventory.json`、`package-history-export.json` 和日志保存在同一目录，构建与 profile 临时目录在退出时删除。

## 输出与输入

`outputs/`、`results/verify/` 和 `ci-results/` 的新文件按本地运行产物忽略。Git 已跟踪的正式输入和证据仍受版本管理；确需纳入的新证据须逐项核对来源、依赖和体积后用 `git add -f -- <具体文件>` 选择，避免整目录加入。

保存实验的冻结脚本、协议和摘要按各自资源身份读取；不能用当前脚本的修改覆盖原始证据。`outputs/` 和 `results/` 的保留边界见[输出索引](../outputs/README.md)与[资源分发](../docs/PUBLICATION.md)。

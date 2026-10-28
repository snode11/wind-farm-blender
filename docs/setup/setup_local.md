# 本地环境配置与入口分流

本说明区分 Blender 离线查看、Python 研究环境、FAST.Farm 动态后端与 Qt/VTK 桌面入口。命令中的解释器、二进制及路径需匹配实际机器；Windows FLORIS 依赖组合不构成 macOS / Linux 运行验证。

## 1. Blender 离线演示

使用 Blender **5.2+**，按[安装说明](../blender/INSTALL.md)取得公开扩展 ZIP，安装后完全重启。公开包与开发机日常安装分别按[发布状态](../blender/发布状态与验证范围.md)识别。

通过 **N → MAPPO → 打开几何重建示例…** 在同一分屏切换几何／同源纹理，也可查看三机柔性回放、三相机和 NREL 缺陷编辑器。601 样本同源纹理与独立 120 样本合成资源使用不同来源和时钟；独立合成工具不在统一重建侧栏显示。随包查看无需 conda、Bridge、FAST.Farm 或网络。

当前项目源码使用 `wind-farm-rl-all-work` 分支，GitHub 自动 Source code 不作为扩展安装包。日常安装的版本、逐文件身份和适用验证见[日常安装核对](../../publication/daily-installation.json)；源码和公开发布不会自动更新实际安装。操作见[前端说明](../../前端readme.md)。软件检查不代表重建精度、现场净空精度或稳定 60 FPS 验收。

## 2. Python 环境：FLORIS 与算法开发

从实际 Git 根目录 `wind farm RL` 工作。根项目支持普通安装 `python -m pip install .`；修改源码时使用 editable 安装 `python -m pip install -e .`。两种安装均提供 `wfrl` 模块和内置机型定义 `wfrl/turbines/*.yaml`、`*.yml`；直接构建的 wheel 及从 sdist 构建的 wheel 也包含这些机型资源，`load_turbine_spec("nrel5mw")` 从实际安装包读取定义。

[pyproject.toml](../../pyproject.toml) 将 `PyYAML>=6.0.1,<7` 声明为核心依赖，训练、GUI 等依赖按功能附加组安装。普通 Python 包不包含 WFCRL、求解器、仓库级场景文件或 Blender 扩展 ZIP；训练仍需 `train` 附加组、匹配的 WFCRL 环境及相应后端运行时。建议新建 Python 3.11 环境，保留现有环境，按以下模板准备 FLORIS 算法环境：

```bash
conda create -n wfrl-doc-example python=3.11
conda activate wfrl-doc-example
python -m pip install -r wfcrl-env/requirements-floris-local.txt
python -m pip install -e ./wfcrl-env --no-deps
python -m pip install -e '.[train]'
python -m pip check
```

`wfcrl-env/requirements-floris-local.txt` 标明的是 Windows 历史工作组合，包含 FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 Qt/VTK 依赖。这些命令是环境模板，执行结果需在目标机器核对。若只做算法，不需要启动 GUI；根项目 `train` / `gui` / `all` 附加组分别定义于 [pyproject.toml](../../pyproject.toml)。

依赖角色：WFCRL 提供物理环境及 Gymnasium / PettingZoo 接口；SB3 提供 PPO；`wfrl/sb3_wrapper.py` 对齐单智能体接口，`wfrl/mappo_sb3.py` 包装参数共享 IPPO；`wfrl/mappo_central.py` 则实现集中式 MAPPO。共享参数和 `Dec_` 前缀不等于集中式 critic。

当前入口、模型与计数口径见[PPO/IPPO/MAPPO 配置](../training/MAPPO_SETUP.md)。脚本已放在 `scripts/train/` 和 `scripts/analysis/`，旧根目录 `train_ppo.py`、`train_mappo.py`、`eval_visualize_ppo.py` 命令不再适用。

```bash
# 执行会训练或求解并写入 results
python scripts/train/train_ppo.py --timesteps 100000
python scripts/train/train_mappo.py --timesteps 50000
python -m wfrl.mappo_central --timesteps 50000
tensorboard --logdir results/logs/ppo_wf
python scripts/analysis/eval_visualize_ppo.py
```

输出由 [wfrl/paths.py](../../wfrl/paths.py) 统一到 `results/checkpoints/`、`results/logs/`、`results/runs/` 和 `results/eval_outputs/`。再次训练可能覆盖同名模型。SB3 IPPO 的 `--eval-only` 与 `--resume` 必须配套加载模型和归一化文件，缺项或不兼容时明确失败；其他入口按各自规则核对文件与来源。[评估、续训和动作合同](../training/MAPPO_SETUP.md#评估续训与归一化)分别按实际训练路线使用。

## 3. FAST.Farm 动态后端与实时 Bridge

仓库已有 [FastFarmDriver](../../wfrl/fastfarm_driver.py)、[动态 MAPPO 训练](../../scripts/train/train_fastfarm.py)、[场景运行器](../../wfrl/scene/runtime.py)和 [Bridge 会话](../../wfrl/blender_bridge/backend_session.py)。

Bridge 的 Interactive 在 POSIX 平台通过 `IsolatedTrainer` 启动独立 worker，由 `wfrl/studio/trainer.py` 执行共享 actor、集中式 critic 的训练；Formal Training 委托独立命令行入口，SB3 PPO/IPPO 使用各自包装器。[算法与统计口径](../training/MAPPO_SETUP.md#interactive-的采样与更新)按实际入口区分。后端 Python 源码、公开扩展 ZIP 和本机已安装扩展分别识别，安装 Python 包不会替换 Blender 扩展。

FAST.Farm 运行需要对应平台的 MPI、FAST.Farm/OpenFAST、supercontroller 与机组控制器二进制及算例资源；FLORIS requirements 刻意没有安装这些运行时。Windows 用 Microsoft MPI，macOS 现有入口使用 OpenMPI；二进制不能跨平台互换。配置检查、解释器选择与实际生命周期证据按[前端使用说明](../../前端readme.md)和[发布状态](../blender/发布状态与验证范围.md)分层阅读。

当前 macOS Blender 启动器入口是：

```bash
# 后端/场景运行入口，与内置离线示例分开使用
scripts/blender/run_wfrl_macos.sh --help
```

它委托 `scripts/blender/wfrl_launcher.py launch`，使用实际配置的解释器和已安装扩展；修改仓库源码不会自动更新已安装扩展。不能将“离线示例能播放”推定为 Bridge 或 FAST.Farm 环境可用。

当前工作区启动器保留虚拟环境解释器路径，使用所选后端 Python 解析 YAML 并统一后端预检与 MPI 配置判定。停止未确认时继续监管并保留会话凭据；普通重连保留原身份，认领新后端须显式确认且不停止旧任务。连接恢复的 `state_revision` 语义需要匹配的前端与 Bridge；源码合同见[维护说明](../../blender_frontend/README.md#55-实时连接与控制生命周期)，公开源码与日常工作区构建的适用范围分别见[源码验证](../blender/发布状态与验证范围.md#源码软件合同与验证)和[日常安装身份](../../publication/daily-installation.json)。

## 4. 旧 Studio / RViz 桌面入口

这些是仍保留的 Python Qt/VTK 路线，与当前 Blender 离线流程不同。macOS 原生窗口需要 `pythonw`；仓库启动器同时检查 `pythonw` 和 `mpiexec`：

```bash
conda install -c conda-forge python.app
scripts/macos/run_desktop.sh studio --scene scenes/turb3_row.yaml
scripts/macos/run_desktop.sh rviz --backend floris
```

这些是源码入口模板，窗口可用性须在目标环境验证。FAST.Farm 模式或 Studio `--replay` 会重新推进物理仿真；后者从 checkpoint 执行确定性策略，不是读取已经保存的数值轨迹。Windows PowerShell [用法见演示指令单](../demos/演示指令单.md)；使用前须替换仓库路径和解释器路径，运行耗时与结果以实际配置和保留证据为准。

## 5. 兼容配置与输入约束

命令中的仓库路径、conda 解释器与 MPI 路径须指向实际机器配置。遇到 `UnicodeDecodeError 'gbk'` 时，须检查实际进程退出码和日志，不能仅凭错误文本判断任务成功。

稳态 FLORIS 返回当前来流与偏航的稳态解，没有动态尾流传播过程；动态 FAST.Farm 按输入与物理模型推进时序，湍流是否存在及其幅度取决于实际来流配置。示意性的“第 10 步到第 40 步风速变化”不构成保留运行数据，换种子也不能保证轨迹几乎相同。

训练测量与限制见[尾流控制研究](../training/WAKE_STEERING_BREAKTHROUGH.md)，长期原型见工业 AGI Prototype [计划](../proposal/platform/工业AGI_4D世界模型_可实现性审计与Prototype计划.md)。

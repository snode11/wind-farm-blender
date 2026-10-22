# 本地环境配置与入口分流

本说明区分 Blender 离线查看、Python 研究环境、FAST.Farm 动态后端与 Qt/VTK 桌面入口。命令中的解释器、二进制及路径需匹配实际机器；Windows FLORIS 依赖组合不构成 macOS / Linux 运行验证。

## 1. Blender 离线演示

使用 Blender **5.2+**，从 [v0.3.24 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.24) 安装 `wfrl_blender-0.3.24.zip` 并完全重启。

通过 **N → MAPPO → 打开几何重建示例…** 在同一分屏切换几何／同源纹理，也可查看三机柔性回放、三相机和 NREL 缺陷编辑器。601 样本同源纹理与独立 120 样本合成资源使用不同来源和时钟；独立合成工具不在统一重建侧栏显示。随包查看无需 conda、Bridge、FAST.Farm 或网络。

当前项目源码使用 `wind-farm-rl-all-work` 分支，GitHub 自动 Source code 不作为扩展安装包。日常本地安装为 **0.3.28 本地包**，181 文件与对应本地 ZIP 一致，尚未公开发布；新进程检查与安装版回归按其记录范围使用，见[日常安装核对](../../publication/daily-installation.json)；源码和公开发布不会自动更新实际安装。[安装身份与验证范围见发布状态](../blender/发布状态与验证范围.md)，操作见[前端说明](../../前端readme.md)。软件检查不代表重建精度、现场净空精度或稳定 60 FPS 验收。

## 2. Python 环境：FLORIS 与算法开发

从实际 Git 根目录 `wind farm RL` 工作。建议新建 Python 3.11 环境，保留现有环境：

```bash
conda create -n wfrl-doc-example python=3.11
conda activate wfrl-doc-example
python -m pip install -r wfcrl-env/requirements-floris-local.txt
python -m pip install -e ./wfcrl-env --no-deps
python -m pip install -e '.[train]'
python -m pip check
```

`wfcrl-env/requirements-floris-local.txt` 标明的是 Windows 历史工作组合，包含 FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 Qt/VTK 依赖。这些命令是环境模板，执行结果需在目标机器核对。若只做算法，不需要启动 GUI；根项目 `train` / `gui` / `all` 附加组分别定义于 pyproject.toml（本地文件：`pyproject.toml`）。

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

输出由 [wfrl/paths.py](../../wfrl/paths.py) 统一到 `results/checkpoints/`、`results/logs/`、`results/runs/` 和 `results/eval_outputs/`。再次训练可能覆盖同名模型；评估前确认权重与归一化匹配，不把缺文件时创建的新模型当作已训练策略。

## 3. FAST.Farm 动态后端与实时 Bridge

仓库已有 [FastFarmDriver](../../wfrl/fastfarm_driver.py)、[动态 MAPPO 训练](../../scripts/train/train_fastfarm.py)、[场景运行器](../../wfrl/scene/runtime.py)和 [Bridge 会话](../../wfrl/blender_bridge/backend_session.py)。

FAST.Farm 运行需要对应平台的 MPI、FAST.Farm/OpenFAST、supercontroller 与机组控制器二进制及算例资源；FLORIS requirements 刻意没有安装这些运行时。Windows 用 Microsoft MPI，macOS 现有入口使用 OpenMPI；二进制不能跨平台互换。配置检查、解释器选择与实际生命周期证据按[前端使用说明](../../前端readme.md)和[发布状态](../blender/发布状态与验证范围.md)分层阅读。

当前 macOS Blender 启动器入口是：

```bash
# 后端/场景运行入口，与内置离线示例分开使用
scripts/blender/run_wfrl_macos.sh --help
```

它委托 `scripts/blender/wfrl_launcher.py launch`，使用实际配置的解释器和已安装扩展；修改仓库源码不会自动更新已安装扩展。不能将“离线示例能播放”推定为 Bridge 或 FAST.Farm 环境可用。

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

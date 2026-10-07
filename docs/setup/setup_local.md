# 本地环境配置与入口分流

对齐日期：2026-10-08。以下按当前仓库文件静态核对；本轮没有安装、运行训练、求解或打开 GUI。旧 Windows FLORIS 配置仍作为历史兼容参考，不替代 macOS / Linux 验证。

## 1. 只看当前 Blender 演示

使用 Blender 5.2+，从 [v0.3.17.1 发布页](https://github.com/snode11/wind-farm-blender/releases/tag/v0.3.17.1) 安装唯一手动附件 `wfrl_blender-0.3.17.1.zip`（包内 `0.3.17+1`），升级后重启 Blender。

通过 **N → MAPPO → 同源纹理同步对照** 查看601样本同源纹理；**打开几何重建示例…**保留规范便携场景，**独立合成纹理样例**为另一个120样本来源，或使用 MAPPO 三机回放、三相机、NREL 缺陷编辑器。示例、40 Hz 保存 MAPPO 数据和纹理随 ZIP 提供，无需 conda、Bridge、FAST.Farm、训练或作者本机路径。自动 Source code 未同步这版 ZIP 的新版实现。

操作见[演示指令单](../demos/演示指令单.md)、[前端使用说明](../../前端readme.md)；验证范围和限制见[当前发布状态](../blender/发布状态与验证范围.md)。0.3.17.1 的软件安装/保存重开证据不代表重建精度、净空现场精度或稳定 60 FPS 已验收。

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

`wfcrl-env/requirements-floris-local.txt` 标明的是 Windows 历史工作组合，包含 FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、NumPy 1.26.4、SB3 2.3.2，以及训练和 Qt/VTK 依赖。上述为对齐后的命令模板，没有在本次重新安装验证。若只做算法，不需要启动 GUI；根项目 `train` / `gui` / `all` 附加组分别定义于 [pyproject.toml](../../pyproject.toml)。

依赖角色：WFCRL 提供物理环境及 Gymnasium / PettingZoo 接口；SB3 提供 PPO；`wfrl/sb3_wrapper.py` 对齐单智能体接口，`wfrl/mappo_sb3.py` 包装参数共享 IPPO；`wfrl/mappo_central.py` 则实现集中式 MAPPO。共享参数和 `Dec_` 前缀不等于集中式 critic。

当前入口、模型与计数口径见[PPO/IPPO/MAPPO 配置](../training/MAPPO_SETUP.md)。脚本已放在 `scripts/train/` 和 `scripts/analysis/`，旧根目录 `train_ppo.py`、`train_mappo.py`、`eval_visualize_ppo.py` 命令不再适用。

```bash
# 实际执行会训练/求解并写 results；本次未执行
python scripts/train/train_ppo.py --timesteps 100000
python scripts/train/train_mappo.py --timesteps 50000
python -m wfrl.mappo_central --timesteps 50000
tensorboard --logdir results/logs/ppo_wf
python scripts/analysis/eval_visualize_ppo.py
```

输出由 [wfrl/paths.py](../../wfrl/paths.py) 统一到 `results/checkpoints/`、`results/logs/`、`results/runs/` 和 `results/eval_outputs/`。再次训练可能覆盖同名模型；评估前确认权重与归一化匹配，不把缺文件时创建的新模型当作已训练策略。

## 3. FAST.Farm 动态后端与实时 Bridge

仓库已有 [FastFarmDriver](../../wfrl/fastfarm_driver.py)、[动态 MAPPO 训练](../../scripts/train/train_fastfarm.py)、[场景运行器](../../wfrl/scene/runtime.py)和 [Bridge 会话](../../wfrl/blender_bridge/backend_session.py)。早期“FAST.Farm 未实现”只描述当时 Windows FLORIS 初装阶段，不能作为当前状态。

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

这是当前源码入口模板，本次没有重新实测窗口。FAST.Farm 模式或 Studio `--replay` 会重新推进物理仿真；后者从 checkpoint 执行确定性策略，不是读取已经保存的数值轨迹。历史 Windows PowerShell 演示保留在[演示指令单](../demos/演示指令单.md)，其机器绝对路径、耗时与结果只适用于当轮。

## 5. 历史配置的使用边界

早期记录使用 `D:\project\wind farm RL`、用户 `s1155` 的 conda 路径及 MS-MPI，并只部署 FLORIS。这些路径须改为实际机器路径；不建议直接复制当时命令。早期 `UnicodeDecodeError 'gbk'` 的原因不能仅凭文本判断为无害，当前应检查实际进程退出码和日志。

稳态 FLORIS 返回当前来流与偏航的稳态解，没有动态尾流传播过程；动态 FAST.Farm 按输入与物理模型推进时序，湍流是否存在及其幅度取决于实际来流配置。示意性的“第 10 步到第 40 步风速变化”不构成保留运行数据，换种子也不能保证轨迹几乎相同。

本文统一维护当前入口与历史边界；历史训练效果见[尾流控制记录](../training/WAKE_STEERING_BREAKTHROUGH.md)，长期原型见[工业 AGI Prototype 计划](../proposal/platform/工业AGI_4D世界模型_可实现性审计与Prototype计划.md)。

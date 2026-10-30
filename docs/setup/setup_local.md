# 本地环境配置与入口分流

完整项目的 Python 安装入口为根目录 [requirements.txt](../../requirements.txt)，覆盖 WFCRL、FLORIS、FAST.Farm Python 接口、训练、Qt/VTK 桌面界面、相机视频与重建工具。本说明同时提供系统组件、资源恢复和启动要求；依赖解析成功不等于目标机器上的求解器、训练和窗口运行已经验证。

## 1. Blender 离线演示

使用 Blender **5.2+**，按[安装说明](../blender/INSTALL.md)取得公开扩展 ZIP，安装后完全重启。公开包与开发机日常安装分别按[发布状态](../blender/发布状态与验证范围.md)识别。

通过 **N → MAPPO → 打开几何重建示例…** 在同一分屏切换几何／同源纹理，也可查看三机柔性回放、三相机和 NREL 缺陷编辑器。601 样本同源纹理与独立 120 样本合成资源使用不同来源和时钟；独立合成工具不在统一重建侧栏显示。随包查看无需 conda、Bridge、FAST.Farm 或网络。

项目源码可从 `main` 或 `wind-farm-rl-all-work` 取得，本页 macOS 安装命令使用 `main`；GitHub 自动 Source code 不作为扩展安装包。日常安装的版本、逐文件身份和适用验证见[日常安装核对](../../publication/daily-installation.json)；源码和公开发布不会自动更新实际安装。操作见[前端说明](../../前端readme.md)。软件检查不代表重建精度、现场净空精度或稳定 60 FPS 验收。

## 2. macOS 完整 Python 环境

从实际 Git 根目录 `wind farm RL` 工作。根项目支持普通安装 `python -m pip install .`；修改源码时使用 editable 安装 `python -m pip install -e .`。两种安装均提供 `wfrl` 模块和内置机型定义 `wfrl/turbines/*.yaml`、`*.yml`；直接构建的 wheel 及从 sdist 构建的 wheel 也包含这些机型资源，`load_turbine_spec("nrel5mw")` 从实际安装包读取定义。

[requirements.txt](../../requirements.txt) 提供简要安装提示与 Python 依赖；[pyproject.toml](../../pyproject.toml) 保留普通包安装的核心与功能分组声明。先安装 [Miniforge](https://conda-forge.org/download/)（M 系列 Mac 选 **arm64**，Intel 选 **x86_64**），执行 `xcode-select --install` 并等待完成，重新打开终端。从[资源 Release](https://github.com/snode11/wind-farm-blender/releases/tag/project-resources-v1)下载 `wfcrl-dependency-01.tar.gz` 到下载目录，然后执行：

```bash
conda create -n wfrl-full -c conda-forge python=3.11 pip git openmpi "mpi4py>=3.1.5,<5" \
  openfast=3.5.3 "cmake<4" c-compiler cxx-compiler fortran-compiler python.app ffmpeg
conda activate wfrl-full
git clone --branch main https://github.com/snode11/wind-farm-blender.git
cd wind-farm-blender
tar -xzf ~/Downloads/wfcrl-dependency-01.tar.gz
python -m pip install -r requirements.txt
python -m pip check
```

资源下载位置不同则调整解压路径；已有源码时跳过克隆并进入实际仓库根目录。`wfcrl-env/` 由资源包恢复，pip 同时安装它与根项目的可编辑包。Conda 安装 OpenFAST **3.5.3**（含原生 `FAST.Farm`）、OpenMPI、编译工具、FFmpeg 与 `pythonw`；[OpenFAST 官方文档](https://openfast.readthedocs.io/en/dev/source/install/)支持通过 conda-forge 安装。`cmake<4` 用于编译资源包中的控制器工程，控制器编译和后端检查见下节；Blender 单独安装。

数值组合使用 NumPy 1.26.4、FLORIS 3.5、Gymnasium 0.29.1、PettingZoo 1.24.3、SB3 2.3.2 和 OpenCV 4.11；同一环境只安装 `opencv-python`，不同时安装其他提供 `cv2` 的包。Intel macOS 的 PyTorch 固定为 2.2.2，Apple Silicon 使用 `torch>=2.2,<3`；Python、MPI、求解器和控制器须使用相同 CPU 架构，避免混用 Rosetta 与原生二进制。MPI 安装方式见 [mpi4py 官方说明](https://mpi4py.github.io/mpi4py/stable/html/install.html)。

本入口包含重建工具的通用依赖；`blade_recon/requirements.txt` 和 `blade_recon0.2/requirements.txt` 固定的是独立研究环境，不能叠加安装到本环境。完整环境不声明精确复现这些固定环境的测量结果，第三方研究模型源码、权重和输入仍按各自资源说明取得。只需要部分模块时可使用 pyproject 的功能分组；`all` 组仅包含训练与桌面 GUI，不等同于完整环境。

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

完成上节安装后，激活 `wfrl-full`，在仓库根目录编译机组控制器 `DISCON` 与 supercontroller `SC_DLL`。每条命令成功后再继续：

```bash
wfrl_solver_root="$PWD/wfcrl-env/wfcrl/simulators/fastfarm"
cmake -S "$wfrl_solver_root/src/DISCON" -B "$wfrl_solver_root/src/DISCON/build-mac" \
  -DCMAKE_Fortran_COMPILER="$(command -v mpifort)"
cmake --build "$wfrl_solver_root/src/DISCON/build-mac"
cmake --install "$wfrl_solver_root/src/DISCON/build-mac"
cp "$wfrl_solver_root/servo_dll/DISCON.dll" "$wfrl_solver_root/servo_dll/DISCON_WT1.dll"
cmake -S "$wfrl_solver_root/src/SC_DLL" -B "$wfrl_solver_root/src/SC_DLL/build-mac" \
  -DCMAKE_Fortran_COMPILER="$(command -v mpifort)"
cmake --build "$wfrl_solver_root/src/SC_DLL/build-mac"
cmake --install "$wfrl_solver_root/src/SC_DLL/build-mac"
```

控制器使用 `.dll` 文件名，但在 macOS 上必须是本机架构的 Mach-O 动态库。Python、MPI、求解器与控制器使用同一架构和兼容的 MPI 运行时；不能使用 Windows 二进制。检查路径、文件类型，再运行短仿真：

```bash
export WFCRL_FASTFARM_EXECUTABLE="$CONDA_PREFIX/bin/FAST.Farm"
export WFCRL_OPENFAST_EXECUTABLE="$CONDA_PREFIX/bin/openfast"
file "$WFCRL_FASTFARM_EXECUTABLE" "$wfrl_solver_root/servo_dll/"{DISCON_WT1,SC_DLL}.dll
openfast -v
mpiexec -n 1 python scripts/experiments/smoke_fastfarm.py
```

M 系列 Mac 的文件类型应包含 `arm64`，Intel 为 `x86_64`。短仿真须正常退出、打印测量数据并出现 `SMOKE_OK`；这些命令是安装与检查入口，不代表目标机器已经通过验收。生成新来流还需可用的 TurbSim，Blender 5.2+ 及扩展按[安装说明](../blender/INSTALL.md)准备。手动视频监管要求 `os.waitid` / `os.WNOWAIT`；已检查的 macOS Python 3.11 环境不具备这些接口，不能据此环境声明该功能可用。

求解脚本可通过 `WFCRL_FASTFARM_EXECUTABLE`、`WFCRL_OPENFAST_EXECUTABLE`、`WFCRL_TURBSIM_EXECUTABLE` 或 `PATH` 定位二进制；Blender 启动器使用自身的 `--python`、`--mpi`、`--fastfarm` 参数或对应配置。机组控制器、supercontroller 路径由算例输入决定，必须分别核对。执行下列入口前，恢复选定场景需要的资源和模型权重，见[分发说明](../PUBLICATION.md)。解释器选择与生命周期的验证范围见[前端使用说明](../../前端readme.md)和[发布状态](../blender/发布状态与验证范围.md)。

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

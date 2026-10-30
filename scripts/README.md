# 脚本入口

从仓库根目录执行。Python、MPI、求解器、Blender 与资源恢复按[本地配置](../docs/setup/setup_local.md)准备；公开扩展离线使用按[安装说明](../docs/blender/INSTALL.md)操作。

| 目录或入口 | 用途 |
| --- | --- |
| `blender/` | Blender 启动、扩展构建、相机与缺陷工具、视频作业及原生回归运行器 |
| `train/`、`analysis/` | 训练、匹配评估及保存数据分析；[算法与输入合同](../docs/training/MAPPO_SETUP.md) |
| `lidar/` | 雷达结果包、数值诊断与来源检查；[计算入口](lidar/README.md) |
| `reconstruction/`、`nrel_*.py` | 叶片重建与独立动态研究；[研究对象与状态](../research/README.md) |
| `experiments/` | 明确对象的物理实验、约束探针及 Qt/VTK 诊断；需要的窗口、MPI 和输入由各脚本声明 |
| `macos/run_desktop.sh` | macOS Studio／RViz 桌面启动器；[Studio 策略评估](../docs/demos/Studio策略评估.md) |
| `camera_video.py` | 已有相机视频包的检查、播放与服务入口；[视频包合同实现](../wfrl/camera_video/package.py) |
| `ci/` | 发布身份摘要检查；[自动检查](../docs/development/自动检查.md) |
| `figures/` | 研究图表生成，使用各脚本指定的数据源 |

## 常用检查

以下入口用于查询参数、检查身份或列出回归脚本，不启动训练或求解：

```bash
scripts/blender/run_wfrl_macos.sh --help
scripts/macos/run_desktop.sh --help
python3 scripts/blender/run_regressions.py --list
python3 scripts/ci/check_release_identity.py --release publication/blender-0.3.41.json
```

`experiments/verify_desktop_contracts.py` 检查 RViz 的来流、地形、中文标签、双视图与源码结构；默认包含可见窗口检查，`--online` 还会运行 FAST.Farm。它依赖完整桌面环境，不能当作无窗口宿主测试执行。

## 输出与输入

`outputs/`、`results/verify/` 和 `ci-results/` 的新文件按本地运行产物忽略。Git 已跟踪的正式输入和证据仍受版本管理；确需纳入的新证据须逐项核对来源、依赖和体积后用 `git add -f -- <具体文件>` 选择，避免整目录加入。

保存实验的冻结脚本、协议和摘要按各自资源身份读取；不能用当前脚本的修改覆盖原始证据。`outputs/` 和 `results/` 的保留边界见[输出索引](../outputs/README.md)与[资源分发](../docs/PUBLICATION.md)。

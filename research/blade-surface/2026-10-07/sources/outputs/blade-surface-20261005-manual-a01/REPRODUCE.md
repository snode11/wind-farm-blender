# 复现正式 attempt02

所有相对路径从真实 Git 根 `/Users/eason/Desktop/wfcrl/wind farm RL` 执行。先阅读 `REPORT.md` 和 `input_contract_final.json`。正式输入已保存在 `input/solver_bundle_clean`；`input/solver_bundle` 和 `groups/A*` 是保留的第一轮，不得混作正式结果。现有输出不要覆盖；复现时给 `--out` 一个新目录。

## 数据与运行环境

Blender 5.2.1 LTS；Python 使用 `blade_recon/.venv-trial/bin/python`，NumPy 2.5.3、SciPy 1.18.1、OpenCV 5.0.0。未安装 torch/CoTracker，未运行自动跟踪；本次使用 Codex 审核的 2D 角点候选。测试依赖仅在本次 `runtime_pytest`，没有改共享环境。

采集命令、场景同步提交、坐标与失败配置见 `capture/README.md`。正式捕获为 132.0–132.7 s、8 张 4K WideInput；WideEval 为独立评价。显式 MODEL→CV 只应用一次。

## 求解

输入预处理只读 WideInput RGB、校准及 2D ROI，保留内部补漆占据，不填洞。命令使用 macOS kernel sandbox，禁止评价、MAPPO 源资产、旧解和原场景进入求解进程。

```sh
/usr/bin/sandbox-exec -f outputs/blade-surface-20261005-manual-a01/solver_read_policy.sb blade_recon/.venv-trial/bin/python outputs/blade-surface-20261005-manual-a01/scripts/prepare_input.py --capture outputs/blade-surface-20261005-manual-a01/input/formal_window --regions outputs/blade-surface-20261005-manual-a01/input/manual_regions.json --out outputs/blade-surface-20261005-manual-a01/input/solver_bundle_clean --min-component-area 10000
```

随后显式复制冻结 `manual_annotations.json`，并在新输入 manifest 写 `annotations` 字段；该步骤已记录在当前 bundle，不进行自动重标注。

```sh
/usr/bin/sandbox-exec -f outputs/blade-surface-20261005-manual-a01/solver_read_policy.sb /usr/bin/time -l blade_recon/.venv-trial/bin/python outputs/blade-surface-20261005-manual-a01/scripts/solve_a0.py --input outputs/blade-surface-20261005-manual-a01/input/solver_bundle_clean --out outputs/blade-surface-20261005-manual-a01/groups/attempt02/A0 --fit-scale .25
/usr/bin/sandbox-exec -f outputs/blade-surface-20261005-manual-a01/solver_read_policy.sb /usr/bin/time -l blade_recon/.venv-trial/bin/python outputs/blade-surface-20261005-manual-a01/scripts/solve_window.py --input outputs/blade-surface-20261005-manual-a01/input/solver_bundle_clean --a0 outputs/blade-surface-20261005-manual-a01/groups/attempt02/A0/recon.json --out outputs/blade-surface-20261005-manual-a01/groups/attempt02 --target 0 --fit-scale .25 --diagnostics
```

`frozen_configuration.json` 保存完整初值、物理先验、实际活动变量与边界、权重、停止预算、像素 affine、q 初值与每个允许输入的 SHA-256。主组停止预算 60、A2 的固定状态 q 定位预算 30。`primary_results_frozen.json` 保存正式输出哈希，`formal_freeze_authorization.json` 记录独立评价开始前的复核。

A1 与 A3 都从同一 fresh A0 开始。A3 只增加 4 个跨帧共享材料坐标 q 和 32 个已审核像素点；不能由 A1 热启动。A2 表面包的 recon 必须与 A1 文件 SHA-256 一致，q-only 定位不更新其状态。

## RGB 外观与实际窗口

`texture/README.md` 保存显式帧列表、生成和验证命令；`texture/frames.json` 只列 WideInput。所有纹理构建同样使用 kernel sandbox。`surface.json` 是版本化 sidecar；v1 recon 原格式保留。每个已捕获 atlas 像素都有源 RGB、相机、帧、原图像素和 q/三角形绑定证据。未知为橙色，冲突为紫色。

`validation/validate_surface_window.py` 使用独立 Blender 配置和 factory 启动实际窗口，验证 8 个采样时刻寻址/暂停、timer 提交、顶点、固定 UV、保存重开和损坏 sidecar 拒绝。`validation/build_surface_comparison.py` 构建同刻 132 s 静态 A2/A3 比较；布局平移只发生在显示对象，不能进入数值评价。

## 独立评价与软件测试

只有正确正式结果冻结后才运行 `evaluation/evaluate_material.py`。实际命令和指标保存在 `evaluation/attempt02`。首帧 RGB 射线在真实显示源网格上绑定仅用于独立评价，后续时间固定该材料点；首帧构造性吻合不计入主要跟踪漂移。留出 RGB 从不用于纹理、初始化或调参。

```sh
PYTHONPATH=outputs/blade-surface-20261005-manual-a01/runtime_pytest:blade_recon:blender_frontend:. blade_recon/.venv-trial/bin/python -m pytest blade_recon/tests/test_window_surface_fit.py -q
env PYTHONPATH=. blade_recon/.venv-trial/bin/python outputs/blade-surface-20261005-manual-a01/texture/test_surface_pipeline.py
```

数学 fixture 只证明软件链路；实际捕获 RGB、源材料参考、留出视角和原生窗口分别保存，不能互相替代。输出保持 REVIEW_ONLY。

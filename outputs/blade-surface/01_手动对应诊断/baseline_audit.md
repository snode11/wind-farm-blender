# 当前基线复核

日期：2026-10-05，实验尚在执行。实际Git根 `REPOSITORY_ROOT`。当前HEAD `e504ed66548c4b774664233341b70c87512561ef`，不能代表未提交磁盘源码。远端 `blender-sync` 为公开前端；`origin` 是其他仓库；本轮不访问、推送或覆盖远端。

算法真实位置是Git根下 `blade_recon/`，旧同级路径已经失效。原 model/recon/camera 不改；新驱动来源于历史a02但移除路径、双路及601假设。旧背景统计从全601取样，不复用。源模型补漆已接入并不意味着历史a02 PNG中有补漆，本轮重新采集。

全部既有Git状态保存在 baseline_git_status.txt，初始28610项（包含未跟踪历史产物）；保留全部。本轮只新增日期目录和独立surface模块，不清理、不重跑FAST.Farm/MAPPO、不替换日常安装。

- `REPOSITORY_ROOT/blade_recon/model.py`: SHA-256 `9253fdfb7bb1e76790129511a24b98311c08c7c14a2a54b0c012d260842b888c`
- `REPOSITORY_ROOT/blade_recon/camera.py`: SHA-256 `3c18384c5bb549a80945a766412c5c0ae14cd890f4900b5b94e6022cc123d644`
- `REPOSITORY_ROOT/blade_recon/recon.py`: SHA-256 `e5aeeb8b5cfe9f69507a07e925e7a85ca763a3204757bb1bb57de5dc07e6bf88`

安装目录身份另存 installed_identity.json；正式采集/验证使用factory-startup与独立配置的工作区源码，这不算日常安装版验证。健康先验固定现有NREL近似模板、13维状态；翼型/结构定义偏差单独分析。

开发/求解：NacelleT1。独立留出：T1Down。划分在背景、颜色、跟踪统计前冻结。时间/相机标定允许，原补漆参数、源网格/状态、对象ID只评价使用。A1/A3比较冻结同参数、先验、边界、初始化及终止规则。无确认用途精度阈值，最终保持REVIEW_ONLY。

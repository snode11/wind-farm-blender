# 正式三窗的求解侧 recipe（尚未启动正式测试）

本文件与 `freeze_window_configuration.py` 在正式 freeze 前准备。每窗仅消费该窗经 root 批准的 public RGB、标定、掩膜与二维审阅轨迹，先由原冻结 `scripts/prepare_input.py` 复制到 canonical input；所有 manifest/annotation/掩膜/RGB 哈希在交付审批后填入，严禁 source、绑定、ID 或 WideEval。

每窗顺序固定：sandbox `scripts/solve_a0.py` 得到本窗 fresh A0（若公开 RGB 的 blade0 身份不唯一则停止）；sandbox `development/freeze_window_configuration.py --kind test8` 从本窗首帧 RGB + 本窗 A0 生成共同 q0，并逐值继承 development 的 config、四组固定子集、sigma、prior、边界、FD 步、radius、60 预算与终止；sandbox 原 `scripts/solve_groups.py --mode groups` 求四组。不得跨窗热启动、复用旧 q 或按评价改参数。

同窗四组共32活动状态，各帧其他9参数固定 A0；三窗共12组。每组按原冻结 driver 写 result/recon/solver_trace，先冻结这三个 SHA，再产生共同12首帧公开RGB射线 q_eval，缺失、miss和错误侧面保持 unknown。三窗全部12组 result/recon/solver_trace/common12 q_eval 的哈希总表 `all12_geometry_frozen.json` 写出后，root 才开启任何正式测试几何评分；任何单窗完成均不足以开始评分。求解侧不读评价结果。

传给各步：`--input <canonical input> --out <assigned p2b/groups/test_windowXX leaf>`；freeze 与 groups 另传 `--a0 <fresh recon.json>`，groups 传 `--configuration <frozen_configuration.json>`。使用 `scripts/p2b_guard.py:write_policy` 为每步写独立 sandbox policy，运行工作目录 `/tmp`，Python 固定 `blade_recon/.venv-trial/bin/python`。五个 BLAS/OMP 线程变量均1；测试窗最多2个独立流水线并行：窗1/2先行，任一完成后窗3接槽，每窗内 A0→q0→四组顺序；auto/long固定队列占第三槽。跨全部 solver 最多3个独立进程，禁止同进程 API 并发。

启动正式窗口依赖 root 的正式一次冻结授权；本 recipe 不构成启动测试授权。新 helper 不改原冻结 driver；helper SHA 见 `test_helper_preparation.json`。

公开 expected 合同逐值校验精确 frame/time、A0 frame/t/sim_t、WideInput 3840×2160、K==开发公开anchor；T保持本窗capture审批的逐帧值，每frame/time/K/T的SHA逐值校验。v2把T视为静态的假设已被公开dev标定否定，失败证据保留；v3不改数据、不引入容差。root-approved plan按 `formal_test_jobs_template.json` 填完整输入文件哈希、planned_times及public_camera_contract列表；每次求解开始前全局调度锁/PID租约限制全部solver子进程最多3。

# =====================================================================
#  WFRL Studio 一键演示脚本
#  演示动线: 先离线功能(不训练) -> 再点「▶ 开始训练」看实时 RL
#  用法: 右键「使用 PowerShell 运行」, 或在 PowerShell 里:  .\demo_studio.ps1
#
#  关键: 场景是 fastfarm 后端, 训练要 MPI_Comm_spawn, 必须经 mpiexec 启动 ——
#        直接 python -m 会在点训练时报 "需要 MPI_Comm_spawn" 并崩线程。
#        本脚本已用官方 mpiexec 拉起 Studio, 离线与训练都在同一进程里可用。
#
#  可选参数:
#     -Real   : 真实时长训练(去掉 --fast, 每回合约 4 分钟), 默认走快预设
#     -NoWake : 不建 FLORIS 尾流平面(省一次稳态求解, 启动更快)
#     -Scene <path> : 换场景 YAML(默认 turb3_stagger 错列三机, 与 D5 训练同一个)
# =====================================================================
param(
    [switch]$Real,
    [switch]$NoWake,
    [string]$Scene = "scenes\turb3_stagger.yaml"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---- 路径(按需改这三行) --------------------------------------------
$PY   = "C:\Users\s1155\.conda\envs\wfcrl\python.exe"
$MPI  = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$ROOT = "D:\project\wind farm RL"

# ---- 前置检查 ------------------------------------------------------
if (-not (Test-Path $PY))   { Write-Host "[X] 找不到 python: $PY"   -ForegroundColor Red; exit 1 }
if (-not (Test-Path $MPI))  { Write-Host "[X] 找不到 mpiexec: $MPI" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $ROOT)) { Write-Host "[X] 找不到项目目录: $ROOT" -ForegroundColor Red; exit 1 }
Set-Location $ROOT
if (-not (Test-Path $Scene)) { Write-Host "[X] 找不到场景文件: $Scene" -ForegroundColor Red; exit 1 }

# ---- 组装参数 ------------------------------------------------------
#  不加 --train => 界面打开后训练「不」自动开始, 先做离线演示, 再手动点开始
$appArgs = @("-m", "wfrl.studio.app", "--scene", $Scene)
if (-not $Real) { $appArgs += "--fast" }      # 快预设: iters=2 n_steps=16, 只看流程不出结论
if ($NoWake)    { $appArgs += "--no-wake" }

# ---- 现场提示 ------------------------------------------------------
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  WFRL Studio 演示  场景: $Scene  (经 mpiexec 启动)" -ForegroundColor Cyan
if ($Real) { Write-Host "  模式: 真实时长(每回合约 4 min)" -ForegroundColor Yellow }
else       { Write-Host "  模式: 快预设 --fast(几分钟看到实时 RL, 不出结论)" -ForegroundColor Green }
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "【第一段 - 离线功能, 先不点训练】" -ForegroundColor White
Write-Host "  1) 左侧「场景」面板: 说明改 YAML 机位/来流, 仿真与 3D 一起变"
Write-Host "  2) 左侧「手动摆位」面板: 选机组, 拨偏航/桨距/转速滑块 -> 3D 里机头立即转"
Write-Host "     (纯几何演示, 不启动重仿真; 讲『上机前先摆姿态』)"
Write-Host "  3) 菜单「视图」->「相机传感器」: 打开 T1 机舱相机的视锥框(视野范围)"
Write-Host "     菜单「视图」->「地形」: 无/山地/戈壁; 「主相机」: 俯视/侧视/斜视"
Write-Host "  4) 左侧「数据通道」面板: 取消勾选 lidar -> 说明订阅是真的停采样"
Write-Host ""
Write-Host "【第二段 - 实时 RL, 点右侧「▶ 开始训练」】" -ForegroundColor White
Write-Host "  5) 点 ▶ 开始训练: 状态栏显示『启动 FAST.Farm(spawn 约 25s)』"
Write-Host "  6) spawn 完: 3D 里机组按实测偏航摆位, 按实测转速自转"
Write-Host "  7) 右侧「训练」面板: 功率/奖励双曲线实时走"
Write-Host "  8) 右侧「物理与安全约束」面板: 动作被占空比清零时标红一行(带源码出处)"
Write-Host "  9) 可点「⏸ 暂停」(可继续) 或「⏹ 停止」(排空预算需几十秒, 状态栏会提示)"
Write-Host ""
if (-not $Real) {
    Write-Host "  * --fast 只够看流程动起来, 功率曲线不体现学习;" -ForegroundColor DarkYellow
    Write-Host "    收敛结论看 D5 的 40 轮离线结果图 slides\figs\fig_d5_binding.png" -ForegroundColor DarkYellow
    Write-Host ""
}
Write-Host "启动中... 关闭窗口即结束演示。" -ForegroundColor Cyan
Write-Host ""

# ---- 启动: 经官方 mpiexec 拉起 Studio(整个脚本仅此一处 &) -----------
& $MPI -n 1 $PY @appArgs
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "[!] Studio 退出码 $code。若为字体/VTK 报错, 见 wfrl-demo-powershell 记忆。" -ForegroundColor Red
}
exit $code

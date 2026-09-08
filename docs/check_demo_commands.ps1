# 校验《演示指令单》里的 PowerShell 语法与路径。演示前跑一次，不启动任何仿真。
#   powershell -NoProfile -ExecutionPolicy Bypass -File docs\check_demo_commands.ps1
#
# 本文件必须存成 **UTF-8 with BOM**。PowerShell 5.1 读 .ps1 时没有 BOM 就按
# 系统 ANSI 代码页解码，中文会被拆成半个字节、把后面的引号一起吃掉，报一串
# "The string is missing the terminator" —— 和语法本身无关。改这个文件时注意
# 别让编辑器把 BOM 去掉。

$ErrorActionPreference = "Stop"
# 控制台输出编码也要钉：文件有 BOM 只保证 PowerShell 把源码读对，写到屏幕上
# 还要看 [Console]::OutputEncoding，默认是 ANSI 代码页，中文会显示成乱码。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location "D:\project\wind farm RL"

$PY  = "C:\Users\s1155\.conda\envs\wfcrl\python.exe"
$MPI = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"

function Check($label, $cond, $detail = "") {
    $mark = if ($cond) { "v" } else { "X" }
    Write-Host "  $mark $label$(if ($detail) { " - $detail" })"
    return [bool]$cond
}

$ok = $true
Write-Host "`n[1] 路径"
$ok = (Check "python.exe 存在" (Test-Path $PY) $PY) -and $ok
$ok = (Check "mpiexec.exe 存在" (Test-Path $MPI) $MPI) -and $ok
$ok = (Check "验收脚本存在" (Test-Path "scripts\experiments\verify_day2.py")) -and $ok
$ok = (Check "湍流盒子存在" (Test-Path "wfcrl-env\wfcrl\simulators\fastfarm\inputs\template\FarmInputs\90m_08mps.bts")) -and $ok

Write-Host "`n[2] 调用运算符（带空格的路径必须用 &）"
$v = & $PY -c "import sys; print(sys.version.split()[0])"
$ok = (Check "& `$PY 能执行" ($LASTEXITCODE -eq 0) "python $v") -and $ok
$m = & $PY -c "import wfrl.viz.rviz_app as r; print(r.TURB_SCALE)"
$ok = (Check "-m wfrl.viz.rviz_app 可导入" ($LASTEXITCODE -eq 0) "TURB_SCALE=$m") -and $ok

Write-Host "`n[3] mpiexec 转发参数（这里最容易写成 '& `$MPI -n 1 & `$PY'，5.1 下会报错）"
$out = & $MPI -n 1 $PY -c "print('mpi-forward-ok')"
$ok = (Check "& `$MPI -n 1 `$PY ... 正确" ($out -match "mpi-forward-ok") "$out") -and $ok

Write-Host "`n[4] 参数解析（不真跑仿真，只让 argparse 走一遍）"
$h = & $PY -X utf8 -m wfrl.viz.rviz_app --help 2>&1 | Out-String
foreach ($flag in @("--backend", "--wake-vtk", "--turb", "--terrain", "--dual-view",
                    "--focus", "--controls", "--headless", "--flex-scale", "--model",
                    "--wind", "--list-wind")) {
    $ok = (Check "$flag 存在" ($h -match [regex]::Escape($flag))) -and $ok
}

Write-Host "`n[4b] 风况预设（--wind 的名字必须都能解析）"
$w = & $PY -X utf8 -m wfrl.viz.rviz_app --list-wind 2>&1 | Out-String
foreach ($name in @("calm", "rated", "strong", "gale", "veer", "turb")) {
    $ok = (Check "预设 $name 在册" ($w -match "\b$name\b")) -and $ok
}

Write-Host "`n[5] 孤儿进程（有残留会让下一次启动无声挂起）"
$orphan = Get-Process FAST.Farm_x64_OMP_2023 -ErrorAction SilentlyContinue
$ok = (Check "无 FAST.Farm 孤儿" ($null -eq $orphan) `
        $(if ($orphan) { "发现 $($orphan.Count) 个，先 Stop-Process -Name FAST.Farm_x64_OMP_2023 -Force" } else { "clean" })) -and $ok

Write-Host "`n$('=' * 56)"
Write-Host $(if ($ok) { "DEMO_COMMANDS_OK - 指令单可用" } else { "DEMO_COMMANDS_FAILED - 见上面的 X" })
exit $(if ($ok) { 0 } else { 1 })

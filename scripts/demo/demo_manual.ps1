# Demo mode: Manual startup-shutdown cycles with yaw/pitch visualization
# Runtime: ~5 minutes (160 steps, 2 cycles)
# Shows: pitch 90->0 (startup), yaw 0->30 (steering), pitch 0->90 (shutdown)

$env:SKIP_MPI_CHECK = "1"

$mpiexec = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$python = "C:\Users\s1155\.conda\envs\wfcrl\python.exe"
$project = "D:\project\wind farm RL"

Write-Host "Starting demo mode: 2 startup-shutdown cycles"
Write-Host "Cycle sequence:"
Write-Host "  1. Parked (pitch=90deg) - turbines stopped"
Write-Host "  2. Startup (pitch 90->0deg) - blades pitch to operating angle"
Write-Host "  3. Running + yaw (0->30deg) - T1 yaws for wake steering"
Write-Host "  4. Shutdown (pitch 0->90deg) - blades feather to stop"
Write-Host ""
Write-Host "Total runtime: ~5 minutes (160 steps)"
Write-Host ""

& $mpiexec -n 1 $python "$project\scripts\demo\demo_manual_control.py"

Write-Host ""
Write-Host "Demo complete!"
Write-Host ""
Write-Host "Key observations:"
Write-Host "  - Pitch changes: 90deg (stopped) <-> 0deg (running)"
Write-Host "  - Yaw changes: T1 sweeps 0->30deg during operation"
Write-Host "  - Wake deflection: visible when T1 yaws"
Write-Host "  - Power output: rises during startup, peaks at optimal yaw"

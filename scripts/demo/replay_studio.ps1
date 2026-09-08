# Studio Replay: 3D visualization of learned wake steering policy
# Runtime: ~12 minutes (400 FAST.Farm steps)
# Expected: T1 yaw 30-36 deg, farm power 3.28 MW, wake deflection visible

$env:SKIP_MPI_CHECK = "1"

$mpiexec = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$python = "C:\Users\s1155\.conda\envs\wfcrl\python.exe"
$project = "D:\project\wind farm RL"

$ckpt = "$project\results\checkpoints\mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref.pt"
$scene = "$project\scenes\turb3_stagger.yaml"

Write-Host "Starting Studio replay mode..."
Write-Host "Checkpoint: stagE400ref (reference reward, +5.3% wake steering)"
Write-Host ""
Write-Host "What to watch:"
Write-Host "  - T1 (upstream) yaw angle grows to ~34 deg"
Write-Host "  - T2/T3 (downstream) stay at 0 deg"
Write-Host "  - Wake plane (white mesh) deflects left as T1 yaws"
Write-Host "  - Farm power stabilizes at 3.28 MW (+5.3% vs 3.12 MW baseline)"
Write-Host ""
Write-Host "Controls:"
Write-Host "  - Replay starts automatically (~12 min, 400 steps)"
Write-Host "  - Press [Pause] to stop at any frame"
Write-Host "  - Left-drag to rotate, scroll to zoom"
Write-Host ""

& $mpiexec -n 1 $python -m wfrl.studio.app `
  --scene $scene `
  --replay `
  --ckpt $ckpt `
  --replay-steps 400 `
  --warmup 8

Write-Host ""
Write-Host "Replay complete!"

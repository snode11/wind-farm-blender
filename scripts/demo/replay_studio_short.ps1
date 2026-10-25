# Short replay test: 50 steps (~3 minutes) to verify visualization works
$env:SKIP_MPI_CHECK = "1"

$mpiexec = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$python = "C:\Users\s1155\.conda\envs\wfcrl\python.exe"
$project = "D:\project\wind farm RL"

$ckpt = "$project\results\checkpoints\mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref.pt"
$scene = "$project\scenes\turb3_stagger.yaml"

Write-Host "Starting SHORT replay test (50 steps, ~3 min)"
Write-Host "Purpose: Verify wake/yaw visualization works"
Write-Host ""
Write-Host "What to check:"
Write-Host "  1. Left panel: Check 'yaw', 'pitch', 'power' in Channel Panel"
Write-Host "  2. Right panel: Watch Step count increase (sampling phase)"
Write-Host "  3. 3D view: Rotate view to see white wake plane at hub height"
Write-Host "  4. After step 20: T1 yaw should start growing"
Write-Host ""

& $mpiexec -n 1 $python -m wfrl.studio.app `
  --scene $scene `
  --replay `
  --ckpt $ckpt `
  --replay-steps 50 `
  --warmup 4

Write-Host ""
Write-Host "Short test complete!"

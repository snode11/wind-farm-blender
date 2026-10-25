# Rollout 确定性策略（Seed 1）—— 读出策略真正学到的偏航角
# 运行时间：约 10-12 分钟
# 预期结果：T1 偏航 30-36°，全场功率 3.28 MW (+5.3%)

$env:SKIP_MPI_CHECK = "1"

$mpiexec = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$python = "C:\Users\s1155\miniconda3\envs\wfcrl\python.exe"
$project = "D:\project\wind farm RL"

$ckpt = "$project\results\checkpoints\mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_s1.pt"
$out = "$project\results\runs\stagE400ref_s1_rollout.npz"
$scene = "$project\scenes\turb3_stagger.yaml"

Write-Host "开始 rollout（确定性策略，无探索噪声）..."
Write-Host "Checkpoint: stagE400ref_s1"
Write-Host ""

& $mpiexec -n 1 $python "$project\scripts\experiments\rollout_ckpt.py" `
  --ckpt $ckpt `
  --out $out `
  --scene $scene `
  --ep-steps 400

Write-Host ""
Write-Host "Rollout 完成！轨迹保存在："
Write-Host "  $out"
Write-Host ""
Write-Host "下一步：运行 visualize_rollout.ps1 可视化策略执行过程"

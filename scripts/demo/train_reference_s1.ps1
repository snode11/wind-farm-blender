# Reference reward 训练（Seed 1）—— 验证 wake steering 可复现性
# 运行时间：约 8 小时
# 预期结果：T1 偏航 30-36°，全场功率 +5% 增益

$env:SKIP_MPI_CHECK = "1"

$mpiexec = "C:\Program Files\Microsoft MPI\Bin\mpiexec.exe"
$python = "C:\Users\s1155\miniconda3\envs\wfcrl\python.exe"
$project = "D:\project\wind farm RL"

Write-Host "开始训练 stagE400ref_s1（seed 1）..."
Write-Host "配置：reference reward + 零 duty penalty"
Write-Host ""

& $mpiexec -n 1 $python "$project\scripts\train\train_fastfarm.py" `
  --scene "$project\scenes\turb3_stagger.yaml" `
  --iters 40 `
  --episode-steps 400 `
  --reward reference `
  --reward-ref 1.7017 `
  --duty-penalty 0.0 `
  --tag stagE400ref_s1 `
  --seed 1

Write-Host ""
Write-Host "训练完成！Checkpoint 保存在："
Write-Host "  results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_reference_E400_none_stagE400ref_s1.pt"
Write-Host ""
Write-Host "下一步：运行 rollout_s1.ps1 验证学到的策略"

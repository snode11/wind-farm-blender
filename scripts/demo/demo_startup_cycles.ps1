# Demo mode: Scripted startup-shutdown cycles (yaw/pitch/rpm visualization)
# Pure scripted animation - NO FAST.Farm (starts instantly, no MPI, no spawn wait).
# Wake plane hidden (--no-wake) to avoid the white FLORIS plane + its warnings.
#
# Sequence (TS=2.0, rates halved / runtime doubled):
#   Cycle 1: parked -> pitch 90->0 -> spin 0..8rpm (staggered yaw 60: T1->T2->T3)
#            -> hold 8rpm -> feather 0->90 + stop
#   Cycle 2+: keep yaw/position, directly re-pitch 90->0 -> spin -> feather (no reset)

$env:SKIP_MPI_CHECK = "1"

$python = "C:\Users\s1155\.conda\envs\wfcrl\python.exe"
$project = "D:\project\wind farm RL"
$scene = "$project\scenes\turb3_demo.yaml"

Write-Host "Starting scripted demo (no FAST.Farm, no wake plane)"
Write-Host ""
Write-Host "What to watch:"
Write-Host "  - 3D view: pitch 90<->0 (feather/operate), rotor spin, staggered yaw"
Write-Host "  - Right panel: per-turbine curve buttons T1/T2/T3/all -> yaw/pitch/power"
Write-Host "  - Stage hint (blue banner): current action"
Write-Host ""

& $python -m wfrl.studio.app `
  --scene $scene `
  --demo `
  --demo-cycles 2 `
  --no-wake

Write-Host ""
Write-Host "Demo complete!"

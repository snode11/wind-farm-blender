# WFRL Blender acceptance matrix

## Reading this matrix

`PASS` means the stated boundary has direct evidence in this checkout. `PARTIAL` means lower-level behavior passed while a required UI or end-to-end boundary remains unproved. `PENDING` means the target platform, hardware, dependency, or native workflow has not been exercised. Python test counts support individual contracts; they do not establish product acceptance by themselves.

The user has deferred Windows hardware testing and Blender Extension/App Template display and final brand evaluation. Those items do not block this Mac-and-delivery-scripts iteration, but they remain outside any future full cross-platform or branded-release acceptance claim.

## macOS

| Area | Mode | State | Evidence and boundary |
| --- | --- | --- | --- |
| Frontend | Extension install/reload and Local Demo | PASS | Native Blender 5.2.1 smoke evidence from Parts 2/6; package version 0.2.0. Local Demo is explicitly `SYNTH`. |
| Frontend | Views and window screenshot | PASS | Native Blender 5.2.1 presentation smoke and `evidence/part6.5/capture.log`; screenshot PNG exists. |
| Frontend | PNG sequence recording | PASS | `evidence/part6.5/capture/recording-*/manifest.json`: `COMPLETE`, one PNG at 1 fps / 0.1 s. This closes the two findings in `evidence/part6/review-2026-09-07.md` for that regression case only. |
| Frontend | Interactive/Formal dashboard in a native window | PENDING | Protocol, state, and dashboard unit coverage exists, but there is no recorded native UI run proving actual trainer JSONL → Bridge → visible dashboard values and freshness transitions. |
| Frontend | JSON history export through UI | PARTIAL | `wfrl.export_history` is registered in the Live Telemetry panel and the fresh-package smoke writes and reads a JSON export. A native visible-window export artifact and failure-state capture remain unrecorded. |
| Frontend | Manual pose isolation | PARTIAL | Code and pure tests enforce paused Local Demo/SYNTH isolation; the requested native `manual_pose_smoke.py` evidence is absent. No real actuator UI is claimed. |
| Backend | Replay numerical/lifecycle | PASS | `evidence/part6/replay_numeric.json` and `.log`: final `STOPPED`, backend not alive. This validates the recorded backend boundary, not policy quality. |
| Backend | Interactive FAST.Farm lifecycle/control | PASS | Fresh `evidence/part7/fastfarm-control.json` and `.log`, plus prior Part 6 evidence: MPI-correct macOS run, `passed=true`, final `STOPPED`, backend not alive. This is real Trainer → Bridge codec/lifecycle evidence, not an installed Blender GUI run or cross-run simulator equivalence. |
| Backend | Formal training progress producer/reader | PARTIAL | Focused Python tests cover JSONL production, reading, run isolation, truncation/error cases, and lifecycle. A native Blender UI run with actual formal training statistics is still required. |
| Backend | FLORIS wire lifecycle | PASS | `evidence/part7/floris-wire.json`: fresh real TCP lifecycle run. This is backend/Bridge evidence and does not establish native Blender dashboard behavior. |
| Backend | Idle same-session reconnect | PASS | `evidence/part7/reconnect.json`: fresh Bridge reconnect evidence while idle. Active-run and visible UI reconnect behavior remain in the pending native UI boundary. |
| Backend | Genuine target FAST.Farm distribution/hardware | PENDING | The recorded macOS binary run is useful local evidence. Release acceptance still requires the intended distributable executable and target hardware/configuration to be named and exercised. |

Current macOS status is partial product acceptance. Capture regressions are closed, and backend Replay/FAST.Farm evidence exists; dashboard/export UI and deferred visual/brand review prevent full acceptance.

## Windows 64-bit（目标平台，当前未完成）

| Area | Mode | State | Missing proof |
| --- | --- | --- | --- |
| Frontend | Install/reload and Local Demo | PENDING | Windows host with Blender 5.2+, installation from the same versioned ZIP, reload, and visible Local Demo evidence. |
| Frontend | Views, screenshot, and PNG recording | PENDING | Native Windows window capture, manifest, and layout evidence. |
| Frontend | Interactive/Formal dashboard | PENDING | Actual trainer-to-visible-UI values and freshness behavior on Windows. |
| Frontend | JSON history export through UI | PENDING | The same operator is packaged for Windows, but no Windows install or UI run has been exercised. |
| Backend | Bridge and Replay | PENDING | Windows Python environment, scene/checkpoint run, lifecycle log, and process cleanup evidence. |
| Backend | Microsoft MPI and FAST.Farm | PENDING | Genuine Windows machine with compatible Conda Python, Microsoft MPI, and FAST.Farm; run `scenes/turb3_ctrl3.yaml` through STARTING/RUNNING/DRAINING/STOPPED and record process cleanup. |

No macOS result substitutes for a Windows result. Windows is an intended target platform, but acceptance remains incomplete because no Windows machine is currently available for installation, Blender, MPI, FAST.Farm, and end-to-end UI validation.

## Required evidence before full acceptance

1. Record native Blender runs for actual Interactive and Formal training dashboards, including run identity, phase/metric age independence, unsupported fields, reconnect behavior, and visible source labels.
2. Record a native visible-window JSON history export, inspect a produced document for bounded/truncated metadata, same-step phases, provenance, errors, and write-failure reporting.
3. Run the manual pose native smoke and confirm Resume restores the scripted pose without producing physical power or backend commands.
4. When the user resumes Windows validation, complete the Windows matrix on genuine Windows hardware with the platform's actual MPI and FAST.Farm dependencies.
5. When the user resumes visual packaging, complete the deferred Extension/App Template display and brand evaluation before any final branded distribution decision.

The fresh scoped baseline is recorded in `evidence/part7/baseline-tests.log` (`170 passed, 2 skipped`). It supports regression status only and does not upgrade pending matrix rows.

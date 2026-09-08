# Part 6 execution record

Plan: `docs/superpowers/plans/2026-09-04-blender-frontend-implementation.md`, Part 6.

Work continues in the existing `now` checkout, consistent with Part 4's recorded workspace preference.
Status: implementation complete within the Part 6 contract; acceptance evidence recorded below.

Baseline: 118 tests and 27 subtests passed with the host Python environment
(`PYTHONPATH=blender_frontend:. /opt/anaconda3/bin/python -m pytest ...`).

| Scope | Producer / consumer and decision |
| --- | --- |
| Configuration and lifecycle | BackendSession owns validation and lifecycle; Blender queues commands without blocking. |
| Telemetry history | SnapshotAdapter supplies values and metadata; charts store bounded per-ID history. Missing channels remain missing. |
| Presentation | Camera and capture UI use Blender APIs without advancing backend control steps. |
| Manual control | Existing Trainer has no external manual actuator API. Do not bypass its policy and safety limiter; unsupported real control remains explicit. |
| Acceptance | Fake, FLORIS and FAST.Farm evidence are separate; no claim of full parity until every matrix row has evidence. |

## Delivered

- Four-mode workflow settings and isolation: Demo, interactive training, formal training and Replay.
- Scene loading/validation with atomic overrides; backend, inflow, terrain and control-channel configuration.
- Async lifecycle commands (`start`, `pause`, `resume`, `single-step`, `stop`, `reset`) with authoritative `DRAINING` and `STOPPED` states.
- Canonical telemetry channels, bounded per-turbine chart history, explicit gaps, units, fidelity and provenance.
- Safety event history with rule counts and requested/applied values when the backend supplies them.
- Camera focus/FOV/pitch controls, SINGLE/DUAL/QUAD layouts, presentation mode, screenshot and wall-clock PNG recording.
- Blender registration and workflow smoke coverage; existing Demo workflow remains green.

## Verification

| Check | Result | Evidence |
| --- | --- | --- |
| Python regression suite | PASS — 118 tests, 27 subtests | `PYTHONPATH=blender_frontend:. /opt/anaconda3/bin/python -m pytest -q blender_frontend/tests tests/blender_bridge -k 'not replay_import'` |
| Blender extension registration | PASS | `blender_frontend/tests/blender/workflow_smoke.py` |
| Camera/presentation registration | PASS | `blender_frontend/tests/blender/part6_presentation_smoke.py` |
| Existing Demo workflow regression | PASS | `blender_frontend/tests/blender/demo_workflow_smoke.py` |
| Extension lifecycle/reload | PASS | `blender_frontend/tests/blender/extension_lifecycle_smoke.py` |
| Interactive view layout | PASS; real Blender window verified `QUAD → SINGLE` joins back to one 3D View | `cameras.set_view_layout()` exercised in Blender 5.2.1 LTS UI process |
| Replay backend numerical acceptance | PASS; `STOPPED`; no backend alive | `evidence/part6/replay_numeric.json` and `.log` |
| FAST.Farm interactive control/safe stop | PASS; `STOPPED`; no backend alive | `evidence/part6/fastfarm_control_rerun.json` (MPI-correct rerun) and `fastfarm_control.json` |

## Boundary kept explicit

- The two backend JSON records validate the Bridge codec and lifecycle against the existing Trainer/FAST.Farm source; they do not claim independent simulator runs are numerically identical.
- Existing `Trainer` exposes no external manual yaw/pitch/torque actuator API. Blender therefore labels real manual control as backend-policy controlled instead of bypassing the safety layer.
- Formal training remains an asynchronous wrapper around the existing `train_fastfarm.py`; its CLI does not provide live telemetry, so the UI says so rather than fabricating it.
- The first direct FAST.Farm rerun without `mpiexec` was rejected by the existing launcher guard and is not counted as acceptance; the recorded PASS used `/opt/homebrew/bin/mpiexec -n 1`.

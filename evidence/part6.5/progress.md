# Part 6.5 execution record

Plan: `docs/superpowers/plans/2026-09-04-blender-frontend-implementation.md`, Part 6.5.
Status: implementation and focused Python checks complete; product acceptance remains partial.
Workspace: existing `now` checkout, following Part 4/6 recorded preference. Existing uncommitted Part 5/6 changes are retained.

## Execution order

1. Protocol, metric definitions, run identity and per-connection capability gating — implemented and covered by focused tests.
2. Actual trainer JSONL producer → Bridge reader → frontend state.
3. Paused-only manual display locks and mode-specific controls.
4. Dashboard, curves and bounded asynchronous JSON export.
5. Backend and native Blender acceptance, including Part 6 recording regression.

## Interface review

| Stages | Producer / consumer | Contract |
| --- | --- | --- |
| 1 / 2 | training_stats codec → JSONL reader and transport | Required run_id, progress vs iteration_stats, safe counters, field metadata, 30-second freshness. |
| 2 / 4 | Actual trainer output → dashboard/history | No derived fake losses; progress cannot refresh previous metric age. |
| 2 / 3 | lifecycle/run_id → display locks | New run/reset/scene clears locks; disconnect never confirms STOPPED. |
| 3 / 4 | manual pose → telemetry/export | Display pose remains SYNTH and separate from measured backend channels. |
| 4 / 5 | bounded history → export evidence | 600 raw records per series; preserve phases and sequences, count dropped records. |

All stages are consistent with the frozen Part 6.5 decisions. Existing Part 6 acceptance has two known recording findings in `evidence/part6/review-2026-09-07.md`; both require revalidation before acceptance.

## Session recovery

Initial protocol subagent failed before edits due to an account usage-limit response. User resumed execution; protocol work was redispatched. Initial test process was lost across turn interruption, so the baseline was rerun rather than counted as passing.

## Verified so far

- Baseline: 121 Python tests passed after interruption recovery; final scoped suite: 170 passed, 2 skipped.
- Stage 1: 79 focused protocol/transport/backend tests passed; independent scoped review and re-review passed. Added run_id to reconnect handshake after review finding. Training source age includes unsent server queue time, frozen once first byte transmits.
- Recording regression: 2 production-method tests passed; native Blender 5.2.1 window produced COMPLETE with 1 PNG frame at 1 FPS / 0.1 s. `capture.log` and `capture/recording-*/manifest.json` are fresh evidence. Background presentation smoke passed.
- Pure training dashboard: 3 tests passed (progress/metric age independence, gating, run isolation, unsupported fields).
- Pure history export: 3 tests passed (601 records, same-step phases, raw metadata, worker serialization, write failure). UI integration pending.
- Snapshot source adaptation: 21 focused backend tests passed. RPM fidelity now follows the actual backend or explicit source provenance; missing per-turbine reward is explicit unsupported. Existing generic FAST.Farm test fixture now identifies its backend.

## Focused verification result

- Fixed the resumed UI-state regression: FAILED sessions remain locked until reset, while formal-training pause/resume follows negotiated capabilities and single-step remains disabled.
- Final command: `PYTHONPATH=blender_frontend:. /opt/anaconda3/bin/python -m pytest -q blender_frontend/tests tests/blender_bridge -k 'not replay_import'`.
- Result: `170 passed, 2 skipped`.
- The full repository collection still includes an unrelated replay-import module requiring the optional `torch` dependency; it is excluded from the scoped Blender/Bridge acceptance command above.

This test count does not establish full UI or product acceptance. Native Blender 5.2.1 evidence closes the earlier recording regression for the exercised 1 FPS / 0.1 s case: `capture.log` reports `COMPLETE`, and the manifest contains one PNG frame. Screenshot capture also completed.

## Evidence still missing

- No native Blender run demonstrates actual trainer JSONL → Bridge → visible Interactive/Formal dashboard values, freshness transitions, or reconnect behavior.
- The raw bounded-history and asynchronous JSON writer have pure tests, but no history-export operator or panel is registered; there is no end-user UI export evidence.
- Paused Local Demo manual-pose isolation is covered in code/pure checks, but the planned native `manual_pose_smoke.py` artifact is absent.
- No Windows extension, capture, Bridge, Replay, MPI, or FAST.Farm run is recorded; the user explicitly deferred Windows target-machine testing until after the Mac and delivery scripts.
- Final Blender Extension/App Template display and brand evaluation is explicitly deferred by the user. These deferred items do not block the current Mac-and-scripts iteration, but remain outside full cross-platform/branded acceptance.

Platform and frontend/backend acceptance states are maintained in `docs/blender/ACCEPTANCE.md`. The repository must not be described as fully accepted until the pending native UI and genuine target-platform requirements are evidenced.

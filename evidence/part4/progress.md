# Part 4 execution record

Plan: `docs/superpowers/plans/2026-09-04-blender-frontend-implementation.md`, Part 4.
Reference task: `01a07265-39dc-7882-accb-adf2da7501a4` (3结尾).

- User reports previous tests completed; do not repeat Part 1–3 or Blender startup diagnostics.
- Work continues on the existing `now` branch, preserving the user's uncommitted Part 2/3 work and assets.
- Backend ownership: snapshot adapter, session, server, CLI, new backend tests.
- Frontend ownership: operators, runtime integration, kinematics, telemetry, new frontend tests.
- Integration ownership: documentation, packaging, Part 4 verification and review.

| Interface / scope | Review and ruling |
| --- | --- |
| Backend Snapshot → frontend animation | Existing protocol v1 is authoritative; stable IDs, channel units and fidelity metadata must survive transport. |
| Lifecycle → frontend state | Capabilities use existing `pause` and `single_step` names. Blocking shutdown must remain off the Blender timer. |
| Existing Trainer → Bridge | Wrap the existing loop; account for Trainer.stop clearing its thread reference even after a timeout. |
| FLORIS → Trainer | SceneRuntime currently rejects FLORIS. Reuse an existing FLORIS path without claiming it is interactive Trainer training. |
| Old tests → new acceptance | Prior acceptance is user-reported. Only new Part 4 checks are run; no previous tests or startup crash reproduction. |

## Completed implementation

- Backend: value-only `SnapshotAdapter`, one-owner `BackendSession`, loopback
  `BridgeServer`, CLI entrypoint, deterministic fake fixture and a steady-state
  FLORIS adapter. Interactive and replay modes wrap the existing `Trainer`;
  formal training launches the existing `scripts/train/train_fastfarm.py`
  through `mpiexec -n 1`.
- Frontend: nonblocking connection/run operators, latest-only replaceable
  Snapshot handling, reliable lifecycle/safety handling, live scene validation,
  telemetry panel and wall-time rotor integration. Local Demo and connected
  backend Demo share the `demo` business mode without conflating connection
  state.
- Shutdown: formal training owns an isolated process group and escalates
  SIGINT to SIGTERM/SIGKILL only after the configured timeout. A forced cleanup
  is reported as `FAILED`, never as a clean stop.

## Verification on 2026-09-06

| Boundary | Result | Evidence |
| --- | --- | --- |
| Backend Part 4 tests | 30 passed + 27 subtests | adapter, protocol, reconnect, reliable queue, failure, timeout and owned process-group cleanup |
| Frontend Part 4/state tests | 46 passed | latest Snapshot, safety retention, mode/capability handoff, geometry IDs and wall-time animation |
| Fake wire lifecycle | PASS, 11 messages | `evidence/part4/fake_wire.json` |
| FLORIS wire lifecycle | PASS, 11 messages | `evidence/part4/floris_wire.json` |
| FAST.Farm wire lifecycle | PASS, 11 messages | `evidence/part4/fastfarm_wire.json` |
| Blender 5.2.1 + fake Bridge | PASS | real Blender background smoke, live scene + rotor integration + unload cleanup |
| Blender 5.2.1 + FLORIS Bridge | PASS | real Blender background smoke; unsupported RPM stayed absent and did not animate |
| Packaged ZIP + Blender 5.2.1 + fake Bridge | PASS | extracted `wfrl_blender-0.2.0.zip` registered and completed the live lifecycle outside the source package path |
| Process cleanup | PASS | no Bridge, `mpiexec`, FAST.Farm or listener remained after stop |

All three wire runs produced the lifecycle sequence `STARTING -> RUNNING ->
PAUSED -> PAUSED(single-step acknowledgement) -> RUNNING -> DRAINING -> STOPPED`.
The fake steps were `0 -> 1 -> 2`, FLORIS steps were `1 -> 2 -> 3`, and the
FAST.Farm snapshots were `0 -> 0 -> 1`: its pause-boundary initialization
Snapshot legitimately repeats step 0, while the explicit single-step advances
to physical step 1.

The real FAST.Farm physical step retained IDs `T1/T2/T3`, direct yaw, pitch,
power and load, synthesized drivetrain RPM with explicit provenance, farm power
and reward. FAST.Farm terminated normally after draining its fixed 129-second
simulation budget. No physical-device or field measurement claim is made.

Verification boundary: Blender consumed the same frozen protocol using real
fake and FLORIS Bridge processes. FAST.Farm was verified through the real
Bridge and transport probe, not by launching another Blender process; the
Blender consumer is backend-agnostic at this protocol boundary.

## Re-review fixes

- Lifecycle events now repeat the authoritative mode and capabilities, so a
  formal training start cannot leave Blender displaying Demo controls.
- Single-step remains pending until a newer Snapshot arrives; the immediate
  duplicate `PAUSED` lifecycle is not treated as step completion.
- A socket disconnect preserves the backend run for explicit session resume;
  only Bridge shutdown stops the owned backend.
- Reliable lifecycle, safety and error events survive same-session reconnect;
  unsent Snapshots coalesce, and a fresh session cannot inherit old backlog.
- `FAILED` cannot be overwritten by repeated stop or bypassed by mode changes;
  natural completion preserves `DRAINING -> STOPPED` and formal launcher
  failures clean the owned process group.
- Saved live scenes are marked separately from local Demo scenes, safety-event
  display memory is bounded, fatal protocol errors close the connection, and
  lone-surrogate strings are rejected.

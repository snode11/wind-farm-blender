# WFRL Blender user guide

For the current step-by-step Chinese manual, including macOS/Windows launch instructions and the Gimbal Camera panel, see [用户使用手册](用户使用手册.md).

## Source labels

Treat every displayed value according to its label:

- `DIRECT`: obtained from a backend channel directly.
- `EXPORTED`: read from a simulator-produced artifact.
- `DERIVED`: calculated from identified source fields.
- `SYNTH`: scripted or decorative data, with no claim that it came from the physical backend.
- `UNKNOWN`, `unsupported`, `waiting`, or `stale`: do not interpret the field as a current physical measurement.

The terrain, atmosphere, offline wake proxy, Local Demo telemetry, and manual pose are presentation features marked `SYNTH`. The Live Telemetry panel shows each backend channel's unit, fidelity, validity, error, and provenance. A missing channel is displayed as unavailable; it is not filled with zero.

## Local Demo

Choose **Demo**, then **Load Demo Scene**. This builds a fixed three-turbine NREL 5 MW presentation. **Start Demo** plays one deterministic 66-second sequence at 25 fps. Pause, step one display frame, stop and reset are local Blender controls.

While paused, expand **Manual Pose / Paused Demo** to preview yaw and pitch on the selected turbine. This changes display geometry only. RPM is held at zero for the override, power becomes unavailable, and Resume restores the scripted pose. It never sends yaw, pitch, or torque to a simulator.

## Offline lidar clearance replay

Version 0.2.2 adds two precomputed FAST.Farm clips. Follow [the Chinese step-by-step instructions](用户使用手册.md#12-激光净空雷达第一次照着操作) to launch the dedicated demo, switch clips, pause, seek, and read clearance and error. Keep the `normal-v1.1` and `close-v1.1` result directories with the checkout; raw solver surfaces are unnecessary for playback. The display is a rigid motion illustration, and the values are simulation results, not field measurements.

## Interactive Training

1. Start the Bridge with a configured FAST.Farm environment, select **Interactive**, and connect. The current FLORIS adapter supports Backend Demo only, not Interactive Training or Replay.
2. Choose a default scene in extension Preferences. Optionally enable **Override scene configuration** and set backend, requested inflow, decorative terrain, and control channels.
3. Choose **Load & Validate Scene**. The effective configuration appears only after backend acknowledgement.
4. Set iterations, rollout steps, warmup steps, and seed.
5. Choose **Start Training**.

Pause, Resume, and Step are enabled only when the connected backend advertises the corresponding capability and its lifecycle state permits the action. Stop enters `DRAINING`; wait for authoritative `STOPPED`. A disconnect leaves the last status unconfirmed and does not mean the backend stopped.

Real yaw, pitch, and torque remain controlled by the Trainer policy and safety layer. The current Trainer has no external manual actuator command API.

## Formal Training

Select **Formal Training**, configure the training fields, and optionally select a compatible checkpoint to resume. **Submit Formal Training** launches the existing formal training workflow through the Bridge. Single-step is disabled.

Formal training uses structured trainer progress when the `training_stats_v1` capability is negotiated. Progress and metric records have separate 30-second freshness clocks. A newer phase record does not make an older metric current. The formal CLI does not supply live per-step telemetry, so the interface must not be read as a live simulator view while that data is unavailable.

## Replay

Select **Replay**, choose a compatible checkpoint, set replay steps, warmup steps, and seed, then choose **Start Replay**. Replay performs deterministic policy inference without PPO updates. Checkpoint compatibility errors are reported rather than hidden.

## Views and fidelity-safe presentation

In Local Demo, use **WFRL / PRESENTATION > Views & Layers** for World, Top, Side, close-up, sensor, and World + Nacelle views. Use the sidebar **Item** tab for the Gimbal Camera controls (older versions used **Camera**). **Views & Capture** currently contains wake, presentation, and capture controls; it does not expose Apply Camera or Single/Dual/Quad buttons. See the Chinese manual for returning from the gimbal local camera to the World view. Presentation mode does not change data fidelity.

Telemetry curves retain up to 600 samples per turbine and channel. Gaps, fidelity changes, and mixed units break or suppress a plot rather than joining incompatible data.

## Capture

Set an output folder in **WFRL / Views & Capture**.

- **Save Screenshot** writes the visible Blender window as a PNG at screen resolution, including source labels.
- **Record PNG Sequence** records the visible window on wall-clock time. Set 1–30 fps and a duration from 0.1 to 3600 seconds. Press Esc to cancel. The recording directory contains numbered PNG files and `manifest.json` with actual timestamps and display-frame numbers.

Slow capture may drop requested samples. Recording never advances or controls backend steps. The output is a PNG sequence, not a video; encode it separately if a video container is required.

In **WFRL / Live Telemetry**, choose **Export Bounded History**, select a `.json` path, and wait for the status to become `COMPLETE`. The export is a bounded snapshot of the current run, includes schema/run/sequence and source metadata, and does not change simulation state. Serialization and file I/O run outside Blender's event loop; a write failure is shown as `FAILED`.

## Appendix: turbine dimensions and assembly

See [附录 A：风机模型尺寸与部件装配位置](用户使用手册.md#附录-a风机模型尺寸与部件装配位置) for tower, nacelle, hub and blade dimensions, relative placement, and source limitations.

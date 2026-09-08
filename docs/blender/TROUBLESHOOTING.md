# WFRL Blender troubleshooting

## The extension does not appear

Confirm Blender is 5.2.0 or newer and that **WFRL Blender** is enabled under **Edit > Preferences > Extensions**. Open a 3D View, press **N**, and select the **Item** tab. Rebuild the ZIP after source changes; the installed extension does not read Python files from the repository.

## Blender crashes before opening on macOS

Run `python scripts/blender/blender_preflight.py` with the Blender executable. Blender 5.2.1 can crash during Metal probing when launched under a restricted `CODEX_SANDBOX`, before extension Python runs. Start Blender from Finder or a normal Terminal outside that sandbox. This startup crash does not diagnose the extension.

## Environment check fails

Open extension Preferences and use absolute paths. Check each result independently:

- Python must be the project environment, not Blender's bundled Python.
- MPI and FAST.Farm are required only for workflows that use them.
- Local Demo works without backend Python, MPI, or FAST.Farm.
- A FAST.Farm executable built for another OS or architecture is not compatible even if the file exists.

## Cannot connect to the Bridge

Start `python -m wfrl.blender_bridge --host 127.0.0.1 --port 8765` in the backend environment. Match the port in Blender Preferences. The Bridge accepts loopback IP literals only and one client at a time. Keep the terminal visible and resolve import or simulator errors there.

After a network interruption, use **Connect / Reconnect**. The interface intentionally marks lifecycle state unconfirmed until a handshake succeeds. Do not assume Disconnect stopped a training or simulator process.

## Controls are disabled

Configuration is locked during an active or unconfirmed session. Pause and Resume require a backend `pause` capability; Step additionally requires `single_step`, is available only while paused, and is always disabled for Formal Training. During `DRAINING`, wait for `STOPPED`. A `FAILED` session must be reset before starting again.

## Scene will not load

Select an absolute scene YAML in extension Preferences, connect first, and stop any active run. **Load & Validate Scene** sends the scene and optional overrides to the backend; displayed effective settings update only after acknowledgement. FAST.Farm ignores requested wind direction in the current workflow, and a turbulence box can override requested speed.

## Telemetry is blank, stale, or unsupported

`waiting` means no qualifying record has arrived. `stale` means its source-age threshold elapsed. `unsupported` means the producer did not provide the channel or the optional protocol capability was not negotiated. Inspect the displayed error and provenance. Do not substitute a nearby field or treat an unavailable value as zero.

Formal training may report structured progress without live per-step telemetry. Progress messages also do not refresh older training metrics. Sensor subscriptions apply at a control-step boundary and are separate from pose telemetry.

## Manual pose does not affect the backend

That is the defined behavior. Manual yaw and pitch are a paused Local Demo geometry preview marked `SYNTH`. Real actuator commands are not exposed because the Trainer does not provide a manual override API through the safety layer.

## Screenshot or recording problems

Capture requires a visible Blender window and a writable output directory. Background Blender cannot use window screenshots. Recording writes PNG files plus `manifest.json`; it does not create MP4. If capture is slow, fewer frames than requested can be normal because sampling follows wall-clock time and never advances backend steps.

The native macOS regression for the prior zero-frame/`Event.timer` failure was revalidated in Blender 5.2.1 with a 1 fps, 0.1-second recording that produced one frame. No equivalent Windows capture run is recorded yet.

## JSON history export fails

Use **WFRL / Live Telemetry > Export Bounded History** and choose a writable `.json` path. `WRITING` means the detached serializer is still running; wait for `COMPLETE` or inspect the visible `FAILED` message. The output is a bounded current-run snapshot, not a complete training archive. If the control is absent, rebuild and reinstall the ZIP: Blender does not load source checkout changes from an already installed package.

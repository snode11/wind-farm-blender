# Frontend display fixes — 2026-09-08

Changes:
- Scene overlay accepts Blender IDPropertyArray power values.
- Live overlay reads runtime lifecycle and KinematicState directly, sharing the telemetry panel's receiver-age validity rules. Disconnected, unconfirmed, stale and incomplete power data do not become farm totals. Power units are normalized before summing; requested wind is labelled as requested.
- Viewport history charts have numeric ticks, grids, a contrasting background, wrapped bounded legends and stronger selected-turbine lines. Missing samples remain gaps.
- Wake geometry, materials, lighting and sidebar panels are unchanged.

Verification:
- `/opt/anaconda3/envs/wfrl-mac/bin/python`, with `PYTHONPATH=blender_frontend`: `pytest blender_frontend/tests tests/blender_bridge -q` — 186 passed, 27 subtests passed, 5 trainer warnings (empty means and tensor-to-scalar conversion), 9.75 seconds.
- New regression cases were first run failing, then passing.
- Blender 5.2.1 native numeric property array: total 6 MW rendered by the formatter without exception.
- Native GPU smoke `blender_frontend/tests/blender/overlay_draw_smoke.py`: both production draw callbacks executed without error. Wide and narrow viewport screenshots inspected.
- `git diff --check` passed.

Screenshots use synthetic test records and Blender's default cube, not a physical backend run. This was intentionally a lightweight display check, without long-duration performance tests, heavy scene rendering or full FAST.Farm end-to-end validation. The script writes screenshots and results to `/tmp/wfrl-overlay-review` and closes its own Blender process.

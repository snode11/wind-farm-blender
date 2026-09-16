# WFRL Blender user guide · 0.3.1

See the [current complete Chinese manual](用户使用手册.md) for Windows/macOS installation, playback, deflection comparison, clearance readings, backend workflows and troubleshooting. A short presentation sequence is available in [MAPPO演示.md](MAPPO演示.md).

The default demo is a recorded 60-second MAPPO segment with three flexible towers and nine flexible blades. It replaces the historical scripted 66-second Local Demo and manual pose workflow.

- **挠度**: select T1 blade 1/2/3, compare the structural tip with an independent rigid reference, and inspect deflection components.
- **净空**: select T1/T2/T3 and inspect B2 slant range, clearance truth, estimate, error and measurement age.
- **工具**: choose cameras, telemetry/charts/export, environment, capture and data reload.

Shared playback, step, reset and progress controls are above the three pages. Page changes preserve time and camera. The reference follows tower-top motion; tower bending has no dedicated ghost or displacement card. Missing values remain missing.

The release ZIP includes all default playback data and works without backend dependencies. Real backend Replay/Interactive/Formal Training require separate setup. The physical recording remains REVIEW_ONLY; Windows host testing, complete numerical convergence, sustained 60 FPS and field accuracy are not established. See [release verification](0.3.1发布核对.md).

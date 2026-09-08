# WFRL Blender installation

## Supported package

The current extension package is `dist/wfrl_blender-0.2.0.zip`. Its manifest requires Blender 5.2.0 or newer. The same ZIP is intended for macOS and 64-bit Windows, but this checkout contains native installation evidence only for Blender 5.2.1 on macOS. Windows installation remains to be exercised on a Windows host.

Build a fresh ZIP from the repository root when source files have changed:

```text
python scripts/blender/build_extension.py
```

The build script copies the extension Python, bundled geometry and landscape assets, and the canonical Bridge protocol into `dist/wfrl_blender-0.2.0.zip`.

## Install the extension

1. Open Blender 5.2 or newer.
2. Open **Edit > Preferences > Extensions**.
3. Use the Extensions menu and choose **Install from Disk**.
4. Select `dist/wfrl_blender-0.2.0.zip` and enable **WFRL Blender**.
5. Open a 3D View and press **N**. WFRL panels appear in the **Item** tab.

Do not unzip the package into the project or add the project environment to Blender's Python. The extension deliberately uses Blender's Python only for UI and rendering; the WFRL Bridge runs in the project's existing Python environment.

## Configure backend workflows

Local Demo needs no backend configuration. For Interactive, Formal Training, or Replay, open **Edit > Preferences > Extensions > WFRL Blender** and set:

- **Project directory**: this repository root.
- **Backend Python**: the Python executable in the environment where WFRL and its simulator dependencies are installed.
- **MPI executable**: required by workflows that launch FAST.Farm through MPI.
- **FAST.Farm executable**: required for FAST.Farm scenes.
- **Default scene**: an absolute scene YAML path, such as `scenes/turb3_ctrl3.yaml`.
- **Port**: the Bridge port, default `8765`.

Use absolute paths. In the **WFRL / CONNECTION** panel, choose **Check Environment** and read the Blender, Python, MPI, and FAST.Farm rows independently. Missing MPI or FAST.Farm does not block Local Demo.

## Start the Bridge manually

From the configured backend environment and repository root:

```text
python -m wfrl.blender_bridge --host 127.0.0.1 --port 8765
```

The Bridge accepts loopback IP addresses only. Keep its terminal open so startup errors and cleanup status remain visible. In Blender, select a backend mode and choose **Connect / Reconnect**. The port must match the extension preference.

## Platform launchers

The extension ZIP must already be installed. The launchers then start an owned Bridge, wait for its loopback listener, open Blender, configure the installed extension preferences, connect, and stop only the Bridge process they started. They refuse an occupied port instead of attaching to an unknown service.

On macOS:

```text
scripts/blender/run_wfrl_macos.sh --blender /Applications/Blender.app/Contents/MacOS/Blender --python /absolute/path/to/python --scene /absolute/path/to/scene.yaml
```

On Windows PowerShell:

```text
scripts\blender\run_wfrl_windows.ps1 --blender C:\path\to\blender.exe --python C:\path\to\python.exe --scene C:\path\to\scene.yaml
```

Both wrappers pass arguments as arrays to `scripts/blender/wfrl_launcher.py`. Supported options are `--blender`, `--python`, `--scene`, `--port`, `--project-dir`, `--mpi`, `--fastfarm`, `--fake`, and `--startup-timeout`. Environment defaults are `WFRL_BLENDER`, `WFRL_PYTHON`, `WFRL_MPI`, and `WFRL_FASTFARM`; `WFRL_LAUNCHER_PYTHON` selects the Python used to run the launcher itself. With no project or scene option, the launcher uses this repository and `scenes/turb3_demo.yaml`; it contains no developer-specific path default.

FAST.Farm scenes require compatible `--mpi` and `--fastfarm` paths and run through `mpiexec -n 1`. Fake and FLORIS modes launch directly. See `ACCEPTANCE.md` before treating a launcher or platform as released.

The verification wrappers accept the same options plus `--json`:

```text
scripts/blender/verify_macos.sh --json
scripts\blender\verify_windows.ps1 --json
```

Verification reports a required backend component as `MISSING` when MPI or FAST.Farm configuration is absent; it does not convert an unconfigured dependency into a pass.

## macOS launch preflight

For a direct Blender launch check:

```text
python scripts/blender/blender_preflight.py --blender /Applications/Blender.app/Contents/MacOS/Blender
```

Blender 5.2.1 is known to crash during Metal capability detection when launched inside a restricted `CODEX_SANDBOX`. The preflight blocks that case before Blender starts. Launch Blender from Finder or a normal Terminal outside that sandbox.

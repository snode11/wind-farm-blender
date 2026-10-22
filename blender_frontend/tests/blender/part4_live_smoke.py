"""Run inside Blender against an external Part 4 fake Bridge."""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
ARGS = sys.argv[sys.argv.index('--') + 1:]
PACKAGE_PARENT = Path(ARGS[2]).resolve() if len(ARGS) > 2 else ROOT / 'blender_frontend'
sys.path.insert(0, str(PACKAGE_PARENT))

import bpy
import wfrl_blender
from wfrl_blender import runtime


def until(predicate, description, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.tick()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError(f'Timed out waiting for {description}: {runtime.get_state()}')


def main():
    port = int(ARGS[0])
    backend = ARGS[1] if len(ARGS) > 1 else 'fake'
    scene_name = 'turb3_floris.yaml' if backend == 'floris' else 'turb3_row.yaml'
    scene = str((ROOT / 'scenes' / scene_name).resolve())
    wfrl_blender.register()
    try:
        runtime.connect(port)
        until(lambda: runtime.get_state().connection == 'CONNECTED', 'handshake')
        assert runtime.desired_mode() == 'demo'
        runtime.send_command('start', {'options': {'scene': scene}})
        until(lambda: runtime.get_state().run_status == 'RUNNING'
              and runtime.kinematics.snapshot is not None, 'first live snapshot')
        first_step = runtime.kinematics.snapshot['step']
        assert first_step >= 0
        assert bpy.data.objects.get('WFRL.Turbine.T1.Rotor') is not None
        power = runtime.kinematics.snapshot['turbines'][0]['channels']['power']
        assert power['validity'] == 'valid' and power['value'] is not None

        runtime.send_command('pause')
        until(lambda: runtime.get_state().run_status == 'PAUSED'
              and runtime.allows_command('step'), 'pause snapshot')
        before = runtime.kinematics.snapshot['step']
        runtime.send_command('step')
        until(lambda: runtime.kinematics.snapshot['step'] > before, 'single step')
        assert runtime.kinematics.snapshot['step'] == before + 1, (
            f"single step advanced {before} -> {runtime.kinematics.snapshot['step']}")

        runtime.send_command('resume')
        until(lambda: runtime.get_state().run_status == 'RUNNING', 'resume')
        time.sleep(.1)
        runtime.tick()
        rpm = runtime.kinematics.snapshot['turbines'][0]['channels']['rotor_speed']
        if backend == 'fake':
            assert rpm['validity'] == 'valid'
            assert runtime.kinematics.rotor_angles['T1'] > 0
        else:
            assert rpm['validity'] == 'unsupported' and rpm['value'] is None

        runtime.send_command('stop')
        until(lambda: runtime.get_state().run_status == 'STOPPED', 'safe stop')
        assert not {'torch', 'mpi4py'} & set(sys.modules)
        assert not any(name.startswith('openfast') for name in sys.modules)
        print(f'WFRL_PART4_LIVE_SMOKE=PASS backend={backend}')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()

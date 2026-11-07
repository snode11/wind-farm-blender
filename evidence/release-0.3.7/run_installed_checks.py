"""Run existing regressions against an isolated, installed 0.3.7 extension."""
from pathlib import Path
import json
import os
import subprocess
import time

root = Path(__file__).resolve().parents[2]
out = root / 'evidence/release-0.3.7'
temp = Path(json.loads((out / 'workspace.json').read_text())['temporary'])
module = 'bl_ext.release_test.wfrl_blender'
env = dict(os.environ, BLENDER_USER_CONFIG=str(temp / 'config'),
           WFRL_V3_AUTO_QUIT='1', WFRL_NATIVE_SECONDS='3',
           WFRL_TEST_LAYOUT=str(root / 'outputs/camera-layout-same-blade-v2/T1-same-blade-four-cameras-v2.json'))
env.pop('PYTHONPATH', None)
env.update(WFRL_VIDEO_SOURCE=str(temp / 'test-video.mp4'),
           WFRL_FFMPEG='/opt/homebrew/bin/ffmpeg', WFRL_MEDIAMTX='/Users/eason/Library/Application Support/Blender/5.2/wfrl-video-tools/mediamtx')
checks = [
    ('custom_camera_registration_regression', False),
    ('custom_camera_v3_regression', False),
    ('native_camera_projection_regression', False),
    ('four_camera_projection_regression', False),
    ('video_output_regression', False),
    ('nacelle_camera_regression', False),
    ('native_camera_views_regression', True),
    ('custom_camera_v3_window', True),
    ('four_cameras_window_regression', True),
    ('custom_camera_exit_regression', True),
]
summary = json.loads((out / 'blender-tests.json').read_text()) if (out / 'blender-tests.json').exists() else []
passed = {item['test'] for item in summary if item['exit_code'] == 0 and not item['error_files']}
for name, window in checks:
    if name in passed:
        continue
    original = root / 'blender_frontend/tests/blender' / f'{name}.py'
    body = original.read_text().replace('Path(__file__).resolve().parents[3]', f'Path({str(root)!r})')
    body = body.replace('import wfrl_blender as addon', f'import {module} as addon')
    body = body.replace('from wfrl_blender', f'from {module}')
    body = body.replace('bl_ext.user_default.wfrl_blender', module)
    if name != 'custom_camera_registration_regression':
        body = body.replace('addon.register()', 'addon_utils.enable(MODULE, default_set=False)')
    prefix = (
        'import addon_utils, sys, hashlib, json\nfrom pathlib import Path\n'
        f'MODULE={module!r}\n'
        'assert addon_utils.check(MODULE)==(True,True), addon_utils.check(MODULE)\n'
        "assert not any('blender_frontend' in p for p in sys.path)\n"
        f'inventory=json.loads(Path({str(root / "dist/wfrl_blender-0.3.7.inventory.json")!r}).read_text())\n'
        f'import {module} as installed_addon\n'
        'installed=Path(installed_addon.__file__).parent\n'
        "assert all(hashlib.sha256((installed/f['path']).read_bytes()).hexdigest()==f['sha256'] for f in inventory['files'])\n"
    )
    if name == 'custom_camera_registration_regression':
        prefix += 'addon_utils.disable(MODULE, default_set=False)\n'
    wrapper = temp / f'{name}.py'
    wrapper.write_text(prefix + body)
    target = out / name
    target.mkdir(exist_ok=True)
    runenv = dict(env, WFRL_TEST_OUTPUT=str(target), WFRL_TEST_RUNTIME=module)
    cmd = ['/Applications/Blender.app/Contents/MacOS/Blender']
    if not window:
        cmd.append('--background')
    cmd += ['--python-exit-code', '1', '--python', str(wrapper)]
    start = time.monotonic()
    with (out / f'{name}.log').open('w') as log:
        try:
            result = subprocess.run(cmd, env=runenv, cwd=root, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=180)
            rc = result.returncode
        except subprocess.TimeoutExpired:
            rc = 124
    errors = list(target.glob('error*'))
    summary.append(dict(test=name, window=window, exit_code=rc,
                        seconds=round(time.monotonic() - start, 2),
                        error_files=[str(p.relative_to(root)) for p in errors]))
    (out / 'blender-tests.json').write_text(json.dumps(summary, indent=2))
    print(name, rc, summary[-1]['seconds'], flush=True)
    if rc or errors:
        raise SystemExit(1)

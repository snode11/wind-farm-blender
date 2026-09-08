"""Reproducible sequential checks, including installation in a disposable profile."""
import os, subprocess, sys, tempfile, json, tomllib
os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
BLENDER = os.environ.get('WFRL_BLENDER', '/Applications/Blender.app/Contents/MacOS/Blender')
OUT = ROOT/'evidence/part2'
sys.path.insert(0, str(Path(__file__).parent))
from blender_preflight import BlenderLaunchBlocked, inspect_blender, require_launchable
VERSION = tomllib.loads((ROOT/'blender_frontend/wfrl_blender/blender_manifest.toml').read_text())['version']
ARCHIVE = f'dist/wfrl_blender-{VERSION}.zip'
results = {}

# Run this check before any Blender subprocess. In the Codex seatbelt the
# executable can segfault during WM_init, before a Python traceback exists.
try:
    require_launchable(BLENDER)
except BlenderLaunchBlocked as exc:
    OUT.mkdir(parents=True, exist_ok=True)
    result = inspect_blender(BLENDER)
    (OUT/'closeout_preflight.txt').write_text(str(exc) + '\n', encoding='utf-8')
    (OUT/'verification_blocked.json').write_text(json.dumps({
        'status': 'BLOCKED', 'preflight': result.__dict__, 'exit_codes': results,
    }, indent=2), encoding='utf-8')
    print(f'PART2_VERIFY_BLOCKED: {exc}', file=sys.stderr)
    raise SystemExit(78)

def run(name, args, env=None):
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    (OUT/f'closeout_{name}.txt').write_text(result.stdout+result.stderr)
    results[name] = result.returncode
    print(name, result.returncode, flush=True)
    if result.returncode: raise RuntimeError(f'{name}: {result.stdout[-2000:]} {result.stderr[-2000:]}')
sys.path.insert(0,str(ROOT/'blender_frontend'))
import runpy
namespace = runpy.run_path(str(ROOT/'blender_frontend/tests/test_part2_python.py'))
checks = [v for k,v in namespace.items() if k.startswith('test_')]
for test in checks: test()
(OUT/'closeout_python.txt').write_text(f'{len(checks)} pure Python tests PASS\n')
for name in ('static_scene_smoke','camera_framing_smoke','geometry_smoke','demo_workflow_smoke'):
    run(name,[BLENDER,'--background','--factory-startup','--python-exit-code','1','--python',f'blender_frontend/tests/blender/{name}.py'])
run('build',[sys.executable,'scripts/blender/build_extension.py'])
run('validate',[BLENDER,'--background','--factory-startup','--command','extension','validate',ARCHIVE])
with tempfile.TemporaryDirectory(prefix='wfrl-clean-profile-') as profile:
    env=os.environ.copy()
    env['BLENDER_USER_RESOURCES']=profile
    env['BLENDER_USER_CONFIG']=str(Path(profile)/'config')
    env['BLENDER_USER_EXTENSIONS']=str(Path(profile)/'extensions')
    env['BLENDER_USER_SCRIPTS']=str(Path(profile)/'scripts')
    run('install',[BLENDER,'--background','--factory-startup','--command','extension','install-file','-r','user_default','-e',ARCHIVE],env)
    code = "import bpy,importlib; m=importlib.import_module('bl_ext.user_default.wfrl_blender'); m.load_demo_scene(); assert len(m.turbine_geometry.geometry_data()['blade_stations'])==19; bpy.ops.preferences.addon_disable(module=m.__name__); bpy.ops.preferences.addon_enable(module=m.__name__); bpy.ops.wm.open_mainfile(filepath="+repr(str(OUT/'wfrl_part2_static.blend'))+"); assert bpy.data.workspaces.get('WFRL Workspace'); assert bpy.context.scene.get('wfrl_run_status') in {'READY','PAUSED','STOPPED'}; print('CLEAN_INSTALL_REOPEN_PASS')"
    run('installed_reopen',[BLENDER,'--background','--python-exit-code','1','--python-expr',code],env)
(OUT/'verification.json').write_text(json.dumps({'python_tests':len(checks),'exit_codes':results},indent=2))
print('PART2_VERIFY_PASS')

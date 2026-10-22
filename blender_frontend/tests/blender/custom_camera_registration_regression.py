"""Extension registration must not read scenes inside Blender RestrictBlend.

Run in an independent --background --factory-startup process. This also checks
that disabling before the deferred migration runs leaves no pending callbacks.
"""
import importlib
import json
import os
from pathlib import Path
import sys

import bpy
from _bpy_restrict_state import RestrictBlend

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if RUNTIME == 'wfrl_blender':
    sys.path.insert(0, str(ROOT / 'blender_frontend'))
addon = importlib.import_module(RUNTIME)
panel = importlib.import_module(RUNTIME + '.panels.custom_cameras')

checks = {}
for cycle in range(2):
    with RestrictBlend():
        addon.register()
    assert hasattr(bpy.types.Scene, 'wfrl_capture_mode')
    assert hasattr(bpy.types.WindowManager, 'wfrl_custom_slot')
    assert bpy.app.timers.is_registered(panel.migrate_loaded)
    assert bpy.app.handlers.load_post.count(panel.migrate_loaded) == 1
    if cycle:
        # In background mode, explicitly run the callback after restrictions
        # end; the real timer is additionally covered by the installed window.
        bpy.app.timers.unregister(panel.migrate_loaded)
        assert panel.migrate_loaded() is None
        assert bpy.app.timers.is_registered(panel.lifecycle_watchdog)
        checks['migration_after_registration'] = True
    addon.unregister()
    assert not bpy.app.timers.is_registered(panel.migrate_loaded)
    assert not bpy.app.timers.is_registered(panel.lifecycle_watchdog)
    assert panel.migrate_loaded not in bpy.app.handlers.load_post
    assert not hasattr(bpy.types.Scene, 'wfrl_capture_mode')
checks['restricted_registration_and_reenable'] = True
checks['pending_migration_and_watchdog_cleanup'] = True

out = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-registration'))
out.mkdir(parents=True, exist_ok=True)
(out / 'result.json').write_text(json.dumps({
    'status': 'PASS', 'blender': bpy.app.version_string,
    'runtime': RUNTIME, 'checks': checks,
}, indent=2))
print('CUSTOM_CAMERA_REGISTRATION_PASS', flush=True)

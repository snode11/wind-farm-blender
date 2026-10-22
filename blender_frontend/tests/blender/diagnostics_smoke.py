"""Verify real Blender registration and lossless diagnostic Text Editor output."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender.panels.diagnostics import WFRL_OT_CopyDiagnostic
from types import SimpleNamespace

wfrl_blender.register()
try:
    full = '错误\n' + 'detail ' * 1000 + '\nROOT CAUSE'
    area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(area=area):
        assert bpy.ops.wfrl.diagnostic_details(report_text=full) == {'FINISHED'}
    assert area.type == 'TEXT_EDITOR'
    assert area.spaces.active.text.as_string().split('\n\n', 1)[1] == full
    assert area.spaces.active.show_word_wrap
    # Background Blender has no OS clipboard; verify the operator's exact assignment.
    wm = SimpleNamespace(clipboard='')
    operator = SimpleNamespace(report_text=full, report=lambda *args: None)
    assert WFRL_OT_CopyDiagnostic.execute(operator, SimpleNamespace(window_manager=wm)) == {'FINISHED'}
    assert wm.clipboard == full
    print('WFRL_DIAGNOSTICS_SMOKE=PASS')
finally:
    wfrl_blender.unregister()

"""Block render dispatch through gw184.defect_outputs during a regression.

Blender's bpy.ops namespaces are dynamic; patching bpy.ops.render.render does
not reliably replace later lookups.  This guard substitutes only the output
module's bpy binding.  It is not a process-wide Blender render interceptor.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock


GUARD_SCOPE = 'gw184.defect_outputs.bpy.ops.render.render only; not process-wide'


class _Delegate:
    def __init__(self, target, **overrides):
        self._target = target
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._target, name)


@contextmanager
def forbid_output_render():
    """Yield a counted, failing renderer without mutating real bpy attributes."""
    from gw184 import defect_outputs

    renderer = Mock(name='forbidden_output_renderer',
                    side_effect=AssertionError('Regression must not dispatch an output render'))
    original_bpy = defect_outputs.bpy
    defect_outputs.bpy = _Delegate(
        original_bpy,
        ops=_Delegate(original_bpy.ops, render=SimpleNamespace(render=renderer)))
    try:
        yield renderer
    finally:
        defect_outputs.bpy = original_bpy

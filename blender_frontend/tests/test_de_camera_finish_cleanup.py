"""Modal teardown attempts independent resources and retries failed ownership."""
from types import SimpleNamespace as NS

import pytest

from blender_frontend.tests.test_br_camera_resource_rollback import preview_operator


@pytest.mark.parametrize('failures', [
    ('draw',), ('timer',), ('draft',), ('preview',), ('restore',),
    ('draw', 'timer', 'draft', 'preview', 'restore'),
])
def test_finish_cleans_independent_resources_and_retries_failures(preview_operator, failures):
    r = preview_operator
    assert r.op.invoke(r.context, None) == {'RUNNING_MODAL'}
    operations = dict(draw=r.draw.draw_handler_remove, timer=r.wm.event_timer_remove,
        draft=r.session.cancel, preview=r.renderer.free, restore=r.op.external_snapshot.restore)
    originals = {name: operation.side_effect for name, operation in operations.items()}
    errors = {name: RuntimeError(name + ' release failed') for name in failures}
    for name, error in errors.items():
        operations[name].side_effect = error

    with pytest.raises(RuntimeError) as caught:
        r.op.finish(restore=True, resume=False)

    assert all(operation.call_count == 1 for operation in operations.values())
    assert all(str(error) in str(caught.value) for error in errors.values())
    assert caught.value.__cause__ is errors[failures[0]]
    assert not r.op.closed and r.op._finishing and r.panel._ACTIVE is r.op
    assert r.op.identity is None and r.op.drag is None
    assert r.op.handle is (r.handle if 'draw' in failures else None)
    assert r.op.timer is (r.timer if 'timer' in failures else None)
    assert r.session.closed is ('draft' not in failures)
    assert r.op.preview is r.renderer
    assert r.op.draw_overlay() is None
    assert r.op.report.call_args[0] == ({'ERROR'}, str(caught.value))

    for name in failures:
        operations[name].side_effect = originals[name]
    assert r.op.modal(r.context, NS(type='TIMER')) == {'FINISHED'}
    assert r.op.closed and r.panel._ACTIVE is None
    assert r.op.handle is None and r.op.timer is None and r.session.closed
    for name, operation in operations.items():
        expected = 2 if name in failures or (name == 'restore' and 'draft' in failures) else 1
        assert operation.call_count == expected
    assert all(call.kwargs == {'restore': False} for call in r.session.cancel.call_args_list)
    counts = {name: operation.call_count for name, operation in operations.items()}
    r.op.finish()
    assert {name: operation.call_count for name, operation in operations.items()} == counts


def test_retry_keeps_initial_no_restore_options(preview_operator):
    r = preview_operator
    assert r.op.invoke(r.context, None) == {'RUNNING_MODAL'}
    r.draw.draw_handler_remove.side_effect = RuntimeError('draw release failed')
    with pytest.raises(RuntimeError):
        r.op.finish(restore=False, resume=False)
    r.session.cancel.assert_called_once_with(restore=False)
    r.op.external_snapshot.restore.assert_not_called()
    r.draw.draw_handler_remove.side_effect = None
    r.op.finish()
    assert r.op.closed
    r.session.cancel.assert_called_once_with(restore=False)
    r.op.external_snapshot.restore.assert_not_called()

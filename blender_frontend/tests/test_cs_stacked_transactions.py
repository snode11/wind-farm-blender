"""Box placement validates contact and keeps failed edits recoverable."""
from copy import deepcopy
from dataclasses import asdict
import math
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from wfrl_blender import stacked_camera_rig as rig


@pytest.fixture
def placement(frontend_module, monkeypatch, stub_module):
    panel = frontend_module('wfrl_blender.panels.stacked_camera_rig')
    scene = NS(frame_current=1, frame_subframe=0.)
    cameras = {slot: NS(parameters=rig.core.CameraParameters((slot, 0., 0.))) for slot in (1, 2, 3)}
    box = {'center': [0., 0., 0.], 'housing_yaw_deg': 0.}

    def layout(scene):
        return {'rig_pose': deepcopy(box), 'cameras': [dict(slot_id=slot, parameters=asdict(cam.parameters))
                                                       for slot, cam in cameras.items()]}

    def restore(scene, previous):
        for record in previous['cameras']:
            cameras[record['slot_id']].parameters = rig.core.CameraParameters(**record['parameters'])
        box.clear()
        box.update(previous['rig_pose'])

    monkeypatch.setattr(rig.core, 'get_camera', lambda scene, slot: cameras[slot])
    monkeypatch.setattr(rig.core, 'model_identity', lambda scene: 'same model')
    monkeypatch.setattr(rig.core, 'layout_dict', layout)
    monkeypatch.setattr(rig.core, 'restore_layout', restore)
    stub_module('wfrl_blender.panels.custom_cameras', session_identity=lambda scene: 'same session',
                installation_block_reason=lambda scene: '', _ACTIVE=None)
    stub_module('wfrl_blender.custom_camera_history', before_change=lambda scene: ('same', layout(scene), 'hash'),
                committed=Mock())
    preview = stub_module('wfrl_blender.custom_camera_preview', changed=Mock())
    return NS(panel=panel, scene=scene, cameras=cameras, box=box, layout=layout,
              transaction=panel.Placement(scene), preview=preview)


@pytest.mark.parametrize('failure', ['camera', 'housing', 'refresh'])
def test_preview_failure_restores_complete_previous_layout(placement, monkeypatch, failure):
    p = placement
    before = p.layout(p.scene)
    proposed = {'center': [1., 2., 3.], 'housing_yaw_deg': 20.}
    payload = deepcopy(before)
    payload['rig_pose'] = proposed
    for record in payload['cameras']:
        record['parameters']['yaw'] = 30.
    monkeypatch.setattr(rig, 'surface_pose', lambda *a, **kw: proposed)
    monkeypatch.setattr(rig, 'transformed_layout', lambda *a: payload)

    def apply(scene, camera, params, **kw):
        camera.parameters = params
        if failure == 'camera' and camera is p.cameras[2]:
            raise RuntimeError('camera failed')

    def pose(scene, value):
        p.box.update(value)
        if failure == 'housing':
            raise RuntimeError('housing failed')

    monkeypatch.setattr(rig.core, 'apply_research', apply)
    monkeypatch.setattr(rig, 'apply_pose', pose)
    if failure == 'refresh':
        p.preview.changed.side_effect = RuntimeError('refresh failed')
    with pytest.raises(RuntimeError, match=failure + ' failed'):
        p.transaction.preview(object(), 0.)
    assert p.layout(p.scene) == before
    assert not p.transaction.ready


def test_replaced_camera_invalidates_placement_without_restoring_over_undo(placement, monkeypatch):
    p = placement
    p.cameras[2] = NS(parameters=rig.core.CameraParameters((99., 0., 0.)))
    restore = Mock()
    monkeypatch.setattr(rig.core, 'restore_layout', restore)
    assert not p.transaction.same_session()
    p.transaction.finish()
    restore.assert_not_called()


def test_deleted_mount_mesh_invalidates_placement(placement, monkeypatch):
    monkeypatch.setattr(rig.core, 'model_identity', Mock(side_effect=KeyError('deleted shell')))
    assert not placement.transaction.same_session()


def test_failed_cancel_keeps_transaction_for_retry(placement, monkeypatch):
    p = placement
    cleanup = Mock()
    active = NS(transaction=p.transaction, cleanup=cleanup)
    monkeypatch.setattr(p.panel, '_ACTIVE', active)
    monkeypatch.setattr(rig.core, 'restore_layout', Mock(side_effect=RuntimeError('restore failed')))
    with pytest.raises(RuntimeError, match='restore failed'):
        p.panel.shutdown()
    assert not p.transaction.closed
    assert p.panel._ACTIVE is active
    cleanup.assert_not_called()


def test_modal_start_failure_rolls_back_and_cancels(placement, monkeypatch, stub_module):
    p = placement
    panel, transaction = p.panel, p.transaction
    transaction.finish = Mock()
    monkeypatch.setattr(panel, 'Placement', lambda scene: transaction)
    monkeypatch.setattr(panel, 'finish_view', Mock())
    monkeypatch.setattr(rig, 'pose', lambda scene: {})
    stub_module('wfrl_blender.playback', cancel=Mock())
    wm = NS(event_timer_add=Mock(return_value='timer'), event_timer_remove=Mock(),
            modal_handler_add=Mock(side_effect=RuntimeError('modal failed')))
    area = NS(regions=[NS(type='WINDOW')], header_text_set=Mock(), tag_redraw=Mock())
    context = NS(scene=p.scene, area=area, window=object(), window_manager=wm)
    op = panel.WFRL_OT_BoxPick()
    op.report = Mock()
    assert op.invoke(context, None) == {'CANCELLED'}
    assert panel._ACTIVE is None
    transaction.finish.assert_called_once_with()
    wm.event_timer_remove.assert_called_once_with('timer')
    assert area.header_text_set.call_args.args == (None,)


@pytest.mark.parametrize('entry', ['keyboard', 'button'])
def test_confirmation_failure_rolls_back_and_cancels(placement, monkeypatch, entry):
    p = placement
    op = p.panel.WFRL_OT_BoxPick()
    op.scene, op.transaction = p.scene, p.transaction
    op.area = NS(type='VIEW_3D', header_text_set=Mock(), tag_redraw=Mock())
    op.wm, op.timer, op.report = NS(event_timer_remove=Mock()), 'timer', Mock()
    p.transaction.ready = True
    monkeypatch.setattr(p.panel, '_ACTIVE', op)
    monkeypatch.setattr(sys.modules['wfrl_blender.custom_camera_history'], 'committed',
                        Mock(side_effect=RuntimeError('history failed')))
    restore = Mock(wraps=rig.core.restore_layout)
    monkeypatch.setattr(rig.core, 'restore_layout', restore)
    context = NS(scene=p.scene)
    if entry == 'keyboard':
        result = op.modal(context, NS(type='RET', value='PRESS'))
    else:
        action = p.panel.WFRL_OT_BoxAction()
        action.action, action.report = 'CONFIRM', Mock()
        result = action.execute(context)
    assert result == {'CANCELLED'}
    restore.assert_called_once_with(p.scene, p.transaction.before[1])
    assert p.transaction.closed and p.panel._ACTIVE is None
    op.wm.event_timer_remove.assert_called_once_with('timer')


@pytest.mark.parametrize('nearest,distance', [(None, None), ((0., 0., 0.), math.nan),
                                             ((0., 0., 0.), math.inf), ((0., 0., 0.), -1.)])
def test_surface_contact_rejects_missing_or_nonfinite_nearest_geometry(monkeypatch, nearest, distance):
    import json
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=lambda values: np.asarray(values, dtype=float)))
    monkeypatch.setattr(rig, 'pose_rotation', lambda value: np.eye(3))
    box = {'assumptions_json': json.dumps(asdict(rig.RigParameters()))}
    scene = NS(objects={rig.PREFIX: box})
    tree = NS(find_nearest=lambda p: (nearest, None, 0, distance))
    monkeypatch.setattr(rig.core, '_geometry', lambda scene: [
        ('shell', [], [], [np.array((0., 0., 1.))], tree)])
    monkeypatch.setattr(rig.core, '_closed_mesh', lambda faces: True)
    monkeypatch.setattr(rig.core, '_inside', lambda *a: False)
    value = {'center': [0., 0., 1.], 'housing_yaw_deg': 0., 'surface_mount': dict(
        point=[0., 0., 0.], normal=[0., 0., 1.], surface_name='shell', type='BACK')}
    with pytest.raises(ValueError, match='安装'):
        rig.validate_surface_contact(scene, value)

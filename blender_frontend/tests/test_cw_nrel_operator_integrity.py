"""Operator failure rollback and read-only panel rendering with bpy substitutes."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_nrel_editor_playback import editor_runtime


@pytest.mark.parametrize('action_name', ['PREVIEW', 'CONFIRM'])
def test_failed_draft_action_restores_candidate(editor_runtime, monkeypatch, action_name):
    r = editor_runtime
    r.session.draft = {'defects': [{'id': 'existing', 'shape': {'length_m': .6}}]}
    r.session.draft_index = 0
    before = deepcopy(r.session.draft)

    def edit(session, _props):
        session.draft['defects'][0]['shape']['length_m'] = -1.

    monkeypatch.setattr(r.namespace, 'draft_from_props', edit)
    r.session.preview_draft = Mock(side_effect=ValueError('invalid dimensions'))
    r.session.finish = Mock(side_effect=ValueError('invalid dimensions'))
    r.namespace.redraw_editor = Mock()
    action = r.namespace.NREL_OT_defect_action()
    action.action, action.report = action_name, Mock()
    assert action.execute(r.context) == {'CANCELLED'}
    assert r.session.draft == before
    assert 'invalid dimensions' in r.scene.wfrl_nrel_defects.status


def test_failed_camera_switch_restores_all_changed_views(editor_runtime):
    r = editor_runtime
    old_camera, new_camera = object(), object()
    r.scene.camera = old_camera
    r.session.camera = lambda _slot: new_camera
    first = NS(overlay=NS(show_overlays=True), region_3d=NS(view_perspective='PERSP'))

    class Region:
        @property
        def view_perspective(self):
            return 'ORTHO'

        @view_perspective.setter
        def view_perspective(self, value):
            if value == 'CAMERA':
                raise RuntimeError('view assignment failed')

    second = NS(overlay=NS(show_overlays=True), region_3d=Region())
    r.context.screen.areas = [NS(type='VIEW_3D', spaces=NS(active=space)) for space in (first, second)]
    r.namespace.redraw_editor = Mock()
    action = r.namespace.NREL_OT_defect_action()
    action.action, action.report = 'CAMERA1', Mock()
    assert action.execute(r.context) == {'CANCELLED'}
    assert r.scene.camera is old_camera
    assert first.overlay.show_overlays and second.overlay.show_overlays
    assert first.region_3d.view_perspective == 'PERSP'


def test_failed_new_action_restores_properties_and_ends_edit(editor_runtime, monkeypatch):
    r = editor_runtime
    p = r.scene.wfrl_nrel_defects
    for name, value in dict(kind='fine_crack', blade_id=1, side='suction', span=45., u=.4, theta=0.,
            length=.6, width=.002, depth=0., seed=17, enabled=True, edge_roughness=0., crater_fraction=.4).items():
        setattr(p, name, value)
    before = vars(p).copy()

    def props_fail(props, _defect):
        props.span, props.kind = 46., 'pit'
        raise ValueError('properties unavailable')

    monkeypatch.setattr(r.namespace, 'props_from_defect', props_fail)
    r.namespace.redraw_editor = Mock()
    action = r.namespace.NREL_OT_defect_action()
    action.action, action.report = 'NEW', Mock()
    assert action.execute(r.context) == {'CANCELLED'}
    assert r.session.draft is None and r.session.edit_state is None
    assert {name: getattr(p, name) for name in before} == before


def _invoke_pick(r):
    r.context.area = NS(type='VIEW_3D', regions=[])
    r.context.workspace = NS(status_text_set=Mock())
    r.context.window_manager.modal_handler_add = Mock()
    r.session.draft = {'defects': [{'id': 'draft'}]}
    pick = r.namespace.NREL_OT_defect_pick()
    pick.report = Mock()
    assert pick.invoke(r.context, NS()) == {'RUNNING_MODAL'}
    return pick


@pytest.mark.parametrize('replacement', ['session', 'draft'])
def test_picker_cancels_when_its_edit_is_replaced(editor_runtime, replacement):
    r = editor_runtime
    pick = _invoke_pick(r)
    if replacement == 'session':
        r.namespace._SESSIONS[r.scene.as_pointer()] = NS(draft={'defects': []})
    else:
        r.session.draft = deepcopy(r.session.draft)
    assert pick.modal(r.context, NS(type='MIDDLEMOUSE')) == {'CANCELLED'}
    r.context.workspace.status_text_set.assert_called_with(None)


def test_picker_rejects_missing_viewport_without_exception(editor_runtime):
    r = editor_runtime
    r.context.area = None
    r.session.draft = {'defects': []}
    pick = r.namespace.NREL_OT_defect_pick()
    pick.report = Mock()
    assert pick.invoke(r.context, NS()) == {'CANCELLED'}


def test_failed_pick_restores_properties_and_cancels(editor_runtime, stub_module, monkeypatch):
    r = editor_runtime
    pick = _invoke_pick(r)
    from wfrl_blender import viewport
    monkeypatch.setattr(viewport, 'in_window_region', lambda *_args: True)
    stub_module('bpy_extras.view3d_utils', region_2d_to_origin_3d=lambda *_args: object(),
                region_2d_to_vector_3d=lambda *_args: object())
    r.context.area.regions = [NS(type='WINDOW', x=0, y=0)]
    r.context.space_data = NS(region_3d=object())

    class Props:
        span, u, blade_id, turbine_id = 45., .4, 1, 'T1'
        @property
        def side(self):
            return 'suction'
        @side.setter
        def side(self, value):
            if value == 'pressure':
                raise ValueError('RNA assignment failed')

    r.scene.wfrl_nrel_defects = Props()
    r.session.locate_ray = lambda *_args: dict(s_m=46., u=.6, surface_side='pressure')
    result = pick.modal(r.context, NS(type='LEFTMOUSE', value='PRESS', mouse_x=10, mouse_y=10))
    assert result == {'CANCELLED'}
    assert r.scene.wfrl_nrel_defects.span == 45. and r.scene.wfrl_nrel_defects.u == .4


def test_scan_poll_matches_invocation_preconditions(editor_runtime):
    r = editor_runtime
    assert r.namespace.NREL_OT_defect_scan.poll(r.context)
    r.session.draft = {'defects': []}
    assert not r.namespace.NREL_OT_defect_scan.poll(r.context)
    r.session.draft = None
    r.context.window = None
    assert not r.namespace.NREL_OT_defect_scan.poll(r.context)


def test_panel_stale_list_is_read_only_and_does_not_index_past_document(editor_runtime):
    r = editor_runtime
    props = r.scene.wfrl_nrel_defects
    props.selected = 1
    props.defect_items = [NS(defect_id='existing'), NS(defect_id='deleted')]
    panel = r.namespace.NREL_PT_defects()
    panel._draw_buttons = Mock()
    layout = Mock()
    before = deepcopy(vars(props))
    panel._draw_defect_list(layout, r.session, props)
    assert vars(props) == before


def test_picker_preconditions_agree_with_poll(editor_runtime):
    r = editor_runtime
    r.context.area = NS(type='VIEW_3D')
    r.session.draft = None
    assert not r.namespace.NREL_OT_defect_pick.poll(r.context)
    r.session.draft = {'defects': []}
    assert r.namespace.NREL_OT_defect_pick.poll(r.context)
    r.session.busy = True
    assert not r.namespace.NREL_OT_defect_pick.poll(r.context)

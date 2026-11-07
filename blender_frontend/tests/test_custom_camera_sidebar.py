"""Execute the real sidebar draw body with a minimal host-side UI adapter.

Button-property objects deliberately have no session ``stage`` attribute. This
checks the full main-sidebar path without needing Blender or rendering a view.
"""
from types import SimpleNamespace

import pytest


class Layout:
    def __init__(self, calls):
        self.calls = calls
        self.enabled = True
        self.alert = False

    def row(self, **_kwargs):
        return Layout(self.calls)

    def box(self):
        return Layout(self.calls)

    def panel(self, _identifier, **_kwargs):
        return Layout(self.calls), Layout(self.calls)

    def label(self, *, text, **_kwargs):
        self.calls['labels'].append(text)

    def prop(self, _data, _property, **_kwargs):
        self.calls['properties'].append(_property)

    def operator(self, identifier, **kwargs):
        button = SimpleNamespace(identifier=identifier, text=kwargs.get('text', ''))
        self.calls['buttons'].append(button)
        return button


@pytest.mark.parametrize('native_error', ['', '相机观察中断：injected native failure'])
@pytest.mark.parametrize('stage', [None, 'WATCH', 'LAYOUT', 'AIM'])
@pytest.mark.parametrize('stacked', [False, True])
def test_main_sidebar_keeps_camera_session_distinct_from_button_properties(
        frontend_module, stub_module, monkeypatch, stage, stacked, native_error):
    sidebar = frontend_module('wfrl_blender.panels.custom_cameras')
    scene = {'wfrl_stacked_camera_rig': stacked}
    camera = {'custom_label': 'C1', 'custom_enabled': True}
    params = SimpleNamespace(location=(1., -2., 3.), yaw=0., pitch=10., roll=0.,
                             fov=75., vfov=45., output_long_edge_px=1920,
                             focal_length_mm_record=None)
    active = (SimpleNamespace(scene=scene, stage=stage, camera=camera,
                              frame_fits=False, ready=True, error='') if stage else None)
    core = SimpleNamespace(SLOTS=(1, 2, 3), get_camera=lambda _scene, _slot: camera,
                           is_available=lambda _scene: True, parameters=lambda _camera: params)
    history = SimpleNamespace(HISTORY=SimpleNamespace(entries=[], notice=''))
    stub_module('wfrl_blender.panels.custom_camera_output', draw_output=lambda *_args: None)
    stub_module('wfrl_blender.panels.stacked_camera_rig', _ACTIVE=None)
    stub_module('wfrl_blender.custom_camera_capture', active=lambda: False)
    stub_module('wfrl_blender.native_camera_views', last_error=lambda _scene: native_error)
    for name, value in {'_ACTIVE': active, 'core': core, 'history': history,
                        'installation_block_reason': lambda _scene: ''}.items():
        monkeypatch.setattr(sidebar, name, value)
    calls = {'buttons': [], 'labels': [], 'properties': []}
    panel = SimpleNamespace(layout=Layout(calls))
    wm = SimpleNamespace(wfrl_custom_slot=1, wfrl_custom_has_focal=False)
    sidebar.WFRL_PT_CustomCameras.draw(panel, SimpleNamespace(scene=scene, window_manager=wm))
    if native_error:
        assert native_error in ''.join(calls['labels'])
    else:
        assert not any(label.startswith('相机观察中断') for label in calls['labels'])
    buttons = calls['buttons']
    native = [button for button in buttons if button.identifier == 'wfrl.native_camera_view']
    grouped = [button for button in native if getattr(button, 'mode', None) == 'TRIPLE']
    if stage == 'AIM':
        assert not native, 'layout switches must not appear during an unconfirmed draft'
        assert any(getattr(button, 'action', None) == 'CONFIRM' for button in buttons)
    else:
        assert [(button.mode, button.layout) for button in grouped] == [('TRIPLE', 'GRID'), ('TRIPLE', 'STRIP')]
        assert all(not hasattr(button, 'stage') for button in grouped)
        assert any(getattr(button, 'mode', None) == 'WATCH' for button in native)
    exits = [button for button in buttons if getattr(button, 'action', None) == 'EXIT']
    assert bool(exits) == (stage in {'WATCH', 'LAYOUT'})
    assert ('视口空间不足，请扩大视图' in calls['labels']) == (stage in {'WATCH', 'AIM'})
    assert any(getattr(button, 'action', None) == 'COPY' for button in buttons), 'draw must reach its final controls'

"""Task summaries display capture errors and the current manual blade pose."""
from types import SimpleNamespace as NS

import pytest


class Layout:
    def __init__(self, labels=None, section=''):
        self.labels = [] if labels is None else labels
        self.section = section

    def row(self, **_kwargs): return self
    def column(self, **_kwargs): return self
    def box(self): return self
    def prop(self, *_args, **_kwargs): pass
    def separator(self): pass
    def operator(self, *_args, **_kwargs): return NS()
    def label(self, *, text='', **_kwargs): self.labels.append((self.section, text))
    def panel(self, name, **_kwargs):
        return self, Layout(self.labels, name) if name == 'wfrl_manual_actual_values' else None


class Scene(dict):
    pass


@pytest.mark.parametrize('individual,actual', [(False, (15., 15., 15.)),
                                            (False, (15., 25., 35.)),
                                            (True, (15., 25., 35.))])
def test_manual_actual_summary_uses_current_pitch_not_target(
        frontend_module, stub_module, monkeypatch, individual, actual):
    stub_module('wfrl_blender.manual_control')
    stub_module('wfrl_blender.live_stream_view')
    panel = frontend_module('wfrl_blender.panels.manual_control')
    fields = ('yaw_deg', 'pitch_deg', 'rpm', 'individual_pitch', 'azimuth_deg')
    entry = NS(turbine_id='T1', yaw_deg=0., pitch_deg=90., rpm=0., running=False,
               individual_pitch=individual, azimuth_deg=0.,
               bl_rna=NS(properties={key: NS(name=key) for key in fields}))
    for blade, pitch in enumerate(actual, 1):
        setattr(entry, f'pitch_actual_b{blade}_deg', pitch)
        setattr(entry, f'pitch_b{blade}_deg', 90.)
        entry.bl_rna.properties[f'pitch_b{blade}_deg'] = NS(name=f'pitch_b{blade}_deg')
    scene = Scene(wfrl_scene_kind='manual')
    scene.wfrl_manual_target, scene.wfrl_manual_batch, scene.wfrl_manual_step_deg = 'ALL', entry, 5.
    monkeypatch.setattr(panel, 'manual_control', NS(is_manual=lambda _scene: True,
        validate_scene=lambda _scene: (True, ''), selected_entries=lambda _scene: [entry],
        mixed_fields=lambda _scene: (), pitch_ramping=lambda _entry: True))
    layout = Layout()
    panel.WFRL_PT_ManualControl.draw(NS(layout=layout), NS(scene=scene))
    labels = [text for section, text in layout.labels if section == 'wfrl_manual_actual_values']
    pitch_label = next(text for text in labels if '桨距' in text)

    assert '90.0' not in pitch_label
    assert '15.0°' in pitch_label
    if len(set(actual)) > 1:
        assert '25.0°' in pitch_label and '35.0°' in pitch_label


def test_home_surfaces_capture_manifest_failure(frontend_module, stub_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.home')
    stub_module('wfrl_blender.manual_control')
    stub_module('wfrl_blender.native_camera_views', last_error=lambda _scene: '')
    stub_module('wfrl_blender.playback', is_playing=lambda _scene: False)
    monkeypatch.setattr(panel, 'busy_reason', lambda _scene: '')
    monkeypatch.setattr(panel, 'foreground_tasks', lambda _scene: ())
    monkeypatch.setattr(panel.runtime, 'get_state', lambda: NS(error=''))
    monkeypatch.setattr(panel.clearance_replay, 'reader_for', lambda _scene: None)
    error = 'Manifest write failed: disk full'
    _status, _busy, issues = panel.snapshot(NS(scene=Scene(wfrl_capture_status=error)))
    assert ('窗口录制', error) in issues

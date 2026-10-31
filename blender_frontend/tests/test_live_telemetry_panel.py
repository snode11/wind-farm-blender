"""Live telemetry cards use the backend's owning scene without clearing its data."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import charts, farm_flex, runtime
from wfrl_blender.animation import KinematicState
from wfrl_blender.state import FrontendState
from test_animation_math import snapshot


class Scene(dict):
    def as_pointer(self):
        return id(self)


class Layout:
    def __init__(self):
        self.labels = []

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def row(self, **kwargs):
        return self

    def box(self, **kwargs):
        return self

    def operator(self, *args, **kwargs):
        return NS()


@pytest.fixture
def live_cards(monkeypatch):
    owner, other = Scene(name='owner'), Scene(name='other')
    bpy = NS(types=NS(Panel=object), data=NS(scenes=[owner, other]), context=NS(scene=owner))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.panels.diagnostics', NS(draw_diagnostic=lambda *args, **kwargs: None))
    module_path = Path(runtime.__file__).parent / 'panels/telemetry.py'
    spec = importlib.util.spec_from_file_location('wfrl_blender.panels._telemetry_contract', module_path)
    panel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel)
    kinematics = KinematicState()
    kinematics.apply_snapshot(snapshot())
    monkeypatch.setattr(runtime, 'kinematics', kinematics)
    monkeypatch.setattr(runtime, '_backend_scene', owner)
    monkeypatch.setattr(runtime, '_state', FrontendState(connection='CONNECTED', confirmed=True, session_id='s'))
    monkeypatch.setattr(runtime, 'safety_events', [dict(payload=dict(message='owner event'))])
    monkeypatch.setattr(charts, 'export_job', NS(poll=lambda: 'IDLE'))
    monkeypatch.setattr(farm_flex, 'is_active', lambda scene: False)
    return NS(panel=panel.WFRL_PT_live_telemetry, owner=owner, other=other, bpy=bpy)


def labels_for(live_cards, scene):
    live_cards.bpy.context.scene = scene
    layout = Layout()
    assert live_cards.panel.poll(live_cards.bpy.context)
    live_cards.panel.draw(NS(layout=layout), live_cards.bpy.context)
    return layout.labels


def test_other_scene_cards_do_not_show_backend_values_or_events(live_cards):
    before = labels_for(live_cards, live_cards.owner)
    assert 'Step 99 / interactive_training' in before
    assert 'Safety events retained: 1' in before

    other = labels_for(live_cards, live_cards.other)
    assert not any(text.startswith(('Step ', 'Yaw:', 'RPM:')) for text in other)
    assert 'WAITING / no backend snapshot' in other
    assert 'Safety events retained: 0' in other

    assert labels_for(live_cards, live_cards.owner) == before
    assert runtime.kinematics.snapshot['step'] == 99


def test_removed_owner_cards_keep_snapshot_hidden(live_cards):
    live_cards.bpy.data.scenes.remove(live_cards.owner)
    labels = labels_for(live_cards, live_cards.other)
    assert 'WAITING / no backend snapshot' in labels
    assert not any(text.startswith('Step ') for text in labels)

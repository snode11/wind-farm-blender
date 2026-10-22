import sys
from types import SimpleNamespace
from unittest.mock import patch
from wfrl_blender import runtime, charts, farm_flex
from wfrl_blender.state import FrontendState


def test_recorded_idle_skips_redraw_but_async_completion_is_visible():
    draws = []
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: draws.append(1))
    bpy = SimpleNamespace(context=SimpleNamespace(scene=object(),
        window_manager=SimpleNamespace(windows=[SimpleNamespace(screen=SimpleNamespace(areas=[area]))])))
    job = SimpleNamespace(status='WRITING', poll=lambda: None)
    with patch.dict(sys.modules, bpy=bpy), patch.multiple(runtime, _client=None, _health=None,
            _last_tick=None, _state=FrontendState(connection='LOCAL DEMO')), \
            patch.object(farm_flex, 'is_active', return_value=True), patch.object(charts, 'export_job', job):
        assert runtime.tick() == .05
        assert draws == []
        job.poll = lambda: setattr(job, 'status', 'COMPLETE')
        assert runtime.tick() == .05
        assert draws == [1]


def test_other_local_scenes_keep_existing_redraw_behavior():
    draws = []
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: draws.append(1))
    bpy = SimpleNamespace(context=SimpleNamespace(scene=object(),
        window_manager=SimpleNamespace(windows=[SimpleNamespace(screen=SimpleNamespace(areas=[area]))])))
    with patch.dict(sys.modules, bpy=bpy), patch.multiple(runtime, _client=None, _health=None,
            _last_tick=None, _state=FrontendState(connection='LOCAL DEMO')), \
            patch.object(farm_flex, 'is_active', return_value=False):
        runtime.tick()
        assert draws == [1]


def test_playing_recorded_scene_keeps_redraw_requests():
    draws = []
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: draws.append(1))
    bpy = SimpleNamespace(context=SimpleNamespace(scene=object(),
        window_manager=SimpleNamespace(windows=[SimpleNamespace(screen=SimpleNamespace(
            areas=[area], is_animation_playing=True))])))
    with patch.dict(sys.modules, bpy=bpy), patch.multiple(runtime, _client=None, _health=None,
            _last_tick=None, _state=FrontendState(connection='LOCAL DEMO')), \
            patch.object(farm_flex, 'is_active', return_value=True):
        runtime.tick()
        assert draws == [1]

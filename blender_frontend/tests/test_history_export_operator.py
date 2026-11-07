"""Export completion belongs to the initiating scene across UI and file changes."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import charts
from wfrl_blender.history import ExportJob, HistoryWindow


class Scene(dict):
    removed = False

    def __setitem__(self, key, value):
        if self.removed:
            raise ReferenceError('Scene has been removed')
        super().__setitem__(key, value)


@pytest.fixture
def export_ui(monkeypatch):
    timers = {}
    scene = Scene()
    context = NS(scene=scene)
    bpy = NS(context=context, types=NS(Operator=object),
             props=NS(StringProperty=lambda **_kwargs: None),
             path=NS(abspath=str), app=NS(timers=NS(
                 is_registered=lambda callback: callback in timers,
                 register=lambda callback, **options: timers.update({callback: options}),
                 unregister=lambda callback: timers.pop(callback))))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(charts, 'export_job', ExportJob())
    monkeypatch.setattr(charts, 'raw_history', HistoryWindow())
    source = Path(__file__).parents[1] / 'wfrl_blender/operators/history_export.py'
    spec = spec_from_file_location('wfrl_blender.operators._host_history_export', source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    def start(path):
        operator = module.WFRL_OT_ExportHistory()
        operator.filepath = str(path)
        operator.report = Mock()
        assert operator.execute(context) == {'FINISHED'}
        return charts.export_job

    return NS(module=module, context=context, scene=scene, timers=timers, start=start)


@pytest.mark.parametrize('failure', [False, True])
def test_switching_scenes_cannot_move_export_completion(export_ui, tmp_path, failure):
    target = tmp_path / 'history.json' if not failure else tmp_path / 'missing/history.json'
    job = export_ui.start(target)
    job.thread.join(2)
    assert not job.thread.is_alive()
    other = Scene(wfrl_history_export_status='other scene status')
    export_ui.context.scene = other
    assert export_ui.module._poll_export() is None
    assert export_ui.scene['wfrl_history_export_status'].startswith('FAILED /' if failure else 'COMPLETE /')
    assert other == {'wfrl_history_export_status': 'other scene status'}


def test_deleted_export_owner_does_not_write_into_replacement_scene(export_ui, tmp_path):
    job = export_ui.start(tmp_path / 'history.json')
    job.thread.join(2)
    export_ui.scene.removed = True
    other = export_ui.context.scene = Scene()
    assert export_ui.module._poll_export() is None
    assert other == {}
    assert job.poll() == 'COMPLETE'


def test_new_export_finalizes_previous_scene_before_reusing_worker(export_ui, tmp_path):
    first = tmp_path / 'first.json'
    job = export_ui.start(first)
    job.thread.join(2)
    assert job.poll() == 'COMPLETE'
    other = export_ui.context.scene = Scene()
    second = tmp_path / 'second.json'
    job = export_ui.start(second)
    assert export_ui.scene['wfrl_history_export_status'] == f'COMPLETE / {first}'
    job.thread.join(2)
    assert export_ui.module._poll_export() is None
    assert other['wfrl_history_export_status'] == f'COMPLETE / {second}'


def test_reenable_reports_finished_background_export_to_its_owner(export_ui, tmp_path):
    job = export_ui.start(tmp_path / 'history.json')
    export_ui.module.unregister_timer()
    assert not export_ui.timers
    job.thread.join(2)
    other = export_ui.context.scene = Scene()
    export_ui.module.register_timer()
    assert export_ui.timers[export_ui.module._poll_export]['persistent']
    assert export_ui.module._poll_export() is None
    assert export_ui.scene['wfrl_history_export_status'].startswith('COMPLETE /')
    assert other == {}


def test_poll_without_an_export_never_changes_scene(export_ui):
    assert export_ui.module._poll_export() is None
    assert export_ui.scene == {}

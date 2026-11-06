"""Studio callback failures using Qt widgets and render doubles without OpenGL."""
import os
from types import SimpleNamespace as NS
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from qtpy import QtCore, QtGui, QtWidgets

from wfrl.channels import registry
from wfrl.scene.schema import Scene, SensorSpec, Turbine
from wfrl.studio import app as module
from wfrl.viz import animate


def assert_logged(caplog, error):
    records = [record for record in caplog.records if record.name == module.__name__]
    assert any(record.exc_info and record.exc_info[1] is error for record in records)
    assert type(error).__name__ in caplog.text
    assert str(error) in caplog.text


@pytest.fixture
def window():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    parent = QtWidgets.QMainWindow()
    parent._on_turbine_window_closed = Mock()

    class RenderWindow(module.TurbineWindow):
        def __init__(self):
            QtWidgets.QMainWindow.__init__(self, parent)
            self._ready = False
            self.idx, self.turb = 0, None
            self._build = Mock()
            self.timer = QtCore.QTimer(self)
            self.plotter = NS(clear=Mock(), set_background=Mock(), add_axes=Mock(), close=Mock())

    win = RenderWindow()
    yield win
    win.timer.stop()
    parent.deleteLater()
    app.processEvents()


def test_failed_initialization_is_not_ready_and_does_not_start_refresh(window, caplog):
    error = RuntimeError("render initialization failed")
    window._build.side_effect = error
    window.showEvent(QtGui.QShowEvent())
    assert not window._ready and not window.timer.isActive()
    assert_logged(caplog, error)

    window._build.side_effect = None
    window.showEvent(QtGui.QShowEvent())
    assert window._ready and window.timer.isActive()
    assert window._build.call_count == 2
    window.showEvent(QtGui.QShowEvent())
    assert window._build.call_count == 2


def test_initialization_retry_clears_partially_created_geometry(window, caplog):
    actors = []
    builds = []

    def build(index):
        assert actors == [] and window.turb is None
        window.turb = object()
        actors.append(window.turb)
        builds.append(window.turb)

    window.plotter.clear.side_effect = actors.clear
    window._build.side_effect = build
    error = RuntimeError("axes initialization failed")
    window.plotter.add_axes.side_effect = error
    window.showEvent(QtGui.QShowEvent())
    assert not window._ready and not window.timer.isActive()
    assert actors == builds
    assert_logged(caplog, error)

    window.plotter.add_axes.side_effect = None
    window.showEvent(QtGui.QShowEvent())
    assert window._ready and window.timer.isActive()
    assert len(builds) == 2 and actors == [builds[-1]]
    window.plotter.clear.assert_called()


def test_close_failure_still_stops_refresh_and_notifies_parent(window, caplog):
    error = RuntimeError("render close failed")
    window._ready = True
    window.timer.start(33)
    window.plotter.close.side_effect = error
    event = QtGui.QCloseEvent()
    window.closeEvent(event)
    assert not window._ready and not window.timer.isActive()
    assert event.isAccepted()
    window.parent()._on_turbine_window_closed.assert_called_once()
    assert_logged(caplog, error)


@pytest.mark.parametrize("method,value", [
    ("_on_cam_changed", 0), ("_set_fov", 35), ("_set_pitch", -30),
])
@pytest.mark.parametrize("failure", ["pose", "repaint"])
def test_camera_callbacks_log_pose_and_repaint_failures(method, value, failure, caplog):
    error = RuntimeError("camera callback failed")
    win = NS(
        _ready=True, turb=object(), _current_cam=lambda: (None, None),
        _fov_lbl=NS(setText=Mock()), _pitch_lbl=NS(setText=Mock()),
        _apply_view=Mock(), plotter=NS(interactor=NS(repaint=Mock())),
    )
    (win._apply_view if failure == "pose" else win.plotter.interactor.repaint).side_effect = error
    getattr(module.TurbineWindow, method)(win, value)
    assert_logged(caplog, error)


def test_turbine_switch_logs_build_failure(caplog):
    error = RuntimeError("turbine build failed")
    win = NS(_ready=True, turb={"tower": object(), "nac": object(), "blades": []},
             plotter=NS(remove_actor=Mock()), _build=Mock(side_effect=error))
    module.TurbineWindow._switch(win, 1)
    assert_logged(caplog, error)


@pytest.mark.parametrize("collector", ["turbine", "studio"])
@pytest.mark.parametrize("fault", ["unknown-sensor", "missing-turbine", "invalid-params"])
def test_invalid_camera_configuration_is_logged(collector, fault, caplog):
    spec = SensorSpec("camera")
    if fault == "unknown-sensor":
        spec.type = "unregistered-fixture"
    elif fault == "missing-turbine":
        spec.turbines = "missing-fixture"
    else:
        spec.params = None
    scene = Scene(turbines=[Turbine("T1", 0., 0.)], sensors=[spec])
    if collector == "turbine":
        assert module.TurbineWindow._collect_cameras(NS(), scene, None) == []
    else:
        win = NS(scene=scene, _cams=[object()])
        module.StudioWindow._collect_cams(win)
        assert win._cams == []
    assert len(caplog.records) == 1 and caplog.records[0].exc_info
    assert "相机传感器配置无效" in caplog.text


@pytest.mark.parametrize("collector", ["turbine", "studio"])
def test_camera_constructor_programming_error_propagates(collector, monkeypatch):
    monkeypatch.setattr(registry, "build", Mock(side_effect=RuntimeError("sensor bug")))
    with pytest.raises(RuntimeError, match="sensor bug"):
        if collector == "turbine":
            module.TurbineWindow._collect_cameras(NS(), NS(), None)
        else:
            module.StudioWindow._collect_cams(NS(scene=NS()))


def test_camera_toggle_logs_failure_and_still_updates_viewport(caplog):
    error = RuntimeError("channel render failed")
    win = NS(view=NS(set_channel=Mock(side_effect=error)), trainer=NS(runtime=None),
             _toggle_camera_viewport=Mock())
    module.StudioWindow._toggle_camera_master(win, False)
    win._toggle_camera_viewport.assert_called_once_with(False)
    assert_logged(caplog, error)


@pytest.mark.parametrize("failure", ["remove", "build", None])
def test_right_turbine_restores_main_viewport_and_propagates_failures(monkeypatch, failure):
    error = TypeError("invalid render actor")
    renderer = NS(RemoveActor=Mock(side_effect=error if failure == "remove" else None))
    build = Mock(side_effect=error if failure == "build" else None)
    monkeypatch.setattr(animate, "build_turbine", build)
    old = {"tower": object(), "nac": object(), "blades": []}
    win = NS(
        plotter=NS(subplot=Mock(), renderers=[NS(), renderer]), _cam_turb=old, _cams=[],
        scene=NS(turbines=[NS(x=0., y=0.)], hub_height=90.), _apply_cam_pose=Mock(),
    )
    if failure:
        with pytest.raises(TypeError) as raised:
            module.StudioWindow._build_right_turbine(win)
        assert raised.value is error
        win._apply_cam_pose.assert_not_called()
    else:
        module.StudioWindow._build_right_turbine(win)
        assert win._cam_turb is build.return_value
        win._apply_cam_pose.assert_called_once()
    assert [call.args for call in win.plotter.subplot.call_args_list] == [(0, 1), (0, 0)]
    if failure == "remove":
        build.assert_not_called()


def test_right_renderer_and_actors_are_marked_dirty():
    actor = NS(Modified=Mock())
    renderer = NS(Modified=Mock(), GetActors=lambda: [actor])
    module.StudioWindow._mark_right_dirty(NS(plotter=NS(renderers=[NS(), renderer])))
    renderer.Modified.assert_called_once()
    actor.Modified.assert_called_once()


def test_invalid_right_renderer_is_not_silenced():
    with pytest.raises(IndexError):
        module.StudioWindow._mark_right_dirty(NS(plotter=NS(renderers=[])))


def render_window():
    return NS(
        _clock=NS(nsecsElapsed=lambda: 1_000_000_000), _last_ns=0,
        _update_right_turbine=Mock(), view=NS(tick=Mock()),
        plotter=NS(interactor=NS(repaint=Mock())),
    )


@pytest.mark.parametrize("failure", ["update", "tick", "repaint"])
def test_render_callback_logs_errors_and_always_attempts_repaint(caplog, failure):
    error = RuntimeError("render callback failed")
    win = render_window()
    actions = {"update": win._update_right_turbine, "tick": win.view.tick,
               "repaint": win.plotter.interactor.repaint}
    actions[failure].side_effect = error
    module.StudioWindow._render(win)
    win.plotter.interactor.repaint.assert_called_once()
    assert_logged(caplog, error)


@pytest.mark.parametrize("callback", ["render", "refresh"])
def test_render_logging_tracks_changed_errors_and_recovers(monkeypatch, caplog, callback):
    if callback == "render":
        win = render_window()
        fail = win._update_right_turbine
        run = module.StudioWindow._render
    else:
        fail = Mock()
        monkeypatch.setattr(animate, "update_turbine", fail)
        win = NS(
            _ready=True, turb={}, idx=0, _diag=True, _current_cam=lambda: (None, None),
            src_view=NS(_yaw=np.zeros(1), _spin=np.zeros(1), _pitch=np.zeros(1), flex_scale=1.),
            plotter=NS(render=Mock(), interactor=NS(repaint=Mock())),
        )
        run = module.TurbineWindow._refresh
    first = RuntimeError("first render failure")
    fail.side_effect = first
    run(win)
    run(win)
    assert len(caplog.records) == 1
    assert_logged(caplog, first)

    second = ValueError("second render failure")
    fail.side_effect = second
    run(win)
    assert len(caplog.records) == 2
    assert_logged(caplog, second)

    fail.side_effect = None
    run(win)
    fail.side_effect = second
    run(win)
    assert len(caplog.records) == 3

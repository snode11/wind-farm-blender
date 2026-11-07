"""Camera construction releases owned data and preserves the initiating error."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from wfrl_blender import cameras


@pytest.fixture
def camera_runtime(monkeypatch):
    class Camera(dict):
        type = 'CAMERA'
    data = NS(users=0)
    camera = Camera()
    camera.data = data
    camera.matrix_parent_inverse = Mock()
    collection = NS(objects=NS(), children_recursive=[])
    root = NS(name='WFRL.Turbine.T1.YawRoot', users_collection=[collection])
    scene = NS(collection=collection, objects={root.name: root,
        'WFRL.Turbine.T1.Nacelle': object(), 'WFRL.Turbine.T1.Rotor': NS(parent=root)})
    def new(name, _data):
        camera.name = name
        return camera
    def link(obj):
        scene.objects[obj.name] = obj
    collection.objects.link = Mock(side_effect=link)
    objects = NS(new=Mock(side_effect=new), get=lambda _name: None, remove=Mock())
    datablocks = NS(new=Mock(return_value=data), remove=Mock())
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(objects=objects, cameras=datablocks, scenes=[scene])))
    monkeypatch.setattr(cameras, 'reset_gimbal', Mock())
    monkeypatch.setattr(cameras, 'reset_nacelle_gimbal', Mock())
    return NS(scene=scene, camera=camera, data=data, objects=objects,
              datablocks=datablocks, link=collection.objects.link)


@pytest.mark.parametrize('kind', ['down', 'nacelle'])
@pytest.mark.parametrize('boundary', ['object', 'link', 'reset'])
def test_camera_construction_failure_disposes_allocated_data(camera_runtime, kind, boundary):
    r = camera_runtime
    primary = KeyError('camera construction failed')
    reset = cameras.reset_gimbal if kind == 'down' else cameras.reset_nacelle_gimbal
    {'object': r.objects.new, 'link': r.link, 'reset': reset}[boundary].side_effect = primary
    ensure = cameras.ensure_gimbal if kind == 'down' else cameras.ensure_nacelle_gimbal
    with pytest.raises(KeyError) as caught:
        ensure(r.scene, 'T1')
    assert caught.value is primary
    r.datablocks.remove.assert_called_once_with(r.data)
    if boundary == 'object':
        r.objects.remove.assert_not_called()
    else:
        r.objects.remove.assert_called_once_with(r.camera, do_unlink=True)


@pytest.mark.parametrize('kind', ['down', 'nacelle'])
def test_camera_cleanup_failure_preserves_primary(camera_runtime, kind, caplog):
    r = camera_runtime
    primary = TypeError('invalid camera state')
    reset = cameras.reset_gimbal if kind == 'down' else cameras.reset_nacelle_gimbal
    reset.side_effect = primary
    r.objects.remove.side_effect = RuntimeError('camera release failed')
    ensure = cameras.ensure_gimbal if kind == 'down' else cameras.ensure_nacelle_gimbal
    with pytest.raises(TypeError) as caught:
        ensure(r.scene, 'T1')
    assert caught.value is primary
    assert 'camera release failed' in caplog.text


def test_layout_cancellation_reports_timer_cleanup_failure(monkeypatch, caplog):
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', {'scene': object()})
    timers = NS(is_registered=lambda _callback: True,
                unregister=Mock(side_effect=RuntimeError('timer release failed')))
    monkeypatch.setitem(sys.modules, 'bpy', NS(app=NS(timers=timers)))
    cameras.cancel_view_layout()
    assert cameras._LAYOUT_JOB is None
    assert 'timer release failed' in caplog.text

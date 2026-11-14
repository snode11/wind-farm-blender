"""Replay view failures preserve scene selection, visibility and viewport state."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


class Scene(dict):
    def as_pointer(self):
        return 72


class Objects(dict):
    def __iter__(self):
        return iter(self.values())


class Object(dict):
    def __init__(self, name, kind='CAMERA'):
        super().__init__()
        self.name, self.type = name, kind
        self.hidden, self.hide_render = False, False
        self.location, self.rotation_euler = (1., 2., 3.), (0., .1, .2)
        self.data = NS(lens=35., shift_x=.1, shift_y=.2, type='PERSP', sensor_fit='AUTO',
                       sensor_width=36., sensor_height=24., show_passepartout=True,
                       passepartout_alpha=.5)

    def hide_get(self):
        return self.hidden

    def hide_set(self, hidden):
        self.hidden = hidden


@pytest.fixture
def views(frontend_module, stub_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.farm_replay')
    demo = frontend_module('wfrl_blender.panels.demo')
    names = ('WFRL.Camera.T1.FrontQuarter', 'WFRL.Camera.FarmFlexOverview',
             'WFRL.Camera.T1.TipComparison', 'WFRL.Camera.Top',
             'WFRL.Camera.T1.Gimbal', 'WFRL.Camera.T2.Gimbal')
    objects = Objects((name, Object(name)) for name in names)
    for turbine in ('T1', 'T2', 'T3'):
        name = f'WFRL.Turbine.{turbine}.Rotor'
        objects[name] = Object(name, 'MESH')
    scene = Scene(wfrl_clearance_turbine='T1', wfrl_camera=names[0], wfrl_farm_view='T1_FRONT',
                  wfrl_nacelle_previous_output=[1920, 1080, 1., 1.])
    scene.objects, scene.camera = objects, objects[names[0]]
    scene.wfrl_gimbal_kind, scene.wfrl_gimbal_turbine = 'DEMO', 'T1'
    scene.wfrl_flex_show_tip_trails, scene.wfrl_deflection_visible = False, False
    scene.render = NS(resolution_x=800, resolution_y=600, pixel_aspect_x=1., pixel_aspect_y=1.)
    region = NS(view_perspective='PERSP', view_camera_zoom=12., view_camera_offset=(.1, .2))
    space = NS(use_local_camera=True, camera=objects[names[1]], region_3d=region)
    area = NS(type='VIEW_3D', spaces=NS(active=space))
    context = NS(scene=scene, screen=NS(areas=[area]), window=object())
    preview = NS(readers={'T1': object(), 'T2': object(), 'T3': object()},
                 visible_turbines={0}, cache={'saved': object()}, comparison=object(), update=Mock(),
                 frame_error='')
    farm = stub_module('wfrl_blender.farm_flex', is_active=lambda _scene: True,
                      active_for=lambda _scene: preview, ready_for=lambda _scene: preview)
    monkeypatch.setattr(panel, 'farm_flex', farm)
    readers = {scene.as_pointer(): preview.readers['T1']}
    clearance = stub_module('wfrl_blender.clearance_replay', _READERS=readers)
    monkeypatch.setattr(panel, 'clearance_replay', clearance)
    gimbal = stub_module('wfrl_blender.panels.gimbal', _ACTIVE=None)
    dismiss = Mock()
    stub_module('wfrl_blender.panels.custom_cameras', dismiss_for_view_switch=dismiss)
    stub_module('wfrl_blender.split_reconstruction', enabled=lambda _scene: False,
                is_reconstruction_view=lambda *_args: False)
    stub_module('wfrl_blender.home_state', can_start=lambda *_args: (True, ''))
    trails = stub_module('wfrl_blender.tip_tracking', active=lambda _scene: None,
                         disable=Mock(), enable=Mock())
    cameras = stub_module('wfrl_blender.cameras',
        ensure_gimbal=Mock(side_effect=lambda _scene, tid: objects[f'WFRL.Camera.{tid}.Gimbal']),
        ensure_nacelle_gimbal=Mock(), down_gimbal=Mock(), nacelle_side_view=Mock(),
        restore_nacelle_view=Mock(), fill_camera_view=Mock(),
        demo_view_areas=lambda _scene: [area], set_view_layout=Mock(), _LAYOUT_JOB=None)
    output = Mock()
    stub_module('wfrl_blender.nacelle_camera', select_output=output)
    monkeypatch.setattr(panel.bpy.app, 'background', False)
    initial = dict(props=dict(scene), camera=scene.camera, render=vars(scene.render).copy(),
                   space=vars(space).copy(), view=vars(region).copy(),
                   visible=set(preview.visible_turbines), cache=preview.cache.copy(),
                   hidden=[(obj, obj.hide_get(), obj.hide_render) for obj in objects])
    return NS(panel=panel, demo=demo, scene=scene, context=context, preview=preview,
              cameras=cameras, gimbal=gimbal, output=output, initial=initial, dismiss=dismiss,
              space=space, view=region, trails=trails, readers=readers)


def assert_view_unchanged(v):
    assert dict(v.scene) == v.initial['props']
    assert v.scene.camera is v.initial['camera']
    assert vars(v.scene.render) == v.initial['render']
    assert vars(v.space) == v.initial['space']
    assert vars(v.view) == v.initial['view']
    assert v.scene.wfrl_gimbal_kind == 'DEMO' and v.scene.wfrl_gimbal_turbine == 'T1'
    assert not v.scene.wfrl_deflection_visible
    assert v.preview.visible_turbines == v.initial['visible']
    assert v.preview.cache == v.initial['cache']
    assert v.readers[v.scene.as_pointer()] is v.preview.readers['T1']
    assert all((obj.hide_get(), obj.hide_render) == (hidden, render)
               for obj, hidden, render in v.initial['hidden'])


@pytest.mark.parametrize('missing', ['turbine', 'camera', 'mount'])
def test_farm_view_preflight_does_not_discard_current_view(views, missing):
    v = views
    operator = v.panel.WFRL_OT_FarmFlexView()
    operator.turbine, operator.angle, operator.report = 'T2', 'DOWN', Mock()
    v.gimbal._ACTIVE = NS(finish=Mock())
    if missing == 'turbine':
        operator.turbine = 'T9'
    elif missing == 'camera':
        operator.turbine = 'all'
        del v.scene.objects['WFRL.Camera.FarmFlexOverview']
    else:
        v.cameras.ensure_gimbal.side_effect = ValueError('mount unavailable')

    assert operator.execute(v.context) == {'CANCELLED'}
    assert_view_unchanged(v)
    v.gimbal._ACTIVE.finish.assert_not_called()
    v.dismiss.assert_not_called()
    assert operator.report.called


@pytest.mark.parametrize('kind', ['farm', 'camera'])
@pytest.mark.parametrize('failure', ['output', 'framing'])
def test_view_failure_rolls_back_scene_and_viewport(views, kind, failure):
    v = views
    operator = v.panel.WFRL_OT_FarmFlexView() if kind == 'farm' else v.demo.WFRL_OT_SelectCamera()
    operator.turbine, operator.angle = 'T2', 'DOWN'
    operator.camera_name, operator.report = 'WFRL.Camera.T2.Gimbal', Mock()

    def output(_scene, _camera):
        v.scene.render.resolution_x = 1200
        del v.scene['wfrl_nacelle_previous_output']
        if failure == 'output':
            raise RuntimeError('output failed')

    def frame(*_args):
        v.view.view_camera_offset = (0., 0.)
        v.view.view_camera_zoom = 99.
        raise RuntimeError('framing failed')

    v.output.side_effect = output
    v.cameras.fill_camera_view.side_effect = frame
    assert operator.execute(v.context) == {'CANCELLED'}
    assert_view_unchanged(v)
    assert failure in operator.report.call_args.args[1]


@pytest.mark.parametrize('kind', ['world', 'deflection'])
def test_cancelled_farm_child_cannot_be_reported_as_finished(views, kind):
    v = views
    v.panel.bpy.ops.wfrl.farm_flex_view.return_value = {'CANCELLED'}
    operator = v.demo.WFRL_OT_SelectCamera() if kind == 'world' else v.panel.WFRL_OT_DeflectionView()
    operator.camera_name, operator.report = 'WFRL.Camera.World', Mock()

    assert operator.execute(v.context) == {'CANCELLED'}
    assert_view_unchanged(v)
    assert operator.report.called


@pytest.mark.parametrize('kind', ['top', 'deflection'])
def test_missing_target_camera_is_rejected_before_farm_child(views, kind):
    v = views
    name = 'WFRL.Camera.Top' if kind == 'top' else 'WFRL.Camera.T1.TipComparison'
    del v.scene.objects[name]
    operator = v.demo.WFRL_OT_SelectCamera() if kind == 'top' else v.panel.WFRL_OT_DeflectionView()
    operator.camera_name, operator.report = name, Mock()
    v.panel.bpy.ops.wfrl.farm_flex_view.return_value = {'FINISHED'}

    assert operator.execute(v.context) == {'CANCELLED'}
    v.panel.bpy.ops.wfrl.farm_flex_view.assert_not_called()
    assert_view_unchanged(v)
    assert operator.report.called


def test_pending_split_layout_does_not_run_farm_child(views):
    v = views
    v.cameras._LAYOUT_JOB = {'screen': v.context.screen, 'layout': 'DUAL'}
    operator = v.panel.WFRL_OT_FarmSplitView()
    operator.mode, operator.report = 'NACELLE', Mock()

    assert not operator.poll(v.context)
    assert operator.execute(v.context) == {'CANCELLED'}
    v.panel.bpy.ops.wfrl.farm_flex_view.assert_not_called()
    v.cameras.set_view_layout.assert_not_called()
    assert_view_unchanged(v)


def test_split_layout_failure_restores_completed_child_view(views):
    v = views
    operator = v.panel.WFRL_OT_FarmSplitView()
    operator.mode, operator.report = 'DOWN', Mock()

    def child(**_kwargs):
        v.scene.camera = v.scene.objects['WFRL.Camera.T1.Gimbal']
        v.scene['wfrl_camera'] = v.scene.camera.name
        v.preview.visible_turbines = {0, 1}
        v.space.camera = v.scene.camera
        v.view.view_perspective = 'CAMERA'
        return {'FINISHED'}

    v.panel.bpy.ops.wfrl.farm_flex_view.side_effect = child
    v.cameras.set_view_layout.side_effect = RuntimeError('split unavailable')
    assert operator.execute(v.context) == {'CANCELLED'}
    assert_view_unchanged(v)


def test_farm_view_waits_for_gimbal_cleanup(views):
    v = views
    v.gimbal._ACTIVE = NS(finish=Mock())
    operator = v.panel.WFRL_OT_FarmFlexView()
    operator.turbine, operator.angle, operator.report = 'T2', 'DOWN', Mock()

    assert operator.execute(v.context) == {'CANCELLED'}
    assert_view_unchanged(v)
    v.dismiss.assert_not_called()
    assert '云台' in operator.report.call_args.args[1]


@pytest.mark.parametrize('kind', ['farm', 'camera'])
def test_failed_camera_preparation_restores_existing_camera(views, kind):
    v = views
    camera = v.scene.objects['WFRL.Camera.T2.Gimbal']
    original = (camera.location, camera.rotation_euler, vars(camera.data).copy(), dict(camera))

    def prepare(*_args):
        camera.location, camera.rotation_euler = (9., 8., 7.), (1., 2., 3.)
        camera['gimbal_yaw'] = 45.
        camera.data.lens = 8.
        raise ValueError('invalid mount geometry')

    v.cameras.ensure_gimbal.side_effect = prepare
    operator = v.panel.WFRL_OT_FarmFlexView() if kind == 'farm' else v.demo.WFRL_OT_SelectCamera()
    operator.turbine, operator.angle = 'T2', 'DOWN'
    operator.camera_name, operator.report = camera.name, Mock()
    assert operator.execute(v.context) == {'CANCELLED'}
    assert (camera.location, camera.rotation_euler, vars(camera.data), dict(camera)) == original
    assert_view_unchanged(v)


@pytest.mark.parametrize('target', ['all', 'T2', 'T1'])
def test_successful_farm_selection_keeps_expected_camera_and_visibility(views, target):
    v = views
    operator = v.panel.WFRL_OT_FarmFlexView()
    operator.turbine, operator.angle, operator.report = target, 'FRONT' if target == 'T1' else 'DOWN', Mock()

    assert operator.execute(v.context) == {'FINISHED'}
    expected = ('WFRL.Camera.FarmFlexOverview' if target == 'all' else
                'WFRL.Camera.T1.FrontQuarter' if target == 'T1' else 'WFRL.Camera.T2.Gimbal')
    assert v.scene.camera is v.scene.objects[expected]
    assert v.space.camera is v.scene.camera and not v.space.use_local_camera
    assert v.view.view_perspective == 'CAMERA'
    assert v.preview.visible_turbines == ({0, 1, 2} if target == 'all' else {int(target[1:]) - 1})
    assert not v.preview.cache
    assert v.scene['wfrl_camera'] == expected
    v.preview.update.assert_called_once_with(v.scene)
    operator.report.assert_not_called()


def test_source_frame_failure_is_not_made_ready_by_view_rollback(views):
    v = views

    def invalid_source(_scene):
        v.preview.frame_error = 'invalid geometry'
        v.readers.clear()
        raise ValueError(v.preview.frame_error)

    v.preview.update.side_effect = invalid_source
    operator = v.panel.WFRL_OT_FarmFlexView()
    operator.turbine, operator.angle, operator.report = 'T2', 'DOWN', Mock()
    assert operator.execute(v.context) == {'CANCELLED'}
    assert v.scene.camera is v.initial['camera']
    assert not v.readers and v.preview.frame_error == 'invalid geometry'


def test_camera_snapshot_preserves_rna_property_reference_without_copying(views):
    v = views
    reference = NS(bl_rna=object(), copy=Mock())
    camera = v.scene.objects['WFRL.Camera.T2.Gimbal']
    camera['reference_object'] = reference
    v.output.side_effect = RuntimeError('output failed')
    operator = v.panel.WFRL_OT_FarmFlexView()
    operator.turbine, operator.angle, operator.report = 'T2', 'DOWN', Mock()

    assert operator.execute(v.context) == {'CANCELLED'}
    assert camera['reference_object'] is reference
    reference.copy.assert_not_called()


@pytest.mark.parametrize('kind', ['farm', 'camera'])
def test_failed_fixed_camera_framing_restores_camera_mask(views, kind):
    v = views
    camera = v.scene.objects['WFRL.Camera.T1.FrontQuarter' if kind == 'farm' else 'WFRL.Camera.Top']
    operator = v.panel.WFRL_OT_FarmFlexView() if kind == 'farm' else v.demo.WFRL_OT_SelectCamera()
    operator.turbine, operator.angle = 'T1', 'FRONT'
    operator.camera_name, operator.report = camera.name, Mock()
    v.panel.bpy.ops.wfrl.farm_flex_view.return_value = {'FINISHED'}

    def framing(*_args):
        camera.data.show_passepartout = False
        camera.data.passepartout_alpha = 0.
        raise RuntimeError('framing failed')

    v.cameras.fill_camera_view.side_effect = framing
    assert operator.execute(v.context) == {'CANCELLED'}
    assert camera.data.show_passepartout and camera.data.passepartout_alpha == .5
    assert_view_unchanged(v)

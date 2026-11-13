"""Demo outputs and package selection commit only completed operations."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


class Scene(dict):
    name = 'Replay'


@pytest.fixture
def still(frontend_module, monkeypatch, tmp_path):
    panel = frontend_module('wfrl_blender.panels.demo')
    scene = Scene(wfrl_last_render='previous.png')
    scene.camera = object()
    scene.render = NS(filepath='previous.exr', use_file_extension=True,
                      image_settings=NS(file_format='OPEN_EXR'))
    context = NS(scene=scene)
    operator = panel.WFRL_OT_RenderStill()
    operator.report = Mock()
    operator.filepath = str(tmp_path / 'capture.png')
    monkeypatch.setattr(panel.bpy.path, 'abspath', lambda value: value)
    return NS(panel=panel, scene=scene, context=context, operator=operator,
              destination=Path(operator.filepath))


@pytest.mark.parametrize('failure', ['cancel', 'exception'])
def test_still_failure_preserves_settings_and_existing_output(still, failure):
    s = still
    s.destination.write_bytes(b'previous complete image')

    def render(**_kwargs):
        Path(s.scene.render.filepath).write_bytes(b'incomplete image')
        if failure == 'exception':
            raise RuntimeError('render failed')
        return {'CANCELLED'}

    s.panel.bpy.ops.render.render.side_effect = render
    assert s.operator.execute(s.context) == {'CANCELLED'}
    assert s.scene.render.filepath == 'previous.exr'
    assert s.scene.render.image_settings.file_format == 'OPEN_EXR'
    assert s.scene['wfrl_last_render'] == 'previous.png'
    assert s.destination.read_bytes() == b'previous complete image'
    assert list(s.destination.parent.iterdir()) == [s.destination]
    assert s.operator.report.call_args.args[0] == {'ERROR'}


@pytest.mark.parametrize('extension', [True, False])
def test_still_success_publishes_png_after_renderer_finishes(still, extension):
    s = still
    s.scene.render.use_file_extension = extension
    s.operator.filepath = str(s.destination.with_suffix('.custom'))
    expected = Path(s.operator.filepath + '.png' if extension else s.operator.filepath)
    expected.write_bytes(b'previous complete image')

    def render(**_kwargs):
        assert expected.read_bytes() == b'previous complete image'
        assert s.scene['wfrl_last_render'] == 'previous.png'
        path = Path(s.scene.render.filepath)
        if s.scene.render.use_file_extension and path.suffix.lower() != '.png':
            path = Path(str(path) + '.png')
        path.write_bytes(b'completed PNG')
        return {'FINISHED'}

    s.panel.bpy.ops.render.render.side_effect = render
    assert s.operator.execute(s.context) == {'FINISHED'}
    assert expected.read_bytes() == b'completed PNG'
    assert s.scene.render.filepath == s.operator.filepath
    assert s.scene.render.image_settings.file_format == 'PNG'
    assert s.scene['wfrl_last_render'] == s.operator.filepath
    assert list(expected.parent.iterdir()) == [expected]


def test_still_cancel_without_previous_status_does_not_claim_output(still):
    del still.scene['wfrl_last_render']
    still.panel.bpy.ops.render.render.return_value = {'CANCELLED'}
    assert still.operator.execute(still.context) == {'CANCELLED'}
    assert 'wfrl_last_render' not in still.scene
    assert not list(still.destination.parent.iterdir())


@pytest.mark.parametrize('selection', ['missing.json', 'different.json'])
def test_dual_beam_requires_selected_manifest_not_just_its_parent(
        frontend_module, monkeypatch, tmp_path, selection):
    panel = frontend_module('wfrl_blender.panels.demo')
    import wfrl_blender
    from wfrl_blender import farm_flex
    (tmp_path / 'manifest.json').write_text('{}', encoding='utf-8')
    if selection == 'different.json':
        (tmp_path / selection).write_text('{}', encoding='utf-8')
    preflight = Mock(return_value=NS(overlay={'config': {}}))
    load = Mock()
    monkeypatch.setattr(farm_flex, 'preflight', preflight)
    monkeypatch.setattr(wfrl_blender, 'load_demo_scene', load)
    monkeypatch.setattr(panel.bpy.path, 'abspath', lambda value: value)
    operator = panel.WFRL_OT_LoadDualBeam()
    operator.filepath, operator.builtin_method = str(tmp_path / selection), 'AXIS'
    operator.report = Mock()
    scene = Scene()
    scene.wfrl_farm_panel_page = 'TOOLS'

    assert operator.execute(NS(scene=scene)) == {'CANCELLED'}
    load.assert_not_called()
    preflight.assert_not_called()
    assert scene.wfrl_farm_panel_page == 'TOOLS'
    assert 'wfrl_home_load_error' in scene
    assert operator.report.call_args.args[0] == {'ERROR'}


@pytest.mark.parametrize('kind', ['demo', 'dual'])
def test_demo_runtime_failure_is_reported_as_cancelled(frontend_module, monkeypatch, tmp_path, kind):
    panel = frontend_module('wfrl_blender.panels.demo')
    import wfrl_blender
    from wfrl_blender import farm_flex
    monkeypatch.setattr(wfrl_blender, 'load_demo_scene', Mock(side_effect=RuntimeError('scene build failed')))
    monkeypatch.setattr(farm_flex, 'preflight', lambda _path: NS(overlay={'config': {}}))
    monkeypatch.setattr(panel.bpy.path, 'abspath', lambda value: value)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}', encoding='utf-8')
    operator = panel.WFRL_OT_LoadDemo() if kind == 'demo' else panel.WFRL_OT_LoadDualBeam()
    operator.package_path, operator.filepath, operator.builtin_method = str(tmp_path), str(manifest), 'AXIS'
    operator.report = Mock()

    assert operator.execute(NS(scene=Scene())) == {'CANCELLED'}
    assert 'scene build failed' in operator.report.call_args.args[1]

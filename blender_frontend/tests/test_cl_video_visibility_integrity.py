"""Visibility validity and isolated video export failure contracts."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender import camera_video as video, nacelle_visibility as visibility


@pytest.mark.parametrize('invalid', [None, np.nan, np.inf, -1, 'visible'])
def test_visibility_rejects_missing_or_non_boolean_ray_results(invalid):
    sample = visibility.RaySample(np.zeros(3), np.ones(3), 0, .5, 0)
    band = visibility.SpanBand(0, 0., 1., ((0., 1.),), 1., (sample,))
    with pytest.raises(ValueError, match='bool'):
        visibility.evaluate_visibility((band,), lambda _: invalid)


def test_visibility_accepts_python_and_numpy_booleans():
    sample = visibility.RaySample(np.zeros(3), np.ones(3), 0, .5, 0)
    band = visibility.SpanBand(0, 0., 1., ((0., 1.),), 1., (sample, sample))
    results = iter((True, np.bool_(False)))
    report = visibility.evaluate_visibility((band,), lambda _: next(results))
    assert report.bands[0].visible == (True, False)


@pytest.mark.parametrize('count', [True, 1.5, np.nan, np.inf])
def test_shutter_requires_integer_sample_count(count):
    with pytest.raises(ValueError, match='integer'):
        video.shutter_times(.5, .01, count)


@pytest.mark.parametrize('center,exposure', [(np.nan, .01), (.5, np.inf), (.5, 0)])
def test_shutter_rejects_invalid_time_or_exposure(center, exposure):
    with pytest.raises(ValueError, match='finite|positive'):
        video.shutter_times(center, exposure, 2)


def test_frame_times_rejects_overflowed_duration():
    with pytest.raises(ValueError, match='finite'):
        video.frame_times(-1e308, 1e308, 20, .01)


@pytest.mark.parametrize('name', ['WFRL.Cinematic.T1', 'WFRL.FlexTipTrail.B1'])
def test_camera_video_excludes_illustrative_flow_and_flex_trails(name):
    obj = SimpleNamespace(name=name, type='CURVE', hide_render=False)
    assert video.clean_scene(SimpleNamespace(objects=[obj])) == [name]
    assert obj.hide_render


class Pixels:
    def foreach_get(self, target):
        target[:] = (.5, .5, .5, 1.)

    def foreach_set(self, values):
        self.values = values.copy()


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'manifest.json').write_text('{}', encoding='utf-8')
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setattr(video, '__file__', str(source / 'camera_video.py'))
    images = []

    def image(*_args, **_kwargs):
        value = SimpleNamespace(pixels=Pixels(), colorspace_settings=SimpleNamespace(),
                                filepath='', packed_file=None)
        value.save_render = lambda path, **_: Path(path).write_bytes(b'PNG master')
        images.append(value)
        return value

    class Images:
        load = staticmethod(image)
        new = staticmethod(image)

        def __iter__(self):
            return iter(images)

        def remove(self, value):
            images.remove(value)

    scene = SimpleNamespace(render=SimpleNamespace(resolution_x=1, resolution_y=1,
        image_settings=SimpleNamespace(exr_codec='ZIP', color_management='FOLLOW_SCENE'),
        engine='CYCLES'), cycles=SimpleNamespace())
    active = SimpleNamespace(times=np.array((0., .05)), readers={'T1': object()},
        manifest={'files': {}, 'status': 'REVIEW_ONLY'}, export_removed_objects=[],
        update=lambda *_args, **_kwargs: None)
    camera = SimpleNamespace(matrix_world=np.eye(4))
    bpy = SimpleNamespace(app=SimpleNamespace(version_string='host-test'),
        data=SimpleNamespace(images=Images()),
        context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(video, 'prepare_scene', lambda _: (scene, camera, active, []))
    monkeypatch.setattr(video, 'camera_configuration', lambda *_: {'resolution_policy': 'TEST'})
    monkeypatch.setattr(video, 'clean_scene', lambda _: [])
    args = SimpleNamespace(package=str(package), output=str(tmp_path / 'export'),
        focus_distance=None, aperture=None, samples=1, exposure_samples=1,
        width=1, height=1, test_resolution=True, exposure_s=.01, start_s=None,
        end_s=None, fps=20, scene=None, scene_scope='single', turbine='T1',
        device='cpu', engine='cycles', max_frames=None)
    output = Path(args.output)
    output.mkdir()

    def render():
        Path(scene.render.filepath).write_bytes(b'EXR sample')
        return {'FINISHED'}

    monkeypatch.setattr(video, 'render_sample', render)
    return args, scene, images, output


def test_camera_matrix_write_failure_preserves_existing_file(export_env, monkeypatch):
    args, _, _, output = export_env
    target = output / 'camera_world.npy'
    original = b'previous verified matrices'
    target.write_bytes(original)

    def fail_save(destination, *_args, **_kwargs):
        if hasattr(destination, 'write'):
            destination.write(b'incomplete')
        else:
            Path(destination).write_bytes(b'incomplete')
        raise OSError('matrix write failure')

    monkeypatch.setattr(video.np, 'save', fail_save)
    with pytest.raises(OSError, match='matrix write failure'):
        video._render_package(args)
    assert target.read_bytes() == original
    assert sorted(p.name for p in output.iterdir()) == [
        'camera.json', 'camera_world.npy', 'checks', 'master_frames', 'render_manifest.json']


@pytest.mark.parametrize('failure', ['render', 'pixels'])
def test_exposure_failure_releases_images_and_temporary_exr(export_env, monkeypatch, failure):
    args, scene, images, output = export_env
    if failure == 'render':
        def fail():
            Path(scene.render.filepath).write_bytes(b'incomplete EXR')
            raise RuntimeError('render failure')
        monkeypatch.setattr(video, 'render_sample', fail)
    else:
        def fail(self, _target):
            raise RuntimeError('pixels failure')
        monkeypatch.setattr(Pixels, 'foreach_get', fail)
    with pytest.raises(RuntimeError, match=failure + ' failure'):
        video._render_package(args)
    assert images == []
    assert not (output / '.exposure_sample.exr').exists()
    assert not list((output / 'master_frames').iterdir())


def test_export_normal_schedule_and_resume_preserve_matrices(export_env):
    args, _, images, output = export_env
    first = video._render_package(args)
    second = video._render_package(args)
    assert first['status'] == second['status'] == 'complete'
    assert first['rendered_this_run'] == 1 and second['rendered_this_run'] == 0
    np.testing.assert_array_equal(np.load(output / 'camera_world.npy'), np.eye(4)[None])
    record = json.loads((output / 'checks/render_frames.json').read_text(encoding='utf-8'))
    assert record['0']['sim_time_s'] == pytest.approx(.025)
    assert record['0']['exposure_samples_s'] == pytest.approx([.025])
    assert images == []

"""Host-only framing checks; Blender-facing functions use controlled adapters."""
import ast
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import traceback
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.blender import framing_metrics as metrics
from scripts.blender.export_geometry import write_export_files
from wfrl_blender import atomic_json


ROOT = Path(__file__).resolve().parents[2]


def script_functions(name, **environment):
    path = ROOT / 'scripts/blender' / f'{name}.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    namespace = dict(np=np, Path=Path, os=os, tempfile=tempfile, math=math,
                     atomic_json=atomic_json, visibility_summary=metrics.visibility_summary)
    namespace.update(environment)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('count', [0, 1, 2, 4])
def test_visibility_requires_three_camera_routes(count):
    with pytest.raises(ValueError, match='(?i)(three|3).*camera'):
        metrics.visibility_summary([{0}] * count, [0], [0., 1.])


@pytest.mark.parametrize('edges', [[0.], [0., 0.], [1., 0.], [0., float('nan')], [0., float('inf')]])
def test_visibility_rejects_invalid_span_grid(edges):
    with pytest.raises(ValueError, match='(?i)(span|edge|grid)'):
        metrics.visibility_summary([set()] * 3, [0], edges)


@pytest.mark.parametrize('sample', [-1, 0.5, 1, True])
def test_visibility_rejects_invalid_surface_sample_ids(sample):
    with pytest.raises(ValueError, match='(?i)(sample|index)'):
        metrics.visibility_summary([{sample}] * 3, [0], [0., 1.])


def test_empty_surface_sampling_keeps_triangle_and_weight_shapes():
    selected, bins, spans, weights = metrics.surface_cross_section_samples(
        np.empty((0, 3), dtype=int), np.empty((0, 3)), [0., 1.])
    assert selected.shape == weights.shape == (0, 3)
    assert bins.shape == spans.shape == (0,)
    assert np.issubdtype(bins.dtype, np.integer)


@pytest.mark.parametrize('count', [0, -1, 1.5, True])
def test_surface_sampling_rejects_invalid_budget(count):
    with pytest.raises(ValueError, match='(?i)(budget|max_per_bin|positive)'):
        metrics.surface_cross_section_samples([[0, 1, 2]], [[0., 0., 1.]], [0., 1.], count)


def row(fraction=0.):
    return {'visibility': metrics.visibility_summary([set()] * 3, [0], [0., 1.]),
            'cameras': [{'target_pixel_fraction': fraction, 'frame_pixels': 100}] * 3}


@pytest.mark.parametrize('fraction', [None, float('nan'), float('inf'), -0.1, 1.1])
def test_rank_rejects_invalid_pixel_measurements(fraction):
    with pytest.raises(ValueError, match='(?i)(pixel|fraction)'):
        metrics.candidate_rank([row(fraction)])


def test_rank_rejects_zero_image_area():
    sample = row()
    sample['cameras'] = [{'target_pixel_fraction': 0., 'frame_pixels': 0}] * 3
    with pytest.raises(ValueError, match='(?i)(pixel|area)'):
        metrics.candidate_rank([sample])


def test_fully_occluded_valid_measurements_are_zero_and_unqualified():
    sample = row()
    assert metrics.candidate_rank([sample]) == (0., 0., 0., 0.)
    assert not metrics.qualified([sample])
    assert sample['visibility']['gap_intervals_m'] == [[0., 1.]]


def test_layout_search_rejects_absent_front_facing_samples():
    parameters = SimpleNamespace(location=(0., 0., 1.), yaw=0., pitch=0., roll=0., fov=75., vfov=45.)
    class Direction:
        def __init__(self, values):
            self.values = np.asarray(values)

        def normalized(self):
            return self.values / np.linalg.norm(self.values)

    namespace = script_functions('find_three_camera_layout',
        core=SimpleNamespace(parameters=lambda camera: parameters),
        projection=SimpleNamespace(rotation=lambda *args: np.eye(3)),
        face_spans=np.array([2.]), centres=np.array([[0., 0., 0.]]),
        normals=np.zeros((1, 3)), world=np.eye(4), Vector=Direction,
        bpy=SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=lambda: object())),
        scene=SimpleNamespace(ray_cast=lambda *args, **kwargs: (False, None)))
    camera = SimpleNamespace(matrix_world=SimpleNamespace(translation=np.zeros(3)))
    with pytest.raises(ValueError, match='(?i)(sample|front)'):
        namespace['score_camera'](camera, (0., 5.))


def test_comparison_json_replace_failure_preserves_evidence(tmp_path, monkeypatch):
    target = tmp_path / 'result.json'
    target.write_bytes(b'old result')
    namespace = script_functions('compare_frontend_framing', json=json, OUT=tmp_path)

    def fail_replace(*args):
        raise OSError('injected replace failure')

    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError, match='injected replace failure'):
        namespace['write']('result.json', {'value': 1.})
    assert target.read_bytes() == b'old result'
    assert list(tmp_path.iterdir()) == [target]


def renderer(tmp_path, *, fail_render=False, fail_pixels=False):
    scene = SimpleNamespace(camera=None, render=SimpleNamespace(filepath=''))
    removed = []

    def render(**kwargs):
        Path(scene.render.filepath).write_bytes(b'new image')
        if fail_render:
            raise RuntimeError('injected render failure')

    def foreach_get(buffer):
        if fail_pixels:
            raise RuntimeError('injected pixel failure')
        buffer[:] = [0., 1., 0., 1., .4, .4, .4, 1.]

    image = SimpleNamespace(pixels=SimpleNamespace(foreach_get=foreach_get))
    bpy = SimpleNamespace(ops=SimpleNamespace(render=SimpleNamespace(render=render)),
        data=SimpleNamespace(images=SimpleNamespace(load=lambda *args, **kwargs: image,
                                                   remove=removed.append)))
    namespace = script_functions('compare_frontend_framing', bpy=bpy, WIDTH=2, HEIGHT=1)
    target = tmp_path / 'image.png'
    target.write_bytes(b'old image')
    return namespace, scene, target, removed


@pytest.mark.parametrize('failure', ['render', 'pixels'])
def test_render_failure_preserves_previous_image_and_releases_resources(tmp_path, failure):
    namespace, scene, target, removed = renderer(
        tmp_path, fail_render=failure == 'render', fail_pixels=failure == 'pixels')
    with pytest.raises(RuntimeError, match='injected'):
        namespace['render_camera'](scene, object(), target)
    assert target.read_bytes() == b'old image'
    assert list(tmp_path.iterdir()) == [target]
    assert len(removed) == int(failure == 'pixels')


def test_render_preserves_normal_mask_measurement_and_releases_image(tmp_path):
    namespace, scene, target, removed = renderer(tmp_path)
    assert namespace['render_camera'](scene, object(), target) == (1, .5)
    assert target.read_bytes() == b'new image'
    assert list(tmp_path.iterdir()) == [target]
    assert len(removed) == 1


class Scene(dict):
    def frame_set(self, frame):
        self.frame_current = frame


def evaluator(*, change_frame=False, clip_far=100., recorded_far=100., focal_length=50.):
    scene = Scene(wfrl_clearance_time_s=2.)
    params = dict(location=[0., 0., 0.], yaw=0., pitch=0., roll=0., fov=90., vfov=90.,
                  clip_near_m=.01, clip_far_m=clip_far, output_long_edge_px=320,
                  focal_length_mm_record=focal_length)
    expected = dict(params, clip_far_m=recorded_far)
    candidate = {'id': 'current', 'layout': {'cameras': [
        {'slot_id': slot, 'parameters': copy.deepcopy(expected)} for slot in (1, 2, 3)]}}

    class Matrix:
        translation = np.zeros(3)

        def inverted(self):
            return np.eye(4)

    camera = SimpleNamespace(matrix_world=Matrix())
    camera.evaluated_get = lambda deps: camera
    points = np.array([[-.5, -.5, -2.], [.5, -.5, -2.], [0., .5, -2.]])

    class Vertices:
        def __len__(self):
            return 3

        def foreach_get(self, key, values):
            values[:] = points.ravel()

    target = SimpleNamespace(data=SimpleNamespace(vertices=Vertices()), matrix_world=np.eye(4))
    target.evaluated_get = lambda deps: target
    scene.ray_cast = lambda *args, **kwargs: (True, points.mean(axis=0), None, None, target, None)

    def render(*args):
        if change_frame:
            scene.frame_current += 1
        return 1, .01

    namespace = script_functions('compare_frontend_framing',
        bpy=SimpleNamespace(context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None),
                                                    evaluated_depsgraph_get=lambda: object())),
        core=SimpleNamespace(SLOTS=(1, 2, 3), get_camera=lambda *args: camera,
                             parameters=lambda camera: SimpleNamespace(**params)),
        Vector=np.asarray, EDGES=[0., 1.], WIDTH=320, HEIGHT=180, OUT=Path('/unused'))
    namespace['render_camera'] = render
    args = (scene, candidate, 0., target, np.array([[0, 1, 2]]), np.array([0]),
            np.array([.5]), np.ones((1, 3)) / 3, [2., 3.], [0., 360.], 30., 'coarse')
    return namespace, args


def test_comparison_rejects_frame_change_during_render():
    namespace, args = evaluator(change_frame=True)
    with pytest.raises((ValueError, RuntimeError), match='(?i)(frame|time)'):
        namespace['evaluate'](*args)


def test_comparison_rejects_camera_clip_change():
    namespace, args = evaluator(clip_far=50., recorded_far=100.)
    with pytest.raises((ValueError, RuntimeError), match='(?i)camera'):
        namespace['evaluate'](*args)


def test_comparison_excludes_samples_beyond_camera_far_plane():
    namespace, args = evaluator(clip_far=1., recorded_far=1.)
    result = namespace['evaluate'](*args)
    assert all(row['in_frustum'] == row['visible_surface_samples'] == 0 for row in result['cameras'])


def test_comparison_keeps_same_frame_and_normal_visibility():
    namespace, args = evaluator()
    result = namespace['evaluate'](*args)
    assert result['frame'] == 1
    assert result['time_s'] == 2.
    assert all(row['visible_surface_samples'] == 1 for row in result['cameras'])
    assert result['visibility']['visible_length_m'] == 1.


def test_comparison_accepts_absent_optional_focal_record():
    namespace, args = evaluator(focal_length=None)
    result = namespace['evaluate'](*args)
    assert len(result['cameras']) == 3
    assert result['visibility']['visible_length_m'] == 1.


def search_outputs(tmp_path):
    path = ROOT / 'scripts/blender/find_three_camera_layout.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    start = next(i for i, node in enumerate(tree.body)
                 if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'sort')
    layout = {'cameras': []}
    scene = {'wfrl_clearance_time_s': 231.5}
    namespace = dict(OUT=tmp_path, json=json, scene=scene, INTERVALS=((0., 18.),), failed=[],
        results=[{'index': 1, 'score': .5, 'point': [0., 0., 0.], 'cameras': [], 'layout': layout}],
        core=SimpleNamespace(restore_layout=lambda *args: None, layout_dict=lambda scene: layout,
                             layout_hash=lambda layout: 'layout-digest'),
        rig=SimpleNamespace(pose=lambda scene: {}), write_export_files=write_export_files,
        bpy=SimpleNamespace(context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None))))
    exec(compile(ast.Module(body=tree.body[start:], type_ignores=[]), str(path), 'exec'), namespace)


def test_layout_search_records_actual_reference_time(tmp_path):
    search_outputs(tmp_path)
    result = json.loads((tmp_path / 'search.json').read_text(encoding='utf-8'))
    assert result['reference_time_s'] == 231.5


def test_layout_search_output_failure_preserves_all_prior_results(tmp_path, monkeypatch):
    names = ('search.json', 'candidate-1.json', 'recommended.json', 'recommended-check.json')
    previous = {name: f'old {name}'.encode() for name in names}
    for name, content in previous.items():
        (tmp_path / name).write_bytes(content)
    real_replace = os.replace

    def fail_last(source, target):
        if Path(target) == tmp_path / 'recommended-check.json':
            raise OSError('injected output failure')
        return real_replace(source, target)

    monkeypatch.setattr(os, 'replace', fail_last)
    with pytest.raises(OSError, match='injected output failure'):
        search_outputs(tmp_path)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == previous


def test_comparison_rejects_preexisting_evidence_without_manifest(tmp_path):
    saved = tmp_path / 'baseline-layout.json'
    saved.write_bytes(b'prior evidence')
    def register():
        raise RuntimeError('attempted registration in occupied output directory')
    namespace = script_functions('compare_frontend_framing', OUT=tmp_path,
                                 addon=SimpleNamespace(register=register))
    with pytest.raises(RuntimeError, match='(?i)fresh'):
        namespace['main']()
    assert saved.read_bytes() == b'prior evidence'


def test_comparison_entry_failure_does_not_write_into_rejected_directory(tmp_path):
    saved = tmp_path / 'error.txt'
    saved.write_bytes(b'prior failure evidence')
    path = ROOT / 'scripts/blender/compare_frontend_framing.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))

    def main():
        raise RuntimeError('Use a fresh output directory')

    with pytest.raises(RuntimeError, match='fresh'):
        exec(compile(ast.Module(body=[tree.body[-1]], type_ignores=[]), str(path), 'exec'),
             dict(__name__='__main__', main=main, OUT=tmp_path, traceback=traceback))
    assert saved.read_bytes() == b'prior failure evidence'


class Vector(np.ndarray):
    def __new__(cls, values):
        return np.asarray(values, dtype=float).view(cls)

    @property
    def length(self):
        return float(np.linalg.norm(self))

    def normalized(self):
        return self / self.length


@pytest.mark.parametrize(('near', 'far'), [(.01, 1.), (3., 100.)])
def test_layout_search_respects_camera_clip_planes(near, far):
    parameters = SimpleNamespace(location=(0., 0., 0.), yaw=0., pitch=0., roll=0.,
                                 fov=75., vfov=45., clip_near_m=near, clip_far_m=far)
    target = object()

    def ray_cast(*args, distance):
        if distance == .5:
            return False, None
        return True, Vector([0., 0., -2.]), None, None, SimpleNamespace(original=target), None

    namespace = script_functions('find_three_camera_layout',
        core=SimpleNamespace(parameters=lambda camera: parameters),
        projection=SimpleNamespace(rotation=lambda *args: np.eye(3)),
        face_spans=np.array([2.]), centres=np.array([[0., 0., -2.]]), normals=np.array([[0., 0., 1.]]),
        world=np.eye(4), Vector=Vector, obj=target,
        bpy=SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=lambda: object())),
        scene=SimpleNamespace(ray_cast=ray_cast))
    camera = SimpleNamespace(matrix_world=SimpleNamespace(translation=Vector([0., 0., 0.])))
    result = namespace['score_camera'](camera, (0., 5.))
    assert result['sampled'] == 1
    assert result['visible'] == result['visible_ratio'] == 0

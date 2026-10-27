"""Mount identity and collision queries share evaluated surfaces and cleanup."""
import hashlib
import json
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_cameras as core


class Vector(tuple):
    def normalized(self):
        return self


class Matrix:
    def __init__(self, offset=(0., 0., 0.)):
        self.offset = offset

    def __matmul__(self, other):
        if isinstance(other, Matrix):
            return Matrix(tuple(a + b for a, b in zip(self.offset, other.offset)))
        return Vector(a + b for a, b in zip(self.offset, other))

    def inverted(self):
        return Matrix(tuple(-a for a in self.offset))

    def to_3x3(self):
        return Matrix()

    def transposed(self):
        return self


@pytest.fixture(params=[True, False], ids=['direct-parent', 'world-transform'])
def evaluated_shell(request, monkeypatch):
    calls = {'mesh': 0, 'clear': 0, 'normals': 0, 'bvh': 0}
    vertices = [NS(co=Vector(v)) for v in ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))]

    class Triangle:
        vertices = (0, 1, 2)

        @property
        def normal(self):
            calls['normals'] += 1
            return Vector((0., 0., 1.))

    mesh = NS(vertices=vertices, loop_triangles=[Triangle()], calc_loop_triangles=lambda: None)

    def to_mesh():
        calls['mesh'] += 1
        return mesh

    def to_mesh_clear():
        calls['clear'] += 1

    root_eval = NS(matrix_world=Matrix((1000., 2000., 3000.)))
    root = NS(evaluated_get=lambda _graph: root_eval)
    evaluated = NS(matrix_world=Matrix((1002., 2003., 3004.)),
                   matrix_parent_inverse=Matrix((1., 1., 1.)),
                   matrix_basis=Matrix((1., 2., 3.)),
                   to_mesh=to_mesh, to_mesh_clear=to_mesh_clear)
    obj = NS(type='MESH', parent=root if request.param else None,
             constraints=[], parent_type='OBJECT', evaluated_get=lambda _graph: evaluated)
    scene = NS(objects={'shell': obj})
    monkeypatch.setattr(core, '_root', lambda _scene: root)
    monkeypatch.setattr(core, 'surface_names', lambda _scene: ['shell'])
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(evaluated_depsgraph_get=lambda: object())))

    def bvh(vertices, faces, **kwargs):
        calls['bvh'] += 1
        return ('tree', vertices, faces)

    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=Vector))
    monkeypatch.setitem(sys.modules, 'mathutils.bvhtree', NS(BVHTree=NS(FromPolygons=bvh)))
    return scene, mesh, evaluated, calls


def test_signature_uses_evaluated_vertices_faces_without_normals_or_bvh(evaluated_shell):
    scene, mesh, _evaluated, calls = evaluated_shell
    signature = core.model_signature(scene)
    assert calls == {'mesh': 1, 'clear': 1, 'normals': 0, 'bvh': 0}
    geometry = core._geometry(scene)
    payload = [(name, [tuple(round(float(v), 4) for v in vertex) for vertex in vertices], faces)
               for name, vertices, faces, _normals, _tree in geometry]
    expected = 'sha256:' + hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()[:20]
    assert signature == expected
    assert geometry[0][1] == [(2., 3., 4.), (3., 3., 4.), (2., 4., 4.)]
    assert calls == {'mesh': 2, 'clear': 2, 'normals': 1, 'bvh': 1}
    mesh.vertices[0].co = Vector((0., 0., .01))
    assert core.model_signature(scene) != signature
    assert calls == {'mesh': 3, 'clear': 3, 'normals': 1, 'bvh': 1}


@pytest.mark.parametrize('collision', [False, True])
def test_evaluated_mesh_is_cleared_when_extraction_fails(evaluated_shell, collision):
    scene, mesh, _evaluated, calls = evaluated_shell

    def fail():
        raise RuntimeError('invalid evaluated mesh')

    mesh.calc_loop_triangles = fail
    with pytest.raises(RuntimeError, match='invalid evaluated mesh'):
        (core._geometry if collision else core.model_signature)(scene)
    assert calls['mesh'] == calls['clear'] == 1

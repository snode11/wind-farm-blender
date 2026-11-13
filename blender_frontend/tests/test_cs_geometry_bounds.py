"""Tower rings require a surface; packaged blade sampling retains exact geometry."""
import hashlib
import struct
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import turbine_geometry as geometry


def mesh_digest(mesh):
    """Hash Blender's float32 vertices and polygon indices in little endian."""
    vertices, faces = mesh
    digest = hashlib.sha256()
    for vertex in vertices:
        digest.update(struct.pack('<3f', *vertex))
    for face in faces:
        digest.update(struct.pack('<' + 'I' * (len(face) + 1), len(face), *face))
    return digest.hexdigest()


@pytest.mark.parametrize('count', [-1, 0, 1, 2])
def test_tower_rejects_degenerate_ring_count(count):
    with pytest.raises(ValueError):
        geometry.tower_mesh(count)
    mesh = NS(polygons=[NS(vertices=face) for face in geometry._ring_faces(
        len(geometry.geometry_data()['tower_stations']), count)])
    with pytest.raises(ValueError):
        geometry.tower_side_polygon_ids(mesh, count)


@pytest.mark.parametrize('model,subdiv,count,expected', [
    ('nrel5mw', 4, 48, 'ee0d62859df45bdbb01e5865f68e157c39404e4decf2715d7e17f96d1eaecaa8'),
    ('nrel5mw', 6, 64, 'd7a918993a9adc73acf0ad64bc7beae8cdb06f3ee9eca985bf878e2fef5a49c3'),
    ('nrel5mw', 8, 96, '84f4afb01f758f3177dc6e8ef37d35ea78a2bf138673521a1ccd20aa68df49af'),
    ('rootkink', 4, 48, 'b75260eba5cd94c59759ab54f52975fb81010ed31bbf2945e2801d7d5404d07f'),
    ('rootkink', 6, 64, 'f966c9db0e9587b3ea88932d983227afaca26cb452b0ae8676661052ff238a81'),
    ('rootkink', 8, 96, 'cb127f8cfaa6654348053babf9ad5c7fb344dae99bf37a2d700b4b243af05085'),
])
def test_packaged_blade_loft_hash(model, subdiv, count, expected):
    assert mesh_digest(geometry.blade_mesh(subdiv, count, model)) == expected


def test_packaged_tower_hash():
    assert mesh_digest(geometry.tower_mesh()) == 'd3da198e1c3be8d82b7abc9013de8cd1e4b495dac9202c81adcbd29d7ab7da83'

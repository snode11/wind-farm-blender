"""Blender check of source-derived scale and independent pitch/azimuth axes."""
from __future__ import annotations

import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "blender_frontend"))
import bpy
import bmesh
from mathutils import Matrix, Vector
from wfrl_blender.scene_builder import _make_turbine
from wfrl_blender.scene_model import TurbineDTO
from wfrl_blender.turbine_geometry import blade_mesh, geometry_data, hub_position, tower_mesh


def main():
    data = geometry_data()
    assert len(data["blade_stations"]) == 19
    assert len(data["airfoils"]) == 8
    assert len(data["source_sha256"]) == 19
    vertices, faces = blade_mesh()
    assert len(vertices) == 73 * 96 and len(faces) == 72 * 96 + 2
    assert min(v[2] for v in vertices) == 1.5
    assert abs(max(v[2] for v in vertices) - 63.0) < 0.001  # source span rounds to 61.4999
    # Curved cross sections have many distinct thickness coordinates, unlike prisms.
    assert len({round(v[0], 4) for v in vertices[:96]}) > 60
    assert max(v[1] for v in vertices) - min(v[1] for v in vertices) < 6.0
    tower, _ = tower_mesh()
    assert max(v[2] for v in tower) == 87.6
    assert abs(max(math.hypot(v[0], v[1]) for v in tower) - 3.0) < 1e-6
    assert abs(math.hypot(*tower[-1][:2]) - 1.935) < 1e-6
    collection = bpy.data.collections.new("GeometrySmoke")
    bpy.context.scene.collection.children.link(collection)
    _make_turbine(collection, TurbineDTO("Geometry", 0, 0))
    bpy.context.view_layer.update()
    prefix = "WFRL.Turbine.Geometry"
    rotor = bpy.data.objects[prefix + ".Rotor"]
    assert (rotor.matrix_world.translation - Vector(hub_position())).length < 1e-5
    assert rotor.matrix_world.translation.x < 0  # upwind of tower
    nacelle = bpy.data.objects[prefix + ".Nacelle"]
    assert all(abs(x - y) < 1e-5 for x, y in zip(nacelle.dimensions, (8.03, 3.3, 3.3)))
    assert bpy.data.objects[prefix + ".Spinner"].parent.name == prefix + ".Rotor"
    assert bpy.data.objects[prefix + ".YawBearing"].parent.name == prefix
    assert all(
        bpy.data.objects[prefix + f".Blade{index}.RootFairing"].parent.name
        == prefix + f".Blade{index}.PitchRoot"
        for index in range(1, 4)
    )
    assert bpy.data.objects[prefix + ".ServiceDoor.Panel"].parent.name == prefix
    assert bpy.data.objects[prefix + ".Vent.Recess-1"].parent.name == prefix + ".YawRoot"
    assert bpy.data.objects[prefix + ".Blade1.PitchSeal"].parent.name == prefix + ".Blade1.PitchRoot"
    # Local fitting offsets survive yaw; no detail is left behind in world space.
    yaw = bpy.data.objects[prefix + ".YawRoot"]
    vent = bpy.data.objects[prefix + ".Vent.Recess-1"]
    local = vent.matrix_basis.copy()
    yaw.rotation_euler.z = math.radians(30)
    bpy.context.view_layer.update()
    assert (vent.matrix_world.translation - (yaw.matrix_world @ local).translation).length < 1e-6
    yaw.rotation_euler.z = 0
    for pitch in (0, 2, 25, 90):
        directions = []
        for index in range(3):
            blade = bpy.data.objects[prefix + f".Blade{index + 1}"]
            assert blade.parent.name == blade.name + ".PitchRoot"
            mesh = bmesh.new()
            mesh.from_mesh(blade.data)
            assert mesh.calc_volume(signed=True) > 0  # normals face outward
            mesh.free()
            blade.rotation_euler.z = math.radians(pitch)
            bpy.context.view_layer.update()
            expected = (Matrix.Rotation(math.radians(5), 4, 'Y') @
                        Matrix.Rotation(math.radians(index * 120), 4, 'X') @
                        Matrix.Rotation(math.radians(-2.5), 4, 'Y') @
                        Matrix.Rotation(math.radians(pitch), 4, 'Z'))
            actual = blade.matrix_world.to_3x3()
            for axis in (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))):
                assert (actual @ axis - expected.to_3x3() @ axis).length < 1e-5
            directions.append((actual @ Vector((0, 0, 1))).normalized())
        assert all(abs(directions[i].dot(directions[j]) + 0.497146) < 1e-4
                   for i, j in ((0, 1), (1, 2), (2, 0)))
    forbidden = {"numpy", "pyvista", "torch", "floris", "wfcrl"} & sys.modules.keys()
    # Blender itself may preload numpy; addon never imports backend or PyVista.
    assert not (forbidden - {"numpy"}), forbidden
    print("WFRL_GEOMETRY_SMOKE=PASS")
    print(f"Source asset: 19 blade stations / 8 airfoils / 12 tower stations / 19 SHA256 hashes")
    print(f"Blade: {len(vertices)} vertices; root 1.5 m / tip 63 m; tower top 87.6 m")
    print(f"Hub center: {hub_position()}; independent pitch tested 0/2/25/90 degrees")


if __name__ == "__main__":
    main()

import math
import pytest
from wfrl_blender.installation_schematic import fit_transform, clipped_polygon


@pytest.mark.parametrize('bounds,viewport', [
    ((-4, -2, 8, 4), (10, 10, 400, 200)),
    ((2, 3, .3, .8), (18, 32, 240, 100)),
    ((-8, -2, 20, 1), (0, 0, 180, 480)),
])
def test_actual_geometry_fits_without_distortion(bounds, viewport):
    scale, tx, ty = fit_transform(bounds, viewport)
    x, y, width, height = bounds
    vx, vy, vw, vh = viewport
    assert scale > 0
    assert vx-1e-9 <= x*scale+tx <= (x+width)*scale+tx <= vx+vw+1e-9
    assert vy-1e-9 <= y*scale+ty <= (y+height)*scale+ty <= vy+vh+1e-9
    assert (width*scale)/(height*scale) == pytest.approx(width/height)


def test_detail_inset_clips_only_drawing_not_underlying_points():
    triangle = [(-2, 0), (3, 0), (0, 3)]
    original = list(triangle)
    result = clipped_polygon(triangle, (0, 0, 1, 1))
    assert len(result) >= 3
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in result)
    assert triangle == original
    assert clipped_polygon([(-3, -3), (-2, -3), (-3, -2)], (0, 0, 1, 1)) == []


@pytest.mark.parametrize('bounds,viewport', [((0, 0, 0, 1), (0, 0, 5, 5)),
                                           ((math.nan, 0, 1, 1), (0, 0, 5, 5)),
                                           ((0, 0, 1, 1), (0, 0, 5, -1))])
def test_invalid_geometry_never_silently_draws(bounds, viewport):
    with pytest.raises(ValueError):
        fit_transform(bounds, viewport)


def test_static_installation_key_ignores_clock_but_tracks_custom_mount_surface(monkeypatch):
    from types import SimpleNamespace
    from wfrl_blender import installation_schematic as schematic, stacked_camera_rig as rig
    mount = SimpleNamespace(name='User mounting shell', type='MESH', hide_render=False,
                            as_pointer=lambda: 2, data=SimpleNamespace(as_pointer=lambda: 3),
                            matrix_basis=[[1., 0., 0., 0.]], get=lambda _key: False)
    scene = SimpleNamespace(objects=[mount], frame_current=1, as_pointer=lambda: 1)
    monkeypatch.setattr(schematic.core, 'surface_names', lambda _scene: (mount.name,))
    monkeypatch.setattr(schematic.core, 'get_camera', lambda _scene, _slot: None)
    monkeypatch.setattr(rig, 'pose', lambda _scene: {'center': [1., 2., 3.]})
    baseline = schematic.fingerprint(scene)
    scene.frame_current = 2500
    assert schematic.fingerprint(scene) == baseline
    mount.matrix_basis[0][3] = .2
    assert schematic.fingerprint(scene) != baseline
    mount.matrix_basis[0][3] = 0.
    mount.hide_render = True
    assert schematic.fingerprint(scene) != baseline
    mount.hide_render = False
    scene.objects.clear()
    assert schematic.fingerprint(scene) != baseline

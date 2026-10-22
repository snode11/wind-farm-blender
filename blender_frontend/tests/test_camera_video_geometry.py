import pytest

from wfrl_blender import camera_projection as projection, camera_video, nacelle_camera


@pytest.mark.parametrize('requested,expected', [((3840, 2160), (3840, 2470)),
                                               ((2560, 1440), (2560, 1646)),
                                               ((160, 90), (160, 102)),
                                               ((90, 160), (160, 102))])
def test_calibrated_export_uses_natural_even_gate(requested, expected):
    camera = {'wfrl_nacelle_intrinsics': nacelle_camera.INTRINSICS_ID}
    width, height, pixel_x, pixel_y, policy = camera_video.output_geometry(camera, *requested)
    assert (width, height) == expected
    assert height % 2 == 0
    assert width * pixel_x / (height * pixel_y) == pytest.approx(projection.aspect(59, 40))
    assert policy == 'CALIBRATED_GATE_LONG_EDGE_EVEN_SHORT_EDGE'


def test_explicit_noncalibrated_export_rectangle_is_preserved():
    assert camera_video.output_geometry({}, 160, 90) == (160, 90, 1, 1, 'REQUESTED_RECTANGLE')

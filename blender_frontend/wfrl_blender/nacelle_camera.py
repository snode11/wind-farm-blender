"""NREL nominal hub-axis camera installation and ideal pinhole intrinsics.

Offsets use YawRoot metres: +X downwind, +Y lateral and +Z upward. The
180-degree optical roll places the approaching blade below the tower in the
image; it does not alter the mounting position or the optical-axis direction.
"""
import math

from . import camera_projection as projection

FOCAL_LENGTH_MM = 8.0
HFOV_DEG = 59.0
VFOV_DEG = 40.0
HUB_OFFSET_M = (1.5, 0.0, -1.5)
YAW_DEG = 180.0
PITCH_DEG = -82.0
ROLL_DEG = 180.0
SENSOR_WIDTH_MM = 2 * FOCAL_LENGTH_MM * math.tan(math.radians(HFOV_DEG / 2))
SENSOR_HEIGHT_MM = 2 * FOCAL_LENGTH_MM * math.tan(math.radians(VFOV_DEG / 2))
INTRINSICS_ID = 'nrel.hub-axis.8mm.59x40.v1'
_OUTPUT_SNAPSHOT = 'wfrl_nacelle_previous_output'


def mount_location(rotor):
    """Optical centre from the mechanical Rotor origin, excluding its spin."""
    return tuple(float(rotor.location[i]) + HUB_OFFSET_M[i] for i in range(3))


def output_settings(long_edge=projection.DEFAULT_LONG_EDGE):
    width, height = projection.resolution(HFOV_DEG, VFOV_DEG, long_edge)
    scale_x, scale_y = projection.pixel_scales(HFOV_DEG, VFOV_DEG, width, height)
    return width, height, scale_x, scale_y


def select_output(scene, camera):
    """Set the calibrated gate on selection, restoring other views' output.

    Blender shares one render aspect across a scene. The pixel aspect retains
    the exact independent HFOV/VFOV ratio after integer output-size rounding.
    Playback and frame updates do not call this function.
    """
    render = scene.render
    calibrated = camera is not None and camera.get('wfrl_nacelle_intrinsics') == INTRINSICS_ID
    if calibrated:
        if _OUTPUT_SNAPSHOT not in scene:
            scene[_OUTPUT_SNAPSHOT] = [render.resolution_x, render.resolution_y,
                                       render.pixel_aspect_x, render.pixel_aspect_y]
        width, height, scale_x, scale_y = output_settings(max(render.resolution_x, render.resolution_y))
        render.resolution_x, render.resolution_y = width, height
        render.pixel_aspect_x, render.pixel_aspect_y = scale_x, scale_y
    elif _OUTPUT_SNAPSHOT in scene:
        width, height, scale_x, scale_y = scene[_OUTPUT_SNAPSHOT]
        render.resolution_x, render.resolution_y = int(width), int(height)
        render.pixel_aspect_x, render.pixel_aspect_y = scale_x, scale_y
        del scene[_OUTPUT_SNAPSHOT]

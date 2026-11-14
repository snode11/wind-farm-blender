"""Elastic section twist from BeamDyn's relative Wiener–Milenković rotations."""
import numpy as np


TORSION_DEFINITION = 'beamdyn-relative-rotation-swing-twist-z.v1'


def elastic_twist_degrees(rotation):
    """Remove minimal swing of +z; return right-hand twist about the swung axis.

    Input is the complete dimensionless (RDxr, RDyr, RDzr) vector relative to
    the undeformed section in the root frame r. Only the verified straight
    blade is supported: its undeformed sectional span axis is +z everywhere.
    Built-in section twist and rigid root pitch are already removed by BeamDyn.
    The 180-degree swing singularity is undefined and must not yield a reading.
    """
    c = np.asarray(rotation, dtype=float)
    if c.ndim == 0 or c.shape[-1] != 3 or not np.isfinite(c).all():
        raise ValueError('Elastic twist requires three finite BeamDyn rotation parameters')
    norm2 = np.sum(c * c, axis=-1)
    scalar = 16. - norm2
    axial = 8. * c[..., 2]
    if not np.isfinite(norm2).all() or np.any(np.hypot(scalar, axial) <= 1.e-10 * (16. + norm2)):
        raise ValueError('Elastic twist is undefined for a 180 degree span-axis swing')
    angle = np.degrees(2. * np.arctan2(axial, scalar))
    return (angle + 180.) % 360. - 180.


def rotation_channels(blade, gauge):
    return tuple(f'B{blade}N{gauge}RD{axis}r' for axis in 'xyz')

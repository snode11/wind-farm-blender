"""Versioned synthetic patch profiles, independent of Blender and rendering.

These are prescribed visible morphologies, not damage/strike physics models.
Dimensions describe surface centreline length and transverse maximum width.
"""
from copy import deepcopy
import math
import random

PATCH_KINDS = ('pit', 'erosion', 'coating_loss', 'lightning')


def patch_width_factor(kind, q, shape):
    """Rounded footprint with small flat end caps and reproducible scalloping."""
    base = .035 + .965 * math.sqrt(max(0., 1. - q*q))
    phase = random.Random(shape['seed']).uniform(-math.pi, math.pi)
    rough = shape.get('edge_roughness', 0.)
    lobes = 5 if kind == 'erosion' else 3
    return base * (1. - rough * abs(q) * math.sin(lobes*math.pi*q + phase)**2)


def patch_depth_factor(kind, q, z, shape):
    """Prescribed inward floor depth / nominal maximum; centre is exactly 1.

    A finite shallow lip keeps the footprint well defined. Erosion adds seeded
    floor variation; this is a local surface patch, not an edge-spanning model.
    """
    bowl = .12 + .88 * max(0., 1.-q*q) * max(0., 1.-z*z)
    if kind != 'erosion':
        return bowl
    phase = random.Random(shape['seed']).uniform(-math.pi, math.pi)
    return bowl * (1. - .5 * min(1., q*q+z*z) *
                   math.sin(7.*q + 11.*z + phase)**2)


def lightning_crater(defect):
    """A nested pit shares the parent event ID; it is never independently saved."""
    result = deepcopy(defect)
    result['morphology'] = 'pit'
    fraction = defect['shape']['crater_fraction']
    result['shape']['length_m'] *= fraction
    result['shape']['max_width_m'] *= fraction
    result['shape']['edge_roughness'] = 0.
    return result

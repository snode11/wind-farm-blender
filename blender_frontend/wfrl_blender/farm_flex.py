"""Portable three-turbine flexible review playback on the shared timeline."""
from collections import OrderedDict
import numpy as np

_ACTIVE=None
# Each scene owns its deformation runtime; frontend code resolves it with
# active_for(scene). _ACTIVE only names the most recently attached runtime for
# single-scene scripts and tests.
_ACTIVES={}
_PACKAGE_CACHE = OrderedDict()
_PACKAGE_CACHE_SIZE = 2

from . import farm_flex_geometry as _geometry
from . import farm_flex_scene as _scene
from . import farm_flex_telemetry as _telemetry
from .farm_flex_package import (
    dual_beam_api as dual_beam_api,
    default_package as default_package,
    default_dual_package as default_dual_package,
    default_dual_tls_package as default_dual_tls_package,
    saved_package as saved_package,
    _VerifiedSource as _VerifiedSource,
    _LoadedPackage as _LoadedPackage,
    _ParsedPackage as _ParsedPackage,
    clear_package_cache as clear_package_cache,
    _load_package as _load_package,
    _parse_package as _parse_package,
    _validate_replay_contents as _validate_replay_contents,
    read_package as read_package,
    validate_candidate as validate_candidate,
    preflight as preflight,
)
from .farm_flex_geometry import (
    _readonly_arrays as _readonly_arrays,
    _update_scene_transforms as _update_scene_transforms,
    _rna_identity as _rna_identity,
    _array_state as _array_state,
    _bound_array_state as _bound_array_state,
    _write_coordinates as _write_coordinates,
    _EvaluatedTransformRequired as _EvaluatedTransformRequired,
    _world_from_basis as _world_from_basis,
    _WorldMatrices as _WorldMatrices,
)
from .farm_flex_telemetry import (
    _read_telemetry as _read_telemetry,
)
from .farm_flex_scene import (
    active_for as active_for,
    ready_for as ready_for,
    is_active as is_active,
    _MeshBindings as _MeshBindings,
    update as update,
    detach as detach,
    on_load_pre as on_load_pre,
    attach as attach,
)


class FarmFlex:
    __init__ = _scene._SceneBinding.__init__
    _bind = _scene._SceneBinding._bind
    restore_support = _geometry._Geometry.restore_support
    record_telemetry = _telemetry.record_telemetry
    rebind_blade = _geometry._Geometry.rebind_blade
    _world_cache = _geometry._Geometry._world_cache
    world_matrix = _geometry._Geometry.world_matrix
    deform_points = _geometry._Geometry.deform_points
    mesh_turbines = _geometry._Geometry.mesh_turbines
    _interpolate_blade = _geometry._Geometry._interpolate_blade
    _structural_tip_local = _geometry._Geometry._structural_tip_local
    structural_tip_world = _geometry._Geometry.structural_tip_world
    _blade_inputs = _geometry._Geometry._blade_inputs
    _geometry_sample = _geometry._Geometry._geometry_sample
    _deformation_batches_for = _geometry._Geometry._deformation_batches_for
    deformed_at = _geometry._Geometry.deformed_at
    _preflight_frame = _geometry._Geometry._preflight_frame
    update = _geometry._Geometry.update
    _update_frame = _geometry._Geometry._update_frame

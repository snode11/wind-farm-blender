"""Scene and command-line options for FAST.Farm physics."""
import argparse

PHYSICS_KEYS = ('tower_flex', 'dynamic_stall', 'tower_shadow', 'blade_torsion')
BLADE_TORSION_FIDELITY_KEY = 'blade_torsion_fidelity'
BLADE_TORSION_FIDELITIES = ('reference', 'fast')
BLADE_TORSION_DEFAULT_FIDELITY = 'fast'
UA_PARAMETER_KEY = 'dynamic_stall_parameters'
UA_PARAMETER_SETS = ('official', 'polar_consistent')


def validate_substructure(value, physics, *, direction=270.):
    """Validate the optional complete offshore model and its supported combinations."""
    if value is None:
        return None
    from wfcrl.fastfarm_substructure import normalize_substructure, validate_combination
    result = normalize_substructure(value)
    validate_combination(result, physics, direction=direction)
    return result


def validate_physics(value):
    if not isinstance(value, dict):
        raise ValueError('physics must be a mapping')
    unknown = set(value) - set(PHYSICS_KEYS) - {UA_PARAMETER_KEY, BLADE_TORSION_FIDELITY_KEY}
    if unknown:
        raise ValueError(f'physics contains unknown fields: {sorted(map(str, unknown))}')
    for key, enabled in value.items():
        if key == BLADE_TORSION_FIDELITY_KEY:
            if not isinstance(enabled, str) or enabled not in BLADE_TORSION_FIDELITIES:
                raise ValueError('physics.blade_torsion_fidelity must be reference or fast')
        elif key == UA_PARAMETER_KEY:
            if not isinstance(enabled, str) or enabled not in UA_PARAMETER_SETS:
                raise ValueError(f'physics.{key} must be official or polar_consistent')
        elif type(enabled) is not bool:
            raise ValueError(f'physics.{key} must be boolean true/false')
    if value.get(UA_PARAMETER_KEY) == 'polar_consistent' and value.get('dynamic_stall') is False:
        raise ValueError('polar_consistent requires dynamic_stall=True')
    if value.get(BLADE_TORSION_FIDELITY_KEY) == 'reference' and value.get('blade_torsion') is False:
        raise ValueError('reference fidelity requires blade_torsion=True')
    return dict(value)


def add_physics_arguments(parser):
    for name in PHYSICS_KEYS:
        parser.add_argument('--' + name.replace('_', '-'), action=argparse.BooleanOptionalAction,
                            default=None, help=f'FAST.Farm {name}; overrides the scene when specified')
    parser.add_argument('--dynamic-stall-parameters', choices=UA_PARAMETER_SETS, default=None,
                        help='UA parameter set; official is the effective default')
    parser.add_argument('--blade-torsion-fidelity', choices=BLADE_TORSION_FIDELITIES, default=None,
                        help='BeamDyn numerical profile; fast is the effective default')


def physics_arguments(args):
    return {key: getattr(args, key) for key in (*PHYSICS_KEYS, UA_PARAMETER_KEY, BLADE_TORSION_FIDELITY_KEY)
            if getattr(args, key, None) is not None}


def physics_cli_arguments(args):
    result = ['--' + ('' if value else 'no-') + key.replace('_', '-')
              for key in PHYSICS_KEYS if (value := getattr(args, key, None)) is not None]
    if getattr(args, UA_PARAMETER_KEY, None) is not None:
        result += ['--dynamic-stall-parameters', getattr(args, UA_PARAMETER_KEY)]
    if getattr(args, BLADE_TORSION_FIDELITY_KEY, None) is not None:
        result += ['--blade-torsion-fidelity', getattr(args, BLADE_TORSION_FIDELITY_KEY)]
    return result


def apply_scene_physics(scene, overrides):
    overrides = validate_physics(overrides)
    if not overrides:
        return scene
    scene.physics = {**scene.physics, **overrides}
    if scene.physics.get(UA_PARAMETER_KEY) == 'official':
        scene.physics.pop(UA_PARAMETER_KEY)
    if scene.physics.get(BLADE_TORSION_FIDELITY_KEY) == BLADE_TORSION_DEFAULT_FIDELITY:
        scene.physics.pop(BLADE_TORSION_FIDELITY_KEY)
    return scene.validate()

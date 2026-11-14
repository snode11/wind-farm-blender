"""Scene and command-line options for FAST.Farm physics."""
import argparse

PHYSICS_KEYS = ('tower_flex', 'dynamic_stall', 'tower_shadow', 'blade_torsion')


def validate_physics(value):
    if not isinstance(value, dict):
        raise ValueError('physics must be a mapping')
    unknown = set(value) - set(PHYSICS_KEYS)
    if unknown:
        raise ValueError(f'physics contains unknown fields: {sorted(map(str, unknown))}')
    for key, enabled in value.items():
        if type(enabled) is not bool:
            raise ValueError(f'physics.{key} must be boolean true/false')
    return dict(value)


def add_physics_arguments(parser):
    for name in PHYSICS_KEYS:
        parser.add_argument('--' + name.replace('_', '-'), action=argparse.BooleanOptionalAction,
                            default=None, help=f'FAST.Farm {name}; overrides the scene when specified')


def physics_arguments(args):
    return {key: getattr(args, key) for key in PHYSICS_KEYS if getattr(args, key) is not None}


def physics_cli_arguments(args):
    return ['--' + ('' if value else 'no-') + key.replace('_', '-')
            for key in PHYSICS_KEYS if (value := getattr(args, key, None)) is not None]


def apply_scene_physics(scene, overrides):
    overrides = validate_physics(overrides)
    if not overrides:
        return scene
    scene.physics = {**scene.physics, **overrides}
    return scene.validate()

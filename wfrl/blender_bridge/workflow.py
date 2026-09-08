"""Validated launch configuration, preserving the existing scene/Trainer contract."""
from copy import deepcopy
from pathlib import Path


def configured_scene(scene, overrides):
    if not isinstance(overrides, dict):
        raise ValueError('scene_overrides must be an object')
    allowed = {'backend', 'inflow', 'terrain', 'controls'}
    if set(overrides) - allowed:
        raise ValueError('Unsupported scene overrides: ' + ', '.join(sorted(set(overrides) - allowed)))
    from wfrl.scene.schema import from_dict
    data = deepcopy(scene.to_dict())
    for key, value in overrides.items():
        if key == 'inflow':
            if not isinstance(value, dict) or set(value) - {'speed', 'direction', 'turbulence'}:
                raise ValueError('inflow supports speed, direction and turbulence only')
            data['inflow'].update(value)
        else:
            data[key] = value
    return from_dict(data, source_path=scene.source_path)


def validate_run_options(mode, options):
    """Reject invalid budgets/checkpoints before starting workers or processes."""
    for key in ('iters', 'n_steps', 'replay_steps', 'demo_cycles', 'max_steps'):
        if key in options and (type(options[key]) is not int or options[key] < 1):
            raise ValueError(f'{key} must be a positive integer')
    for key in ('warmup_steps', 'episode_steps', 'seed'):
        if key in options and (type(options[key]) is not int or options[key] < 0):
            raise ValueError(f'{key} must be a nonnegative integer')
    checkpoint = options.get('ckpt_path' if mode == 'replay' else 'resume_from')
    if mode == 'replay' and not checkpoint:
        raise ValueError('Replay requires ckpt_path')
    if checkpoint and not Path(checkpoint).is_file():
        raise ValueError(f'Checkpoint does not exist: {checkpoint}')
    if mode == 'interactive_training' and options.get('ckpt_path'):
        raise ValueError('Existing Trainer does not support resuming interactive training')

"""Illustrative aeroelastic motion of the manual turbines: blade bend and tower lean.

The manual scene is rigid. This module adds a flapwise bend to the blades and a
fore-aft lean of each turbine about its tower base, so blade tips no longer
follow identical paths and the tip clearance differs from blade to blade and
from pass to pass. It runs live in the viewport on the manual-control clock
(``live_update``) and per frame in Down video exports (``start``/``apply``/``end``).

The blade mesh and turbine pose of the manual scene stand for the mean
operating shape, so the Down framing is unchanged: only deviations from the
mean are added. The motion is a seeded synthetic signal, not a structural
solution. The amplitudes below hold at the reference operating point of the
bundled MAPPO FAST.Farm/BeamDyn replay of T1 (REFERENCE_RPM, REFERENCE_PITCH_DEG)
and scale with ``load_factor``: the square of rotor speed (capped at rated
speed) times a pitch term that falls to zero at 45 deg, so there is no motion
when stopped or feathered. At the reference point:

- once-per-revolution (wind shear) term, amplitude 0.87 m: blades bend more
  near the top of the rotor and less when passing the tower;
- turbulent part common to the three blades (std about 0.26 m) and a per-blade
  part (std about 0.15 m), both below 1 Hz;
- constant blade-to-blade imbalance: the three blades differ by about 0.8 m
  (0.7–1.0 m, seeded), so successive blades pass the tower at visibly
  different clearances;
- tower-top fore-aft deviation following the common load and the first tower
  mode, std about 0.04 m.

Shear and tower magnitudes follow the statistics of the replay; the turbulent
and imbalance parts follow the field observation for the Down camera (clearance
of the three blades differing by about 1–2 m near rated speed).
"""
import json
import math
import random

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector
import numpy as np

from . import manual_control

REFERENCE_RPM = 11.0
REFERENCE_PITCH_DEG = 6.3
RATED_RPM = 12.1
PITCH_ZERO_THRUST_DEG = 45.0
SHEAR_TIP_M = 0.87
SHEAR_PHASE_DEG = 18.0
MEAN_TIP_M = 2.85  # replay mean tip deflection, carried by the mean operating shape
TOWER_MEAN_M = 0.15  # replay mean tower-top deflection
TOWER_COUPLING = TOWER_MEAN_M / MEAN_TIP_M  # tower-top metres per metre of common tip deviation
TOWER_MODE_HZ = 0.32
# (amplitude m, frequency Hz) sinusoids; seeded phases and a 0.8–1.25 frequency jitter.
_COMMON_TERMS = ((0.25, .05), (0.20, .11), (0.15, .19), (0.10, .35), (0.06, .55))
_BLADE_TERMS = ((0.15, .15), (0.12, .35), (0.08, .80))
# Blade-to-blade imbalance (pitch offset, mass and aerodynamic differences): a
# constant per-blade deviation, so the three blades pass the tower at different
# clearances. The 1P term is the same for every blade at a given azimuth.
BLADE_IMBALANCE_M = 0.8
_TOWER_TERMS = ((0.05, TOWER_MODE_HZ),)
# Live amplitude follows the operating point with this first-order lag, so
# starting, pausing or changing speed does not make the blades jump.
LOAD_TAU_S = 2.0
NODE_GROUP = 'WFRL.ManualFlapBend'
MODIFIER = 'WFRL.ManualFlap'
BAKED_SUFFIX = '.FlapBend'
_RESTORE_KEY = 'wfrl_flap_restore'
_LEAN_KEY = 'wfrl_flap_base'
_TIP_OFFSET = {}
_LIVE = {}


def _pitch_term(pitch_deg):
    return min(1.0, max(0.0, 1.0 - float(pitch_deg) / PITCH_ZERO_THRUST_DEG))


def load_factor(rpm, pitch_deg):
    """Thrust proxy of the structural motion, 1 at the reference operating point."""
    speed = min(max(float(rpm), 0.0), RATED_RPM) / REFERENCE_RPM
    return speed * speed * _pitch_term(pitch_deg) / _pitch_term(REFERENCE_PITCH_DEG)


def _terms(rng, table):
    return [(amp, hz * rng.uniform(.8, 1.25), rng.uniform(0, 2 * math.pi)) for amp, hz in table]


def make_signals(seed, turbine_ids):
    """Seeded per-turbine signal parameters; one entry per turbine id."""
    rng = random.Random(seed)
    return {tid: dict(common=_terms(rng, _COMMON_TERMS),
                      blades=[_terms(rng, _BLADE_TERMS) for _ in range(3)],
                      tower=_terms(rng, _TOWER_TERMS),
                      bias=_bias(rng))
            for tid in turbine_ids}


def _bias(rng):
    """Persistent per-blade deviations with zero mean: blade-to-blade imbalance.

    The largest and smallest differ by BLADE_IMBALANCE_M (0.85–1.25 times), the third lies between."""
    spread = BLADE_IMBALANCE_M * rng.uniform(.85, 1.25)
    values = [-.5 * spread, rng.uniform(-.3, .3) * spread, .5 * spread]
    rng.shuffle(values)
    mean = sum(values) / 3
    return [value - mean for value in values]


def _sum(terms, time_s):
    return sum(amp * math.sin(2 * math.pi * hz * time_s + phase) for amp, hz, phase in terms)


def deviation(signal, loads, azimuth_deg, time_s):
    """Tip deviations of blades 1–3 and tower-top deviation (m, towards the tower positive)."""
    common = _sum(signal['common'], time_s)
    tips = [load * (common + SHEAR_TIP_M * math.cos(math.radians(azimuth_deg + 120.0 * index + SHEAR_PHASE_DEG))
                    + signal['bias'][index] + _sum(signal['blades'][index], time_s))
            for index, load in enumerate(loads)]
    tower = sum(loads) / len(loads) * (TOWER_COUPLING * common + _sum(signal['tower'], time_s))
    return tips, tower


def sample(signal, rpm, azimuth_deg, pitches, time_s):
    """Deviations for a turbine turning at ``rpm`` with blade pitches ``pitches``."""
    return deviation(signal, [load_factor(rpm, pitch) for pitch in pitches], azimuth_deg, time_s)


def _node_group():
    """Geometry Nodes: offset = Direction * Tip * (3 s^2 - s^3) / 2, s = clamp((z - Root) / Length)."""
    group = bpy.data.node_groups.get(NODE_GROUP)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(NODE_GROUP, 'GeometryNodeTree')
    face = group.interface
    face.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    face.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    face.new_socket('Direction', in_out='INPUT', socket_type='NodeSocketVector')
    for name in ('Tip', 'Root', 'Length'):
        face.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
    nodes, links = group.nodes, group.links
    source, sink = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    position = nodes.new('GeometryNodeInputPosition')
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(position.outputs[0], split.inputs[0])

    def math_node(operation, a, b, clamp=False):
        node = nodes.new('ShaderNodeMath')
        node.operation = operation
        node.use_clamp = clamp
        for socket, value in zip(node.inputs, (a, b)):
            if isinstance(value, (int, float)):
                socket.default_value = value
            else:
                links.new(value, socket)
        return node.outputs[0]

    s = math_node('DIVIDE', math_node('SUBTRACT', split.outputs['Z'], source.outputs['Root']),
                  source.outputs['Length'], clamp=True)
    shape = math_node('MULTIPLY', math_node('MULTIPLY', s, s), math_node('SUBTRACT', 3.0, s))
    amount = math_node('MULTIPLY', math_node('MULTIPLY', shape, .5), source.outputs['Tip'])
    scale = nodes.new('ShaderNodeVectorMath')
    scale.operation = 'SCALE'
    links.new(source.outputs['Direction'], scale.inputs[0])
    links.new(amount, scale.inputs['Scale'])
    move = nodes.new('GeometryNodeSetPosition')
    links.new(source.outputs['Geometry'], move.inputs['Geometry'])
    links.new(scale.outputs[0], move.inputs['Offset'])
    links.new(move.outputs['Geometry'], sink.inputs[0])
    return group


def _set_input(modifier, identifier, value):
    """Set a Geometry Nodes modifier input (Blender 5 interface, older ID-property fallback)."""
    interface = getattr(modifier, 'properties', None)
    if interface is not None and hasattr(interface, 'inputs'):
        getattr(interface.inputs, identifier).value = value
    else:
        modifier[identifier] = value


def _socket_ids(group):
    return {item.name: item.identifier for item in group.interface.items_tree
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT'}


def _span(mesh):
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get('co', co)
    z = co.reshape(-1, 3)[:, 2]
    return float(z.min()), float(z.max() - z.min())


def _world(obj):
    matrix = obj.matrix_basis.copy()
    while obj.parent is not None:
        matrix = obj.matrix_parent_inverse @ matrix
        obj = obj.parent
        matrix = obj.matrix_basis @ matrix
    return matrix


def _matrix(values):
    values = list(values)
    return Matrix([values[0:4], values[4:8], values[8:12], values[12:16]])


def _bake(blade):
    """Copy of the blade's evaluated mesh (edge bevel applied), so the bevel is not recomputed per frame.

    All manual blades share one mesh and modifier stack, so one copy serves every blade."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(blade.evaluated_get(depsgraph), preserve_all_data_layers=True,
                                           depsgraph=depsgraph)
    mesh.name = blade.data.name + BAKED_SUFFIX
    mesh['wfrl_flap_baked'] = True
    if 'wfrl_blade_model' in blade.data:
        mesh['wfrl_blade_model'] = blade.data['wfrl_blade_model']
    mesh['wfrl_flap_span'] = _span(mesh)
    return mesh


def _attach(entry, baked):
    """Bend the turbine's blades using ``baked``; a restore record is kept on each object."""
    group = _node_group()
    ids = _socket_ids(group)
    low, length = baked['wfrl_flap_span']
    blades = []
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        if _RESTORE_KEY not in blade:
            kept = [[m.name, m.show_viewport, m.show_render] for m in blade.modifiers]
            blade[_RESTORE_KEY] = json.dumps(dict(mesh=blade.data.name, modifiers=kept))
            for modifier in blade.modifiers:
                modifier.show_viewport = modifier.show_render = False
            blade.data = baked
        modifier = blade.modifiers.get(MODIFIER) or blade.modifiers.new(MODIFIER, 'NODES')
        modifier.node_group = group
        _set_input(modifier, ids['Root'], low)
        _set_input(modifier, ids['Length'], length)
        _set_input(modifier, ids['Tip'], 0.0)
        blades.append((index, blade, modifier))
    root = entry.yaw_object.parent
    if _LEAN_KEY not in root:
        root[_LEAN_KEY] = [value for row in root.matrix_basis for value in row]
    return dict(ids=ids, blades=blades, root=root)


def _restore_blade(blade):
    _TIP_OFFSET.pop(blade.as_pointer(), None)
    modifier = blade.modifiers.get(MODIFIER)
    if modifier is not None:
        blade.modifiers.remove(modifier)
    record = blade.get(_RESTORE_KEY)
    if record is None:
        return
    record = json.loads(record)
    mesh = bpy.data.meshes.get(record['mesh'])
    if mesh is not None:
        blade.data = mesh
    for name, viewport, render in record['modifiers']:
        modifier = blade.modifiers.get(name)
        if modifier is not None:
            modifier.show_viewport, modifier.show_render = viewport, render
    del blade[_RESTORE_KEY]


def _restore_root(root):
    base = root.get(_LEAN_KEY)
    if base is None:
        return
    root.matrix_basis = _matrix(base)
    del root[_LEAN_KEY]


def _detach(rig):
    for _index, blade, _modifier in rig['blades']:
        try:
            _restore_blade(blade)
        except ReferenceError:
            pass
    try:
        _restore_root(rig['root'])
    except ReferenceError:
        pass


def _remove_unused_baked():
    for mesh in list(bpy.data.meshes):
        if mesh.get('wfrl_flap_baked') and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _downwind(entry):
    """World horizontal unit vector from the hub towards the tower (rotor is upwind)."""
    hub = _world(entry.rotor_object).translation
    top = _world(entry.yaw_object).translation
    direction = Vector((top.x - hub.x, top.y - hub.y, 0.0))
    return direction.normalized() if direction.length > 1e-6 else Vector((1.0, 0.0, 0.0))


def _pose(entry, rig, tips, tower):
    """Lean the turbine about its tower base and bend its blades towards the tower."""
    root = rig['root']
    base = _matrix(root[_LEAN_KEY])
    root.matrix_basis = base
    wind = _downwind(entry)
    axis = Vector((0.0, 0.0, 1.0)).cross(wind)
    lean = Matrix.Rotation(math.atan2(tower, entry.yaw_object.location.z), 4, axis)
    location = base.to_translation()
    root.matrix_basis = Matrix.Translation(location) @ lean @ Matrix.Translation(-location) @ base
    ids = rig['ids']
    for index, blade, modifier in rig['blades']:
        local = _world(blade).to_3x3().inverted() @ wind
        local.z = 0.0
        local = local.normalized() if local.length > 1e-9 else Vector((1.0, 0.0, 0.0))
        tip = tips[index - 1]
        _set_input(modifier, ids['Direction'], local)
        _set_input(modifier, ids['Tip'], tip)
        _TIP_OFFSET[blade.as_pointer()] = local * tip
        blade.update_tag()


def tip_offset(blade):
    """Blade-local displacement of the tip while the blade is bent; zero otherwise."""
    return _TIP_OFFSET.get(blade.as_pointer(), Vector((0.0, 0.0, 0.0)))


def cleanup_scene(scene):
    """Remove bends and leans left on a manual scene, e.g. restored by undo or file load."""
    if not manual_control.is_manual(scene):
        return
    for entry in scene.wfrl_manual_turbines:
        try:
            for index in range(1, 4):
                blade = getattr(entry, f'blade{index}_object')
                if blade is not None:
                    _restore_blade(blade)
            if entry.yaw_object is not None and entry.yaw_object.parent is not None:
                _restore_root(entry.yaw_object.parent)
        except (ReferenceError, AttributeError):
            pass


# Video export: the exported turbine on the frame clock -----------------------------------------

def start(scene, job, turbine, seed=None):
    """Bend the blades and lean the tower of ``turbine`` for a video export.

    The caller stops the live motion first, so the export starts from the rigid pose."""
    job['dynamics_seed'] = random.randrange(1 << 30) if seed is None else int(seed)
    entry = next(e for e in scene.wfrl_manual_turbines if e.turbine_id == turbine)
    job['dynamics_signals'] = make_signals(job['dynamics_seed'], [turbine])
    job['dynamics_rig'] = dict(turbine=turbine, rig=_attach(entry, _bake(entry.blade1_object)))


def apply(scene, job, frame, poses):
    """Apply blade bends and tower lean and return the frame's sample."""
    state = job.get('dynamics_rig')
    if not state:
        return
    tid = state['turbine']
    entry = next(e for e in scene.wfrl_manual_turbines if e.turbine_id == tid)
    rpm, azimuth, pitches = poses[tid]
    tips, tower = sample(job['dynamics_signals'][tid], rpm, azimuth, pitches, frame / job['plan']['fps'])
    _pose(entry, state['rig'], tips, tower)
    return {tid: dict(tip_flap_m=[round(v, 4) for v in tips], tower_top_m=round(tower, 4))}


def end(job):
    """Remove the export bend and restore the turbine."""
    state = job.pop('dynamics_rig', None)
    if state:
        _detach(state['rig'])
        _remove_unused_baked()


def sidecar(job, data):
    """Record the structural motion model and seed in the sidecar header."""
    if 'dynamics_seed' not in job:
        return
    data['structural_motion'] = dict(
        seed=job['dynamics_seed'],
        model=('Illustrative seeded signal (shear and tower scaled from the bundled MAPPO FAST.Farm/BeamDyn '
               'T1 replay, turbulence from the Down camera field observation); not a structural or '
               'aerodynamic solution'),
        load_factor=('(min(rpm, %.1f) / %.0f)^2 * clamp(1 - pitch / %.0f, 0, 1) / %.3f'
                     % (RATED_RPM, REFERENCE_RPM, PITCH_ZERO_THRUST_DEG, _pitch_term(REFERENCE_PITCH_DEG))),
        blade=('flapwise tip deviation from the mean operating shape, towards the tower positive = '
               'load * (common + %.2f cos(azimuth_b + %.0f deg) + blade imbalance + per-blade term); bend shape '
               '(3 s^2 - s^3) / 2 along the span; azimuth_b 0 = blade up' % (SHEAR_TIP_M, SHEAR_PHASE_DEG)),
        tower='rigid lean of the whole turbine about the tower base giving the tower-top fore-aft deviation')


# Live viewport: every turbine on the manual-control clock ---------------------------------------

def _entry_pitches(entry):
    return [getattr(entry, f'pitch_actual_b{index}_deg') for index in range(1, 4)]


def live_update(scene, now):
    """Advance the live bend of every turbine of a visible manual scene; True while anything moves."""
    if not scene.wfrl_manual_structural_motion:
        live_stop(scene)
        return False
    key = scene.as_pointer()
    state = _LIVE.get(key)
    if state is None:
        seed = random.randrange(1 << 30)
        state = _LIVE[key] = dict(seed=seed, start=now, last=now, rigs={}, baked=None, signals={}, loads={})
    alpha = 1.0 - math.exp(-max(0.0, now - state['last']) / LOAD_TAU_S)
    state['last'] = now
    active = False
    for entry in scene.wfrl_manual_turbines:
        tid = entry.turbine_id
        rpm = entry.rpm if entry.running else 0.0
        targets = [load_factor(rpm, pitch) for pitch in _entry_pitches(entry)]
        loads = state['loads'].setdefault(tid, [0.0] * 3)
        loads[:] = [load + (target - load) * alpha for load, target in zip(loads, targets)]
        if max(targets) == 0.0 and max(loads) < 1e-3:
            loads[:] = [0.0] * 3
        if max(loads) > 0.0:
            active = True
            rig = state['rigs'].get(tid)
            if rig is None:
                if state['baked'] is None:
                    state['baked'] = _bake(entry.blade1_object)
                rig = state['rigs'][tid] = _attach(entry, state['baked'])
            if tid not in state['signals']:
                state['signals'][tid] = make_signals(hash((state['seed'], tid)), [tid])[tid]
            tips, tower = deviation(state['signals'][tid], loads, entry.azimuth_deg, now - state['start'])
            _pose(entry, rig, tips, tower)
        elif tid in state['rigs']:
            _detach(state['rigs'].pop(tid))
            active = True  # one more redraw shows the restored pose
    if not state['rigs'] and state['baked'] is not None:
        state['baked'] = None
        _remove_unused_baked()
    return active


def live_detach(scene):
    """Restore the scene's geometry; the live amplitudes resume on the next update."""
    state = _LIVE.get(scene.as_pointer())
    if state is None:
        return
    for rig in state['rigs'].values():
        _detach(rig)
    state['rigs'].clear()
    state['baked'] = None
    _remove_unused_baked()


def live_stop(scene):
    """Restore the scene's geometry and forget the live amplitudes."""
    try:
        live_detach(scene)
    except ReferenceError:
        pass
    _LIVE.pop(scene.as_pointer(), None)


def live_active(scene):
    state = _LIVE.get(scene.as_pointer())
    return bool(state and state['rigs'])


def _stop_all():
    for scene in getattr(bpy.data, 'scenes', ()):
        if scene.as_pointer() in _LIVE:
            live_stop(scene)
    _LIVE.clear()


@persistent
def _before_save(_unused):
    """Saved files keep the rigid model; the live bend is rebuilt on the next update."""
    for scene in bpy.data.scenes:
        if scene.as_pointer() in _LIVE:
            live_detach(scene)


@persistent
def _after_restore(_unused):
    _LIVE.clear()
    _TIP_OFFSET.clear()
    for scene in bpy.data.scenes:
        cleanup_scene(scene)
    _remove_unused_baked()


def _toggle(scene, context):
    if not scene.wfrl_manual_structural_motion:
        live_stop(scene)
    manual_control._wake_timer()


_HANDLERS = (('save_pre', _before_save), ('load_post', _after_restore),
             ('undo_post', _after_restore), ('redo_post', _after_restore))


def register():
    bpy.types.Scene.wfrl_manual_structural_motion = bpy.props.BoolProperty(
        name='叶片摆动与塔架运动', default=True, update=_toggle,
        description='运转时按转速与桨距叠加示意性的叶片挥舞弯曲和塔顶前后运动，'
                    '视口与导出视频中各叶片净空随时间不同；不是结构求解')
    if live_update not in manual_control.MOTION_HOOKS:
        manual_control.MOTION_HOOKS.append(live_update)
    if live_stop not in manual_control.STOP_HOOKS:
        manual_control.STOP_HOOKS.append(live_stop)
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)


def unregister():
    _stop_all()
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for hooks, function in ((manual_control.MOTION_HOOKS, live_update), (manual_control.STOP_HOOKS, live_stop)):
        if function in hooks:
            hooks.remove(function)
    if hasattr(bpy.types.Scene, 'wfrl_manual_structural_motion'):
        del bpy.types.Scene.wfrl_manual_structural_motion

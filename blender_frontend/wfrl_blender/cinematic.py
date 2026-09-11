"""Realtime wind-tunnel filaments. Illustrative SYNTH, never a CFD result.

Fixed curve topology, deterministic time sampling, and bulk coordinate writes
keep scrubbing reversible without a simulation cache or per-frame allocations
of Blender datablocks. Incoming wind stays world-aligned; yaw deflects the wake.
"""
import math
from functools import lru_cache

PREFIX = 'WFRL.Cinematic.'
STRANDS = 72
SAMPLES = 64
START, END = -150.0, 350.0


def smooth(a, b, x):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


@lru_cache(maxsize=2048)
def _random_keyframe(seed, step):
    import random
    return random.Random(seed + step * 104729).uniform(-math.pi, math.pi)


def random_heading(time_s, seed, interval):
    """Reproducible headings, with a hold then a shortest-arc transition."""
    t = max(0.0, time_s) / max(2.0, interval)
    step = math.floor(t)
    a = _random_keyframe(seed, step)
    b = _random_keyframe(seed, step + 1)
    delta = (b - a + math.pi) % math.tau - math.pi
    return a + delta * smooth(.4, 1.0, t - step)


def wind_heading(scene, time_s):
    if getattr(scene, 'wfrl_cinematic_wind_mode', 'FRONT') == 'RANDOM':
        return random_heading(time_s, scene.wfrl_cinematic_seed, scene.wfrl_cinematic_interval)
    tid = getattr(scene, 'wfrl_cinematic_reference', 'T1')
    root = scene.objects.get('WFRL.Turbine.' + tid + '.YawRoot')
    if root is None:
        root = next((o for o in scene.objects if o.name.startswith('WFRL.Turbine.') and o.name.endswith('.YawRoot')), None)
    baseline = root.rotation_euler.z if root else 0.0
    return baseline + math.radians(getattr(scene, 'wfrl_cinematic_offset', 0.0))


def filament(index, time_s, yaw, speed=8.0):
    """A filled stream bundle, central body bypass, and convecting wake curls.

    The rotor is permeable, unlike a solid obstacle. Only a small central region
    is displaced around the nacelle; downstream yaw steering is illustrative.
    """
    theta = index * 2.399963229728653
    radius = 66.0 * math.sqrt((index + .5) / STRANDS)
    # Some near-axis threads make the small nacelle bypass visible in closeups.
    if index < 12:
        radius = 2.5 + index * .8
    travel = max(0.0, speed) * time_s
    # Half the strands are long flow paths, half are finite advecting streaks.
    moving = index % 4 != 0
    length = 65.0 + 70.0 * ((index * .61803398875) % 1)
    head = START + ((index * 73.31 + travel) % (END - START + length))
    coords, widths = [], []
    for j in range(SAMPLES):
        u = j / (SAMPLES - 1)
        x = head - length + length * u if moving else START + (END - START) * u
        downstream = smooth(0, 220, x)
        age = max(0.0, x) / 450.0
        core = math.exp(-((radius / 57.0) ** 4))
        expansion = 1 + .22 * downstream * core
        # Localized central displacement, with smooth approach and recovery.
        bypass = 8.0 * math.exp(-((x / 24.0) ** 2)) * math.exp(-((radius / 12.0) ** 2))
        swirl = .12 * downstream * core * math.sin(.024 * (x - travel))
        angle = theta + swirl
        r = radius * expansion + bypass
        curl = downstream * (0.6 + 1.6 * age) * core
        y = r * math.cos(angle) + curl * math.sin(.048 * (x - travel))
        z = r * math.sin(angle) + curl * .65 * math.cos(.061 * (x - travel))
        y += math.sin(yaw) * max(0, x) * .22 * core * downstream
        # Deflect near-body centerline along the nacelle axis at nonzero yaw.
        y += math.sin(yaw) * x * math.exp(-((x / 26.0) ** 2)) * math.exp(-((radius / 15.0) ** 2))
        fade = smooth(START, START + 40, x) * (1 - smooth(310, END, x))
        taper = math.sin(math.pi * u) ** .65 if moving else .28
        width = fade * taper * (.75 + .25 * math.sin(index * 4.1) ** 2)
        coords.extend((x, y, z, 1.0))
        widths.append(width)
    return coords, widths


class WindTransport:
    """Integrate the shared wind history; moving tracers retain their birth pose.

    Coordinates remain hub-relative but world-oriented. At constant heading,
    this is exactly the authored local curve rotated into the wind. During a
    turn the emitter can change direction, but existing particles are advected
    by the velocity integral instead of being rotated around a turbine.
    """
    def __init__(self, time_s, speed, heading):
        import numpy as np
        self.time_s = time_s
        self.speed = max(.01, speed)
        self.heading = heading
        # Cover visible points and hidden leading/trailing points on respawn.
        max_age = (END - START + 140) / self.speed
        # A fixed, absolute grid makes scrubbing and evaluation order irrelevant.
        step = .125
        low = math.floor((time_s - max_age) / step)
        high = math.ceil(time_s / step) + 1
        self.times = np.arange(low, high + 1) * step
        angles = np.array([heading(float(t)) for t in self.times])
        self.angles = np.unwrap(angles)
        velocities = np.column_stack((np.cos(angles), np.sin(angles))) * self.speed
        self.integral = np.zeros_like(velocities)
        self.integral[1:] = np.cumsum((velocities[:-1] + velocities[1:]) * (.5 * step), axis=0)
        self.current = np.array([np.interp(time_s, self.times, self.integral[:, j]) for j in (0, 1)])

    def points(self, coords):
        import numpy as np
        p = np.array(coords, dtype=float).reshape((-1, 4))
        born = self.time_s - (p[:, 0] - START) / self.speed
        angle = np.interp(born, self.times, self.angles)
        displacement = self.current - np.column_stack([np.interp(born, self.times, self.integral[:, j]) for j in (0, 1)])
        y = p[:, 1].copy()
        p[:, 0] = START * np.cos(angle) - y * np.sin(angle) + displacement[:, 0]
        p[:, 1] = START * np.sin(angle) + y * np.cos(angle) + displacement[:, 1]
        return p.ravel()


def material():
    import bpy
    mat = bpy.data.materials.get('WFRL.Cinematic.Silk')
    if mat is not None and mat.get('wfrl_revision') == 2:
        return mat
    mat = mat or bpy.data.materials.new('WFRL.Cinematic.Silk')
    mat['wfrl_revision'] = 2
    mat.diffuse_color = (.66, .83, 1.0, .65)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new('ShaderNodeOutputMaterial')
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (.66, .83, 1.0, 1)
    emission.inputs['Strength'].default_value = .85
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    mix = nodes.new('ShaderNodeMixShader')
    mix.inputs[0].default_value = .45
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs['Surface'])
    mat.surface_render_method = 'DITHERED'
    return mat


def ensure(scene):
    import bpy
    for root in list(scene.objects):
        if not root.name.startswith('WFRL.Turbine.') or not root.name.endswith('.YawRoot'):
            continue
        tid = root.name.split('.')[2]
        name = PREFIX + tid
        obj = scene.objects.get(name)
        if obj is not None and obj.get('wfrl_revision') == 2:
            continue
        curve = obj.data if obj else bpy.data.curves.new(name + '.Data', 'CURVE')
        curve.dimensions = '3D'
        curve.bevel_depth = .26
        curve.bevel_resolution = 2
        curve.materials.clear()
        curve.materials.append(material())
        curve.splines.clear()
        for index in range(STRANDS):
            spline = curve.splines.new('POLY')
            spline.points.add(SAMPLES - 1)
        if obj is None:
            obj = bpy.data.objects.new(name, curve)
            root.users_collection[0].objects.link(obj)
        obj['wfrl_revision'] = 2
        obj.visible_shadow = False
        obj['fidelity'] = 'SYNTH'
        obj['provenance'] = 'Procedural wind-tunnel illustration; no solved velocity field or mesh collision solver'
        obj['turbine_id'] = tid


def set_visibility(scene):
    enabled = getattr(scene, 'wfrl_wake_display', 'SCIENTIFIC') == 'CINEMATIC' and scene.wfrl_show_wake
    if enabled:
        ensure(scene)
    for obj in scene.objects:
        if obj.name.startswith(PREFIX):
            obj.hide_render = not enabled
            obj.hide_set(not enabled)


def update(scene, phase):
    if getattr(scene, 'wfrl_wake_display', 'SCIENTIFIC') != 'CINEMATIC' or not scene.wfrl_show_wake:
        return
    from .turbine_geometry import hub_position, geometry_data
    ensure(scene)
    for obj in scene.objects:
        if obj.name.startswith('WFRL.WakeProxy.') and not obj.hide_render:
            obj.hide_render = True
            obj.hide_set(True)
    hub_offset = hub_position()[2] - geometry_data()['scalars']['TowerHt']
    wind_angle = wind_heading(scene, phase / .9)
    scene['wfrl_cinematic_direction_deg'] = (270 - math.degrees(wind_angle)) % 360
    transports = {}
    for obj in list(scene.objects):
        if not obj.name.startswith(PREFIX):
            continue
        root = scene.objects.get('WFRL.Turbine.' + obj['turbine_id'] + '.YawRoot')
        if root is None:
            continue
        collection = root.users_collection[0]
        speed = float(scene.get('wfrl_wind_speed_mps', collection.get('wind_speed_mps', 8.0)) or 0)
        if speed <= .001:
            obj.hide_render = True
            obj.hide_set(True)
            continue
        obj.location = root.matrix_world.translation
        obj.location.z += hub_offset
        obj.rotation_euler.z = 0.0
        yaw = root.rotation_euler.z - wind_angle
        if speed not in transports:
            transports[speed] = WindTransport(phase / .9, speed, lambda at: wind_heading(scene, at))
        transport = transports[speed]
        for index, spline in enumerate(obj.data.splines):
            coords, widths = filament(index, phase / .9, yaw, speed)
            coords = transport.points(coords)
            spline.points.foreach_set('co', coords)
            spline.points.foreach_set('radius', widths)
        obj.hide_render = False
        obj.hide_set(False)

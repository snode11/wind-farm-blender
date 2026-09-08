"""Small Eevee-safe material palette for the WFRL industrial presentation."""

from __future__ import annotations


PALETTE = {
    "graphite": (0.018, 0.025, 0.035, 1.0),
    "tower": (0.68, 0.73, 0.76, 1.0),
    "blade": (0.78, 0.86, 0.92, 1.0),
    "nacelle": (0.72, 0.77, 0.79, 1.0),
    "hub": (0.52, 0.59, 0.63, 1.0),
    "accent": (0.015, 0.55, 0.82, 1.0),
    "wake": (0.01, 0.48, 0.86, 1.0),
    "wake_line": (0.01, 1.0, 0.01, 1.0),
    "terrain": (0.10, 0.30, 0.12, 1.0),
    "terrain_dirt": (0.25, 0.13, 0.055, 1.0),
    "terrain_stone": (0.28, 0.31, 0.29, 1.0),
    "grid": (0.16, 0.38, 0.20, 1.0),
    "foundation": (0.20, 0.27, 0.25, 1.0),
}


def get_material(name: str):
    import bpy

    material = bpy.data.materials.get(f"WFRL.{name}") or bpy.data.materials.new(f"WFRL.{name}")
    material.diffuse_color = PALETTE.get(name, PALETTE["tower"])
    material.use_nodes = True
    if name in {"wake_line", "wake_pulse"}:
        return flow_material(material, pulse=name == "wake_pulse")
    if name == "terrain":
        from .landscape import terrain_shader
        terrain_shader(material)
        return material
    node = next((item for item in material.node_tree.nodes if item.type == "BSDF_PRINCIPLED"), None)
    if node:
        node.inputs["Base Color"].default_value = PALETTE.get(name, PALETTE["tower"])
        node.inputs["Roughness"].default_value = 0.92 if name == "terrain" else (0.46 if name in {"tower", "blade"} else 0.66)
        node.inputs["Metallic"].default_value = 0.08 if name == "hub" else 0.0
        if name in {"tower", "blade", "nacelle", "hub", "foundation"}:
            node.inputs["Emission Color"].default_value = PALETTE[name]
            node.inputs["Emission Strength"].default_value = 0.04
        elif name == "wake":
            node.inputs["Alpha"].default_value = 0.13
            node.inputs["Emission Color"].default_value = PALETTE["accent"]
            node.inputs["Emission Strength"].default_value = 0.16
            material.surface_render_method = "DITHERED"
        elif name == "wake_line":
            node.inputs["Alpha"].default_value = 1.0
            material.surface_render_method = "DITHERED"
            node.inputs["Emission Color"].default_value = PALETTE["wake_line"]
            node.inputs["Emission Strength"].default_value = 0.65
            node.inputs["Roughness"].default_value = 0.38
        elif name in {"accent", "grid"}:
            node.inputs["Emission Color"].default_value = PALETTE[name]
            node.inputs["Emission Strength"].default_value = 0.22
    return material


def wake_volume_material():
    """Soft radial and axial falloff in generated coordinates, without a shell."""
    import bpy
    material = bpy.data.materials.get('WFRL.WakeVolume') or bpy.data.materials.new('WFRL.WakeVolume')
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    volume = nodes.new('ShaderNodeVolumePrincipled')
    volume.inputs['Color'].default_value = (.10, .58, .72, 1)
    volume.inputs['Anisotropy'].default_value = .15
    links.new(volume.outputs['Volume'], output.inputs['Volume'])
    coord = nodes.new('ShaderNodeTexCoord')
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(coord.outputs['Generated'], split.inputs[0])
    def math_node(op, a, b=0):
        n = nodes.new('ShaderNodeMath'); n.operation = op
        for index, value in enumerate((a,b)):
            if isinstance(value, (float,int)): n.inputs[index].default_value = value
            else: links.new(value, n.inputs[index])
        return n.outputs[0]
    x,y,z = split.outputs['X'], split.outputs['Y'], split.outputs['Z']
    yy = math_node('SUBTRACT', y, .5); zz = math_node('SUBTRACT', z, .5)
    radial = math_node('SQRT', math_node('ADD', math_node('MULTIPLY', yy, yy), math_node('MULTIPLY', zz, zz)))
    radius = math_node('ADD', .30, math_node('MULTIPLY', x, .19))
    falloff = math_node('MAXIMUM', math_node('SUBTRACT', 1., math_node('DIVIDE', radial, radius)), 0.)
    falloff = math_node('POWER', falloff, 1.4)
    axial = math_node('MULTIPLY', math_node('MINIMUM', math_node('MULTIPLY', x, 14.), 1.), math_node('POWER', math_node('SUBTRACT', 1., x), .8))
    noise = nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 5.; noise.inputs['Detail'].default_value = 2.
    links.new(coord.outputs['Generated'], noise.inputs['Vector'])
    density = math_node('MULTIPLY', math_node('MULTIPLY', falloff, axial), math_node('MULTIPLY', noise.outputs['Fac'], .026))
    links.new(density, volume.inputs['Density'])
    return material


def flow_material(material, *, pulse=False):
    """Hub-local downstream alpha falloff, shared by rings, traces and pulses."""
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (.22, 1.0, .045, 1) if pulse else (.025, .75, .006, 1)
    emission.inputs['Strength'].default_value = 4.0 if pulse else 1.1
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    coord = nodes.new('ShaderNodeTexCoord')
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(coord.outputs['Object'], split.inputs[0])
    fade = nodes.new('ShaderNodeMapRange')
    fade.name = 'WFRL.DownstreamFade'
    fade.clamp = True
    fade.inputs['From Min'].default_value = 240
    fade.inputs['From Max'].default_value = 457
    fade.inputs['To Min'].default_value = 1.0 if pulse else .55
    fade.inputs['To Max'].default_value = 0
    links.new(split.outputs['X'],fade.inputs['Value'])
    inlet = nodes.new('ShaderNodeMapRange'); inlet.clamp=True
    inlet.inputs['From Min'].default_value=12; inlet.inputs['From Max'].default_value=24
    links.new(split.outputs['X'],inlet.inputs['Value'])
    weight=nodes.new('ShaderNodeMath');weight.operation='MULTIPLY'
    links.new(fade.outputs[0],weight.inputs[0]);links.new(inlet.outputs[0],weight.inputs[1])
    mix=nodes.new('ShaderNodeMixShader')
    links.new(weight.outputs[0],mix.inputs[0]);links.new(transparent.outputs[0],mix.inputs[1]);links.new(emission.outputs[0],mix.inputs[2])
    links.new(mix.outputs[0],output.inputs['Surface'])
    material.surface_render_method='DITHERED'
    return material

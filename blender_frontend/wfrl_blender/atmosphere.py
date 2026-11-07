"""Lightweight, explicitly non-physical atmosphere presets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AtmospherePreset:
    name: str
    world_color: tuple[float, float, float, float]
    world_strength: float
    sun_color: tuple[float, float, float]
    sun_energy: float
    fog_color: tuple[float, float, float, float]
    fog_density: float
    horizon_color: tuple[float, float, float]


PRESETS: Mapping[str, AtmospherePreset] = {
    "clear": AtmospherePreset("clear", (0.12, 0.28, 0.55, 1.0), 0.5,
                               (0.95, 0.98, 1.0), 3.0, (0.55, 0.70, 0.84, 1.0), 0.00005, (0.16, 0.29, 0.42)),
    "overcast": AtmospherePreset("overcast", (0.32, 0.38, 0.44, 1.0), 0.72,
                                  (0.72, 0.78, 0.82), 1.5, (0.47, 0.52, 0.56, 1.0), 0.00022, (0.28, 0.32, 0.35)),
    "dusk": AtmospherePreset("dusk", (0.42, 0.10, 0.045, 1.0), 0.68,
                             (1.0, 0.46, 0.22), 2.0, (0.45, 0.17, 0.12, 1.0), 0.00012, (0.35, 0.10, 0.08)),
}


def get_preset(name: str) -> AtmospherePreset:
    key = str(name).lower()
    if key not in PRESETS:
        raise ValueError(f"unknown atmosphere preset: {name}")
    return PRESETS[key]


def private_world(scene):
    """Copy a World only when another Scene actually references it."""
    world = getattr(scene, "world", None)
    if world is not None:
        import bpy
        if any(other != scene and other.world == world for other in bpy.data.scenes):
            world = world.copy()
            scene.world = world
    return world


def apply_preset(scene, name: str = "clear", *, enabled: bool = True, quality: str = "realtime",
                 image_loader=None) -> AtmospherePreset:
    """Apply a preset to Blender scene nodes when Blender is available."""
    preset = get_preset(name)
    if quality not in {"realtime", "render"}:
        raise ValueError("quality must be realtime or render")
    scene["wfrl_atmosphere_preset"] = preset.name
    scene["wfrl_atmosphere_enabled"] = bool(enabled)
    scene["wfrl_atmosphere_quality"] = quality
    scene["wfrl_atmosphere_fog_density"] = preset.fog_density if enabled else 0.0
    world = private_world(scene)
    if world is not None and getattr(world, "use_nodes", False):
        nodes = world.node_tree.nodes
        links = world.node_tree.links
        background = next((node for node in nodes if node.type == "BACKGROUND"), None)
        if background is not None:
            sky = nodes.get("WFRL.Atmosphere.Sky")
            if sky is None:
                sky = nodes.new("ShaderNodeTexSky")
                sky.name = "WFRL.Atmosphere.Sky"
            try:
                sky.sky_type = "NISHITA"
                sky.sun_elevation = 0.30 if preset.name == "clear" else 0.16
                sky.sun_rotation = 2.35
                sky.altitude = 0.2
                sky.air_density = 1.0
                sky.dust_density = 0.55 if preset.name == "clear" else 2.5
                sky.ozone_density = 0.35
            except (AttributeError, TypeError):
                pass
            for link in list(links):
                if link.to_node == background and link.to_socket == background.inputs["Color"]:
                    links.remove(link)
            if preset.name == "clear":
                from pathlib import Path
                import bpy
                env = nodes.get('WFRL.Atmosphere.HDRI') or nodes.new('ShaderNodeTexEnvironment')
                env.name = 'WFRL.Atmosphere.HDRI'
                path = Path(__file__).parent / 'assets/landscape/kloofendal_48d_partly_cloudy_puresky_2k.hdr'
                env.image = (image_loader(path) if image_loader else
                             bpy.data.images.load(str(path), check_existing=True))
                links.new(env.outputs['Color'], background.inputs['Color'])
            else:
                background.inputs["Color"].default_value = preset.world_color
            background.inputs["Strength"].default_value = preset.world_strength if enabled else 0.8
        output = next((node for node in nodes if node.type == "OUTPUT_WORLD"), None)
        if output is not None:
            volume = nodes.get("WFRL.Atmosphere.Volume")
            if volume is None:
                volume = nodes.new("ShaderNodeVolumePrincipled")
                volume.name = "WFRL.Atmosphere.Volume"
                volume.label = "WFRL visual atmosphere (not weather simulation)"
            volume.inputs["Color"].default_value = preset.fog_color
            # A world volume extends to infinity and can swallow the sky in
            # real-time preview.  Keep preview atmospheric color in the world
            # background; only explicit render quality enables a very light
            # depth cue.
            volume.inputs["Density"].default_value = (preset.fog_density * 0.02 if enabled and quality == "render" else 0.0)
            for link in list(links):
                if link.to_node == output and link.to_socket == output.inputs.get("Volume"):
                    links.remove(link)
            if enabled and quality == "render":
                links.new(volume.outputs["Volume"], output.inputs["Volume"])
    if hasattr(scene, "view_settings"):
        scene.view_settings.look = "AgX - Medium High Contrast"
    # Eevee's world volume is intentionally omitted: a compositor/world node
    # setup is version-sensitive, while the metadata and switches remain stable.
    return preset


def set_enabled(scene, enabled: bool) -> None:
    scene["wfrl_atmosphere_enabled"] = bool(enabled)


def set_feature_visibility(scene, enabled: bool) -> None:
    """Toggle sun/cloud geometry without affecting simulation objects."""
    import bpy
    for obj in bpy.data.objects:
        if obj.name.startswith("WFRL.Atmosphere."):
            obj.hide_render = not enabled
            obj.hide_set(not enabled)

"""Use the packaged canonical radar core in installed extensions."""
try:
    from .._vendor.radar_x100 import schema, ply, recording, simulation, channel, acquisition
except ModuleNotFoundError as exc:
    if '._vendor.radar_x100' not in exc.name:
        raise
    from wfrl.radar_x100 import schema, ply, recording, simulation, channel, acquisition

"""Small, testable presentation overlay model for live and recorded data."""
from __future__ import annotations

from typing import Mapping


def fidelity_label(value: str | None) -> str:
    return str(value or "UNKNOWN").upper()


def overlay_lines(*, time_s=None, wind_speed_mps=None, backend=None, fidelity=None,
                  power_mw=None, status=None, wake=None, sensor=None) -> tuple[str, ...]:
    lines = [f"STATUS  {status or 'READY'}"]
    if time_s is not None:
        lines.append(f"TIME    {float(time_s):.1f} s")
    if wind_speed_mps is not None:
        lines.append(f"WIND    {float(wind_speed_mps):.1f} m/s")
    if backend:
        lines.append(f"BACKEND {backend}")
    if fidelity:
        lines.append(f"DATA    {fidelity_label(fidelity)}")
    if power_mw is not None:
        lines.append(f"POWER   {float(power_mw):.2f} MW")
    if wake:
        lines.append(f"WAKE    {wake.get('source', 'unknown')} / {fidelity_label(wake.get('fidelity'))}")
    if sensor:
        lines.append(f"SENSOR  {sensor.get('source', 'unknown')} / {fidelity_label(sensor.get('fidelity'))}")
    return tuple(lines)


def scene_overlay(scene, *, wake: Mapping[str, object] | None = None,
                  sensor: Mapping[str, object] | None = None) -> tuple[str, ...]:
    power = scene.get("wfrl_power_mw")
    if isinstance(power, (list, tuple)):
        power = sum(float(value) for value in power)
    return overlay_lines(time_s=scene.get("wfrl_telemetry_time_s"),
                         wind_speed_mps=scene.get("wfrl_wind_speed_mps"),
                         backend=scene.get("wfrl_backend"),
                         fidelity=scene.get("wfrl_fidelity"), power_mw=power,
                         status=scene.get("wfrl_run_status"), wake=wake, sensor=sensor)

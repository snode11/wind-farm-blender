"""Small, testable presentation overlay model for live and recorded data."""
from __future__ import annotations

from typing import Mapping
import math
from numbers import Real


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
    if power is not None and not isinstance(power, (Real, str, bytes)):
        power = sum(float(value) for value in power)
    return overlay_lines(time_s=scene.get("wfrl_telemetry_time_s"),
                         wind_speed_mps=scene.get("wfrl_wind_speed_mps"),
                         backend=scene.get("wfrl_backend"),
                         fidelity=scene.get("wfrl_fidelity"), power_mw=power,
                         status=scene.get("wfrl_run_status"), wake=wake, sensor=sensor)


def live_overlay(ui, kinematics, *, wake=None) -> tuple[str, ...]:
    """Read authoritative state and the same receiver-age rules as telemetry.

    No Scene cache: disconnects and expiry must update without a new snapshot.
    A partial sum is never presented as the farm total.
    """
    connected = ui.connection == 'CONNECTED' and ui.confirmed
    lines = [f"STATUS  {ui.run_status}",
             f"LINK    {ui.connection}" + (" / UNCONFIRMED" if not ui.confirmed else "")]
    data = kinematics.snapshot
    if not data:
        return tuple(lines + ['DATA    WAITING / no backend snapshot'])
    scene = data.get('scene') or {}
    lines.append(f"BACKEND {scene.get('backend', 'backend')}")
    stamp = data['timestamp']
    lines.append(f"SNAPSHOT {stamp['value']:.1f} {stamp['timebase']} / step {data['step']}")
    speed = (scene.get('inflow') or {}).get('speed')
    if speed is not None:
        lines.append(f"WIND REQUEST {speed:g} m/s")
    values, fidelities, invalid = [], set(), set()
    turbines = data['turbines']
    factors = {'MW': 1., 'kW': .001, 'W': .000001}
    for turbine in turbines:
        record = turbine['channels'].get('power')
        if record is None:
            invalid.add('MISSING')
            continue
        validity = kinematics.validity(record, connected)
        if validity != 'valid':
            invalid.add(validity.upper())
            continue
        value, factor = record.get('value'), factors.get(record.get('unit'))
        if type(value) not in (int, float) or not math.isfinite(value) or factor is None:
            invalid.add('INVALID')
            continue
        values.append(value * factor)
        fidelities.add(record.get('fidelity', 'UNKNOWN'))
    if turbines and len(values) == len(turbines):
        lines.append(f"POWER   {sum(values):.2f} MW")
        lines.append('DATA    ' + ' / '.join(sorted(fidelities)))
    else:
        reason = ' / '.join(sorted(invalid)) or 'WAITING'
        coverage = f'PARTIAL {len(values)}/{len(turbines)} / ' if values else ''
        lines.append(f"POWER   — / {coverage}{reason}")
    if wake:
        lines.append(f"WAKE    {wake.get('source', 'unknown')} / {fidelity_label(wake.get('fidelity'))}")
    return tuple(lines)

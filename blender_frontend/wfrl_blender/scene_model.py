"""Scene DTOs kept independent from bpy for unit testing and future bridge use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TurbineDTO:
    turbine_id: str
    x_m: float
    y_m: float


@dataclass(frozen=True)
class SceneDTO:
    name: str
    backend: str
    turbine_model: str
    dt_s: float
    turbines: tuple[TurbineDTO, ...]
    wind_speed_mps: float
    wind_direction_deg: float
    terrain: str = "flat"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SceneDTO":
        layout = data.get("layout", ())
        turbines = tuple(
            TurbineDTO(str(item["id"]), float(item["x"]), float(item["y"]))
            for item in layout
        )
        if not turbines:
            raise ValueError("scene layout must contain at least one turbine")
        inflow = data.get("inflow") or {}
        return cls(
            name=str(data.get("name", "wfrl_scene")),
            backend=str(data.get("backend", "floris")),
            turbine_model=str(data.get("turbine", "nrel5mw")),
            dt_s=float(data.get("dt", 1.0)),
            turbines=turbines,
            wind_speed_mps=float(inflow.get("speed", 8.0)),
            wind_direction_deg=float(inflow.get("direction", 270.0)),
            terrain=str(data.get("terrain", "flat")),
        )


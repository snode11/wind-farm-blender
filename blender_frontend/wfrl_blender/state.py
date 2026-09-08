"""One deterministic SYNTH timeline for animation, telemetry and offline controls."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import math

FPS = 25
DURATION_S = 66.0
END_FRAME = 1651
TURBINES = ("T1", "T2", "T3")


class RunStatus(StrEnum):
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DemoFrame:
    time_s: float
    yaw_deg: tuple[float, ...]
    pitch_deg: tuple[float, ...]
    rpm: tuple[float, ...]
    power_mw: tuple[float, ...]
    rotor_rad: tuple[float, ...]
    status: RunStatus
    label: str


def time_for_frame(frame: int) -> float:
    return max(0.0, min(DURATION_S, (frame - 1) / FPS))


def sample_demo(time_s: float) -> DemoFrame:
    """Sample the fixed 66 s presentation; no simulation or policy is invoked.

    Rotor angle integrates the same RPM ramp used by telemetry analytically.
    Startup lasts four seconds; feathering occupies the final ten seconds.
    """
    t = max(0.0, min(DURATION_S, float(time_s)))
    if t < 4:
        factor, integral = t / 4, t * t / 8
        status, label = RunStatus.STARTING, "Starting rotors"
    elif t < 56:
        factor, integral = 1.0, t - 2
        status, label = RunStatus.RUNNING, "Running / yaw sweep"
    else:
        delta = t - 56
        factor, integral = max(0.0, 1 - delta / 10), 54 + delta - delta * delta / 20
        status = RunStatus.STOPPED if t >= DURATION_S else RunStatus.RUNNING
        label = "Stopped / feathered" if t >= DURATION_S else "Feathering / slowing rotors"
    max_rpm = (10.8, 10.368, 9.828)
    yaw = (0.0, 8 * math.sin(t / 9), 15 * math.sin(t / 11))
    return DemoFrame(t, yaw, (90 * (1 - factor),) * 3,
                     tuple(value * factor for value in max_rpm),
                     tuple(value * factor for value in (1.71, 0.88, 0.73)),
                     tuple(value * math.tau / 60 * integral for value in max_rpm), status, label)


@dataclass
class DemoState:
    status: RunStatus = RunStatus.READY
    elapsed_s: float = 0.0
    selected_turbine: str = "T1"
    show_wake_proxy: bool = True
    show_sensor_view: bool = False

    def frame(self) -> DemoFrame:
        return replace(sample_demo(self.elapsed_s), status=self.status)

    def start(self) -> DemoFrame:
        self.elapsed_s = 0.0
        self.status = RunStatus.STARTING
        return self.frame()

    def pause(self) -> DemoFrame:
        if self.status in (RunStatus.STARTING, RunStatus.RUNNING):
            self.status = RunStatus.PAUSED
        return self.frame()

    def resume(self) -> DemoFrame:
        if self.status == RunStatus.PAUSED:
            self.status = sample_demo(self.elapsed_s).status
        return self.frame()

    def advance(self, seconds: float) -> DemoFrame:
        if self.status in (RunStatus.STARTING, RunStatus.RUNNING):
            self.elapsed_s = min(DURATION_S, self.elapsed_s + max(0.0, seconds))
            self.status = sample_demo(self.elapsed_s).status
        return self.frame()

    def step(self) -> DemoFrame:
        if self.status == RunStatus.PAUSED:
            self.elapsed_s = min(DURATION_S, self.elapsed_s + 1 / FPS)
            if self.elapsed_s >= DURATION_S:
                self.status = RunStatus.STOPPED
        return self.frame()

    def stop(self) -> DemoFrame:
        self.elapsed_s = DURATION_S
        self.status = RunStatus.STOPPED
        return self.frame()

    def reset(self) -> DemoFrame:
        self.elapsed_s = 0.0
        self.status = RunStatus.READY
        return self.frame()


def apply_demo_state(yaw_deg, pitch_deg, rpm, rotor_rad=None) -> None:
    """Apply an offline pose. Blades pitch about local Z on radial pitch roots."""
    import bpy
    if {len(yaw_deg), len(pitch_deg), len(rpm)} != {3}:
        raise ValueError("demo state requires yaw, pitch, and rpm for exactly three turbines")
    for index, turbine_id in enumerate(TURBINES):
        prefix = f"WFRL.Turbine.{turbine_id}"
        yaw = bpy.data.objects.get(prefix + ".YawRoot")
        rotor = bpy.data.objects.get(prefix + ".Rotor")
        if yaw is None or rotor is None:
            continue
        yaw.rotation_euler.z = math.radians(yaw_deg[index])
        rotor["demo_rpm"] = float(rpm[index])
        if rotor_rad is not None:
            rotor.rotation_euler.x = rotor_rad[index]
        for blade_index in range(1, 4):
            blade = bpy.data.objects.get(prefix + f".Blade{blade_index}")
            if blade is not None:
                blade.rotation_euler.z = math.radians(pitch_deg[index])


def demo_keyframes(collection) -> None:
    """Bake every presentation frame from sample_demo for scrubbing and rendering."""
    import bpy
    for obj in collection.objects:
        if obj.name.startswith("WFRL.Turbine."):
            obj.animation_data_clear()
    for frame_number in range(1, END_FRAME + 1):
        sample = sample_demo(time_for_frame(frame_number))
        apply_demo_state(sample.yaw_deg, sample.pitch_deg, sample.rpm, sample.rotor_rad)
        for turbine_id in TURBINES:
            prefix = f"WFRL.Turbine.{turbine_id}"
            bpy.data.objects[prefix + ".YawRoot"].keyframe_insert("rotation_euler", index=2, frame=frame_number)
            bpy.data.objects[prefix + ".Rotor"].keyframe_insert("rotation_euler", index=0, frame=frame_number)
            for blade_index in range(1, 4):
                bpy.data.objects[prefix + f".Blade{blade_index}"].keyframe_insert("rotation_euler", index=2, frame=frame_number)
    timeline = bpy.context.scene
    timeline.frame_start, timeline.frame_end = 1, END_FRAME
    timeline.use_preview_range = False
    timeline.render.fps = FPS
    timeline.render.fps_base = 1.0
    timeline.frame_set(1)


MODES = {"demo", "interactive_training", "formal_training", "replay"}
ACTIVE_STATUSES = {"STARTING", "RUNNING", "PAUSED", "DRAINING"}
TRANSITIONS = {
    "READY": {"STARTING"}, "STARTING": {"RUNNING", "PAUSED", "DRAINING", "FAILED"},
    "RUNNING": {"PAUSED", "DRAINING", "FAILED"},
    "PAUSED": {"STARTING", "RUNNING", "DRAINING", "FAILED"},
    "DRAINING": {"STOPPED", "FAILED"}, "STOPPED": {"READY", "STARTING"},
    "FAILED": {"READY"},
}


@dataclass
class FrontendState:
    """Connection and authoritative lifecycle are independent of Demo's timeline."""
    mode: str = "demo"
    connection: str = "LOCAL DEMO"
    run_status: str = "READY"
    confirmed: bool = True
    session_id: str = ""
    run_id: str | None = None
    capabilities: set[str] = field(default_factory=set)
    error: str = ""

    def allows(self, action: str) -> bool:
        if action == "configure":
            return self.confirmed and self.run_status in {"READY", "STOPPED"}
        if not self.confirmed:
            return False
        local = self.mode == "demo" and self.connection == "LOCAL DEMO"
        if not local and self.connection != "CONNECTED":
            return False
        if action == "start":
            return self.run_status in {"READY", "STOPPED"}
        if action == "step" and self.mode == "formal_training":
            return False
        if action == "reset":
            return self.run_status in {"READY", "STOPPED", "FAILED"}
        if action == "pause":
            return self.run_status in {"STARTING", "RUNNING"} and (local or "pause" in self.capabilities)
        if action == "resume":
            return self.run_status == "PAUSED" and (local or "pause" in self.capabilities)
        if action == "step":
            return (self.run_status == "PAUSED" and self.mode != "formal_training"
                    and (local or "single_step" in self.capabilities))
        if action == "stop":
            return self.run_status in {"STARTING", "RUNNING", "PAUSED"}
        return False

    def disconnect(self, error: str = "") -> None:
        self.connection = "DISCONNECTED"
        self.confirmed = False
        self.error = error

    def synchronize(self, session_id: str, status: str, mode: str, capabilities) -> None:
        if not session_id or status not in TRANSITIONS or mode not in MODES:
            raise ValueError("Invalid session synchronization")
        self.session_id, self.run_status, self.mode = session_id, status, mode
        self.capabilities = set(capabilities)
        self.connection, self.confirmed, self.error = "CONNECTED", True, ""

    def lifecycle(self, session_id: str, status: str, mode: str, capabilities) -> None:
        if not self.confirmed or session_id != self.session_id:
            raise ValueError("Unconfirmed or obsolete session")
        if mode not in MODES:
            raise ValueError("Invalid lifecycle mode")
        capabilities = set(capabilities)
        if any(not isinstance(capability, str) or not capability.strip()
               for capability in capabilities):
            raise ValueError("Invalid lifecycle capabilities")
        if status != self.run_status and status not in TRANSITIONS.get(self.run_status, set()):
            raise ValueError(f"Invalid lifecycle transition: {self.run_status} -> {status}")
        self.run_status, self.mode, self.capabilities = status, mode, capabilities

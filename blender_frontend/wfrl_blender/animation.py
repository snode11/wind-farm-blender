"""Dependency-free live poses; receiver wall time is independent of simulation steps."""
import math
import time
from copy import deepcopy


def integrate_rotor_angle(angle_rad, rpm, frame_dt_seconds, *, running=True, connected=True):
    """Integrate a rotor phase in receiver wall time without resetting it."""
    if not all(math.isfinite(float(value)) for value in (angle_rad, rpm, frame_dt_seconds)):
        raise ValueError('Rotor inputs must be finite')
    if frame_dt_seconds < 0:
        raise ValueError('Frame interval must be nonnegative')
    if not running or not connected:
        return float(angle_rad) % math.tau
    return (float(angle_rad) + float(rpm) * math.tau / 60.0 * float(frame_dt_seconds)) % math.tau


def pose_values(yaw_deg, pitch_deg, *, manual=None, manual_enabled=False, paused=False):
    """Resolve backend pose versus a paused manual override.

    Manual values win only while explicitly enabled and paused.  Missing or
    invalid values never get replaced with zero, which keeps stale telemetry
    visually identifiable.
    """
    if manual_enabled and paused and manual is not None:
        try:
            manual_yaw, manual_pitch = float(manual[0]), float(manual[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError('manual pose requires yaw and pitch') from exc
        if not all(math.isfinite(value) for value in (manual_yaw, manual_pitch)):
            raise ValueError('manual pose must be finite')
        return manual_yaw, manual_pitch
    return yaw_deg, pitch_deg


def apply_pose(objects, turbine_id, yaw_deg, pitch_deg, *, manual=None,
               manual_enabled=False, paused=False, connected=True):
    """Apply the yaw-root and local blade-pitch axes for one turbine."""
    if not connected:
        return
    yaw_value, pitch_value = pose_values(yaw_deg, pitch_deg, manual=manual,
                                         manual_enabled=manual_enabled, paused=paused)
    prefix = 'WFRL.Turbine.' + str(turbine_id)
    if yaw_value is not None and math.isfinite(float(yaw_value)):
        obj = objects.get(prefix + '.YawRoot')
        if obj is not None:
            obj.rotation_euler.z = math.radians(float(yaw_value))
    if pitch_value is not None and math.isfinite(float(pitch_value)):
        for index in range(1, 4):
            obj = objects.get(prefix + f'.Blade{index}')
            if obj is not None:
                # Blade pitch is the local-Z rotation on the per-blade PitchRoot
                # hierarchy established by scene_builder.
                obj.rotation_euler.z = math.radians(float(pitch_value))


class KinematicState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.snapshot = None
        self.received = 0.0
        self.rotor_angles = {}

    def apply_snapshot(self, message):
        self.snapshot = deepcopy(message.get('payload', message))
        self.received = self.clock()
        for turbine in self.snapshot['turbines']:
            self.rotor_angles.setdefault(turbine['turbine_id'], 0.0)

    def validity(self, record, connected=True):
        if record['validity'] != 'valid':
            return record['validity']
        age = record['source_age_seconds'] + max(0, self.clock() - self.received)
        return 'stale' if not connected or age >= record['stale_after_seconds'] else 'valid'

    def advance(self, frame_dt_seconds, running=True, connected=True):
        if not self.snapshot or not running or not connected:
            return
        if not math.isfinite(frame_dt_seconds) or frame_dt_seconds < 0:
            raise ValueError('Frame interval must be finite and nonnegative')
        for turbine in self.snapshot['turbines']:
            record = turbine['channels'].get('rotor_speed')
            if record and self.validity(record, connected) == 'valid':
                tid = turbine['turbine_id']
                self.rotor_angles[tid] = integrate_rotor_angle(
                    self.rotor_angles[tid], record['value'], frame_dt_seconds)

    def apply_objects(self, objects, connected=True):
        """Called only by the Blender main-thread timer; keep absent values absent."""
        if not self.snapshot:
            return
        for turbine in self.snapshot['turbines']:
            prefix = 'WFRL.Turbine.' + turbine['turbine_id']
            channels = turbine['channels']
            yaw = channels.get('yaw')
            pitch = channels.get('pitch')
            yaw_value = yaw['value'] if yaw and self.validity(yaw, connected) == 'valid' else None
            pitch_value = pitch['value'] if pitch and self.validity(pitch, connected) == 'valid' else None
            apply_pose(objects, turbine['turbine_id'], yaw_value, pitch_value, connected=connected)
            rotor = objects.get(prefix + '.Rotor')
            if rotor is not None:
                rotor.rotation_euler.x = self.rotor_angles[turbine['turbine_id']]

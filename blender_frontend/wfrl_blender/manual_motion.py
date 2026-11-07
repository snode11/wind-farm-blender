"""Wall-clock rotor motion for the independent rigid turbine controls."""
from dataclasses import dataclass
import math


def _finite(value, name):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{name} must be finite')
    return number


@dataclass
class RotorClock:
    """Preserve phase when the speed or running state changes between ticks."""

    azimuth_deg: float = 0.0
    rpm: float = 9.0
    running: bool = False
    last_time: float = 0.0

    def sample(self, now):
        now = _finite(now, 'Time')
        elapsed = max(0.0, now - self.last_time)
        if self.running:
            self.azimuth_deg = (self.azimuth_deg + self.rpm * 6.0 * elapsed) % 360.0
        self.last_time = now
        return self.azimuth_deg

    def configure(self, now, *, rpm=None, running=None, azimuth_deg=None):
        self.sample(now)
        if rpm is not None:
            value = _finite(rpm, 'RPM')
            if value < 0:
                raise ValueError('RPM must be nonnegative')
            self.rpm = value
        if running is not None:
            self.running = bool(running)
        if azimuth_deg is not None:
            self.azimuth_deg = _finite(azimuth_deg, 'Azimuth') % 360.0
        return self.azimuth_deg


def pitch_at(start_deg, target_deg, rate_deg_s, elapsed_s):
    """Rate-limited pitch that holds once it reaches the target; rate 0 is instant."""
    start = _finite(start_deg, 'Pitch')
    target = _finite(target_deg, 'Pitch')
    rate = _finite(rate_deg_s, 'Pitch rate')
    elapsed = max(0.0, _finite(elapsed_s, 'Time'))
    if rate <= 0:
        return target
    step = rate * elapsed
    return target if abs(target - start) <= step else start + math.copysign(step, target - start)

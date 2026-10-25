"""Wall-clock motion and deterministic rigid pitch-trial poses."""
from dataclasses import dataclass
import math


def _finite(value, name):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{name} must be finite')
    return number


def pitch_at(start_deg, target_deg, rate_deg_s, elapsed_s):
    """Move toward the target without overshoot; zero rate means immediate placement."""
    start, target = _finite(start_deg, 'Pitch'), _finite(target_deg, 'Pitch')
    rate = _finite(rate_deg_s, 'Pitch rate')
    elapsed = max(0.0, _finite(elapsed_s, 'Time'))
    if rate < 0:
        raise ValueError('Pitch rate must be nonnegative')
    if rate == 0:
        return target
    step = rate * elapsed
    return target if abs(target - start) <= step else start + math.copysign(step, target - start)


@dataclass
class PitchClock:
    """Preserve actual pitch across target, rate and pause changes."""

    pitches: tuple[float, float, float]
    targets: tuple[float, float, float]
    rate: float = 0.0
    running: bool = False
    last_time: float = 0.0

    def sample(self, now):
        now = _finite(now, 'Time')
        if self.running:
            self.pitches = tuple(pitch_at(a, b, self.rate, max(0.0, now - self.last_time))
                                 for a, b in zip(self.pitches, self.targets))
        self.last_time = now
        return self.pitches

    def configure(self, now, *, targets=None, rate=None, running=None):
        checked = tuple(_finite(value, 'Pitch') for value in targets) if targets is not None else self.targets
        if len(checked) != 3:
            raise ValueError('Pitch requires B1/B2/B3')
        checked_rate = self.rate if rate is None else _finite(rate, 'Pitch rate')
        if checked_rate < 0:
            raise ValueError('Pitch rate must be nonnegative')
        self.sample(now)
        self.targets, self.rate = checked, checked_rate
        if running is not None:
            self.running = bool(running)
        if self.rate == 0:
            self.pitches = self.targets
        return self.pitches


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


@dataclass(frozen=True)
class TrialPose:
    time_s: float
    azimuth_deg: float
    pitches_deg: tuple[float, float, float]


@dataclass(frozen=True)
class PitchTrial:
    """One simultaneous pitch transition at a fixed positive RPM."""

    azimuth_deg: float
    rpm: float
    start_pitches: tuple[float, float, float]
    targets: tuple[float, float, float]
    selected: tuple[bool, bool, bool]
    duration_s: float
    interpolation: str = 'SMOOTH'

    def __post_init__(self):
        for name in ('start_pitches', 'targets', 'selected'):
            values = tuple(getattr(self, name))
            if len(values) != 3:
                raise ValueError('试验必须包含 B1/B2/B3')
            object.__setattr__(self, name, values)
        if any(type(value) is not bool for value in self.selected) or not any(self.selected):
            raise ValueError('请至少选择一片叶片')
        for value in (*self.start_pitches, *self.targets):
            if not -30 <= _finite(value, '桨距') <= 90:
                raise ValueError('桨距预览范围为 −30° 至 90°')
        if not 0 < _finite(self.rpm, 'RPM') <= 30:
            raise ValueError('试验转速必须大于 0 且不超过 30 RPM')
        if _finite(self.duration_s, '变桨时长') <= 0:
            raise ValueError('变桨时长必须是有限正数')
        if self.interpolation not in ('SMOOTH', 'LINEAR'):
            raise ValueError('变桨方式必须为平滑或匀速')
        _finite(self.azimuth_deg, '起始叶轮角度')

    def pose_at(self, time_s):
        """Sample unwrapped rotor phase and all pitches without mutating the trial."""
        time_s = _finite(time_s, '试验时间')
        if time_s < 0:
            raise ValueError('试验时间不能为负数')
        u = min(1.0, time_s / self.duration_s)
        fraction = u if self.interpolation == 'LINEAR' else u * u * (3.0 - 2.0 * u)
        pitches = tuple(start + (target - start) * fraction if selected else start
                        for start, target, selected in zip(self.start_pitches, self.targets, self.selected))
        return TrialPose(time_s, self.azimuth_deg + 6.0 * self.rpm * time_s, pitches)


@dataclass
class TrialClock:
    """A single elapsed time drives both rotor phase and blade pitch."""

    trial: PitchTrial
    elapsed_s: float = 0.0
    running: bool = False
    last_time: float = 0.0

    def sample(self, now):
        now = _finite(now, 'Time')
        if self.running:
            self.elapsed_s += max(0.0, now - self.last_time)
        self.last_time = now
        return self.trial.pose_at(self.elapsed_s)

    def configure(self, now, *, running=None, reset=False):
        self.sample(now)
        if reset:
            self.elapsed_s = 0.0
        if running is not None:
            self.running = bool(running)
        return self.trial.pose_at(self.elapsed_s)


"""Pure time mapping for a saved reconstruction held on the MAPPO timeline.

The timeline rate is a data contract, independent of Blender's playback FPS.
Interpolation of the displayed geometry never creates an observation here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


METADATA_KEYS = {
    'start_s': 'split_reconstruction_start_s',
    'timeline_fps': 'split_reconstruction_timeline_fps',
    'stride': 'split_reconstruction_stride',
    'samples': 'split_reconstruction_samples',
    'frame_start': 'split_reconstruction_frame_start',
}


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def _integer(value, label, minimum=None):
    number = _finite(value, label)
    if not number.is_integer() or (minimum is not None and number < minimum):
        raise ValueError(f'{label} must be an integer' + (f' >= {minimum}' if minimum is not None else ''))
    return int(number)


@dataclass(frozen=True)
class TimingSpec:
    start_s: float = 117.0
    timeline_fps: float = 60.0
    stride: int = 6
    samples: int = 601
    frame_start: int = 1

    def __post_init__(self):
        object.__setattr__(self, 'start_s', _finite(self.start_s, 'start_s'))
        fps = _finite(self.timeline_fps, 'timeline_fps')
        if fps <= 0:
            raise ValueError('timeline_fps must be positive')
        object.__setattr__(self, 'timeline_fps', fps)
        object.__setattr__(self, 'stride', _integer(self.stride, 'stride', 1))
        object.__setattr__(self, 'samples', _integer(self.samples, 'samples', 1))
        object.__setattr__(self, 'frame_start', _integer(self.frame_start, 'frame_start'))

    @property
    def frame_end(self):
        return self.frame_start + self.stride * (self.samples - 1)

    @property
    def sampling_hz(self):
        return self.timeline_fps / self.stride


def spec_from_scene(scene):
    """Read explicit timing, or the known legacy a02 contract if none is saved.

    Callers must first establish that this is a supported split reconstruction
    scene. Partial metadata is rejected rather than mixed with guessed defaults.
    ``frame_start`` is optional for early metadata using the fixed first frame 1.
    """
    required = tuple(key for name, key in METADATA_KEYS.items() if name != 'frame_start')
    present = [key in scene for key in required]
    if not any(present) and METADATA_KEYS['frame_start'] not in scene:
        return TimingSpec()
    if not all(present):
        raise ValueError('Incomplete saved reconstruction timing metadata')
    return TimingSpec(**{name: scene.get(key, 1) if name == 'frame_start' else scene[key]
                         for name, key in METADATA_KEYS.items()})


@dataclass(frozen=True)
class FrameTiming:
    timeline_time_s: float
    sample_time_s: float
    age_s: float | None
    index: int
    sample_frame: int
    in_range: bool

    @property
    def age_ms(self):
        return None if self.age_s is None else self.age_s * 1000.0


def timing_for_frame(frame, spec=None):
    """Return the actual held sample; flag frames outside the saved interval.

    No age is claimed outside that interval, including frames before the first
    sample. The clamped index is useful for seeking, not evidence of fresh data.
    """
    spec = spec or TimingSpec()
    frame = _finite(frame, 'frame')
    offset = frame - spec.frame_start
    index = min(max(math.floor(offset / spec.stride), 0), spec.samples - 1)
    sample_frame = spec.frame_start + index * spec.stride
    in_range = spec.frame_start <= frame <= spec.frame_end
    return FrameTiming(
        timeline_time_s=spec.start_s + offset / spec.timeline_fps,
        sample_time_s=spec.start_s + index * spec.stride / spec.timeline_fps,
        age_s=(frame - sample_frame) / spec.timeline_fps if in_range else None,
        index=index, sample_frame=sample_frame, in_range=in_range,
    )


def step_sample_frame(frame, direction, spec=None):
    """Seek strictly earlier/later samples from on- or off-grid frames, clamped."""
    spec = spec or TimingSpec()
    frame = _finite(frame, 'frame')
    if isinstance(direction, bool) or direction not in (-1, 1):
        raise ValueError('direction must be -1 or +1')
    position = (frame - spec.frame_start) / spec.stride
    index = math.floor(position) + 1 if direction == 1 else math.ceil(position) - 1
    index = min(max(index, 0), spec.samples - 1)
    return spec.frame_start + index * spec.stride

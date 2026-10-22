"""Pure-Python checks for the Part 5 motion-render artifacts."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import subprocess


FRAME_START = 301
FRAME_STEP = 2
FRAME_COUNT = 36
FPS = 12
FRAME_WIDTH = 3840
FRAME_HEIGHT = 2160


@dataclass(frozen=True)
class MotionFrameReport:
    expected: tuple[int, ...]
    valid: tuple[int, ...]
    missing: tuple[int, ...]
    invalid: tuple[int, ...]
    dimensions: tuple[int, int] | None

    @property
    def complete(self) -> bool:
        return not self.missing and not self.invalid and len(self.valid) == len(self.expected)


def expected_indices(count: int = FRAME_COUNT) -> tuple[int, ...]:
    return tuple(range(count))


def frame_timeline(index: int) -> int:
    if index < 0 or index >= FRAME_COUNT:
        raise ValueError(f"motion frame index out of range: {index}")
    return FRAME_START + index * FRAME_STEP


def png_dimensions(path: Path) -> tuple[int, int] | None:
    """Read the PNG signature/IHDR only; never trusts a filename as evidence."""

    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return (width, height) if width > 0 and height > 0 else None


def inspect_frames(
    directory: str | Path,
    *,
    expected_size: tuple[int, int] | None = (FRAME_WIDTH, FRAME_HEIGHT),
    count: int = FRAME_COUNT,
) -> MotionFrameReport:
    root = Path(directory)
    expected = expected_indices(count)
    valid: list[int] = []
    missing: list[int] = []
    invalid: list[int] = []
    dimensions: tuple[int, int] | None = None
    for index in expected:
        path = root / f"{index:03d}.png"
        if not path.is_file():
            missing.append(index)
            continue
        size = png_dimensions(path)
        if size is None or (expected_size is not None and size != expected_size):
            invalid.append(index)
            continue
        dimensions = dimensions or size
        valid.append(index)
    return MotionFrameReport(tuple(expected), tuple(valid), tuple(missing), tuple(invalid), dimensions)


def _ffprobe(path: Path) -> dict[str, object] | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,nb_frames,nb_read_frames,r_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        return streams[0] if streams else None
    except (TypeError, ValueError, IndexError):
        return None


def inspect_movie(path: str | Path, *, expected_frames: int = FRAME_COUNT) -> dict[str, object]:
    movie = Path(path)
    result: dict[str, object] = {
        "path": str(movie),
        "exists": movie.is_file(),
        "nonempty": movie.is_file() and movie.stat().st_size > 0,
    }
    if result["exists"] and result["nonempty"]:
        stream = _ffprobe(movie)
        result["stream"] = stream
        if stream:
            try:
                frame_count = stream.get("nb_read_frames", stream.get("nb_frames", -1))
                result["frame_count_matches"] = int(frame_count) == expected_frames
            except (TypeError, ValueError):
                result["frame_count_matches"] = False
    else:
        result["stream"] = None
        result["frame_count_matches"] = False
    return result


def verify_artifacts(frames_dir: str | Path, movie_path: str | Path) -> dict[str, object]:
    frames = inspect_frames(frames_dir)
    movie = inspect_movie(movie_path)
    manifest_path = Path(frames_dir) / "render_manifest.json"
    manifest: dict[str, object] | None = None
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, TypeError, ValueError):
            manifest = None
    manifest_complete = bool(
        manifest
        and manifest.get("status") == "complete"
        and manifest.get("frame_count") == FRAME_COUNT
        and manifest.get("completed_indices") == list(frames.expected)
    )
    return {
        "frames": asdict(frames) | {"complete": frames.complete},
        "movie": movie,
        "manifest": manifest,
        "manifest_complete": manifest_complete,
        "complete": frames.complete and manifest_complete and bool(movie.get("exists")) and bool(movie.get("nonempty")) and bool(movie.get("frame_count_matches")),
    }

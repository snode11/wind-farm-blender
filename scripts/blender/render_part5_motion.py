"""Render and package the Part 5 motion study.

The default run renders all 36 frames so the movie always matches the current
presentation blend. Pass ``--resume`` after Blender's ``--`` separator to
reuse valid frames from an interrupted run and render only missing/invalid
ones. The script never creates a movie until every expected frame passes the
PNG check.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blender_frontend"))
sys.path.insert(0, str(Path(__file__).parent))

# This check is intentionally before importing bpy. It gives a useful error
# when a surviving wrapper invokes the script from the known unsafe sandbox.
from blender_preflight import BlenderLaunchBlocked, inspect_blender  # noqa: E402
from part5_motion import (  # noqa: E402
    FRAME_COUNT,
    FRAME_HEIGHT,
    FRAME_START,
    FRAME_STEP,
    FRAME_WIDTH,
    FPS,
    frame_timeline,
    inspect_frames,
    inspect_movie,
    png_dimensions,
)


if os.environ.get("CODEX_SANDBOX", "").strip().lower() in {"seatbelt", "sandbox", "restricted"}:
    result = inspect_blender(
        os.environ.get("WFRL_BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")
    )
    raise BlenderLaunchBlocked(result.reason)

sys.dont_write_bytecode = True
import bpy  # noqa: E402
import wfrl_blender  # noqa: E402


_SOURCE_SHA256 = ""


def _extra_args() -> list[str]:
    """Read only arguments after Blender's conventional ``--`` separator."""

    try:
        return sys.argv[sys.argv.index("--") + 1:]
    except ValueError:
        return []


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(out: Path, *, status: str, completed: tuple[int, ...], message: str = "") -> None:
    _atomic_json(out / "render_manifest.json", {
        "status": status,
        "frame_start": FRAME_START,
        "frame_step": FRAME_STEP,
        "frame_count": FRAME_COUNT,
        "fps": FPS,
        "cycles_samples": 64,
        "expected_size": [FRAME_WIDTH, FRAME_HEIGHT],
        "source_blend": "evidence/part5_presentation.blend",
        "source_sha256": _SOURCE_SHA256,
        "completed_indices": list(completed),
        "message": message,
    })


def _render_frame(scene, out: Path, index: int) -> None:
    target = out / f"{index:03d}.png"
    temporary = out / f".{index:03d}.rendering.png"
    temporary.unlink(missing_ok=True)
    scene.frame_set(frame_timeline(index))
    bpy.context.view_layer.update()
    scene.render.filepath = str(temporary)
    bpy.ops.render.render(write_still=True)
    if not temporary.is_file() or png_dimensions(temporary) != (FRAME_WIDTH, FRAME_HEIGHT):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Rendered frame {index:03d} is not a {FRAME_WIDTH}x{FRAME_HEIGHT} PNG")
    temporary.replace(target)


def _make_movie(root: Path, out: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to create part5_presentation_motion.mp4")
    movie = root / "evidence/part5_presentation_motion.mp4"
    temporary = root / "evidence/.part5_presentation_motion.rendering.mp4"
    temporary.unlink(missing_ok=True)
    command = [
        ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS),
        "-start_number", "0", "-i", str(out / "%03d.png"),
        "-frames:v", str(FRAME_COUNT), "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(temporary),
    ]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(detail)
    temporary.replace(movie)
    return movie


def main() -> None:
    global _SOURCE_SHA256
    source = ROOT / "evidence/part5_presentation.blend"
    if not source.is_file():
        raise FileNotFoundError(source)
    out = ROOT / "evidence/part5_presentation_frames"
    out.mkdir(parents=True, exist_ok=True)
    _SOURCE_SHA256 = _sha256(source)
    resume = "--resume" in _extra_args() or os.environ.get("WFRL_MOTION_RESUME") == "1"

    wfrl_blender.register()
    bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    scene.render.resolution_x = FRAME_WIDTH
    scene.render.resolution_y = FRAME_HEIGHT
    scene.render.resolution_percentage = 100
    scene.cycles.samples = 64
    scene.cycles.use_denoising = True
    if sys.platform == "darwin":
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "METAL"
        preferences.get_devices()
        if any(device.type == "METAL" for device in preferences.devices):
            for device in preferences.devices:
                device.use = device.type == "METAL"
            scene.cycles.device = "GPU"

    report = inspect_frames(out, expected_size=(FRAME_WIDTH, FRAME_HEIGHT), count=FRAME_COUNT)
    existing_manifest = out / "render_manifest.json"
    manifest_matches_source = False
    if resume and existing_manifest.is_file():
        try:
            saved = json.loads(existing_manifest.read_text(encoding="utf-8"))
            manifest_matches_source = (
                saved.get("source_sha256") == _SOURCE_SHA256
                and saved.get("frame_count") == FRAME_COUNT
                and saved.get("frame_start") == FRAME_START
                and saved.get("frame_step") == FRAME_STEP
                and saved.get("expected_size") == [FRAME_WIDTH, FRAME_HEIGHT]
                and saved.get("cycles_samples") == 64
            )
        except (OSError, TypeError, ValueError):
            manifest_matches_source = False
    use_resume = resume and manifest_matches_source
    targets = list(report.missing + report.invalid) if use_resume else list(range(FRAME_COUNT))
    _write_manifest(out, status="rendering", completed=tuple(report.valid),
                    message=("resume" if use_resume else
                             "full render (source changed or no matching manifest)"))
    for index in targets:
        _render_frame(scene, out, index)
        report = inspect_frames(out, expected_size=(FRAME_WIDTH, FRAME_HEIGHT), count=FRAME_COUNT)
        _write_manifest(out, status="rendering", completed=tuple(report.valid))

    report = inspect_frames(out, expected_size=(FRAME_WIDTH, FRAME_HEIGHT), count=FRAME_COUNT)
    if not report.complete:
        _write_manifest(out, status="blocked", completed=tuple(report.valid),
                        message=f"missing={report.missing} invalid={report.invalid}")
        raise RuntimeError(f"motion frames incomplete: missing={report.missing}, invalid={report.invalid}")
    movie = _make_movie(ROOT, out)
    movie_report = inspect_movie(movie)
    if not movie_report.get("frame_count_matches"):
        _write_manifest(out, status="blocked", completed=tuple(report.valid),
                        message="movie frame count failed")
        raise RuntimeError(f"movie verification failed: {movie_report}")
    _write_manifest(out, status="complete", completed=tuple(report.valid), message=str(movie))
    print(f"WFRL_MOTION_RENDER=PASS frames={FRAME_COUNT} movie={movie}")


if __name__ == "__main__":
    main()

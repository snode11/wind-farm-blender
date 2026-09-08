"""Verify the Part 5 motion frames and movie without launching Blender."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from part5_motion import verify_artifacts  # noqa: E402


def main() -> int:
    report = verify_artifacts(
        ROOT / "evidence/part5_presentation_frames",
        ROOT / "evidence/part5_presentation_motion.mp4",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Optional dependencies for the standalone preview, never backend imports."""
from functools import lru_cache


@lru_cache(maxsize=1)
def opencv():
    try:
        import cv2
    except ModuleNotFoundError as exc:
        if exc.name != 'cv2':
            raise
        raise RuntimeError("OpenCV preview requires: python -m pip install -e '.[tip-preview]'") from exc
    return cv2

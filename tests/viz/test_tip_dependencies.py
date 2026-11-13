"""Optional OpenCV imports remain isolated from the installed environment."""

import builtins
from types import ModuleType

import pytest

from wfrl.viz import tip_dependencies


@pytest.fixture(autouse=True)
def clear_opencv_cache():
    tip_dependencies.opencv.cache_clear()
    yield
    tip_dependencies.opencv.cache_clear()


@pytest.fixture
def cv2_import(monkeypatch):
    real_import = builtins.__import__
    state = {"module": ModuleType("cv2"), "error": None, "calls": 0}

    def controlled_import(name, *args, **kwargs):
        if name != "cv2":
            return real_import(name, *args, **kwargs)
        state["calls"] += 1
        if state["error"] is not None:
            raise state["error"]
        return state["module"]

    monkeypatch.setattr(builtins, "__import__", controlled_import)
    return state


def test_opencv_returns_and_caches_the_imported_module(cv2_import):
    expected = cv2_import["module"]
    assert tip_dependencies.opencv() is expected
    cv2_import["module"] = ModuleType("replacement_cv2")
    assert tip_dependencies.opencv() is expected
    assert cv2_import["calls"] == 1
    assert tip_dependencies.opencv.cache_info().currsize == 1


def test_cache_clear_allows_a_fresh_import(cv2_import):
    assert tip_dependencies.opencv() is cv2_import["module"]
    replacement = cv2_import["module"] = ModuleType("replacement_cv2")
    tip_dependencies.opencv.cache_clear()
    assert tip_dependencies.opencv() is replacement
    assert cv2_import["calls"] == 2


def test_missing_cv2_has_installation_guidance_and_preserves_cause(cv2_import):
    error = ModuleNotFoundError("No module named 'cv2'", name="cv2")
    cv2_import["error"] = error
    with pytest.raises(RuntimeError) as caught:
        tip_dependencies.opencv()
    assert str(caught.value) == "OpenCV preview requires: python -m pip install -e '.[tip-preview]'"
    assert caught.value.__cause__ is error
    assert tip_dependencies.opencv.cache_info().currsize == 0
    cv2_import["error"] = None
    assert tip_dependencies.opencv() is cv2_import["module"]
    assert cv2_import["calls"] == 2


@pytest.mark.parametrize("missing_name", ["numpy", "cv2.internal", None])
def test_missing_internal_dependency_propagates_unchanged(cv2_import, missing_name):
    error = ModuleNotFoundError("OpenCV dependency unavailable", name=missing_name)
    cv2_import["error"] = error
    with pytest.raises(ModuleNotFoundError) as caught:
        tip_dependencies.opencv()
    assert caught.value is error
    assert tip_dependencies.opencv.cache_info().currsize == 0


def test_other_import_errors_are_not_wrapped_or_cached(cv2_import):
    error = ImportError("OpenCV binary could not be loaded")
    cv2_import["error"] = error
    for _ in range(2):
        with pytest.raises(ImportError) as caught:
            tip_dependencies.opencv()
        assert caught.value is error
    assert cv2_import["calls"] == 2
    assert tip_dependencies.opencv.cache_info().currsize == 0

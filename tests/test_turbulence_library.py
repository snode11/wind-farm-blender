"""Library identity, explicit selection and independent hub statistics."""
import json
from pathlib import Path

import numpy as np
import pytest

from wfrl import inflow
from wfrl.windcond import resolve_turbulence


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    template = tmp_path / "template"
    template.mkdir()
    (template / "legacy.bts").write_bytes(b"legacy")
    monkeypatch.setattr(inflow, "template_dir", lambda: str(template))
    monkeypatch.setenv("WFRL_TURBULENCE_DIR", str(root))
    entries = []
    for speed, ti, seed in ((8, 8, 42), (12, 16, -7), (8, "A", 42)):
        name = inflow.turbulence_name(speed, ti, seed)
        path = root / (name + ".bts")
        path.write_bytes(f"fixture {name}".encode("utf-8"))
        entries.append(dict(name=name, file=path.name, wind_speed_mps=speed, ti=ti, seed=seed,
                            size_bytes=path.stat().st_size, sha256=inflow.file_sha256(path)))
    manifest = {"schema": inflow.MANIFEST_SCHEMA, "boxes": entries}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, template, manifest


@pytest.mark.parametrize("selection", ["ts_u8_ti8_s42", "ts_u8_ti8_s42.bts",
                                       (8, 8, 42), "bts:8,8,42"])
def test_name_filename_and_tuple_resolve_same_file(library, selection):
    root, _, _ = library
    assert inflow.resolve_bts(selection) == str(root / "ts_u8_ti8_s42.bts")
    assert resolve_turbulence(selection) == inflow.resolve_bts(selection)
    assert resolve_turbulence(wind_speed=8., ti=8., seed=42) == inflow.resolve_bts(selection)


def test_iec_class_is_not_confused_with_numeric_ti(library):
    assert resolve_turbulence(wind_speed=8, ti="a", seed=42).endswith("ts_u8_iecA_s42.bts")
    assert inflow.resolve_bts("bts:8,A,42").endswith("ts_u8_iecA_s42.bts")


def test_legacy_and_absolute_paths_do_not_require_a_valid_catalog(library):
    root, template, _ = library
    (root / "manifest.json").write_text("broken", encoding="utf-8")
    assert inflow.resolve_bts("legacy.bts") == str(template / "legacy.bts")
    path = root / "ts_u8_ti8_s42.bts"
    assert inflow.resolve_bts(path) == str(path)


def test_unknown_combination_lists_available_choices(library):
    with pytest.raises(FileNotFoundError, match="可选项.*ts_u8_ti8_s42.*legacy.bts"):
        inflow.resolve_bts((9, 8, 42))


@pytest.mark.parametrize("selection", [(8, .0, 42), (8, True, 42), (True, 8, 42),
                                       (8, 8, True), (8, 8, 1.5), (8, 8, 2**31),
                                       (float("nan"), 8, 42), (8, float("inf"), 42),
                                       "bts:8,8", "bts:8,8,1.5", {}, None])
def test_invalid_selection_is_explicit(library, selection):
    with pytest.raises(ValueError):
        inflow.resolve_bts(selection)


def test_selection_modes_cannot_be_mixed(library):
    with pytest.raises(ValueError, match="不能同时"):
        resolve_turbulence("ts_u8_ti8_s42", wind_speed=8)


def test_selected_file_is_checked_against_manifest(library):
    root, _, _ = library
    path = root / "ts_u8_ti8_s42.bts"
    path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(ValueError, match="SHA-256"):
        inflow.resolve_bts((8, 8, 42))
    path.unlink()
    with pytest.raises(FileNotFoundError, match="不存在"):
        inflow.resolve_bts((8, 8, 42))


@pytest.mark.parametrize("mutation", ["duplicate", "path_escape", "wrong_schema", "invalid_hash"])
def test_manifest_corruption_is_not_treated_as_an_empty_library(library, mutation):
    root, _, manifest = library
    if mutation == "duplicate":
        manifest["boxes"].append(manifest["boxes"][0])
    elif mutation == "path_escape":
        manifest["boxes"][0]["file"] = "../escape.bts"
    elif mutation == "wrong_schema":
        manifest["schema"] = "unknown"
    else:
        manifest["boxes"][0]["sha256"] = "bad"
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="清单无效"):
        inflow.resolve_bts("ts_u8_ti8_s42")


def test_manifest_statistics_use_time_variance_at_each_lateral_point(tmp_path):
    from openfast_toolbox.io import TurbSimFile

    path = tmp_path / "statistics.bts"
    ts = TurbSimFile()
    ts["t"], ts["y"], ts["z"] = np.arange(4.), np.array([-5., 5.]), np.array([80., 100.])
    ts["u"] = np.zeros((3, 4, 2, 2))
    fluctuation = np.array([-1., 1., -1., 1.])
    ts["u"][0, :, 0, :] = 8. + fluctuation[:, None] + np.array([-2., 2.])
    ts["u"][0, :, 1, :] = 12. + 2 * fluctuation[:, None] + np.array([-2., 2.])
    ts["ID"] = 8
    ts.write(str(path))
    stats = inflow.bts_statistics(path)
    assert stats["mean_speed_mps"] == pytest.approx(10., abs=.001)
    assert [row["std_mps"] for row in stats["per_lateral_point"]] == pytest.approx([1., 2.], abs=.001)
    assert stats["mean_ti_percent"] == pytest.approx((12.5 + 100/6) / 2, abs=.01)
    assert stats["periodic"] is True


def test_explicit_library_directory_is_independent_of_cwd(library, tmp_path, monkeypatch):
    root, _, _ = library
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WFRL_TURBULENCE_DIR", str(tmp_path / "missing"))
    assert inflow.resolve_bts((8, 8, 42), library_dir=root) == str(root / "ts_u8_ti8_s42.bts")


def test_storage_directory_is_git_ignored():
    import subprocess

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "check-ignore", "data/turbulence/example.bts"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", check=True)
    assert result.stdout.strip() == "data/turbulence/example.bts"

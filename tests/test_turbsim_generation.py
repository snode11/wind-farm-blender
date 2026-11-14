"""TurbSim grid/target contracts and publication failure handling."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.inflow import generate_turbsim as generator
from wfrl.inflow import file_sha256, resolve_bts, template_dir


@pytest.mark.parametrize("speed", [8., 12.])
@pytest.mark.parametrize("ti", [8., 16., "A", "B", "C"])
def test_90m_target_and_domain_coverage(speed, ti):
    params, target, coverage = generator.generation_parameters(speed, ti, 42)
    sigma = float(params["IECturbc"].strip('"')) / 100 * speed * (180/90) ** .2
    expected = ({"A": .16, "B": .14, "C": .12}[ti] * (.75 * speed + 5.6)
                if isinstance(ti, str) else speed * ti / 100)
    assert sigma == pytest.approx(expected, rel=1e-10)
    assert target == pytest.approx(expected / speed * 100)
    assert params["HubHt"] == 180. and params["RefHt"] == 90.
    assert params["NumGrid_Y"] == 69 and params["NumGrid_Z"] == 35
    assert coverage["required_duration_s"] <= 300
    assert coverage["layout_coordinates_m"] == {"x": [0., 504., 1008.], "y": [0., 0., 0.]}


@pytest.mark.parametrize("kwargs", [dict(duration=30), dict(duration=float("nan")),
                                    dict(grid_spacing=12), dict(time_step=10),
                                    dict(grid_spacing=.1), dict(simulation_seconds=500)])
def test_insufficient_or_invalid_grids_fail_before_turbsim(kwargs):
    with pytest.raises(ValueError):
        generator.generation_parameters(8, 8, 42, **kwargs)


def test_template_bytes_are_preserved_except_requested_values():
    template = Path(template_dir()) / "90m_08mps.inp"
    before = template.read_bytes()
    rendered = generator.replace_parameters(before.decode("utf-8"), {"URef": 12., "RandSeed1": 42})
    assert rendered.count("\r\n") == before.count(b"\r\n")
    assert rendered.replace("12.0   URef", "8.0   URef").replace("42   RandSeed1", "2073448544   RandSeed1") == before.decode("utf-8")
    assert template.read_bytes() == before
    with pytest.raises(ValueError, match="恰好"):
        generator.replace_parameters(rendered, {"MissingLabel": 1})


def test_version_flag_and_simulation_banner_refer_to_the_same_build():
    assert generator._version("TurbSim-v3.5.3-dirty") == generator._version(
        "Running TurbSim (from OpenFAST-v3.5.3-dirty)")


@pytest.fixture
def fake_turbsim(tmp_path, monkeypatch):
    import openfast_toolbox.fastfarm
    import openfast_toolbox.io

    calls = []
    executable = tmp_path / "turbsim"
    executable.write_bytes(b"fixture executable")
    state = {"fail": False}

    def run(command, **kwargs):
        calls.append(command)
        assert Path(kwargs["cwd"]).is_relative_to(tmp_path)
        if command[-1] != "-v":
            if state["fail"]:
                return SimpleNamespace(returncode=1, stdout="fixture failure", stderr="")
            path = Path(command[-1])
            path.with_suffix(".bts").write_bytes(b"wind fixture")
            path.with_suffix(".sum").write_text("TurbSim-v3.5.3", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="TurbSim-v3.5.3", stderr="")

    _, _, preflight = generator.generation_parameters(8, 8, 42)
    grid = dict(preflight["low_domain"], **preflight["high_domain"])
    monkeypatch.setattr(generator.subprocess, "run", run)
    monkeypatch.setattr(generator, "bts_statistics", lambda *a, **kw: {
        "y_range_m": [-340., 340.], "z_range_m": [10., 350.], "time_span_s": 299.9,
        "mean_speed_mps": 8., "mean_ti_percent": 8.})
    monkeypatch.setattr(openfast_toolbox.io, "TurbSimFile", lambda *a, **kw: {"uRef": 9.19})
    monkeypatch.setattr(openfast_toolbox.fastfarm, "fastFarmTurbSimExtent", lambda *a, **kw: grid)
    return executable, calls, state


def test_generation_manifest_and_no_overwrite(tmp_path, fake_turbsim):
    executable, calls, _ = fake_turbsim
    root = tmp_path / "library"
    kwargs = dict(wind_speeds=[8, 12], intensities=[8, 16], seeds=[42],
                  turbsim=executable, output_dir=root, scratch_root=tmp_path)
    manifest = generator.generate_library(**kwargs)
    assert len(manifest["boxes"]) == 4
    assert len(calls) == 5
    assert sorted(p.suffix for p in root.iterdir()) == sorted([".bts", ".sum", ".inp"] * 4 + [".json"])
    assert json.loads((root / "manifest.json").read_text(encoding="utf-8")) == manifest
    for box in manifest["boxes"]:
        assert box["sha256"] == file_sha256(root / box["file"])
        assert box["turbsim_version"] == "TurbSim-v3.5.3"
        assert resolve_bts((box["wind_speed_mps"], box["ti"], box["seed"]), library_dir=root) == str(root / box["file"])
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        generator.generate_library(**kwargs)
    assert len(calls) == 5
    assert not list(tmp_path.glob("wfrl-turbsim-*"))


def test_failed_turbsim_does_not_publish_manifest_or_orphan_files(tmp_path, fake_turbsim):
    executable, _, state = fake_turbsim
    state["fail"] = True
    root = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="fixture failure"):
        generator.generate_library(wind_speeds=[8], intensities=[8], seeds=[42],
                                   turbsim=executable, output_dir=root, scratch_root=tmp_path)
    assert not list(root.iterdir())
    assert not list(tmp_path.glob("wfrl-turbsim-*"))


def test_manifest_write_failure_rolls_back_only_the_new_artifacts(tmp_path, fake_turbsim, monkeypatch):
    executable, _, _ = fake_turbsim
    root = tmp_path / "library"

    def fail(*args):
        raise OSError("fixture disk failure")

    monkeypatch.setattr(generator, "_write_manifest", fail)
    with pytest.raises(OSError, match="fixture disk"):
        generator.generate_library(wind_speeds=[8], intensities=[8], seeds=[42],
                                   turbsim=executable, output_dir=root, scratch_root=tmp_path)
    assert not list(root.iterdir())


def test_smoke_worker_timeout_reaps_its_child(tmp_path):
    import os
    import sys
    from scripts.inflow.smoke_turbulence import run_worker

    assert run_worker([sys.executable, "-c", "import time; time.sleep(30)"],
                      tmp_path, dict(os.environ), timeout=.1) == 124

"""Generated input bytes and the selected-BTS copy boundary without running a solver."""
import inspect
import json
from pathlib import Path
import shutil

import pytest

from wfcrl import simul_utils
from wfrl.inflow import MANIFEST_SCHEMA, file_sha256, turbulence_name


def _legacy_generator():
    """Reference the template-only BTS resolution and unconditional copy contract."""
    source = inspect.getsource(simul_utils.create_ff_case)
    start = source.index("        # Resolve the selected file")
    end = source.index("        # --- Get box extents", start)
    source = source[:start] + '''        inflow["FileName_BTS"] = f'"{case["wind_time_series"]}"'
        BTS_filename = os.path.join(
            f"{template_dir}FarmInputs/", case["wind_time_series"]
        )
''' + source[end:]
    source = source.replace('''    bts_files = (glob.glob(f"{template_dir}FarmInputs/*.bts")
                 if case["wind_time_series"] is None else [BTS_filename])
    for file in bts_files:
''', '''    for file in glob.glob(f"{template_dir}FarmInputs/*.bts"):
''')
    assert "bts_files" not in source
    namespace = dict(vars(simul_utils))
    exec(compile(source, "<template-only reference>", "exec"), namespace)
    return namespace["create_ff_case"]


def _case(selection):
    return dict(num_turbines=3, xcoords=[0., 504., 1008.], ycoords=[0., 0., 0.],
                max_iter=10, dt=3, speed=8., wind_time_series=selection)


def _hash_tree(root):
    return {path.relative_to(root).as_posix(): file_sha256(path)
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("selection", [None, "90m_08mps.bts"])
def test_default_case_tree_is_byte_identical_to_template_only_reference(tmp_path, selection, monkeypatch):
    from wfrl import inflow

    monkeypatch.setattr(inflow, "resolve_bts", lambda *a, **kw: pytest.fail("default case must not consult the library"))
    before, after = tmp_path / "reference", tmp_path / "actual"
    _legacy_generator()(_case(selection), output_dir=str(before))
    simul_utils.create_ff_case(_case(selection), output_dir=str(after))
    assert _hash_tree(after) == _hash_tree(before)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    source = Path(simul_utils.TEMPLATE_DIR.format("fastfarm"))
    template = tmp_path / "template"
    shutil.copytree(source, template)
    (template / "FarmInputs" / "unused.bts").write_bytes(b"unused")
    monkeypatch.setattr(simul_utils, "TEMPLATE_DIR", str(template) + "/")
    library = tmp_path / "library"
    library.mkdir()
    name = turbulence_name(8, 8, 42)
    bts = library / (name + ".bts")
    shutil.copyfile(template / "FarmInputs" / "90m_08mps.bts", bts)
    manifest = {"schema": MANIFEST_SCHEMA, "boxes": [dict(
        name=name, file=bts.name, wind_speed_mps=8, ti=8, seed=42,
        sha256=file_sha256(bts), size_bytes=bts.stat().st_size)]}
    (library / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("WFRL_TURBULENCE_DIR", str(library))
    return library, template, bts


@pytest.mark.parametrize("mode", ["name", "filename", "tuple_string", "absolute"])
def test_explicit_selection_copies_only_selected_bts_and_uses_relative_reference(tmp_path, catalog, mode):
    _, _, bts = catalog
    selection = {"name": bts.stem, "filename": bts.name, "tuple_string": "bts:8,8,42",
                 "absolute": str(bts)}[mode]
    target = tmp_path / "case"
    farm = simul_utils.create_ff_case(_case(selection), output_dir=str(target))
    assert list((target / "FarmInputs").glob("*.bts")) == [target / "FarmInputs" / bts.name]
    inflow = simul_utils.FASTInputFile(simul_utils.get_inflow_file_path(farm))
    assert inflow["WindType"] == 3
    assert inflow["FileName_BTS"].strip('"') == bts.name
    assert file_sha256(target / "FarmInputs" / bts.name) == file_sha256(bts)
    moved = tmp_path / "relocated"
    target.rename(moved)
    assert (moved / "FarmInputs" / inflow["FileName_BTS"].strip('"')).is_file()


def test_steady_case_keeps_template_copy_behavior_even_with_multiple_boxes(tmp_path, catalog):
    before, after = tmp_path / "reference", tmp_path / "actual"
    _legacy_generator()(_case(None), output_dir=str(before))
    simul_utils.create_ff_case(_case(None), output_dir=str(after))
    assert _hash_tree(after) == _hash_tree(before)


def test_scene_yaml_turbulence_reaches_case_generation(tmp_path, catalog):
    from wfrl.scene import load_scene

    scene = load_scene(Path(__file__).resolve().parents[1] / "scenes" / "turb3_row.yaml")
    data = scene.to_dict()
    data["inflow"]["turbulence"] = "bts:8,8,42"
    import yaml

    scene_path = tmp_path / "scene.yaml"
    scene_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = load_scene(scene_path)
    case = loaded.make_case(max_iter=10)
    farm = simul_utils.create_ff_case(case.dict(), output_dir=str(tmp_path / "scene_case"))
    inflow = simul_utils.FASTInputFile(simul_utils.get_inflow_file_path(farm))
    assert inflow["FileName_BTS"].strip('"') == catalog[2].name


@pytest.mark.parametrize("mode", ["absolute", "name", "filename", "tuple_string", "legacy"])
def test_selection_passes_startup_identity_check(tmp_path, catalog, mode):
    from wfcrl.environments import FastFarmCase
    from wfcrl.interface import FastFarmInterface
    from wfrl.windcond import resolve_turbulence

    selected = {"absolute": resolve_turbulence(wind_speed=8, ti=8, seed=42),
                "name": "ts_u8_ti8_s42", "filename": "ts_u8_ti8_s42.bts",
                "tuple_string": "bts:8,8,42", "legacy": "90m_08mps.bts"}[mode]
    case = FastFarmCase(num_turbines=1, xcoords=[0.], ycoords=[0.], dt=3,
                        t_init=0, buffer_window=3, max_iter=10)
    case.wind_time_series = selected
    interface = FastFarmInterface.from_case(case, output_dir=str(tmp_path / "case"))
    interface.set_wind_request_context({"wind_time_series": selected}, record_dir=tmp_path / "records")
    interface._prepare_wind_record(None, None, {})
    assert interface.wind_record["status"] == "validated"
    assert interface.wind_record["application"]["wind_time_series"]["status"] == "bts_determined"
    assert interface.wind_record["loaded_inputs"]["wind_file"]["sha256"] == file_sha256(catalog[2])
    assert interface.wind_record["requested"]["wind_time_series"] == selected


def test_steady_startup_does_not_consult_turbulence_resolver(tmp_path, monkeypatch):
    from wfcrl.environments import FastFarmCase
    from wfcrl.interface import FastFarmInterface
    from wfrl import inflow

    monkeypatch.setattr(inflow, "resolve_bts", lambda *a, **kw: pytest.fail("steady wind must not resolve BTS"))
    case = FastFarmCase(num_turbines=1, xcoords=[0.], ycoords=[0.], dt=3,
                        t_init=0, buffer_window=3, max_iter=10)
    interface = FastFarmInterface.from_case(case, output_dir=str(tmp_path / "case"))
    interface.set_wind_request_context({"wind_speed": 8., "wind_time_series": None},
                                      record_dir=tmp_path / "records")
    before = _hash_tree(tmp_path / "case")
    interface._prepare_wind_record(8., None, {})
    assert _hash_tree(tmp_path / "case") == before
    assert interface.wind_record["status"] == "validated"
    assert interface.wind_record["loaded_inputs"]["settings"]["WindType"] == 1


def test_legacy_startup_ignores_a_broken_library(tmp_path, monkeypatch):
    from wfcrl.environments import FastFarmCase
    from wfcrl.interface import FastFarmInterface

    library = tmp_path / "broken"
    library.mkdir()
    (library / "manifest.json").write_text("invalid", encoding="utf-8")
    monkeypatch.setenv("WFRL_TURBULENCE_DIR", str(library))
    case = FastFarmCase(num_turbines=1, xcoords=[0.], ycoords=[0.], dt=3,
                        t_init=0, buffer_window=3, max_iter=10, wind_time_series="90m_08mps.bts")
    interface = FastFarmInterface.from_case(case, output_dir=str(tmp_path / "case"))
    interface.set_wind_request_context({"wind_time_series": "90m_08mps.bts"},
                                      record_dir=tmp_path / "records")
    before = _hash_tree(tmp_path / "case")
    interface._prepare_wind_record(None, None, {})
    assert _hash_tree(tmp_path / "case") == before
    assert interface.wind_record["status"] == "validated"


def test_catalog_replacement_is_rejected_before_mpi(tmp_path, catalog):
    from wfcrl.environments import FastFarmCase
    from wfcrl.interface import FastFarmInterface, WindRequestError

    case = FastFarmCase(num_turbines=1, xcoords=[0.], ycoords=[0.], dt=3,
                        t_init=0, buffer_window=3, max_iter=10, wind_time_series="ts_u8_ti8_s42")
    interface = FastFarmInterface.from_case(case, output_dir=str(tmp_path / "case"))
    interface.set_wind_request_context({"wind_time_series": case.wind_time_series},
                                      record_dir=tmp_path / "records")
    catalog[2].write_bytes(b"replaced")
    with pytest.raises(WindRequestError, match="SHA-256"):
        interface._prepare_wind_record(None, None, {})
    assert interface.wind_record["status"] == "rejected"


def test_nested_source_keeps_crlf():
    # interface.py already mixes CRLF and LF upstream; only simul_utils.py is pure CRLF.
    raw = Path(simul_utils.__file__).read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")

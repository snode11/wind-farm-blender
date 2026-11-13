"""Scene input validation and YAML persistence contracts."""

import copy
import re

import numpy as np
import pytest
import yaml

from wfrl.scene import schema


def scene_data():
    return {
        "name": "中文场景",
        "backend": "fastfarm",
        "turbine": "nrel5mw",
        "dt": 3.0,
        "layout": [
            {"id": "T2", "x": 504.1234567890123, "y": -0.0},
            {"id": "T1", "x": -12.125, "y": 1.23456789012345},
        ],
        "inflow": {"speed": 8.123456789012345, "direction": 270.25, "turbulence": None},
        "controls": ["pitch", "yaw"],
        "sensors": [{"type": "camera", "mount": "nacelle", "turbines": "T1,T2",
                     "params": {"fov": 49.123456789, "pitch": -30.25}}],
        "terrain": None,
    }


def set_field(data, path, value):
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


@pytest.mark.parametrize("path,label", [
    (("unexpected",), "unexpected"),
    (("layout", 0, "z"), "layout[0]"),
    (("inflow", "speeed"), "inflow"),
    (("sensors", 0, "unexpected"), "sensors[0]"),
])
def test_unknown_scene_fields_are_rejected_with_path(path, label):
    data = scene_data()
    set_field(data, path, 12)
    with pytest.raises(schema.SceneError, match=re.escape(label)):
        schema.from_dict(data)


@pytest.mark.parametrize("path,label,value", [
    (("name",), "name", None),
    (("dt",), "dt", True),
    (("dt",), "dt", "3"),
    (("dt",), "dt", None),
    (("dt",), "dt", 10**400),
    (("dt",), "dt", np.timedelta64(3, "ns")),
    (("dt",), "dt", np.timedelta64(3, "s")),
    (("layout", 0, "id"), "layout[0].id", None),
    (("layout", 0, "id"), "layout[0].id", []),
    (("layout", 0, "x"), "layout[0].x", True),
    (("layout", 0, "y"), "layout[0].y", "0"),
    (("inflow",), "inflow", []),
    (("inflow",), "inflow", None),
    (("inflow", "speed"), "inflow.speed", None),
    (("inflow", "direction"), "inflow.direction", False),
    (("inflow", "turbulence"), "inflow.turbulence", []),
    (("controls",), "controls", []),
    (("controls",), "controls", None),
    (("controls",), "controls", {"yaw": True}),
    (("sensors",), "sensors", {}),
    (("sensors",), "sensors", None),
    (("sensors", 0, "mount"), "sensors[0].mount", None),
    (("sensors", 0, "turbines"), "sensors[0].turbines", []),
    (("sensors", 0, "params"), "sensors[0].params", []),
    (("sensors", 0, "params"), "sensors[0].params", False),
    (("terrain",), "terrain", False),
])
def test_malformed_scene_values_do_not_become_defaults(path, label, value):
    data = scene_data()
    set_field(data, path, value)
    with pytest.raises(schema.SceneError, match=re.escape(label)):
        schema.from_dict(data)


@pytest.mark.parametrize("field,value,label", [
    ("dt", True, "dt"),
    ("wind_speed", "8", "inflow.speed"),
    ("wind_direction", False, "inflow.direction"),
    ("controls", {"yaw": True}, "controls"),
    ("sensors", None, "sensors"),
    ("turbines", [schema.Turbine([], 0, 0)], "layout[0].id"),
])
def test_direct_scene_validation_enforces_field_types(field, value, label):
    scene = schema.from_dict(scene_data())
    setattr(scene, field, value)
    with pytest.raises(schema.SceneError, match=re.escape(label)):
        scene.validate()


def test_sensor_parameter_errors_are_scene_errors_with_sensor_path():
    data = scene_data()
    data["sensors"][0]["params"]["range"] = -1
    with pytest.raises(schema.SceneError, match=r"sensors\[0\].*range"):
        schema.from_dict(data)


@pytest.mark.parametrize("document", [
    "layout: [{x: 0, y: 0}]\ndt: 3\ndt: 4\n",
    "layout: [{x: 0, x: 500, y: 0}]\n",
    "layout: [{x: 0, y: 0}]\ninflow: {speed: 8, speed: 9}\n",
    "layout: [{<<: {x: 0, x: 500, y: 0}}]\n",
])
def test_duplicate_yaml_keys_cannot_overwrite_scene_values(tmp_path, document):
    path = tmp_path / "duplicate.scene"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(schema.SceneError, match="重复|duplicate"):
        schema.load_scene(path)


@pytest.mark.parametrize("failure", ["serialize", "replace"])
def test_dump_scene_failure_preserves_existing_file_and_cleans_temporary(tmp_path, monkeypatch, failure):
    path = tmp_path / "scene.saved"
    original = b"existing complete scene\n"
    path.write_bytes(original)

    def fail_dump(data, stream=None, **options):
        if stream is not None:
            stream.write("partial scene")
        raise OSError("injected serialization failure")

    def fail_replace(*args):
        raise OSError("injected replace failure")

    if failure == "serialize":
        monkeypatch.setattr(schema.yaml, "safe_dump", fail_dump)
    else:
        monkeypatch.setattr(schema.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        schema.dump_scene(schema.from_dict(scene_data()), path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_scene_round_trip_preserves_values_order_units_and_yaml_layout(tmp_path):
    data = scene_data()
    scene = schema.from_dict(copy.deepcopy(data))
    path = tmp_path / "roundtrip.scene"
    schema.dump_scene(scene, path)
    assert path.read_text(encoding="utf-8") == yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    loaded = schema.load_scene(path)
    assert loaded.to_dict() == data
    assert loaded.turbines[0].y.hex() == (-0.0).hex()
    assert loaded.source_path == str(path)


def test_omitted_optional_configuration_and_yaml_merge_keep_existing_defaults(tmp_path):
    path = tmp_path / "defaults.scene"
    path.write_text("layout:\n  - &first {id: T1, x: 0, y: 0}\n"
                    "  - {<<: *first, id: T2, x: 504}\n", encoding="utf-8")
    scene = schema.load_scene(path)
    assert scene.dt == 3 and scene.wind_speed == 8 and scene.wind_direction == 270
    assert scene.controls == ["yaw"] and scene.sensors == []
    assert [t.id for t in scene.turbines] == ["T1", "T2"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("path", [("dt",), ("layout", 0, "x"), ("inflow", "speed"),
                                 ("inflow", "direction")])
def test_nonfinite_scene_numbers_are_rejected(path, value):
    data = scene_data()
    set_field(data, path, value)
    with pytest.raises(schema.SceneError):
        schema.from_dict(data)


def test_duplicate_turbine_ids_and_missing_coordinates_are_rejected():
    data = scene_data()
    data["layout"][1]["id"] = data["layout"][0]["id"]
    with pytest.raises(schema.SceneError, match="重复"):
        schema.from_dict(data)
    data = scene_data()
    del data["layout"][0]["x"]
    with pytest.raises(schema.SceneError, match=r"layout\[0\]"):
        schema.from_dict(data)

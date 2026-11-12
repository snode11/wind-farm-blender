"""Wind request/input/sample contracts using file fixtures and an in-memory MPI peer."""
import collections
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfcrl import environments  # Initialize the package's registration imports.
from wfcrl import interface, simul_utils
from wfrl import fastfarm_driver as driver_module


class InputFile(dict):
    """Minimal on-disk input adapter; no simulator executable is involved."""

    def __init__(self, path):
        super().__init__(json.loads(Path(path).read_text(encoding="utf-8")))

    def write(self, path):
        Path(path).write_text(json.dumps(self), encoding="utf-8")


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(interface, "FASTInputFile", InputFile)
    monkeypatch.setattr(simul_utils, "FASTInputFile", InputFile)
    monkeypatch.setattr(driver_module.paths, "RUNS", str(tmp_path / "runs"))
    input_dir = tmp_path / "__simul__" / "fastfarm" / "fixture" / "FarmInputs"
    input_dir.mkdir(parents=True)
    farm_path = input_dir / "Case.fstf"
    inflow_path = input_dir / "InflowWind.dat"
    wind_path = input_dir / "loaded.bts"
    wind_path.write_bytes(b"loaded wind fixture")
    for i in range(1, 4):
        (input_dir / f"T{i}.fst").write_text(json.dumps({"EDFile": f'"T{i}.dat"', "DT": .00625}), encoding="utf-8")
        (input_dir / f"T{i}.dat").write_text(json.dumps({"NacYaw": float(i - 1)}), encoding="utf-8")
    farm_path.write_text(json.dumps({
        "InflowFile": '"InflowWind.dat"', "Mod_AmbWind": 2, "DT_Low": 3., "DT_High": .05,
        "TMax": 9., "NumTurbines": 3,
        "WindTurbines": [[0., 0., 0., '"T1.fst"'], [500., 0., 0., '"T2.fst"'], [1000., 0., 0., '"T3.fst"']],
    }), encoding="utf-8")
    inflow_path.write_text(json.dumps({
        "WindType": 1, "PropagationDir": 0., "VFlowAng": 0., "HWindSpeed": 8.,
        "RefHt": 90., "PLExp": .2, "FileName_BTS": '"loaded.bts"',
    }), encoding="utf-8")

    class Peer:
        def __init__(self):
            self.samples = []
            self.disconnected = False

        def Get_rank(self): return 0
        def Send(self, **kwargs): pass
        def Barrier(self): pass
        def Disconnect(self): self.disconnected = True

        def Recv(self, buffer, **kwargs):
            if buffer.size == 1:
                buffer[:] = 12
            else:
                speeds, radians = self.samples.pop(0)
                values = np.zeros((3, 12))
                values[:, 0], values[:, 2] = speeds, radians
                values[:, 1] = [1e6, 2e6, 3e6]
                buffer[:] = values.ravel()

    peer = Peer()
    spawns = []

    def spawn(*args, **kwargs):
        spawns.append((args, kwargs))
        return peer

    monkeypatch.setattr(interface, "MPI", SimpleNamespace(
        COMM_WORLD=peer, COMM_SELF=SimpleNamespace(Spawn=spawn), Intercomm=Peer))
    itf = interface.FastFarmInterface(3, str(farm_path), max_iter=3, fast_farm_executable=sys.executable)

    def edit(path, **updates):
        document = InputFile(path)
        document.update(updates)
        document.write(path)

    def bts_data(speeds):
        u = np.zeros((3, 4, 2, 2))
        u[0] = np.asarray(speeds)[:, None, None] + np.array([0., 2.])[None, :, None] + np.array([-1., 1.])[None, None, :]
        return {"t": np.arange(4.), "y": np.array([-5., 5.]), "z": np.array([85., 95.]), "u": u}

    fields = {str(wind_path): bts_data([10., 16., 10., 16.])}
    import openfast_toolbox.io
    monkeypatch.setattr(openfast_toolbox.io, "TurbSimFile", lambda path: copy.deepcopy(fields[str(path)]))

    return SimpleNamespace(itf=itf, path=farm_path, inflow=inflow_path, wind=wind_path,
                           peer=peer, spawns=spawns, edit=edit, fields=fields, bts_data=bts_data)


def saved(record):
    return json.loads(Path(record["record_path"]).read_text(encoding="utf-8"))


def make_driver(case):
    itf = case.itf

    class Env:
        mdp = SimpleNamespace(interface=itf, ACTUATORS_RATE={})
        infos = {f"T{i}": {"power": float(i)} for i in range(1, 4)}

        def reset(self, options=None):
            options = options or {}
            itf.init(options.get("wind_speed", 7.5), options.get("wind_direction", 290.))
            case.peer.samples.append(([10., 8., 6.], [0., 0., 0.]))
            itf.update_command()

        def observe(self, agent):
            i = int(agent[1:]) - 1
            return {"yaw": [0.], "wind_speed": [itf.get_measure("wind_speed")[i]],
                    "wind_direction": [itf.get_measure("wind_direction")[i]]}

    driver = driver_module.FastFarmDriver.__new__(driver_module.FastFarmDriver)
    driver.env = Env()
    driver.wind_time_series = None
    driver.max_steps = 3
    driver.dt = 3.
    driver.hub_height = 90.
    driver.agents = ["T1", "T2", "T3"]
    driver._idx = {agent: i for i, agent in enumerate(driver.agents)}
    driver.n = 3
    driver.controls = ["yaw"]
    driver.yaw_duty_sliding = False
    driver._yaw_win = collections.deque()
    driver._duty = lambda: {agent: {} for agent in driver.agents}
    return driver


def test_steady_request_applied_and_read_back_with_file_identity(case):
    record = case.itf.init(wind_speed=11.)
    assert len(case.spawns) == 1
    assert record["requested"]["wind_speed"] == 11.
    assert record["application"]["wind_speed"]["status"] == "applied"
    assert record["loaded_inputs"]["settings"]["HWindSpeed"] == 11.
    assert record["loaded_inputs"]["inflow_file"]["sha256"] == hashlib.sha256(case.inflow.read_bytes()).hexdigest()
    assert record["loaded_inputs"]["farm_settings"]["DT_High"] == .05
    assert record["loaded_inputs"]["turbines"][2]["initial_nacelle_yaw_deg"] == 2.
    assert record["statistics"]["farm_inflow"]["status"] == "unavailable"
    assert saved(record) == record


@pytest.mark.parametrize("parameters", [
    {"wind_direction": 270. + 1e-6}, {"wind_direction": 280.}, {"ti": .12},
    {"turbulence_intensity": .12}, {"shear": .2}, {"gust": {"amplitude": 3}},
    {"seed": 4}, {"unknown_parameter": None},
])
def test_unsupported_request_rejects_whole_transaction_without_mutation(case, parameters):
    before = case.inflow.read_bytes()
    with pytest.raises(interface.WindRequestError, match="未应用") as rejected:
        case.itf.init(wind_speed=11., **parameters)
    record = rejected.value.record
    assert record["status"] == "rejected"
    assert record["application"][next(iter(parameters))]["status"] == "not_applied"
    assert record["application"]["wind_speed"]["status"] == "not_applied"
    assert case.inflow.read_bytes() == before
    assert not case.spawns
    assert saved(record) == record


@pytest.mark.parametrize("speed", [True, "9", 0., -1., float("nan"), float("inf")])
def test_invalid_speed_is_explicitly_unapplied(case, speed):
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_speed=speed)
    assert saved(rejected.value.record)["application"]["wind_speed"]["status"] == "not_applied"
    assert not case.spawns


@pytest.mark.parametrize("wind_type", [1, 3])
@pytest.mark.parametrize("direction", [270., -90., 630., 270. - 1e-9, 270. + 1e-9])
def test_aligned_direction_matches_fixed_coordinates_without_rotating_inputs(case, wind_type, direction):
    case.edit(case.inflow, WindType=wind_type)
    before = {path: path.read_bytes() for path in case.path.parent.iterdir() if path.is_file()}
    record = case.itf.init(wind_direction=direction)
    application = record["application"]["wind_direction"]
    assert record["requested"]["wind_direction"] == direction
    assert application["status"] == "MATCHES_FIXED_COORDINATES"
    assert application["value"] == interface.WIND_COORDINATES["aligned_public_wind_direction_deg"]
    assert "no rotation" in application["reason"]
    assert record["coordinates"]["layout_transform"] == "identity"
    assert record["coordinates"]["wind_vector_transform"] == "identity"
    assert all(path.read_bytes() == contents for path, contents in before.items())
    assert len(case.spawns) == 1
    assert saved(record) == record


@pytest.mark.parametrize("direction", [True, np.bool_(False), "270", 0., 269.9999999,
                                      float("nan"), float("inf"), -float("inf")])
def test_invalid_direction_is_explicitly_unapplied(case, direction):
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_direction=direction)
    assert saved(rejected.value.record)["application"]["wind_direction"]["status"] == "not_applied"
    assert not case.spawns


@pytest.mark.parametrize("requested,forwarded", [(280., 270.), (270., 280.)])
def test_both_requested_and_forwarded_directions_must_match_fixed_coordinates(case, requested, forwarded):
    case.itf.set_wind_request_context({"wind_direction": requested})
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_direction=forwarded)
    assert rejected.value.record["application"]["wind_direction"]["status"] == "not_applied"
    assert not case.spawns


@pytest.mark.parametrize("override", [{"wind_speed": 13.}, {"wind_direction": 280.}])
def test_bts_scalar_overrides_rejected_even_when_mean_matches(case, override):
    case.edit(case.inflow, WindType=3)
    before = case.inflow.read_bytes()
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(**override)
    assert rejected.value.record["application"][next(iter(override))]["status"] == "not_applied"
    assert case.inflow.read_bytes() == before
    assert not case.spawns


def test_bts_authority_uses_resolved_file_and_never_hwind_speed(case):
    case.edit(case.inflow, WindType=3, HWindSpeed=99.)
    record = case.itf.init()
    assert record["application"]["wind_speed"]["status"] == "bts_determined"
    assert "HWindSpeed" not in record["loaded_inputs"]["settings"]
    assert record["loaded_inputs"]["wind_file"]["path"] == str(case.wind)
    assert InputFile(case.inflow)["HWindSpeed"] == 99.
    assert record["statistics"]["loaded_bts"]["mean_streamwise_speed_mps"] == 14.
    for name in ("background_turbulence", "gust_superposed", "farm_inflow"):
        assert record["statistics"][name]["status"] == "unavailable"
    assert len(case.spawns) == 1


@pytest.mark.parametrize("need,exceeds", [(2., False), (3., False), (4., True)])
def test_bts_duration_comparison_is_saved_and_warns_before_spawn(case, monkeypatch, capsys, need, exceeds):
    case.edit(case.inflow, WindType=3)
    case.edit(case.path, TMax=need)
    case.fields[str(case.wind)]["t"] += 10.
    spawn = interface.MPI.COMM_SELF.Spawn
    at_spawn = []

    def inspect_spawn(*args, **kwargs):
        at_spawn.append((saved(case.itf.wind_record), capsys.readouterr().out))
        return spawn(*args, **kwargs)

    monkeypatch.setattr(interface.MPI.COMM_SELF, "Spawn", inspect_spawn)
    record = case.itf.init()
    check = record["verification"]["bts_duration"]
    assert check["episode_duration_s"] == need
    assert check["duration_basis"] == "FAST.Farm TMax"
    assert check["bts_duration_s"] == 3.
    assert check["exceeds_bts_duration"] is exceeds
    assert check["status"] == ("exceeds_bts_duration" if exceeds else "within_bts_duration")
    assert at_spawn[0][0]["verification"]["bts_duration"] == check
    if exceeds:
        warning = ("[fastfarm-driver] 警告：回合需 4s 风场 > 盒子 3s，超出部分由 InflowWind 处理"
                   "（周期回绕或报错），长训练需重新生成 .bts")
        assert check["warning"] == warning
        assert warning in at_spawn[0][1]
    else:
        assert check["warning"] is None
        assert "警告：回合需" not in at_spawn[0][1]
    assert saved(record) == record


def test_driver_duration_uses_its_control_budget_and_refreshes_bts_span(case, capsys):
    case.edit(case.inflow, WindType=3)
    driver = make_driver(case)
    driver.max_steps = 2
    first = driver.reset()["wind_conditions"]
    check = first["verification"]["bts_duration"]
    assert check["episode_duration_s"] == 6.
    assert check["duration_basis"] == "driver max_steps * dt"
    assert check["exceeds_bts_duration"] is True
    assert "回合需 6s 风场 > 盒子 3s" in capsys.readouterr().out
    case.fields[str(case.wind)]["t"] *= 2.
    second = driver.reset()["wind_conditions"]
    assert second["verification"]["bts_duration"]["bts_duration_s"] == 6.
    assert second["verification"]["bts_duration"]["exceeds_bts_duration"] is False
    assert "警告：回合需" not in capsys.readouterr().out
    assert saved(first) == first
    assert saved(second) == second


def test_background_and_gust_statistics_remain_separate(case, tmp_path):
    case.edit(case.inflow, WindType=3)
    background = tmp_path / "background.bts"
    background.write_bytes(b"background only")
    case.fields[str(background)] = case.bts_data([10., 12., 10., 12.])
    case.itf.set_wind_request_context({}, bts_role="gust_superposed", background_bts=background)
    record = case.itf.init()
    stats = record["statistics"]
    bg = stats["background_turbulence"]["per_lateral_point"][0]
    gust = stats["gust_superposed"]["per_lateral_point"][0]
    assert (bg["mean_mps"], bg["std_mps"]) == (11., 1.)
    assert bg["ti_percent"] == pytest.approx(100. / 11.)
    assert (gust["mean_mps"], gust["std_mps"]) == (13., 3.)
    assert gust["ti_percent"] == pytest.approx(300. / 13.)
    assert stats["background_turbulence"]["file"]["path"] == str(background)
    assert stats["gust_superposed"]["file"]["path"] == str(case.wind)
    assert stats["background_turbulence"]["window_s"] == [0., 3.]
    assert stats["farm_inflow"]["status"] == "unavailable"


def test_background_label_requires_explicit_provenance(case):
    case.edit(case.inflow, WindType=3)
    case.itf.set_wind_request_context({}, bts_role="background")
    record = case.itf.init()
    assert record["bts_role"]["value"] == "background"
    assert record["statistics"]["background_turbulence"]["status"] == "available"
    assert record["statistics"]["gust_superposed"]["status"] == "not_applicable"


def test_selected_bts_must_match_loaded_content(case, tmp_path):
    case.edit(case.inflow, WindType=3)
    selected = tmp_path / "selected.bts"
    selected.write_bytes(b"different wind")
    case.itf.set_wind_request_context({"wind_time_series": str(selected)})
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init()
    assert rejected.value.record["application"]["wind_time_series"]["status"] == "not_applied"
    assert not case.spawns


def test_bts_same_path_new_content_is_remeasured_each_reset(case):
    case.edit(case.inflow, WindType=3)
    first = case.itf.init()
    case.wind.write_bytes(b"replacement wind")
    case.fields[str(case.wind)] = case.bts_data([8., 8., 8., 8.])
    second = case.itf.init()
    assert first["record_id"] != second["record_id"]
    assert first["loaded_inputs"]["wind_file"]["sha256"] != second["loaded_inputs"]["wind_file"]["sha256"]
    assert second["statistics"]["loaded_bts"]["mean_streamwise_speed_mps"] == 9.
    assert second["loaded_inputs"]["farm_file"]["path"].endswith("Case_iter1.fstf")
    assert saved(first) == first


@pytest.mark.parametrize("settings", [{"PropagationDir": 15.}, {"VFlowAng": 2.}, {"WindType": 2}])
def test_unimplemented_input_coordinates_and_modes_are_rejected(case, settings):
    case.edit(case.inflow, **settings)
    with pytest.raises(interface.WindRequestError):
        case.itf.init(wind_direction=270.)
    assert not case.spawns


def test_mode_three_requires_separate_synchronization_support(case):
    case.edit(case.path, Mod_AmbWind=3)
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init()
    assert rejected.value.record["application"]["Mod_AmbWind"]["status"] == "not_applied"
    assert not case.spawns


def test_runtime_statistics_preserve_invalid_samples_and_wake_scope(case):
    case.itf.init(wind_speed=11.)
    case.peer.samples.extend([
        ([10., 8., 6.], np.radians([89., 0., -720.])),
        ([12., 6., np.nan], np.radians([91., 450., np.nan])),
        ([11., 7., 5.], np.radians([90., 0., 0.])),
    ])
    for _ in range(3):
        case.itf.update_command()
    record = case.itf.wind_record
    stats = record["statistics"]
    t1 = stats["turbine_hub"]["per_turbine"]["T1"]
    assert t1["mean_mps"] == 11.
    assert t1["direction_mean_deg"] == pytest.approx(0., abs=1e-8)
    downstream = stats["downstream_wake_affected"]["per_turbine"]
    assert set(downstream) == {"T2", "T3"}
    assert downstream["T2"]["mean_mps"] == 7.
    assert downstream["T3"]["count"] == 2
    assert downstream["T3"]["invalid_count"] == 1
    assert downstream["T3"]["direction_invalid_count"] == 1
    assert stats["farm_inflow"]["status"] == "unavailable"
    assert record["sampling"]["sample_count"] == 3
    assert record["sampling"]["nominal_elapsed_s"] == 6.
    assert record["status"] == "completed" and case.peer.disconnected
    assert saved(record) == record


def test_fixed_coordinate_conversion_and_yaw_commands(case):
    case.itf.init()
    case.peer.samples.append(([10., 9., 8.], np.radians([0., -720., 450.])))
    case.itf.update_command(yaw=np.array([0., 10., -10.]))
    np.testing.assert_allclose(case.itf.get_measure("wind_direction"), [270., 270., 0.])
    np.testing.assert_allclose(case.itf._current_yaw_command[1:], np.radians([0., 10., -10.]))
    assert case.itf.wind_record["coordinates"]["rotation_supported"] is False
    assert case.itf.wind_record["coordinates"]["layout_transform"] == "identity"


@pytest.mark.parametrize("wind_type,speed,status", [(1, 11., "applied"), (1, None, "applied"), (3, None, "bts_determined")])
def test_driver_exposes_three_layers_and_preserves_caller_intent(case, wind_type, speed, status):
    case.edit(case.inflow, WindType=wind_type)
    driver = make_driver(case)
    result = driver.reset(wind_speed=speed)
    record = result["wind_conditions"]
    assert record["requested"]["wind_speed"] == speed
    assert record["requested"]["wind_direction"] is None
    assert record["application"]["wind_speed"]["status"] == status
    assert record["sampling"]["sample_count"] == 1
    assert record["sampling"]["control_start_sample_index"] == 1
    assert "runs/wind_conditions" in record["record_path"]
    if wind_type == 3:
        assert record["environment_reset"]["wind_speed"] is None
        assert driver.u_inf == 14.
    else:
        assert driver.u_inf == (speed if speed is not None else 7.5)
    driver._read(0.)
    assert case.itf.wind_record["sampling"]["sample_count"] == 1
    record["requested"]["wind_speed"] = -10.
    assert case.itf.wind_record["requested"]["wind_speed"] == speed


@pytest.mark.parametrize("wind_type", [1, 3])
def test_driver_preserves_explicit_aligned_direction(case, wind_type):
    case.edit(case.inflow, WindType=wind_type)
    record = make_driver(case).reset(wind_direction=-90.)["wind_conditions"]
    assert record["requested"]["wind_direction"] == -90.
    assert record["environment_reset"]["wind_direction"] == -90.
    assert record["application"]["wind_direction"]["status"] == "MATCHES_FIXED_COORDINATES"
    assert len(case.spawns) == 1


def scene_runtime(case, monkeypatch, direction=280., turbulence=None):
    from wfrl.scene.runtime import SceneRuntime
    from wfrl.scene.schema import Scene, Turbine

    scene = Scene(wind_speed=8.123456789, wind_direction=direction, turbulence=turbulence,
                  turbines=[Turbine(f"T{i + 1}", i * 500., 0.) for i in range(3)])

    def build(runtime):
        runtime.driver = make_driver(case)
        runtime.driver.wind_time_series = runtime.scene.turbulence
        runtime.driver.act_low, runtime.driver.act_high = -5., 5.
        runtime.driver.close = lambda **kwargs: None

    monkeypatch.setattr(SceneRuntime, "_build_driver", build)
    return SceneRuntime(scene)


@pytest.mark.parametrize("direction", [270., 280.])
def test_scene_direction_is_recorded_as_display_only(case, monkeypatch, direction):
    runtime = scene_runtime(case, monkeypatch, direction)
    runtime.reset()
    record = runtime.last_measure["wind_conditions"]
    assert record["upstream_request"]["scene"] == {
        "wind_speed": 8.123456789, "wind_direction": direction, "wind_time_series": None,
        "wind_speed_role": "solver_request", "wind_direction_role": "display_only",
    }
    assert record["requested"]["wind_speed"] == 8.123456789
    assert record["requested"]["wind_direction"] is None
    assert record["environment_reset"]["wind_direction"] is None
    assert record["application"]["wind_direction"]["status"] == "input_file"
    assert saved(record) == record
    assert len(case.spawns) == 1


@pytest.mark.parametrize("direction", [270., 280.])
def test_scene_explicit_solver_direction_keeps_display_separate(case, monkeypatch, direction):
    runtime = scene_runtime(case, monkeypatch, direction=250.)
    if direction == 270.:
        runtime.reset(wind_direction=direction)
        record = runtime.last_measure["wind_conditions"]
        assert record["application"]["wind_direction"]["status"] == "MATCHES_FIXED_COORDINATES"
        assert len(case.spawns) == 1
    else:
        with pytest.raises(interface.WindRequestError, match="未应用") as rejected:
            runtime.reset(wind_direction=direction)
        record = rejected.value.record
        assert record["application"]["wind_direction"]["status"] == "not_applied"
        assert not case.spawns
    assert record["requested"]["wind_direction"] == direction
    assert record["requested"]["wind_speed"] == 8.123456789
    assert record["environment_reset"]["wind_direction"] == direction
    assert record["upstream_request"]["scene"]["wind_direction"] == 250.
    assert record["upstream_request"]["scene"]["wind_direction_role"] == "display_only"
    assert saved(record) == record


def test_scene_bts_keeps_display_and_overridden_speed_without_solver_request(case, monkeypatch):
    case.edit(case.inflow, WindType=3)
    runtime = scene_runtime(case, monkeypatch, direction=280., turbulence=str(case.wind))
    runtime.reset()
    record = runtime.last_measure["wind_conditions"]
    assert record["upstream_request"]["scene"] == {
        "wind_speed": 8.123456789, "wind_direction": 280., "wind_time_series": str(case.wind),
        "wind_speed_role": "overridden_by_bts", "wind_direction_role": "display_only",
    }
    assert record["requested"]["wind_speed"] is None
    assert record["requested"]["wind_direction"] is None
    assert record["application"]["wind_speed"]["status"] == "bts_determined"
    assert record["application"]["wind_direction"]["status"] == "input_file"
    assert saved(record) == record
    assert len(case.spawns) == 1


@pytest.mark.parametrize("scene_mode", [False, True])
@pytest.mark.parametrize("direction", [None, 270., 280.])
def test_training_cli_direction_is_preserved_through_config_and_sampler(
        case, monkeypatch, tmp_path, scene_mode, direction):
    from scripts.train import train_fastfarm as trainer

    runtime = scene_runtime(case, monkeypatch)
    scene_path = tmp_path / "scene.yaml"
    scene_path.write_text(json.dumps(runtime.scene.to_dict()), encoding="utf-8")

    def driver_factory(*args, **kwargs):
        driver = make_driver(case)
        driver.act_low, driver.act_high = -5., 5.
        driver.close = lambda **kwargs: None
        return driver

    class ResetObserved(Exception):
        def __init__(self, record):
            self.record = record

    sampler_type = trainer.FastFarmSampler

    def observe_reset(*args, **kwargs):
        sampler = sampler_type(*args, **kwargs)
        try:
            raise ResetObserved(sampler._m["wind_conditions"])
        finally:
            sampler.close()

    monkeypatch.setattr(trainer, "FastFarmDriver", driver_factory)
    monkeypatch.setattr(trainer, "FastFarmSampler", observe_reset)
    monkeypatch.setattr(trainer, "source_input_identity", lambda *args: "wind-request-fixture")
    monkeypatch.setattr(trainer, "RUNS_DIR", str(tmp_path / "training-runs"))
    monkeypatch.setattr(trainer, "CKPT_DIR", str(tmp_path / "checkpoints"))
    argv = ["train_fastfarm.py", "--iters", "1", "--n-steps", "1", "--episode-steps", "1",
            "--warmup-steps", "0", "--wind-speed", "11.987654321"]
    if direction is not None:
        argv += ["--wind-direction", str(direction)]
    if scene_mode:
        argv += ["--scene", str(scene_path)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(interface.WindRequestError if direction == 280. else ResetObserved) as outcome:
        trainer.main()
    record = outcome.value.record
    assert record["requested"]["wind_direction"] == direction
    assert record["environment_reset"]["wind_direction"] == direction
    assert record["requested"]["wind_speed"] == (8.123456789 if scene_mode else 11.987654321)
    assert record["upstream_request"]["training"] == {
        "wind_speed": 11.987654321, "wind_direction": direction, "wind_sched": None,
        "wind_speed_role": "overridden_by_scene" if scene_mode else "solver_request",
        "wind_direction_role": "solver_request",
    }
    if scene_mode:
        assert record["upstream_request"]["scene"]["wind_direction"] == 280.
        assert record["upstream_request"]["scene"]["wind_direction_role"] == "display_only"
    assert record["application"]["wind_direction"]["status"] == (
        "input_file" if direction is None else
        "MATCHES_FIXED_COORDINATES" if direction == 270. else "not_applied")
    assert saved(record) == record
    assert len(case.spawns) == (0 if direction == 280. else 1)


def test_upstream_context_is_copied_and_does_not_relabel_previous_episode(case, monkeypatch):
    driver = make_driver(case)
    upstream = {"scene": {"wind_direction": 280., "wind_direction_role": "display_only"}}
    record = driver.reset(upstream_request=upstream)["wind_conditions"]
    upstream["scene"]["wind_direction"] = 90.
    assert record["upstream_request"]["scene"]["wind_direction"] == 280.
    assert saved(record) == record

    def fail_reset(**kwargs):
        raise RuntimeError("reset failed before preparing wind conditions")

    monkeypatch.setattr(driver.env, "reset", fail_reset)
    with pytest.raises(RuntimeError, match="before preparing"):
        driver.reset(upstream_request=upstream)
    assert case.itf.wind_record == record
    assert saved(record) == record


def test_driver_reset_and_measurements_allow_mdp_without_interface(case):
    driver = make_driver(case)
    options = []
    driver.env = SimpleNamespace(
        mdp=SimpleNamespace(ACTUATORS_RATE={}),
        reset=lambda **kwargs: options.append(kwargs["options"]),
        infos={agent: {"power": 1.} for agent in driver.agents},
        observe=lambda agent: {"wind_speed": [8.], "wind_direction": [270.]},
    )
    result = driver.reset(wind_speed=8., wind_direction=270.)
    assert options == [{"wind_speed": 8., "wind_direction": 270.}]
    assert result["wind_conditions"] is None
    assert driver.u_inf == 8.
    np.testing.assert_allclose(result["power"], [1., 1., 1.])
    assert np.isnan(result["rotor_speed"]).all()
    assert not case.spawns


@pytest.mark.parametrize("parameters", [{"wind_speed": 11.}, {"wind_direction": 280.}, {"gust": 2.}])
def test_driver_does_not_discard_bts_requests(case, parameters):
    case.edit(case.inflow, WindType=3)
    driver = make_driver(case)
    driver.wind_time_series = str(case.wind)
    with pytest.raises(interface.WindRequestError) as rejected:
        driver.reset(**parameters)
    assert rejected.value.record["requested"][next(iter(parameters))] == next(iter(parameters.values()))
    assert not case.spawns


def test_driver_selected_file_is_compared_to_actual_loaded_file(case):
    case.edit(case.inflow, WindType=3)
    driver = make_driver(case)
    driver.wind_time_series = str(case.wind)
    record = driver.reset(bts_role="background")["wind_conditions"]
    assert record["selected_wind_file"]["sha256"] == record["loaded_inputs"]["wind_file"]["sha256"]
    assert record["application"]["wind_time_series"]["status"] == "bts_determined"


def test_missing_bts_rejected_without_solver(case):
    case.edit(case.inflow, WindType=3, FileName_BTS='"missing.bts"')
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init()
    assert rejected.value.record["application"]["loaded_inputs"]["status"] == "not_applied"
    assert not case.spawns


def test_speed_readback_mismatch_is_unapplied(case, monkeypatch):
    monkeypatch.setattr(interface, "write_inflow_info", lambda *args: None)
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_speed=11.)
    assert rejected.value.record["application"]["wind_speed"]["status"] == "not_applied"
    assert not case.spawns


def test_bts_statistics_cannot_extrapolate_hub_height(case):
    case.edit(case.inflow, WindType=3)
    case.itf.set_wind_request_context({}, hub_height=200.)
    with pytest.raises(interface.WindRequestError, match="height"):
        case.itf.init()
    assert not case.spawns


def test_failed_readback_restores_input_and_preserves_failure_evidence(case, monkeypatch):
    before = case.inflow.read_bytes()
    monkeypatch.setattr(interface, "write_inflow_info", lambda path, speed: case.edit(path, HWindSpeed=4.))
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_speed=11.)
    assert case.inflow.read_bytes() == before
    assert saved(rejected.value.record)["status"] == "rejected"
    assert not case.spawns


def test_executable_failure_is_saved_without_claiming_application(case):
    case.itf._path_to_fastfarm_exe = None
    with pytest.raises(RuntimeError, match="未配置"):
        case.itf.init(wind_speed=11.)
    assert saved(case.itf.wind_record)["status"] == "failed"
    assert case.itf.wind_record["application"]["wind_speed"]["status"] == "not_applied"
    assert not case.spawns


def test_environment_cannot_silently_replace_caller_speed(case):
    case.itf.set_wind_request_context({"wind_speed": 11.})
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_speed=8.)
    assert rejected.value.record["application"]["wind_speed"]["status"] == "not_applied"
    assert not case.spawns


@pytest.mark.parametrize("caller", [True, np.bool_(True)])
def test_boolean_caller_speed_is_not_equal_to_forwarded_number(case, caller):
    case.itf.set_wind_request_context({"wind_speed": caller})
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_speed=1.)
    assert saved(rejected.value.record)["application"]["wind_speed"]["status"] == "not_applied"
    assert not case.spawns


@pytest.mark.parametrize("wind_type", [1, 3])
@pytest.mark.parametrize("duration", [0., -1., float("nan"), float("inf"), True, "6"])
def test_invalid_episode_duration_rejected_before_inputs_change(case, wind_type, duration):
    case.edit(case.inflow, WindType=wind_type)
    before = case.inflow.read_bytes()
    case.itf.set_wind_request_context({"wind_speed": 11. if wind_type == 1 else None},
                                      episode_duration_s=duration)
    with pytest.raises(interface.WindRequestError) as rejected:
        case.itf.init(wind_speed=11. if wind_type == 1 else None)
    record = saved(rejected.value.record)
    assert record["application"]["episode_duration_s"]["status"] == "not_applied"
    assert case.inflow.read_bytes() == before
    assert not case.spawns


def test_driver_audit_survives_generated_case_purge(case, monkeypatch):
    driver = make_driver(case)
    record = driver.reset(wind_speed=11.)["wind_conditions"]
    driver.case_dir = str(case.path.parent.parent)
    monkeypatch.setattr(driver_module, "_PENDING_PURGE", [])
    driver._purge_case()
    assert not case.path.exists()
    assert saved(record) == record


def test_all_invalid_runtime_samples_do_not_become_zero_wind(case):
    case.itf.init()
    case.peer.samples.append(([np.nan] * 3, [np.nan] * 3))
    case.itf.update_command()
    for item in case.itf.wind_record["statistics"]["turbine_hub"]["per_turbine"].values():
        assert item["count"] == 0 and item["invalid_count"] == 1
        assert item["mean_mps"] is None and item["ti_percent"] is None
        assert item["direction_mean_deg"] is None
    saved(case.itf.wind_record)


def test_existing_small_template_can_be_inspected_without_starting_solver():
    itf = interface.FastFarmInterface.__new__(interface.FastFarmInterface)
    itf._simul_file = str(Path(simul_utils.TEMPLATE_DIR.format("fastfarm")) / "FarmInputs/Case.fstf")
    result = itf._inspect_wind_inputs()
    assert result["settings"]["WindType"] == 1
    assert result["settings"]["PropagationDir"] == 0.
    assert result["turbines"][0]["initial_nacelle_yaw_deg"] == 0.
    assert len(result["farm_file"]["sha256"]) == 64


@pytest.mark.parametrize("fault", ["missing", "malformed"])
def test_driver_input_read_failure_still_saves_rejection(case, fault):
    if fault == "missing":
        case.path.unlink()
    else:
        case.inflow.write_text("invalid input", encoding="utf-8")
    driver = make_driver(case)
    with pytest.raises(interface.WindRequestError) as rejected:
        driver.reset()
    assert saved(rejected.value.record)["application"]["loaded_inputs"]["status"] == "not_applied"
    assert not case.spawns

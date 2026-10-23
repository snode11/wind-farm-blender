"""Exercise native workflow option builders without launching Blender or a solver."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import runtime


@pytest.fixture
def workflow(monkeypatch):
    props = NS(**{name: lambda **kwargs: kwargs for name in (
        "BoolProperty", "EnumProperty", "FloatProperty", "StringProperty", "IntProperty")})
    bpy = NS(types=NS(PropertyGroup=object, Operator=object, Panel=object), props=props,
             path=NS(abspath=lambda path: path))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "wfrl_blender.preferences", NS(
        get_preferences=lambda context: NS(default_scene="/fixture/scene.yaml")))
    root = Path(__file__).resolve().parents[1] / "wfrl_blender"

    def load(name, relative):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    module = load("wfrl_blender.operators.workflow", "operators/workflow.py")
    operator = load("wfrl_blender.operators._workflow_run_test", "operators/run.py")
    panel = load("wfrl_blender.panels._workflow_run_test", "panels/run.py")
    fields = module.WFRL_WorkflowSettings.__annotations__
    settings = NS(**{key: value.get("default") for key, value in fields.items()})
    context = NS(scene=NS(wfrl_workflow=settings))
    return NS(module=module, operator=operator, panel=panel, context=context, fields=fields)


@pytest.mark.parametrize("episode", [0, 1, 3, 128])
def test_interactive_start_command_transmits_episode_budget_without_changing_total(workflow, monkeypatch, episode):
    from wfrl.blender_bridge.workflow import validate_run_options

    workflow.context.scene.wfrl_workflow.episode_steps = episode
    sent = []
    monkeypatch.setattr(runtime, "desired_mode", lambda: "interactive_training")
    monkeypatch.setattr(runtime, "send_command", lambda action, args: sent.append((action, args)))
    operator = workflow.operator.WFRL_OT_BackendRun()
    operator.action = "start"
    assert operator.execute(workflow.context) == {"FINISHED"}
    action, arguments = sent[0]
    assert action == "start" and arguments["mode"] == "interactive_training"
    options = dict(arguments["options"])
    assert options.pop("scene") == "/fixture/scene.yaml"
    assert options == dict(iters=8, n_steps=64, warmup_steps=4, seed=0, episode_steps=episode)
    validate_run_options("interactive_training", options)
    assert workflow.fields["episode_steps"]["default"] == 0
    assert workflow.fields["episode_steps"]["min"] == 0


@pytest.mark.parametrize("mode", ["interactive_training", "formal_training", "replay", "demo"])
def test_episode_field_is_only_shown_and_sent_for_interactive(workflow, monkeypatch, mode):
    workflow.context.scene.wfrl_workflow.checkpoint = "/fixture/model.pt"
    workflow.context.scene.wfrl_workflow.episode_steps = 17
    properties = []

    class Layout:
        def column(self): return self
        def row(self): return self
        def prop(self, settings, name): properties.append(name)
        def label(self, **kwargs): pass
        def operator(self, *args, **kwargs): return NS()

    monkeypatch.setattr(runtime, "desired_mode", lambda: mode)
    monkeypatch.setattr(runtime, "configuration_editable", lambda: True)
    monkeypatch.setattr(runtime, "allows_command", lambda _: True)
    monkeypatch.setattr(runtime, "get_state", lambda: NS(run_status="READY"))
    panel = workflow.panel.WFRL_PT_WorkflowRun()
    panel.layout = Layout()
    panel.draw(workflow.context)
    options = workflow.module.launch_options(workflow.context, mode)
    assert ("episode_steps" in properties) == (mode == "interactive_training")
    assert ("episode_steps" in options) == (mode == "interactive_training")
    if mode == "interactive_training":
        assert options["episode_steps"] == 17

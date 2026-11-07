"""Native RNA registration and Interactive launch options; no solver or scene file writes."""

from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wfrl_blender.operators import workflow


def main():
    bpy.utils.register_class(workflow.WFRL_WorkflowSettings)
    workflow.register_properties()
    get_preferences = workflow.get_preferences
    workflow.get_preferences = lambda context: SimpleNamespace(default_scene="/fixture/scene.yaml")
    try:
        settings = bpy.context.scene.wfrl_workflow
        assert settings.episode_steps == 0
        defaults = workflow.launch_options(bpy.context, "interactive_training")
        assert defaults == dict(scene="/fixture/scene.yaml", iters=8, n_steps=64,
                                warmup_steps=4, seed=0, episode_steps=0)
        settings.episode_steps = 17
        assert workflow.launch_options(bpy.context, "interactive_training")["episode_steps"] == 17
        assert "episode_steps" not in workflow.launch_options(bpy.context, "formal_training")
        settings.episode_steps = -1
        assert settings.episode_steps == 0
        print("INTERACTIVE_OPTIONS_REGRESSION_PASS")
    finally:
        workflow.get_preferences = get_preferences
        workflow.unregister_properties()
        bpy.utils.unregister_class(workflow.WFRL_WorkflowSettings)


if __name__ == "__main__":
    main()

"""Studio CLI defaults and explicit overrides without a GUI or simulation."""
from unittest.mock import Mock

import pytest

from wfrl.studio import app as module


@pytest.fixture(params=["argv", "sys.argv"])
def run_studio(monkeypatch, request):
    monkeypatch.setattr(module, "load_scene", Mock(return_value=object()))
    application = Mock()
    application.exec_.return_value = 0
    monkeypatch.setattr(module.QtWidgets, "QApplication", Mock(return_value=application))
    window = Mock()
    monkeypatch.setattr(module, "StudioWindow", window)

    def run(options):
        argv = ["--scene", "scene.yaml", *options]
        if request.param == "sys.argv":
            monkeypatch.setattr(module.sys, "argv", ["wfrl.studio.app", *argv])
            result = module.main()
        else:
            result = module.main(argv)
        assert result == 0
        return window.call_args.kwargs["train_kw"]

    return run


@pytest.mark.parametrize("options, expected", [
    ([], (8, 32, 4)),
    (["--fast"], (2, 16, 1)),
])
def test_training_defaults(run_studio, options, expected):
    values = run_studio(options)
    assert (values["iters"], values["n_steps"], values["warmup_steps"]) == expected


@pytest.mark.parametrize("option, key, value", [
    ("--iters", "iters", 7),
    ("--n-steps", "n_steps", 23),
    ("--warmup", "warmup_steps", 0),
])
@pytest.mark.parametrize("equals", [False, True], ids=["space", "equals"])
@pytest.mark.parametrize("fast_first", [False, True], ids=["fast-last", "fast-first"])
def test_fast_preserves_explicit_training_values(run_studio, option, key, value,
                                                equals, fast_first):
    explicit = [f"{option}={value}"] if equals else [option, str(value)]
    options = ["--fast", *explicit] if fast_first else [*explicit, "--fast"]
    assert run_studio(options)[key] == value


def test_fast_preserves_explicit_values_equal_to_regular_defaults(run_studio):
    values = run_studio(["--fast", "--iters=8", "--n-steps", "32", "--warmup=4"])
    assert (values["iters"], values["n_steps"], values["warmup_steps"]) == (8, 32, 4)


def test_fast_help_describes_preset(capsys):
    with pytest.raises(SystemExit) as caught:
        module.main(["--help"])
    assert caught.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "iters=2 n_steps=16 warmup=1" in help_text

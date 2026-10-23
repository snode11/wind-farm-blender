"""普通 Python 发布包在源码树之外提供内置机型资源。"""
from __future__ import annotations

from email.parser import BytesParser
from importlib.metadata import version
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile

from packaging.requirements import Requirement
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _python(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-B", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(params=["wheel", "sdist-wheel"])
def built_wheel(request, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    shutil.copytree(
        ROOT / "wfrl",
        source / "wfrl",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    for pattern in (
        "pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in",
        "README*", "LICENSE*", "COPYING*", "NOTICE*",
    ):
        for path in ROOT.glob(pattern):
            if path.is_file():
                shutil.copy2(path, source / path.name)

    resources = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in (source / "wfrl" / "turbines").iterdir()
        if path.suffix in {".yaml", ".yml"}
    }
    assert resources, "源码包必须包含内置机型定义"
    excluded = (
        "outputs/package-sentinel.dat",
        "research/package-sentinel.dat",
        "blender_frontend/wfrl_blender/assets/package-sentinel.blend",
    )
    for name in excluded:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a Python package resource")

    build_input = source
    if request.param == "sdist-wheel":
        sdist_dir = tmp_path / "sdist"
        sdist_dir.mkdir()
        _python(
            "-c",
            "import sys; from setuptools.build_meta import build_sdist; "
            "build_sdist(sys.argv[1])",
            str(sdist_dir),
            cwd=source,
        )
        sdists = list(sdist_dir.glob("*.tar.gz"))
        assert len(sdists) == 1
        build_input = sdists[0]
        with tarfile.open(build_input) as archive:
            members = {
                name.split("/", 1)[1]: name
                for name in archive.getnames()
                if "/" in name
            }
            assert not set(excluded).intersection(members)
            for name, data in resources.items():
                assert name in members, f"sdist 缺少机型资源: {name}"
                with archive.extractfile(members[name]) as stream:
                    assert stream.read() == data

    wheel_dir = tmp_path / "wheels"
    _python(
        "-m", "pip", "wheel",
        "--no-build-isolation", "--no-deps", "--no-index", "--no-cache-dir",
        "--disable-pip-version-check", "--wheel-dir", str(wheel_dir),
        str(build_input),
        cwd=tmp_path,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0], resources


def test_wheel_turbines_load_outside_source_tree(built_wheel, tmp_path):
    wheel, resources = built_wheel
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        assert len(metadata_paths) == 1
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
        yaml_requirements = [req for req in requirements if req.name.lower() == "pyyaml"]
        assert len(yaml_requirements) == 1, "wheel 必须声明 PyYAML 依赖"
        assert yaml_requirements[0].marker is None, "内置机型加载不应依赖可选 extra"
        assert version("PyYAML") in yaml_requirements[0].specifier
        assert all(
            name.startswith("wfrl/") or name.split("/", 1)[0].endswith(".dist-info")
            for name in names
        ), "wheel 只能包含 Python 包及其发布元数据"
        for name, data in resources.items():
            assert name in names, f"wheel 缺少机型资源: {name}"
            assert archive.read(name) == data
        archive.extractall(installed)

    dependencies = tmp_path / "dependencies"
    shutil.copytree(
        Path(yaml.__file__).parent,
        dependencies / "yaml",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    isolated_cwd = tmp_path / "outside-source"
    isolated_cwd.mkdir()
    _python(
        "-S",
        "-c",
        textwrap.dedent("""
            import sys
            from pathlib import Path

            installed = Path(sys.argv[1]).resolve()
            dependencies = Path(sys.argv[2]).resolve()
            resources = sys.argv[3:]
            sys.path[:0] = [str(installed), str(dependencies)]

            import wfrl
            import yaml
            from wfrl import turbines

            assert sys.flags.no_site
            assert Path(yaml.__file__).resolve() == dependencies / "yaml/__init__.py"
            assert Path(wfrl.__file__).resolve() == installed / "wfrl/__init__.py"
            assert Path(turbines.__file__).resolve() == installed / "wfrl/turbines/__init__.py"
            assert turbines.available_turbines() == sorted(Path(name).stem for name in resources)
            for name in resources:
                path = installed / name
                lookup = path.stem if path.suffix == ".yaml" else str(path)
                spec = turbines.load_turbine_spec(lookup)
                assert spec.name
                assert Path(spec.source_path).resolve() == path
        """),
        str(installed),
        str(dependencies),
        *sorted(resources),
        cwd=isolated_cwd,
    )

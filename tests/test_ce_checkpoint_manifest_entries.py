"""Checkpoint readers reject invalid manifest entries without legacy fallback."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.train import train_ppo
from wfrl import mappo_sb3


@pytest.fixture(params=["mappo", "ppo"])
def reader(request, tmp_path, monkeypatch):
    directory = tmp_path / "checkpoints"
    directory.mkdir()
    model = directory / "policy"
    normalization = directory / "normalization.pkl"
    legacy = (model.with_suffix(".zip"), normalization)
    for path in legacy:
        path.write_bytes(b"legacy artifact")

    generation = "1" * 32
    pair = (directory / f"policy.{generation}.zip",
            directory / f"normalization.{generation}.pkl")
    document = {"schema": 1, "generation": generation}
    for key, path in zip(("model", "normalization"), pair):
        contents = f"current {key}".encode()
        path.write_bytes(contents)
        document[key] = path.name
        document[key + "_sha256"] = hashlib.sha256(contents).hexdigest()
    manifest = directory / "policy.manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    if request.param == "mappo":
        monkeypatch.setattr(mappo_sb3, "MODEL_PATH", str(model))
        monkeypatch.setattr(mappo_sb3, "VECNORM_PATH", str(normalization))
        resolve, require = mappo_sb3._checkpoint_paths, mappo_sb3._require_checkpoint_files
    else:
        checkpoint = train_ppo.CheckpointPair(model, normalization)
        resolve, require = checkpoint.resolve, checkpoint.require
    return SimpleNamespace(manifest=manifest, pair=pair, legacy=legacy,
                           resolve=resolve, require=require)


def test_absent_manifest_allows_legacy_pair(reader):
    reader.manifest.unlink()
    assert reader.resolve() == reader.legacy
    assert reader.require() == reader.legacy


def test_valid_manifest_selects_generation(reader):
    assert reader.resolve() == reader.pair
    assert reader.require() == reader.pair


def test_dangling_manifest_never_falls_back(reader):
    reader.manifest.unlink()
    reader.manifest.symlink_to(reader.manifest.parent / "missing-manifest.json")
    with pytest.raises(FileNotFoundError):
        reader.resolve()


def test_manifest_directory_never_falls_back(reader):
    reader.manifest.unlink()
    reader.manifest.mkdir()
    with pytest.raises(IsADirectoryError):
        reader.resolve()


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="chmod read denial requires an unprivileged POSIX user")
def test_unreadable_manifest_never_falls_back(reader):
    reader.manifest.chmod(0)
    try:
        with pytest.raises(PermissionError):
            reader.resolve()
    finally:
        reader.manifest.chmod(0o600)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="directory search denial requires an unprivileged POSIX user")
def test_unsearchable_manifest_directory_never_falls_back(reader):
    directory = reader.manifest.parent
    directory.chmod(0)
    try:
        with pytest.raises(PermissionError):
            reader.resolve()
    finally:
        directory.chmod(0o700)


@pytest.mark.parametrize("contents", ["", "{", "null", "[]", "false", "{}"])
def test_invalid_manifest_content_never_falls_back(reader, contents):
    reader.manifest.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        reader.resolve()


def test_manifest_disappearing_during_read_never_falls_back(reader, monkeypatch):
    read_text = Path.read_text

    def remove_before_read(path, *args, **kwargs):
        if path == reader.manifest:
            path.unlink()
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", remove_before_read)
    with pytest.raises(FileNotFoundError):
        reader.resolve()


@pytest.mark.parametrize("relative", [False, True])
def test_manifest_symlink_outside_directory_is_rejected(reader, tmp_path, relative):
    outside = tmp_path / "checkpoints-other"
    outside.mkdir()
    target = outside / reader.manifest.name
    target.write_bytes(reader.manifest.read_bytes())
    reader.manifest.unlink()
    reader.manifest.symlink_to(os.path.relpath(target, reader.manifest.parent)
                               if relative else target)
    with pytest.raises(ValueError, match="outside"):
        reader.resolve()


@pytest.mark.parametrize("component", [0, 1], ids=["model", "normalization"])
def test_artifact_symlink_outside_directory_is_rejected(reader, tmp_path, component):
    outside = tmp_path / "checkpoints-other"
    outside.mkdir()
    path = reader.pair[component]
    target = outside / path.name
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(ValueError, match="outside"):
        reader.resolve()


@pytest.mark.parametrize("key", ["model", "normalization"])
@pytest.mark.parametrize("relative", [False, True])
def test_manifest_cannot_reference_outside_filenames(reader, tmp_path, key, relative):
    outside = tmp_path / "checkpoints-other"
    outside.mkdir()
    document = json.loads(reader.manifest.read_text(encoding="utf-8"))
    path = reader.manifest.parent / document[key]
    target = outside / path.name
    target.write_bytes(path.read_bytes())
    document[key] = os.path.relpath(target, path.parent) if relative else str(target)
    reader.manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError):
        reader.resolve()


@pytest.mark.parametrize("component", [0, 1], ids=["model", "normalization"])
def test_digest_mismatch_never_falls_back(reader, component):
    reader.pair[component].write_bytes(b"different generation")
    with pytest.raises(ValueError, match="does not match"):
        reader.resolve()


@pytest.mark.parametrize("component", [0, 1], ids=["model", "normalization"])
def test_missing_artifact_never_falls_back(reader, component):
    reader.pair[component].unlink()
    with pytest.raises(FileNotFoundError):
        reader.resolve()


@pytest.mark.parametrize("component", ["manifest", "model", "normalization"])
def test_symlink_within_checkpoint_directory_keeps_valid_pair(reader, component):
    path = (reader.manifest if component == "manifest" else
            reader.pair[component == "normalization"])
    target = path.with_name("linked-" + path.name)
    path.rename(target)
    path.symlink_to(target.name)
    assert reader.resolve() == reader.pair
    assert reader.require() == reader.pair

"""Blade response decoding consumes exactly the bytes whose digests were checked."""
import json
from pathlib import Path

from wfrl_blender import imbalance


ASSETS = Path(__file__).resolve().parents[1] / 'wfrl_blender/assets'


def test_analysis_uses_verified_result_bytes(monkeypatch):
    analysis, source = ASSETS / 'blade_response', ASSETS / 'mappo'
    target = analysis / 'results.json'
    raw = target.read_bytes()
    expected = json.loads(raw)
    replacement = json.loads(raw)
    replacement['unverified_marker'] = True
    read_bytes = Path.read_bytes
    calls = []

    def read(path):
        if path == target:
            calls.append(path)
            return raw if len(calls) == 1 else json.dumps(replacement).encode('utf-8')
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    assert imbalance.read_package(analysis, source) == expected
    assert len(calls) == 1


def test_analysis_uses_verified_source_manifest_and_telemetry(monkeypatch):
    analysis, source = ASSETS / 'blade_response', ASSETS / 'mappo'
    expected = json.loads((analysis / 'results.json').read_bytes())
    read_bytes = Path.read_bytes
    calls = {}

    def read(path):
        calls[path] = calls.get(path, 0) + 1
        if path in (source / 'manifest.json', source / 'telemetry.json') and calls[path] > 1:
            return b'{}'
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    assert imbalance.read_package(analysis, source) == expected
    assert calls[source / 'manifest.json'] == calls[source / 'telemetry.json'] == 1

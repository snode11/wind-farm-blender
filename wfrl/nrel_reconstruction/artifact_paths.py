"""Resolve paths recorded before the NREL result-directory consolidation.

Frozen records keep their original spelling and bytes.  Only callers' paths
are relocated; verifying historical code may return a saved snapshot rather
than the current, subsequently edited implementation.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


NREL_DIRECTORY = Path("outputs/nrel-video-single-blade")
STAGE_DIRECTORIES = {
    "20261002-first": "stages/01-first-run",
    "20261002-second": "stages/02-second-run",
    "20261003-diagnostics": "stages/03-diagnostics",
    "20261003-p3-center-scale": "stages/04-p3-center-scale",
    "20261003-p4-greville": "stages/05-p4-greville",
    "20261003-p5-observation": "stages/06-p5-observation",
    "review-for-webgpt-20261002": "review/20261002-initial-materials",
}
MIGRATION_ORIGINALS = Path("review/layout-migration-20261003/originals")
SOURCE_DIRECTORIES = {"wfrl", "scripts", "tests", "docs"}
SOURCE_SNAPSHOTS = (
    "stages/02-second-run/baseline-code",
    "stages/05-p4-greville/implementation",
    "stages/06-p5-observation/implementation",
    "review/20261002-initial-materials/optional_code",
)
REVIEW_FILES = {
    "review-for-webgpt-20261002.zip",
    "20261003-diagnostics-review.zip",
    "20261003-p3-center-scale-review.zip",
    "20261003-p3-center-scale-review.verification.json",
    "20261003-p4-greville-review.zip",
    "20261003-p4-greville-review.zip.sha256",
    "20261003-p5-observation-review.zip",
    "20261003-p5-observation-review.zip.sha256",
    "20261003-p5-observation-review.zip.validation.json",
}


def repository_root(start: str | os.PathLike | None = None) -> Path:
    """Find the enclosing checkout without relying on a script's depth."""
    candidate = Path(start if start is not None else __file__).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists() and (parent / "wfrl").is_dir():
            return parent
    raise FileNotFoundError(f"Cannot find WFRL repository above {candidate}")


def _recorded_relative(value: str | os.PathLike, root: Path) -> Path | None:
    """Extract repository-relative NREL paths, including another checkout."""
    path = Path(value).expanduser()
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index:index + 2] == ("outputs", "nrel-video-single-blade"):
            return Path(*parts[index + 2:])
    if not path.is_absolute() and parts and (parts[0] in STAGE_DIRECTORIES or parts[0] in REVIEW_FILES):
        return path
    try:
        return path.relative_to(root / NREL_DIRECTORY)
    except ValueError:
        return None


def relocated_path(value: str | os.PathLike, *, root: str | os.PathLike | None = None) -> Path:
    """Return the current location of an old absolute or relative artifact path.

    Other relative paths are resolved against the checkout. New paths are idempotent;
    records may be read without modifying their JSON or requiring old symlinks.
    """
    checkout = Path(root).resolve() if root is not None else repository_root()
    relative = _recorded_relative(value, checkout)
    if relative is not None:
        if relative.parts and relative.parts[0] in STAGE_DIRECTORIES:
            relative = Path(STAGE_DIRECTORIES[relative.parts[0]], *relative.parts[1:])
        elif relative.parts and (relative.parts[0] in REVIEW_FILES
                                 or relative.parts[0].endswith(".zip")
                                 or ".zip." in relative.parts[0]):
            relative = Path("review", relative)
        return (checkout / NREL_DIRECTORY / relative).resolve()
    path = Path(value).expanduser()
    return (path if path.is_absolute() else checkout / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _recorded_source_relative(value: str | os.PathLike, checkout: Path) -> Path | None:
    """Recognize a signed repository source in this checkout or its snapshots.

    Old absolute source paths must not take precedence over the new checkout,
    even when the original machine's working directory is still available.
    Artifact paths are handled separately by the result-directory mapping.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        return path if path.parts and path.parts[0] in SOURCE_DIRECTORIES else None
    try:
        relative = path.relative_to(checkout)
        return relative if relative.parts and relative.parts[0] in SOURCE_DIRECTORIES else None
    except ValueError:
        pass
    for index, part in enumerate(path.parts):
        if part not in SOURCE_DIRECTORIES:
            continue
        relative = Path(*path.parts[index:])
        candidates = [checkout / relative,
                      checkout / NREL_DIRECTORY / MIGRATION_ORIGINALS / "repository" / relative]
        candidates.extend(checkout / NREL_DIRECTORY / snapshot / relative
                          for snapshot in SOURCE_SNAPSHOTS)
        if any(candidate.is_file() for candidate in candidates):
            return relative
    return None


def frozen_path(value: str | os.PathLike, expected_sha256: str, *,
                root: str | os.PathLike | None = None) -> Path:
    """Locate bytes matching a historical signature, or fail explicitly.

    The current relocated file is tried first, then its visible migration
    original.  Saved experiment implementations can verify a historical source
    signature after current code changes, without claiming current source
    identity.  The returned path tells callers which bytes were verified.
    """
    checkout = Path(root).resolve() if root is not None else repository_root()
    relative = _recorded_relative(value, checkout)
    source_relative = (_recorded_source_relative(value, checkout)
                       if relative is None else None)
    current = (checkout / source_relative if source_relative is not None
               else relocated_path(value, root=checkout))
    candidates = [current]
    nrel_root = checkout / NREL_DIRECTORY
    if relative is not None:
        # Backup paths retain the pre-migration stage names.
        for old, new in STAGE_DIRECTORIES.items():
            try:
                relative = Path(old) / relative.relative_to(new)
                break
            except ValueError:
                pass
        candidates.append(nrel_root / MIGRATION_ORIGINALS / relative)
    try:
        source_relative = current.relative_to(checkout)
    except ValueError:
        source_relative = None
    if source_relative is not None and source_relative.parts[0] in SOURCE_DIRECTORIES:
        candidates.append(nrel_root / MIGRATION_ORIGINALS / "repository" / source_relative)
        for snapshot in SOURCE_SNAPSHOTS:
            candidates.append(nrel_root / snapshot / source_relative)
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file() and _sha256(candidate) == expected_sha256:
            return candidate
    raise RuntimeError(f"No saved artifact matches frozen SHA256 {expected_sha256}: {value}")

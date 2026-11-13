"""Publish complete files and new result directories from system temporary storage."""
from contextlib import contextmanager
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def write_text(path, text, *, exclusive=False):
    """A failed write leaves the destination intact; exclusive writes never replace it."""
    path = Path(path)
    with TemporaryDirectory(prefix='wfrl-lidar-write-') as directory:
        temporary = Path(directory) / 'payload'
        temporary.write_text(text, encoding='utf-8')
        if exclusive:
            os.link(temporary, path)
        else:
            temporary.replace(path)


@contextmanager
def new_directory(path):
    """Expose a result directory only after all producers return successfully.

    Publication requires temporary storage and the destination on the same
    filesystem. Cross-filesystem moves fail without publishing partial output.
    """
    path = Path(path)
    if os.path.lexists(path):
        raise FileExistsError(path)
    with TemporaryDirectory(prefix='wfrl-lidar-result-') as directory:
        staging = Path(directory) / 'result'
        staging.mkdir()
        yield staging
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(path):
            raise FileExistsError(path)
        staging.rename(path)


@contextmanager
def updated_files(path):
    """Stage flat reports and restore earlier files if publication raises.

    Each file replacement is atomic. A group update requires exclusive access
    to its destination; readers can see different complete files during commit.
    """
    path = Path(path)
    if not path.exists():
        with new_directory(path) as staging:
            yield staging
        return
    if not path.is_dir():
        raise NotADirectoryError(path)
    with TemporaryDirectory(prefix='wfrl-lidar-update-') as directory:
        staging = Path(directory) / 'result'; staging.mkdir()
        backups = Path(directory) / 'backups'; backups.mkdir()
        yield staging
        files = sorted(staging.iterdir())
        for file in files:
            if not file.is_file():
                raise ValueError('Only flat output files can be published')
            target = path / file.name
            if os.path.lexists(target):
                os.link(target, backups / file.name, follow_symlinks=False)
        installed = []
        try:
            for file in files:
                file.replace(path / file.name)
                installed.append(file.name)
        except BaseException:
            for name in reversed(installed):
                if os.path.lexists(backups / name):
                    (backups / name).replace(path / name)
                else:
                    (path / name).unlink()
            raise

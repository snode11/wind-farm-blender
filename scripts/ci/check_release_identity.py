"""Check or render a Markdown identity summary from release and installation records."""
import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
START = '<!-- release-identity:start -->'
END = '<!-- release-identity:end -->'


def render_summary(release, installed, release_link, installation_link):
    if release.get('schema') != 'wfrl.extension-release.v1':
        raise ValueError('无法识别发布身份记录')
    if installed.get('schema') != 'wfrl.daily-installation.v1':
        raise ValueError('无法识别安装身份记录')
    archive = release['archive']
    same = installed['same_as_public_archive']
    if not isinstance(same, bool):
        raise ValueError('同一载荷标识必须是布尔值')
    if same and (installed.get('status') != 'PASS' or installed.get('file_mismatches') != 0
                 or installed['version'] != release['version']
                 or installed['archive_sha256'] != archive['sha256']
                 or installed['installed_files_checked'] != archive['file_count']):
        raise ValueError('安装记录宣称同一载荷，但核对状态、版本、摘要或文件数不符')
    identity = '与公开包同一载荷' if same else '独立载荷，按安装记录核对'
    return '\n'.join((
        START,
        '| 对象 | 版本 | 身份依据 |',
        '| --- | --- | --- |',
        f"| NREL 公开安装包 | **{release['version']}** | "
        f"[{archive['name']}]({archive['download_url']})；{archive['file_count']} 个文件；"
        f'[交付身份]({release_link}) |',
        f"| 开发机日常安装 | **{installed['version']}** | {identity}；"
        f"{installed['installed_files_checked']} 个文件、"
        f"{installed['loaded_module_files_checked']} 个新进程模块已核对；"
        f'[安装身份]({installation_link}) |',
        END,
    ))


def synchronize(root, release_path, installation_path, document, *, write=False):
    root = Path(root).resolve()
    release_path, installation_path, document = (
        path.resolve() if path.is_absolute() else root / path
        for path in map(Path, (release_path, installation_path, document)))
    contents = document.read_text(encoding='utf-8')
    if (contents.count(START) != 1 or contents.count(END) != 1
            or contents.index(START) >= contents.index(END)):
        raise ValueError('身份摘要必须包含一对顺序正确的唯一标记')
    release = json.loads(release_path.read_text(encoding='utf-8'))
    installed = json.loads(installation_path.read_text(encoding='utf-8'))
    relative = lambda path: Path(os.path.relpath(path, document.parent)).as_posix()
    summary = render_summary(release, installed, relative(release_path), relative(installation_path))
    start, end = contents.index(START), contents.index(END) + len(END)
    expected = contents[:start] + summary + contents[end:]
    changed = expected != contents
    if changed:
        if not write:
            raise ValueError('文档身份摘要与发布／安装记录不一致；使用 --write 同步摘要')
        document.write_text(expected, encoding='utf-8')
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--installation', type=Path, default=Path('publication/daily-installation.json'))
    parser.add_argument('--document', type=Path, default=Path('README.md'))
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    try:
        changed = synchronize(args.root, args.release, args.installation, args.document, write=args.write)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'发布身份检查失败：{exc}\n')
    print('发布身份摘要已同步' if changed else '发布身份摘要一致')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run installed-extension window checks and their independent reopen check.

Unconfirmed process cleanup fails the check and stops subsequent launches.
The failure's *.cleanup.json record can be retried through
scripts.blender.run_regressions.retry_cleanup(path).
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time


if __name__ == '__main__':
    sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from wfrl.blender_bridge.formal_process import OwnedProcesses
from scripts.blender.run_regressions import (
    ProcessCleanupError, cleanup_pending, cleanup_process, finite_json_float,
    reject_json_constant, traceback_exceptions,
)

TEST_ROOT = ROOT / 'blender_frontend/tests/blender'
VALIDATOR = Path(__file__).with_name('validate_frontend_package.py').resolve()
write_json = runpy.run_path(str(ROOT / 'blender_frontend/wfrl_blender/atomic_json.py'))['write_json']
EXAMPLE = Path('assets/examples/mappo_reconstruction_split.blend')
ADDON_ID = 'wfrl_blender'
REPOSITORY_ID = 'wfrl_frontend'
MODULE = f'bl_ext.{REPOSITORY_ID}.{ADDON_ID}'
PROFILE_DIRS = {
    'BLENDER_USER_CONFIG': 'config',
    'BLENDER_USER_SCRIPTS': 'scripts',
    'BLENDER_USER_DATAFILES': 'datafiles',
}
BLENDER_FLAGS = ('--disable-autoexec', '--python-exit-code', '1',
                 '--python-expr', 'import sys; sys.dont_write_bytecode=True')


@dataclass(frozen=True)
class InstalledCheck:
    script: str
    mode: str
    report: str
    marker: str
    timeout_s: int
    load_cli_scene: bool = True
    depends_on: str | None = None
    saved_scene: str | None = None


CHECKS = (
    InstalledCheck('mappo_full_view_default_window.py', 'visible', 'full-view-window-validation.json',
                   'MAPPO_FULL_VIEW_WINDOW_PASS', 210),
    InstalledCheck('mappo_same_source_navigation_window.py', 'visible', 'navigation-window-validation.json',
                   'MAPPO_SAME_SOURCE_NAVIGATION_WINDOW_PASS', 330),
    InstalledCheck('mappo_same_source_texture_window.py', 'visible', 'window-validation.json',
                   'MAPPO_SAME_SOURCE_TEXTURE_WINDOW_PASS', 450, saved_scene='same-source-saved.blend'),
    InstalledCheck('mappo_same_source_texture_reopen.py', 'background', 'independent-reopen-validation.json',
                   'MAPPO_SAME_SOURCE_TEXTURE_INDEPENDENT_REOPEN_PASS', 240,
                   depends_on='mappo_same_source_texture_window.py'),
    InstalledCheck('split_texture_sidebar_window.py', 'visible', 'sidebar-window-validation.json',
                   'SPLIT_TEXTURE_SIDEBAR_WINDOW_PASS', 330),
    InstalledCheck('reconstruction_workflow_window.py', 'visible', 'reconstruction-workflow-window-validation.json',
                   'RECONSTRUCTION_WORKFLOW_WINDOW_PASS', 510, load_cli_scene=False),
)


@dataclass(frozen=True)
class Profile:
    directory: Path
    environment: dict[str, str]
    addon_root: Path


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    timed_out: bool = False
    interrupted: bool = False
    error: str | None = None


def select_tests(names):
    if names is None:
        return CHECKS
    names = [name if name.endswith('.py') else name + '.py' for name in names]
    unknown = set(names) - {check.script for check in CHECKS}
    if unknown:
        raise ValueError('Unknown installed checks: ' + ', '.join(sorted(unknown)))
    if len(set(names)) != len(names):
        raise ValueError('Duplicate installed checks are not allowed')
    return tuple(check for check in CHECKS if check.script in names)


def read_object(path):
    data = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_json_constant,
                      parse_float=finite_json_float)
    if not isinstance(data, dict):
        raise ValueError(f'Expected a JSON object in {path}')
    return data


def isolated_path(value, *, within=None):
    path = Path(value).expanduser().resolve()
    home = Path.home()
    defaults = [home / 'Library/Application Support/Blender', home / '.config/blender']
    if config_home := os.environ.get('XDG_CONFIG_HOME'):
        defaults.append(Path(config_home) / 'blender')
    if appdata := os.environ.get('APPDATA'):
        defaults.append(Path(appdata) / 'Blender Foundation/Blender')
    # Blender paths can have case aliases on macOS and Windows.
    folded = Path(str(path).casefold())
    if any(folded.is_relative_to(Path(str(default.resolve()).casefold())) for default in defaults):
        raise ValueError(f'Refusing daily Blender installation or configuration: {path}')
    if within is not None and not path.is_relative_to(within):
        raise ValueError(f'Path escapes the isolated profile {within}: {path}')
    return path


def load_profile(directory):
    directory = isolated_path(directory)
    environment = read_object(isolated_path(directory / 'environment.json', within=directory))
    required = PROFILE_DIRS.keys() | {'WFRL_ADDON_MODULE'}
    roots = {'BLENDER_USER_RESOURCES', 'BLENDER_USER_EXTENSIONS'}
    # The package validator records the installed root it checked; ZIP preparation leaves it out.
    optional = roots | {'WFRL_EXPECTED_ADDON_ROOT'}
    if not required <= environment.keys() <= required | optional or any(
            not isinstance(value, str) or not value for value in environment.values()):
        raise ValueError('Invalid isolated installation environment.json')
    recorded_root = environment.pop('WFRL_EXPECTED_ADDON_ROOT', None)
    module = environment['WFRL_ADDON_MODULE']
    namespace = module.split('.')
    if module.casefold().split('.')[:2] == ['bl_ext', 'user_default']:
        raise ValueError(f'Refusing daily Blender module: {module}')
    if (len(namespace) != 3 or namespace[0] != 'bl_ext' or namespace[2] != ADDON_ID
            or not namespace[1].isidentifier()):
        raise ValueError(f'Invalid installed extension module: {module}')
    environment.setdefault('BLENDER_USER_RESOURCES', str(Path(environment['BLENDER_USER_CONFIG']).expanduser().resolve().parent))
    environment.setdefault('BLENDER_USER_EXTENSIONS', str(Path(environment['BLENDER_USER_RESOURCES']) / 'extensions'))
    for key in (*PROFILE_DIRS, 'BLENDER_USER_RESOURCES', 'BLENDER_USER_EXTENSIONS'):
        value = Path(environment[key]).expanduser()
        if not value.is_absolute():
            raise ValueError(f'Installed profile path must be absolute: {key}')
        path = isolated_path(value, within=directory)
        if not path.is_dir():
            raise ValueError(f'Installed profile directory is missing: {key} ({path})')
        environment[key] = str(path)
    config = Path(environment['BLENDER_USER_CONFIG'])
    repo = Path(environment['BLENDER_USER_EXTENSIONS'])
    addon = isolated_path(repo / ADDON_ID, within=repo)
    for root, relative in ((config, 'userpref.blend'), (addon, '__init__.py'),
                           (addon, 'blender_manifest.toml'), (addon, EXAMPLE)):
        path = isolated_path(root / relative, within=root)
        if not path.is_file():
            raise ValueError(f'Installed profile file is missing: {path}')
    if recorded_root is not None and Path(recorded_root).expanduser().resolve() != addon:
        raise ValueError(f'Recorded WFRL_EXPECTED_ADDON_ROOT {recorded_root} is not the installed extension {addon}')
    return Profile(directory, environment, addon)


def isolated_environment(scratch, inherited=None):
    inherited = os.environ if inherited is None else inherited
    env = {key: value for key, value in inherited.items()
           if not key.startswith(('BLENDER_USER_', 'BLENDER_SYSTEM_', 'WFRL_', 'PYTHON'))}
    scratch.mkdir()
    env.update(TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch), XDG_CACHE_HOME=str(scratch),
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    return env


def run_process(command, env, log_path, timeout_s):
    owner = OwnedProcesses(env_key='WFRL_INSTALLED_CHECK_RUN_TOKEN')
    process = None
    timed_out, interrupted, error = False, False, None
    with log_path.open('xb') as log:
        try:
            process = owner.start(command, cwd=log_path.parent, env=env, stdout=log,
                                  stderr=subprocess.STDOUT)
            process.wait(timeout=timeout_s)
            if owner.alive():
                error = 'Installed check left live descendants after its launcher exited'
        except subprocess.TimeoutExpired:
            timed_out = True
        except KeyboardInterrupt:
            interrupted = True
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        finally:
            process = process if process is not None else owner.process
            if process is not None:
                try:
                    cleanup_process(process, owner, log_path.with_suffix('.cleanup.json'))
                except ProcessCleanupError as exc:
                    error = '; '.join(part for part in (error, str(exc)) if part)
    return ProcessResult(process.returncode if process is not None else None,
                         timed_out=timed_out, interrupted=interrupted, error=error)


def has_marker(log, marker):
    return any(line == marker or line.startswith(marker + ' ') for line in log.splitlines())


def setup_command(command, env, log, marker=None, *, timeout_s=240):
    process = run_process(command, env, log, timeout_s)
    detail = f'; {process.error}' if process.error else ''
    if process.interrupted:
        raise KeyboardInterrupt('Installation interrupted by caller' + detail)
    if process.timed_out:
        raise RuntimeError(f'Installation step exceeded {timeout_s}s{detail}. See {log}')
    text = log.read_text(encoding='utf-8', errors='replace')
    if (process.returncode != 0 or process.error or traceback_exceptions(text)
            or (marker and not has_marker(text, marker))):
        raise RuntimeError(f'Installation step failed (exit {process.returncode}){detail}. See {log}')


def prepare_installation(blender, output, archive=None):
    directory = output / 'package'
    resources = directory / 'profile'
    scratch = output / 'install-tmp'
    env = isolated_environment(scratch)
    if archive is None:
        setup_command([sys.executable, '-B', str(VALIDATOR), '--blender', str(blender),
                       '--output', str(directory)], env, output / 'package.log',
                      'FRONTEND_PACKAGE_VALIDATION_PASS', timeout_s=1800)
    else:
        overrides = {key: str(resources / name) for key, name in PROFILE_DIRS.items()}
        repository = resources / 'extensions'
        for path in (*map(Path, overrides.values()), repository):
            path.mkdir(parents=True)
        overrides.update(BLENDER_USER_RESOURCES=str(resources), BLENDER_USER_EXTENSIONS=str(repository))
        overrides['WFRL_ADDON_MODULE'] = MODULE
        env.update(overrides)
        command = [str(blender), *BLENDER_FLAGS]
        setup_command([*command, '--command', 'extension', 'repo-add', REPOSITORY_ID,
                       '--name', 'WFRL Isolated Window Checks', '--directory', str(repository), '--clear-all'],
                      env, directory / 'repo-add.log')
        setup_command([*command, '--command', 'extension', 'install-file', '-r', REPOSITORY_ID,
                       '--enable', str(archive)], env, directory / 'install-file.log',
                      f'STATUS Installed "{ADDON_ID}"')
        setup_command([str(blender), '--background', *BLENDER_FLAGS, '--python', str(VALIDATOR), '--',
                       '--inside-probe', 'module', '--repository', str(repository),
                       '--output', str(directory / 'module-probe.json')],
                      env, directory / 'module-probe.log', 'FRONTEND_INSTALLED_MODULE_PROBE_PASS')
        write_json(directory / 'environment.json', overrides)
    return load_profile(directory)


def prepare_case(check, blender, profile, case, prerequisite):
    output = case / 'artifacts'
    env = isolated_environment(case / 'tmp')
    env.update(profile.environment, WFRL_EXPECTED_ADDON_ROOT=str(profile.addon_root),
               WFRL_TEST_OUTPUT=str(output), WFRL_QUIT_AFTER_TEST='1')
    if prerequisite is None:
        scene = case / EXAMPLE.name
        shutil.copyfile(profile.addon_root / EXAMPLE, scene)
    else:
        scene = Path(prerequisite['saved_scene'])
        env['WFRL_EXPECTED_WINDOW_REPORT'] = prerequisite['report']
    env['WFRL_SPLIT_INPUT'] = str(scene)
    if check.saved_scene:
        env['WFRL_SPLIT_SAVE'] = str(output / check.saved_scene)
    command = [str(blender)]
    if check.mode == 'background':
        command.append('--background')
    command.extend(BLENDER_FLAGS)
    if check.load_cli_scene:
        command.append(str(scene))
    command.extend(['--python', str(TEST_ROOT / check.script)])
    return command, env, output / check.report


def evaluate(check, process, report, log):
    reasons = []
    if process.error:
        reasons.append(process.error)
    if exceptions := traceback_exceptions(log):
        reasons.append(f'Python exception diagnostics: {exceptions}')
    if process.timed_out:
        reasons.append(f'Process exceeded its {check.timeout_s}s timeout')
    if process.interrupted:
        reasons.append('Interrupted by caller')
    if process.returncode != 0:
        reasons.append(f'Process exit code: {process.returncode}')
    if report is None:
        reasons.append('Missing or invalid final script JSON')
    else:
        status = report.get('status')
        if status in ('SKIP', 'NOT_RUN') and not reasons:
            return status, [report.get('reason') or 'Script reported incomplete coverage']
        if status != 'PASS':
            reasons.append(f'Script JSON status: {status}')
        if report.get('final', True) is not True:
            reasons.append('Script JSON is not final')
        if report.get('error') and status == 'FAIL':
            reasons.append(str(report['error']).strip())
    if not has_marker(log, check.marker):
        reasons.append(f'Missing final completion marker: {check.marker}')
    return ('FAIL', reasons) if reasons else ('PASS', [])


def run_case(check, blender, profile, case, prerequisite):
    command, env, report_path = prepare_case(check, blender, profile, case, prerequisite)
    log_path = case / 'blender.log'
    write_json(case / 'launch.json', dict(script=check.script, mode=check.mode, command=command,
               timeout_s=check.timeout_s, marker=check.marker, log=str(log_path), report=str(report_path),
               environment={key: value for key, value in env.items()
                            if key.startswith(('BLENDER_USER_', 'WFRL_')) or key == 'TMPDIR'}))
    started = time.monotonic()
    process = run_process(command, env, log_path, check.timeout_s)
    log = log_path.read_text(encoding='utf-8', errors='replace')
    try:
        report = read_object(report_path)
    except (OSError, ValueError):
        report = None
    status, reasons = evaluate(check, process, report, log)
    record = dict(status=status, reasons=reasons, returncode=process.returncode,
                  timed_out=process.timed_out, interrupted=process.interrupted,
                  script_status=report.get('status') if report else None, report=str(report_path),
                  log=str(log_path), duration_s=round(time.monotonic() - started, 3))
    if status == 'PASS' and check.saved_scene:
        saved = Path(env['WFRL_SPLIT_SAVE'])
        if not saved.is_file() or saved.is_symlink() or report.get('saved') != str(saved):
            record.update(status='FAIL', reasons=[f'Script did not confirm the expected saved scene: {saved}'])
        else:
            record['saved_scene'] = str(saved)
    if record['status'] == 'FAIL':
        record['log_tail'] = log.splitlines()[-40:]
    return record


def run_checks(selected, blender, profile, output):
    results = {}
    stopped = None
    for check in selected:
        case = output / Path(check.script).stem
        case.mkdir()
        record = dict(script=check.script, mode=check.mode)
        prerequisite = results.get(check.depends_on)
        if profile is None:
            reason = 'Installation setup did not pass'
        elif stopped:
            reason = stopped
        elif check.depends_on and (not prerequisite or prerequisite['status'] != 'PASS'):
            previous = prerequisite['status'] if prerequisite else 'not selected'
            reason = f'Requires PASS from {check.depends_on} ({previous})'
        else:
            reason = None
        if reason:
            record.update(status='NOT_RUN', reasons=[reason])
        else:
            print(f'RUN {check.script} [{check.mode}]', flush=True)
            try:
                record.update(run_case(check, blender, profile, case, prerequisite))
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                log = case / 'blender.log'
                tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-40:] if log.is_file() else []
                record.update(status='FAIL', reasons=[str(exc)], log_tail=tail)
            if cleanup_pending(output):
                stopped = 'Batch stopped after unconfirmed process cleanup'
            elif record.get('interrupted', False):
                stopped = 'Batch interrupted'
        if not (case / 'launch.json').exists():
            write_json(case / 'launch.json', dict(record, command=None))
        results[check.script] = record
        print(f"{record['status']} {check.script}", flush=True)
    return list(results.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path, default=os.environ.get('WFRL_BLENDER_BIN') or
                        shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--output', type=Path, help='New directory for launch metadata, reports and logs')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--installed-profile', type=Path, help='Isolated validation directory containing environment.json')
    source.add_argument('--zip', type=Path, help='Install this extension ZIP into a new isolated profile')
    parser.add_argument('--tests', nargs='+', help='Script names or stems in the installed check table')
    parser.add_argument('--list', action='store_true', help='Print the execution plan without launching Blender')
    args = parser.parse_args(argv)
    try:
        selected = select_tests(args.tests)
    except ValueError as exc:
        parser.error(str(exc))
    installation_source = ('installed-profile' if args.installed_profile else 'zip' if args.zip else 'workspace')
    if args.list:
        print(f'Installation: {installation_source}')
        for check in selected:
            dependency = f' (requires PASS from {check.depends_on})' if check.depends_on else ''
            print(f'{check.script} [{check.mode}]{dependency}')
        return 0
    if args.output is None:
        parser.error('--output is required')
    try:
        profile = load_profile(args.installed_profile) if args.installed_profile else None
        archive = isolated_path(args.zip) if args.zip else None
        if archive is not None and (archive.suffix.lower() != '.zip' or not archive.is_file()):
            raise ValueError(f'Extension ZIP does not exist: {archive}')
        blender = args.blender.expanduser().resolve()
        if not blender.is_file():
            raise ValueError(f'Blender executable does not exist: {blender}')
        output = isolated_path(args.output)
        if args.output.expanduser().is_symlink() or output.exists():
            raise ValueError(f'Output must be a new directory: {output}')
        output.mkdir(parents=True)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    installation = dict(source=installation_source, archive=str(archive) if archive else None)
    try:
        if profile is None:
            print(f'PREPARE {installation_source} installation', flush=True)
            profile = prepare_installation(blender, output, archive)
        installation.update(status='PASS', profile=str(profile.directory), addon_root=str(profile.addon_root),
                            module=profile.environment['WFRL_ADDON_MODULE'])
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        message = str(exc) or 'Installation interrupted by caller'
        installation.update(status='FAIL', reason=message, profile=str(output / 'package'))
        print(message, file=sys.stderr, flush=True)

    tests = run_checks(selected, blender, profile, output)
    counts = dict(Counter(row['status'] for row in tests))
    if installation['status'] == 'FAIL' or 'FAIL' in counts:
        status, code = 'FAIL', 1
    elif counts == {'PASS': len(tests)}:
        status, code = 'PASS', 0
    else:
        status, code = 'NOT_RUN', 2
    result = output / 'result.json'
    write_json(result, dict(status=status, final=True, blender=str(blender),
                            installation=installation, counts=counts, tests=tests))
    print(json.dumps(dict(status=status, counts=counts, result=str(result))), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())

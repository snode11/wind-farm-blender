#!/usr/bin/env python3
"""Launch one dedicated visible benchmark with an external phase watchdog.

The child writes stage deadlines; this process also catches a frozen Blender
main loop that cannot execute its timer-based timeout. It never reuses results.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
PATHS = ('single', 'triple', 'close_reopen', 'playing_switch', 'paused_switch')
EXIT_MEMORY_WARNING = re.compile(r'^Error: Not freed memory blocks: (?P<blocks>\d+), total unfreed memory (?P<mb>\d+(?:\.\d+)?) MB[ \t]*$', re.MULTILINE)
ERROR_PATTERN = re.compile(r'Traceback \(most recent call last\)|(?:^|\n)(?:Error:|ERROR[ :]|FATAL[ :])|ERROR Python script error|Segmentation fault|EXCEPTION_ACCESS_VIOLATION')


def inspect_process_log(content, expected_status=None):
    """Blender UI draw exceptions can leave both exit code and JSON successful."""
    warnings = [{'kind': 'blender_exit_memory_diagnostic', 'message': match.group(0),
        'blocks': int(match.group('blocks')), 'reported_mb': float(match.group('mb')),
        'bytes': round(float(match.group('mb'))*1024*1024),
        'bytes_note': 'Reported MB converted by 1024^2 and rounded; limited by log precision'}
        for match in EXIT_MEMORY_WARNING.finditer(content)]
    errors = ERROR_PATTERN.findall(EXIT_MEMORY_WARNING.sub('', content))
    marker = f'BENCHMARK_RESULT {expected_status} ' if expected_status else None
    missing = [marker] if marker and marker not in content else []
    return {'status': 'ERROR' if errors or missing else 'PASS',
            'error_markers': errors, 'missing_markers': missing, 'warnings': warnings}


def run(args):
    output = Path(args.output).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be new or empty')
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WFRL_TEST_OUTPUT=str(output), WFRL_BENCH_PATH=args.path,
               WFRL_BENCH_LAYOUT=args.layout, WFRL_BENCH_SECONDS=str(args.seconds),
               WFRL_BENCH_PROFILE='1' if args.profile else '0')
    installed_profile = getattr(args, 'installed_profile', None)
    if installed_profile:
        profile_file = Path(installed_profile).expanduser().resolve()/'environment.json'
        profile_env = json.loads(profile_file.read_text())
        required = {'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES', 'WFRL_ADDON_MODULE'}
        if set(profile_env) != required or not profile_env['WFRL_ADDON_MODULE'].startswith('bl_ext.'):
            raise ValueError('Invalid isolated installation environment.json')
        for key in required-{'WFRL_ADDON_MODULE'}:
            if not Path(profile_env[key]).is_dir():
                raise ValueError(f'Installed profile directory is missing: {key}')
        for key in ('WFRL_ADDON_ROOT', 'WFRL_ADDON_PATH', 'PYTHONPATH'):
            env.pop(key, None)
        env.update(profile_env)
    if args.addon_root:
        env['WFRL_ADDON_ROOT'] = str(Path(args.addon_root).expanduser().resolve())
    if args.addon_module:
        env['WFRL_ADDON_MODULE'] = args.addon_module
    if args.fault:
        env['WFRL_BENCH_FAULT'] = args.fault
    command = [args.blender]
    if not installed_profile and not getattr(args, 'no_factory_startup', False):
        command.append('--factory-startup')
    if (args.width is None) != (args.height is None):
        raise ValueError('--width and --height must be supplied together')
    if args.width is not None:
        command += ['--window-geometry', '0', '0', str(args.width), str(args.height)]
    command += ['--python-exit-code', '1',
                '--python', str(ROOT/'blender_frontend/tests/blender/demo_playback_benchmark.py')]
    # Keep metadata in memory until the child's empty-directory check passes.
    launch = json.dumps({'command': command, 'path': args.path,
        'layout': args.layout, 'profile': args.profile, 'geometry': [args.width, args.height],
        'installed_profile': installed_profile, 'addon_module': env.get('WFRL_ADDON_MODULE', 'wfrl_blender')}, indent=2)
    log_path = output.parent/(output.name+'-process.log')
    started = time.monotonic()
    last_data = None
    phase = None
    phase_seen = started
    watchdog_reason = None
    with log_path.open('w') as log:
        child = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            while child.poll() is None:
                result_path = output/'result.json'
                try:
                    data = json.loads(result_path.read_text())
                except (OSError, json.JSONDecodeError):
                    data = None
                if data is not None:
                    last_data = data
                    key = (data.get('stage'), data.get('stage_started_s'))
                    if key != phase:
                        phase, phase_seen = key, time.monotonic()
                    if data.get('termination') is not None:
                        limit = 15.
                    else:
                        limit = float(data.get('configuration', {}).get('timeouts_s', {}).get(data.get('stage'), 30.))+10.
                    if time.monotonic()-phase_seen > limit:
                        watchdog_reason = f"external_{data.get('stage', 'unknown')}_timeout"
                        break
                elif time.monotonic()-started > args.startup_timeout:
                    watchdog_reason = 'external_startup_timeout'
                    break
                if time.monotonic()-started > args.process_timeout:
                    watchdog_reason = 'external_process_timeout'
                    break
                time.sleep(.2)
        except KeyboardInterrupt:
            watchdog_reason = 'active_stop'
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5.)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    (output/'launch.json').write_text(launch)
    result_path = output/'result.json'
    try:
        data = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        data = last_data or {'schema_version': 2, 'results': []}
    log_check = inspect_process_log(log_path.read_text(errors='replace'),
                                   data.get('status') if data.get('termination') else None)
    if watchdog_reason:
        if result_path.exists():
            (output/'interrupted-result.json').write_text(result_path.read_text())
        data.update(status='ABORTED' if watchdog_reason == 'active_stop' else 'INCOMPLETE',
                    termination={'reason': watchdog_reason},
                    external_watchdog={'elapsed_s': time.monotonic()-started,
                                       'partial_result_preserved': bool(last_data)})
    elif not result_path.exists() or data.get('termination') is None or child.returncode != 0:
        data.update(status='ERROR', termination={'reason': 'process_exit_without_verified_result'},
                    process_returncode=child.returncode)
    if log_check['error_markers'] or (not watchdog_reason and log_check['missing_markers']):
        if result_path.exists():
            (output/'driver-result.json').write_text(result_path.read_text())
        data.update(status='ERROR', termination={'reason': 'process_log_validation_failed'},
                    error={'source': 'process_log', 'path': str(log_path),
                           'error_markers': log_check['error_markers'],
                           'missing_markers': log_check['missing_markers']})
    data['process_log_validation'] = log_check
    data.update(process_returncode=child.returncode, process_log=str(log_path))
    result_path.write_text(json.dumps(data, indent=2))
    print(f"{data['status']}: {result_path}")
    return 0 if data['status'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--path', choices=PATHS, default='triple')
    parser.add_argument('--layout', choices=('GRID', 'STRIP'), default='STRIP')
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=float, default=100.)
    parser.add_argument('--width', type=int, help='Explicit Blender CLI window geometry; physical pixels, not native logical width')
    parser.add_argument('--height', type=int, help='Explicit Blender CLI window geometry; supply with --width')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--installed-profile', help='Isolated package validation output containing environment.json; preserves repository preferences')
    parser.add_argument('--no-factory-startup', action='store_true', help='Preserve an explicitly prepared Blender profile')
    parser.add_argument('--addon-root')
    parser.add_argument('--addon-module')
    parser.add_argument('--fault', choices=('active_stop', 'timeout', 'runtime_error', 'zero_draws', 'missing_route', 'unexpected_backward'))
    parser.add_argument('--startup-timeout', type=float, default=180.)
    parser.add_argument('--process-timeout', type=float, default=420.)
    return run(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())

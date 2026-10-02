"""Failure shields: incomplete or invalid viewport observations never pass."""
import pytest

from wfrl_blender.playback_benchmark import PlaybackTarget, evaluate_playback


def evidence(frames=(1, 31, 61, 91, 101), reason='completed_segment', routes=('1', '2', '3')):
    rows = [[i*.1, frame] for i, frame in enumerate(frames)]
    return dict(target=PlaybackTarget(1, 101, terminal_tolerance_frames=10),
        expected_routes=routes, scene_rows=rows,
        draws={slot: [[t+index*.001, f] for t, f in rows] for index, slot in enumerate(routes)},
        started=0., ended=rows[-1][0]+.01,
        termination={'reason': reason, 'previous_frame': frames[-2], 'frame': frames[-1],
                     'playback_running': True, 'driver_seek': False},
        route_states={slot: {'valid': True} for slot in routes})


def test_complete_segment_has_full_window_fps_and_separate_cross_route_times():
    result = evaluate_playback(**evidence())
    assert result['status'] == 'PASS'
    assert result['completed_target_s'] == pytest.approx(100/60)
    assert result['routes']['1']['full_segment_draw_fps'] == pytest.approx(4/.41)
    assert result['route_draw_time_differences']['C1-C3']['shared_frames'] == 5
    assert result['route_draw_time_differences']['C1-C3']['p95_ms'] == pytest.approx(2.)
    assert result['same_frames_all_routes']


def test_legal_frame_skips_and_crossing_do_not_require_exact_end_frame():
    result = evaluate_playback(**evidence((1, 41, 91, 105)))
    assert result['status'] == 'PASS'
    assert result['observed_max_frame'] == 105
    assert result['simulation_progress_s'] == pytest.approx(100/60)


def test_expected_wrap_requires_both_terminal_coverage_and_start_boundary():
    result = evaluate_playback(**evidence((1, 41, 95, 3), 'completed_loop'))
    assert result['status'] == 'PASS'
    assert result['observed_max_frame'] == 95
    assert result['completed_target_s'] == pytest.approx(100/60)


@pytest.mark.parametrize('frames', [(1, 41, 61, 3), (1, 41, 95, 40)])
def test_arbitrary_decreasing_frame_is_not_a_completed_loop(frames):
    result = evaluate_playback(**evidence(frames, 'completed_loop'))
    assert result['status'] == 'FAIL'
    assert any('backward' in message for message in result['failures'])


def test_disabled_loop_expectation_rejects_wrap():
    data = evidence((1, 41, 95, 3), 'completed_loop')
    data['target'] = PlaybackTarget(1, 101, terminal_tolerance_frames=10, loop_expected=False)
    assert evaluate_playback(**data)['status'] == 'FAIL'


@pytest.mark.parametrize('reason,status', [('active_stop', 'ABORTED'), ('measure_timeout', 'INCOMPLETE'),
    ('ready_timeout', 'INCOMPLETE'), ('warm_timeout', 'INCOMPLETE'), ('runtime_error', 'ERROR'),
    ('playback_stopped', 'INCOMPLETE'), ('unexpected_backward', 'INCOMPLETE')])
def test_noncompletion_reason_cannot_pass_even_with_terminal_draws(reason, status):
    result = evaluate_playback(**evidence(reason=reason))
    assert result['status'] == status
    assert result['routes']['1']['full_segment_draw_fps'] is None
    assert result['completed_target_s'] is None


def test_long_trailing_stop_is_counted_and_short_active_fps_is_not_full_fps():
    data = evidence((1, 31, 61), 'measure_timeout')
    data['ended'] = 100.
    result = evaluate_playback(**data)
    assert result['status'] == 'INCOMPLETE'
    assert result['observation_s'] == 100.
    assert result['stopped_progress_s'] == pytest.approx(99.3)
    assert result['effective_progress_s'] == pytest.approx(.7)
    assert result['last_scene_progress_age_s'] == pytest.approx(99.8)
    assert result['routes']['1']['active_draw_fps'] == pytest.approx(10.)
    assert result['routes']['1']['observation_draw_rate_hz'] == pytest.approx(.02)
    assert result['routes']['1']['full_segment_draw_fps'] is None


def test_three_empty_matching_sequences_do_not_pass():
    data = evidence()
    data['draws'] = {slot: [] for slot in data['expected_routes']}
    result = evaluate_playback(**data)
    assert result['same_frames_all_routes']
    assert result['status'] == 'FAIL'
    assert sum('zero draws' in message for message in result['failures']) == 3


def test_missing_route_cannot_pass():
    data = evidence()
    del data['draws']['2']
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert 'C2: zero draws' in result['failures']


def test_static_draw_heartbeats_do_not_prove_forward_progress():
    data = evidence()
    data['draws']['3'] = [[.0, 101], [.2, 101], [.4, 101]]
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert result['routes']['3']['draw_callbacks'] == 3
    assert result['routes']['3']['distinct_draws'] == 1
    assert 'C3: no drawing progression' in result['failures']


def test_scene_completion_without_route_terminal_draw_is_rejected():
    data = evidence()
    data['draws']['3'] = [[.0, 1], [.1, 31], [.4, 61]]
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert 'C3: missing terminal drawing evidence' in result['failures']


def test_stale_terminal_heartbeat_rejects_frozen_viewport():
    data = evidence()
    data['ended'] = 2.
    result = evaluate_playback(**data)
    assert result['status'] == 'FAIL'
    assert 'C1: stale final drawing heartbeat' in result['failures']


@pytest.mark.parametrize('states', [{}, {'1': {'valid': False}, '2': {'valid': True}, '3': {'valid': True}}])
def test_final_camera_route_state_must_be_verified(states):
    data = evidence()
    data['route_states'] = states
    assert evaluate_playback(**data)['status'] == 'FAIL'


def test_fabricated_boundary_event_without_observed_transition_is_rejected():
    data = evidence((1, 31, 61, 95))
    data['termination'] = {'reason': 'completed_segment', 'previous_frame': 95, 'frame': 101}
    assert evaluate_playback(**data)['status'] == 'FAIL'


def test_later_recovery_cannot_replace_original_timeout():
    data = evidence((1, 31, 61), 'measure_timeout')
    result = evaluate_playback(**data)
    result['pause_resume'] = {'status': 'PASS'}
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -.1, 10.])
def test_invalid_timestamps_are_rejected(bad):
    data = evidence()
    data['draws']['3'][2][0] = bad
    assert evaluate_playback(**data)['status'] == 'FAIL'


def test_routes_may_have_different_valid_skip_sequences():
    data = evidence()
    data['draws']['2'].pop(1)
    result = evaluate_playback(**data)
    assert result['status'] == 'PASS'
    assert not result['same_frames_all_routes']
    assert result['route_draw_time_differences']['C1-C2']['shared_frames'] == 4


def test_every_route_needs_own_terminal_evidence_even_after_scene_wrap():
    data = evidence((1, 41, 95, 3), 'completed_loop')
    data['draws']['2'] = [[.001, 1], [.101, 41], [.301, 3]]
    assert evaluate_playback(**data)['status'] == 'FAIL'


def _driver_functions(*names):
    """Exercise driver functions without starting Blender or importing bpy."""
    import ast
    from pathlib import Path
    path = Path(__file__).parent/'blender'/'demo_playback_benchmark.py'
    parsed = ast.parse(path.read_text())
    return compile(ast.Module(body=[node for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names],
        type_ignores=[]), str(path), 'exec')


def test_phase_events_accept_stage_name_without_argument_collision():
    from types import SimpleNamespace as NS
    namespace = {'state': {'events': []}, 'snapshot': lambda: {'frame': 1},
                 'time': NS(perf_counter=lambda: 12.), 'write_result': lambda: None}
    exec(_driver_functions('event', 'stage'), namespace)
    namespace['stage']('warm')
    assert namespace['state']['stage'] == 'warm'
    assert namespace['state']['events'] == [{'event': 'stage', 'context': {'frame': 1}, 'name': 'warm'}]


@pytest.mark.parametrize('initial,desired,expected_calls', [(False, True, ['start']),
    (True, True, []), (True, False, ['cancel']), (False, False, [])])
def test_driver_start_and_pause_are_idempotent_and_record_actual_states(initial, desired, expected_calls):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS
    flags, calls, events = {'playing': initial}, [], []
    area = NS(type='VIEW_3D', regions=[NS(type='WINDOW')])
    window = NS(screen=NS(is_animation_playing=initial, areas=[area]), scene=object())
    def operation(name, value):
        calls.append(name)
        flags['playing'] = value
        window.screen.is_animation_playing = value
        return {'FINISHED'}
    namespace = {'is_playing': lambda: flags['playing'], 'windows': lambda: [window],
        'active_context': lambda: (window, area, area.regions[0]), 'pointer': id,
        'event': lambda name, **data: events.append((name, data)),
        'bpy': NS(context=NS(temp_override=lambda **kwargs: nullcontext()),
            ops=NS(screen=NS(animation_play=lambda: operation('start', True),
                animation_cancel=lambda **kwargs: operation('cancel', False))))}
    exec(_driver_functions('set_playing'), namespace)
    namespace['set_playing'](desired)
    assert calls == expected_calls
    assert events[-1][1]['before'] == initial
    assert events[-1][1]['after'] == desired
    assert set(events[-1][1]['operator_context']) == {'window', 'screen', 'scene', 'area', 'region'}


def test_driver_does_not_assume_animation_play_succeeded():
    from contextlib import nullcontext
    from types import SimpleNamespace as NS
    window = NS(screen=object(), scene=object())
    namespace = {'is_playing': lambda: False, 'active_context': lambda: (window, object(), object()),
        'pointer': id, 'event': lambda *args, **kwargs: None,
        'bpy': NS(context=NS(temp_override=lambda **kwargs: nullcontext()),
            ops=NS(screen=NS(animation_play=lambda: {'CANCELLED'})))}
    exec(_driver_functions('set_playing'), namespace)
    with pytest.raises(RuntimeError, match='requested=True, observed=False'):
        namespace['set_playing'](True)


def _runner():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('benchmark_runner', Path(__file__).resolve().parents[2]/'scripts/blender/run_playback_benchmark.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner_args(tmp_path, code):
    import sys
    from types import SimpleNamespace as NS
    executable = tmp_path/'fake-blender'
    executable.write_text(f'#!{sys.executable}\n'+code)
    executable.chmod(0o755)
    return NS(output=str(tmp_path/'new-output'), blender=str(executable), path='triple',
        layout='STRIP', seconds=100., profile=False, addon_root=None, addon_module=None,
        fault=None, width=820, height=473, startup_timeout=1., process_timeout=2.)


@pytest.mark.parametrize('status,exitcode', [('PASS', 0), ('INCOMPLETE', 1), ('FAIL', 1)])
def test_launcher_requires_a_fresh_terminal_json_not_just_process_exit(tmp_path, status, exitcode):
    import json
    from pathlib import Path
    args = _runner_args(tmp_path,
        "import os,json\nfrom pathlib import Path\n"
        "p=Path(os.environ['WFRL_TEST_OUTPUT'])\n"
        f"(p/'result.json').write_text(json.dumps({{'status': '{status}', 'termination': {{'reason': 'finished'}}}}))\n"
        f"print('BENCHMARK_RESULT {status} result.json')\n")
    assert _runner().run(args) == exitcode
    result = json.loads((Path(args.output)/'result.json').read_text())
    assert result['status'] == status
    assert result['process_returncode'] == 0


def test_launcher_normal_exit_without_result_is_error(tmp_path):
    import json
    from pathlib import Path
    args = _runner_args(tmp_path, 'pass\n')
    assert _runner().run(args) == 1
    result = json.loads((Path(args.output)/'result.json').read_text())
    assert result['status'] == 'ERROR'
    assert result['termination']['reason'] == 'process_exit_without_verified_result'


def test_external_watchdog_records_a_hung_process_as_incomplete(tmp_path):
    import json
    from pathlib import Path
    args = _runner_args(tmp_path, 'import time\ntime.sleep(10)\n')
    args.process_timeout = .01
    assert _runner().run(args) == 1
    result = json.loads((Path(args.output)/'result.json').read_text())
    assert result['status'] == 'INCOMPLETE'
    assert result['termination']['reason'] == 'external_process_timeout'


def test_launcher_rejects_a_preexisting_result_directory(tmp_path):
    from pathlib import Path
    args = _runner_args(tmp_path, 'pass\n')
    Path(args.output).mkdir()
    (Path(args.output)/'result.json').write_text('{"status":"PASS"}')
    with pytest.raises(ValueError, match='new or empty'):
        _runner().run(args)


@pytest.mark.parametrize('flag,value', [('playback_running', False), ('driver_seek', True)])
def test_near_end_seek_or_paused_reset_is_not_expected_playback_wrap(flag, value):
    data = evidence((1, 41, 95, 3), 'completed_loop')
    data['termination'][flag] = value
    assert evaluate_playback(**data)['status'] == 'FAIL'


def test_installed_profile_runner_preserves_repository_preferences(tmp_path):
    import json
    from pathlib import Path
    args = _runner_args(tmp_path,
        "import os,json\nfrom pathlib import Path\n"
        "p=Path(os.environ['WFRL_TEST_OUTPUT'])\n"
        "(p/'result.json').write_text(json.dumps({'status': 'PASS', 'termination': {'reason': 'finished'}}))\n"
        "print('BENCHMARK_RESULT PASS result.json')\n")
    profile = tmp_path/'isolated'
    profile.mkdir()
    environment = {'WFRL_ADDON_MODULE': 'bl_ext.wfrl_frontend.wfrl_blender'}
    for name in ('CONFIG', 'SCRIPTS', 'DATAFILES'):
        directory = profile/name.lower()
        directory.mkdir()
        environment['BLENDER_USER_'+name] = str(directory)
    (profile/'environment.json').write_text(json.dumps(environment))
    args.installed_profile = str(profile)
    args.width = args.height = None
    assert _runner().run(args) == 0
    launch = json.loads((Path(args.output)/'launch.json').read_text())
    assert '--factory-startup' not in launch['command']
    assert '--window-geometry' not in launch['command']
    assert launch['addon_module'] == 'bl_ext.wfrl_frontend.wfrl_blender'


@pytest.mark.parametrize('explicit_status,termination,expected', [
    (None, None, 'INCOMPLETE'), ('PASS', None, 'INCOMPLETE'),
    ('PASS', {'reason': 'finished'}, 'PASS')])
def test_progress_status_requires_explicit_final_verdict_and_termination(tmp_path, explicit_status, termination, expected):
    import json
    import os
    import platform
    from pathlib import Path
    from types import SimpleNamespace as NS
    namespace = {'state': {'results': [{'status': 'PASS', 'pause_resume': {'status': 'PASS'}}],
        'error': None, 'stage': 'resume_check' if termination else 'warm', 'stage_started': 1., 'events': []},
        'PATH': 'close_reopen', 'LAYOUT': 'STRIP', 'START': 0., 'OUT': tmp_path,
        'Path': Path, 'json': json, 'os': os, 'platform': platform,
        'addon': NS(__file__=__file__), 'SOURCE_HASHES': {}, 'now': lambda: 2.,
        'WARM': 5., 'TIMEOUTS': {}, 'SWITCH_AT': 3., 'FAULT': '',
        'bpy': NS(app=NS(version_string='test', background=False),
                  context=NS(preferences=NS(system=NS(ui_scale=1))))}
    exec(_driver_functions('write_result'), namespace)
    namespace['write_result'](explicit_status, termination)
    result = json.loads((tmp_path/'result.json').read_text())
    assert result['status'] == expected
    assert result['stage'] == ('resume_check' if termination else 'warm')


@pytest.mark.parametrize('diagnostic', [
    'Traceback (most recent call last):\nAttributeError: missing stage',
    '00:08.636 bpy.rna | ERROR Python script error in Panel.draw',
    'Error: drawing failed',
    'Error: Not freed memory blocks: invalid, total unfreed memory 1 MB',
])
def test_launcher_rejects_ui_errors_despite_clean_exit_and_pass_json(tmp_path, diagnostic):
    import json
    from pathlib import Path
    args = _runner_args(tmp_path,
        "import os,json\nfrom pathlib import Path\n"
        "p=Path(os.environ['WFRL_TEST_OUTPUT'])\n"
        "(p/'result.json').write_text(json.dumps({'status': 'PASS', 'termination': {'reason': 'finished'}}))\n"
        f"print({diagnostic!r})\nprint('BENCHMARK_RESULT PASS result.json')\n")
    assert _runner().run(args) == 1
    result = json.loads((Path(args.output)/'result.json').read_text())
    original = json.loads((Path(args.output)/'driver-result.json').read_text())
    assert result['status'] == 'ERROR'
    assert result['termination']['reason'] == 'process_log_validation_failed'
    assert result['error']['source'] == 'process_log'
    assert result['process_returncode'] == 0
    assert original == {'status': 'PASS', 'termination': {'reason': 'finished'}}


def test_launcher_requires_final_log_marker_even_with_pass_json(tmp_path):
    import json
    from pathlib import Path
    args = _runner_args(tmp_path,
        "import os,json\nfrom pathlib import Path\n"
        "p=Path(os.environ['WFRL_TEST_OUTPUT'])\n"
        "(p/'result.json').write_text(json.dumps({'status': 'PASS', 'termination': {'reason': 'finished'}}))\n")
    assert _runner().run(args) == 1
    result = json.loads((Path(args.output)/'result.json').read_text())
    assert result['status'] == 'ERROR'
    assert result['process_log_validation']['missing_markers'] == ['BENCHMARK_RESULT PASS ']


def test_runner_records_only_exact_exit_memory_diagnostic_as_warning():
    message = 'Error: Not freed memory blocks: 350, total unfreed memory 0.025940 MB'
    result = _runner().inspect_process_log(message+'\nBENCHMARK_RESULT PASS result.json\n', 'PASS')
    assert result['status'] == 'PASS'
    assert result['warnings'][0]['message'] == message
    assert result['warnings'][0]['blocks'] == 350
    assert result['warnings'][0]['bytes'] == 27200
    assert not result['error_markers']

"""Summarize every fresh playback run, including incomplete and failed runs."""
import argparse
import json
from pathlib import Path


def summarize(root):
    runs = []
    for path in sorted(root.glob('*/result.json')):
        data = json.loads(path.read_text())
        if data.get('schema_version') != 2 or 'path' not in data:
            continue
        run = {key: data.get(key) for key in ('status', 'path', 'layout', 'termination',
                                             'addon_path', 'source_hashes', 'error')}
        run['directory'] = path.parent.name
        run['segments'] = []
        for segment in data.get('results', []):
            item = {key: segment.get(key) for key in ('status', 'mode', 'termination', 'failures',
                'observation_s', 'effective_progress_s', 'stopped_progress_s', 'simulation_progress_s',
                'simulation_speed_ratio', 'same_frames_all_routes', 'window_px', 'pause_resume',
                'profile', 'paused_profile')}
            item['routes'] = {slot: {key: row.get(key) for key in ('distinct_draws',
                'full_segment_draw_fps', 'observation_draw_rate_hz', 'p50_ms', 'p95_ms', 'max_ms',
                'intervals_over_100ms', 'last_heartbeat_age_s', 'has_terminal_evidence')}
                for slot, row in segment.get('routes', {}).items()}
            item['route_draw_time_differences'] = {pair: {key: value for key, value in row.items()
                if key != 'frame_deltas_ms'} for pair, row in segment.get('route_draw_time_differences', {}).items()}
            run['segments'].append(item)
        runs.append(run)
    return {'scope': 'All runs, never filter failures; viewport draws are not hardware scanout', 'runs': runs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = summarize(args.directory)
    destination = args.directory/'playback-summary.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    for run in result['runs']:
        print(run['directory'], run['status'], [
            {slot: round(row['full_segment_draw_fps'], 2) if row['full_segment_draw_fps'] is not None else None
             for slot, row in segment['routes'].items()} for segment in run['segments']])

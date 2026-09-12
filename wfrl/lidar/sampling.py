"""Compare complete independently sampled B2 evaluation grids, not paired hits only.

Usage: python -m wfrl.lidar.sampling BASE_RUN REFINED_RUN OUTPUT.json
Each run contains run_config.json and processed.json from the same fixed window.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .replay import _stats


def summarize_grid(data, fps):
    rows = data['measurements']
    passages = {}
    for row in rows:
        if not row['expected']:
            continue
        group = passages.setdefault(row['passage_id'], [])
        group.append(row)
    summary = _stats(rows)
    summary['sample_interval_s'] = 1. / fps
    summary['expected_duration_s'] = summary['expected_samples'] / fps
    summary['valid_duration_s'] = summary['valid_samples'] / fps
    summary['passages'] = {}
    for passage, group in sorted(passages.items()):
        valid = [r for r in group if r['beams']['B2']['valid']]
        summary['passages'][passage] = dict(expected_samples=len(group), valid_samples=len(valid),
            valid_ratio=len(valid) / len(group), expected_duration_s=len(group) / fps,
            valid_duration_s=len(valid) / fps, missed=not valid,
            first_valid_time_s=min(r['time_s'] for r in valid) if valid else None,
            last_valid_time_s=max(r['time_s'] for r in valid) if valid else None)
    return summary


def compare_grids(base_data, refined_data, base_fps, refined_fps):
    if base_fps <= 0 or refined_fps <= base_fps:
        raise ValueError('refined grid must have a higher positive sampling rate')
    base_times = [r['time_s'] for r in base_data['motion']]
    refined_times = [r['time_s'] for r in refined_data['motion']]
    if (base_times[0], base_times[-1]) != (refined_times[0], refined_times[-1]):
        raise ValueError('sampling comparison requires identical simulation interval')
    base = summarize_grid(base_data, base_fps)
    refined = summarize_grid(refined_data, refined_fps)
    difference = {}
    for key in ('valid_ratio','valid_duration_s','expected_duration_s','mae_m','max_abs_error_m','p95_abs_error_m','max_positive_bias_m','missed_passage_count'):
        difference[key] = refined[key] - base[key] if refined[key] is not None and base[key] is not None else None
    names = set(base['passages']) | set(refined['passages'])
    passage_differences = {}
    for name in sorted(names):
        b, r = base['passages'].get(name), refined['passages'].get(name)
        passage_differences[name] = dict(base=b, refined=r,
            validity_class_changed=(b['missed'] != r['missed']) if b and r else True,
            valid_duration_difference_s=r['valid_duration_s'] - b['valid_duration_s'] if b and r else None)
    return dict(time_range_s=[base_times[0], base_times[-1]], base=base, refined=refined,
        refined_minus_base=difference, passage_comparison=passage_differences,
        verdict='NUMERICAL_COMPARISON_ONLY',
        interpretation='Both independently sampled fixed evaluation grids include misses. Duration uses rectangular sample-count/fps quadrature, with boundary uncertainty of approximately one sample at each transition. Differences combine integration timestep and output-grid effects. No accuracy acceptance tolerance was specified; these differences are evidence, not an automatic convergence pass.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path); parser.add_argument('refined', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    datasets = [json.loads((p / 'processed.json').read_text()) for p in (args.base,args.refined)]
    configs = [json.loads((p / 'run_config.json').read_text()) for p in (args.base,args.refined)]
    if any(configs[0].get(k) != configs[1].get(k) for k in ('wind_mps','duration_s','startup_discard_s','span_refined')):
        raise ValueError('temporal comparison requires matching wind, duration, startup and spatial grid')
    result = compare_grids(*datasets, configs[0]['fps'], configs[1]['fps'])
    result['base_run'] = str(args.base); result['refined_run'] = str(args.refined)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result['refined_minus_base'],indent=2))

if __name__ == '__main__':
    main()

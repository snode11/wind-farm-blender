"""Two real solver grids: descriptive temporal sensitivity, never acceptance."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lidar.compare_dual_beam_methods import coverage, load_verified, statistics
from wfrl.lidar.dual_beam_replay import digest

SCHEMA = 'wfrl.dual-beam-time-refinement.v1'
STATUS = 'TEMPORAL_SENSITIVITY_TWO_LEVELS'
METHODS = ('baseline', 'candidate')
ALLOWED_FST_FIELDS = ('DT', 'DT_Out', 'VTK_fps')


def read_json(path):
    return json.loads(Path(path).read_text())


def _hash_inventory(value, context):
    if isinstance(value, dict):
        entries = [dict(path=k, sha256=v if isinstance(v, str) else v.get('sha256'))
                   for k, v in value.items()]
    elif isinstance(value, list):
        entries = value
    else:
        raise ValueError('Missing hash inventory: '+context)
    result = {}
    for entry in entries:
        path, sha = entry.get('path'), entry.get('sha256')
        if not isinstance(path, str) or not re.fullmatch('[0-9a-f]{64}', str(sha)):
            raise ValueError('Invalid hash entry: '+context)
        if path in result:
            raise ValueError('Duplicate input identity: '+path)
        result[path] = sha
    if not result:
        raise ValueError('Empty hash inventory: '+context)
    return result


def _resolve_input(run, config, path):
    p = Path(path)
    if p.is_absolute():
        return p
    candidates = [run/p]
    for field in ('case_dir', 'case_directory'):
        if config.get(field):
            case = Path(config[field])
            if not case.is_absolute():
                case = run/case
            candidates.append(case/p)
    candidates.append(ROOT/p)
    existing = list(dict.fromkeys(p.resolve() for p in candidates if p.is_file()))
    if len(existing) != 1:
        raise ValueError('Input path missing or ambiguous: '+str(p))
    return existing[0]


def _input_identity(run, config, path, resolved):
    if not Path(path).is_absolute():
        return Path(path).as_posix()
    for base in (config.get('case_dir'), config.get('case_directory'), run):
        if base is None:
            continue
        base = Path(base)
        if not base.is_absolute():
            base = run/base
        try:
            return resolved.relative_to(base.resolve()).as_posix()
        except ValueError:
            pass
    raise ValueError('Absolute input lacks a run-relative identity: '+path)


def normalize_fst(text):
    """Normalize only the values of the three declared temporal controls."""
    seen = Counter()
    pattern = r'^\s*\S+\s+(DT|DT_Out|VTK_fps)(\b.*)$'
    # Keep all other text, including comments and non-temporal numerical fields.
    normalized = []
    for original in text.splitlines():
        m = re.match(pattern, original)
        if m:
            seen[m.group(1)] += 1
            normalized.append('<TEMPORAL> '+m.group(1)+m.group(2))
        else:
            normalized.append(original)
    if any(seen[field] != 1 for field in ALLOWED_FST_FIELDS):
        raise ValueError('FST requires exactly one DT, DT_Out and VTK_fps')
    return '\n'.join(normalized)


def verify_run_inputs(coarse_run, fine_run):
    """Hash actual inputs and compare FST text beyond declared refinements."""
    runs = [Path(coarse_run).resolve(), Path(fine_run).resolve()]
    configs = [read_json(p/'run-config.json') for p in runs]
    for run, config in zip(runs, configs):
        if config.get('schema') != 'wfrl.dual-beam-validation-run.v1' or config.get('status') != 'SOLVER_COMPLETE':
            raise ValueError('Expected a completed validation runner')
        exit_status = read_json(run/'exit-status.json')
        if exit_status.get('status') != 'COMPLETE' or exit_status.get('exit_code') != 0:
            raise ValueError('Solver did not complete successfully')
        # Absolute template/reference/solver paths identify the originating
        # machine. Portable comparison hashes the current runner input files;
        # replay does not execute a solver or require the historical templates.
    for field in ('duration_s','wind_mps','rpm','pitch_deg','azimuth_deg','evaluation_window_s',
                  'template_config_sha256','reference_config_sha256','template_input_hashes'):
        if field not in configs[0] or configs[0][field] != configs[1].get(field):
            raise ValueError('Different or missing physical/reference setting: '+field)
    if not all(isinstance(c.get('fps'),(int,float)) and c['fps']>0 and
               isinstance(c.get('dt_s'),(int,float)) and c['dt_s']>0 for c in configs):
        raise ValueError('Invalid temporal settings')
    inventories = []
    for run, config in zip(runs, configs):
        inventory = {}
        for path, expected in _hash_inventory(config.get('inputs'), 'inputs').items():
            resolved = _resolve_input(run, config, path)
            if digest(resolved) != expected:
                raise ValueError('Actual input hash differs: '+str(resolved))
            identity = _input_identity(run, config, path, resolved)
            if identity in inventory:
                raise ValueError('Duplicate normalized input identity: '+identity)
            inventory[identity] = (resolved, expected)
        inventories.append(inventory)
    if set(inventories[0]) != set(inventories[1]):
        raise ValueError('Different input inventories')
    comparisons = []
    for identity in sorted(inventories[0]):
        (a, ah), (b, bh) = inventories[0][identity], inventories[1][identity]
        fst = a.suffix.lower() == '.fst'
        if fst:
            if normalize_fst(a.read_text()) != normalize_fst(b.read_text()):
                raise ValueError('FST differs beyond DT/DT_Out/VTK_fps: '+identity)
            for file,config in zip((a,b),configs):
                fields={m.group(2):float(m.group(1)) for l in file.read_text().splitlines()
                        if (m:=re.match(r'^\s*(\S+)\s+(DT|DT_Out|VTK_fps)\b',l))}
                for field,want in (('DT',config['dt_s']),('DT_Out',1./config['fps']),('VTK_fps',config['fps'])):
                    if not math.isclose(fields[field],want,rel_tol=0.,abs_tol=1e-12):
                        raise ValueError('Actual temporal field differs from run config: '+field)
        elif ah != bh:
            raise ValueError('Non-temporal input differs: '+identity)
        comparisons.append(dict(path=identity, coarse_sha256=ah, fine_sha256=bh,
                                comparison='normalized_fst_text' if fst else 'byte_hash'))
    def solver_sha(c):
        for key in ('solver_sha256', 'executable_sha256'):
            if c.get(key):
                return c[key]
        for key in ('solver', 'executable'):
            if isinstance(c.get(key), dict):
                return c[key].get('sha256')
        return None
    solver_hashes = [solver_sha(c) for c in configs]
    if not re.fullmatch('[0-9a-f]{64}', str(solver_hashes[0])) or solver_hashes[0] != solver_hashes[1]:
        raise ValueError('Different or missing solver hashes')
    references = []
    for c in configs:
        value = c.get('reference_hashes', c.get('reference'))
        if isinstance(value, dict) and 'files' in value:
            value = value['files']
        if value is None and c.get('reference_config_sha256'):
            value = {'probe-config.json':c['reference_config_sha256']}
        references.append(_hash_inventory(value, 'reference'))
    if references[0] != references[1]:
        raise ValueError('Different structural/reference hashes')
    return configs, dict(run_config_hashes={k:digest(p/'run-config.json') for k,p in zip(('coarse','fine'),runs)},
                         solver_sha256=solver_hashes[0], input_comparisons=comparisons,
                         reference_hashes=references[0], allowed_fst_fields=list(ALLOWED_FST_FIELDS))


def verify_source_link(manifest,source_path,run_path,run_config_sha256):
    if manifest.get('run_config_sha256') != run_config_sha256:
        raise ValueError('Scored geometry is not linked to actual runner config')
    relative=manifest.get('run_package')
    if not isinstance(relative,str) or Path(relative).is_absolute():
        raise ValueError('Scored geometry lacks a portable relative runner pointer')
    if (Path(source_path)/relative).resolve() != Path(run_path).resolve():
        raise ValueError('Scored geometry runner pointer differs')
    return dict(run_package=relative,run_config_sha256=run_config_sha256,
                historical_run_path=manifest.get('run_path'))


def compare_passage_minima(coarse,fine):
    keyed=[{(p['turbine_id'],p['passage_id']):p for p in records} for records in (coarse,fine)]
    comparisons=[]
    for key in sorted(set(keyed[0])|set(keyed[1])):
        a,b=keyed[0].get(key),keyed[1].get(key)
        row=dict(turbine_id=key[0],passage_id=key[1],coarse=deepcopy(a),fine=deepcopy(b),
                 presence='both' if a is not None and b is not None else 'coarse_only' if a is not None else 'fine_only',
                 reference_min_difference_m=_difference(a['reference_min_m'],b['reference_min_m']) if a and b else None,
                 reference_min_time_difference_s=_difference(a['reference_min_time_s'],b['reference_min_time_s']) if a and b else None,
                 methods={})
        for method in METHODS:
            am,bm=(a[method] if a else None),(b[method] if b else None)
            row['methods'][method]=dict(
                predicted_min_difference_m=_difference(am['predicted_min_m'],bm['predicted_min_m']) if am and bm else None,
                predicted_min_time_difference_s=_difference(am['predicted_min_time_s'],bm['predicted_min_time_s']) if am and bm else None,
                error_difference_m=_difference(am['error_m'],bm['error_m']) if am and bm else None,
                unknown_changed=am['unknown']!=bm['unknown'] if am and bm else None)
        comparisons.append(row)
    return comparisons


def _package_path(report_path, report, name, override=None):
    if override:
        return Path(override).resolve()
    p = Path(report[name+'_package'])
    if p.is_absolute():
        return p
    base = report_path.parent if report.get('package_path_reference') == 'comparison output directory' else ROOT
    return (base/p).resolve()


def load_comparison(path, candidate_override=None):
    path = Path(path).resolve()
    report = read_json(path)
    if report.get('schema') != 'wfrl.dual-beam-method-comparison.v1':
        raise ValueError('Unsupported method comparison schema')
    if (report.get('baseline_method'), report.get('candidate_method')) != ('hub-axis.v1', 'hub-tls.v1'):
        raise ValueError('Unexpected method identities')
    if not report.get('integrity', {}).get('reader_validation_passed') or not report['integrity'].get('raw_observations_and_s1_identical'):
        raise ValueError('Comparison lacks reader/independent scoring integrity')
    packages = {}
    for name in METHODS:
        package = _package_path(path, report, name, candidate_override if name == 'candidate' else None)
        if digest(package/'manifest.json') != report['package_manifest_hashes'][name]:
            raise ValueError('Comparison package hash link differs: '+name)
        packages[name] = load_verified(package)
        manifest = packages[name]['overlay']['manifest']
        if manifest['source_hashes'] != report['source_hashes'] or manifest['source_fps'] != report['source_fps']:
            raise ValueError('Comparison source hash/fps link differs')
    return dict(report=report, packages=packages, path=path, sha256=digest(path))


def grid_records(bundle):
    """Retain every source time, with reasons also for invalid/outside rows."""
    report = bundle['report']
    windows = {(r['turbine_id'], r['time_s']):r for r in report['fixed_window_samples']}
    result = {}
    for turbine, data in bundle['packages']['candidate']['overlay']['results'].items():
        other = bundle['packages']['baseline']['overlay']['results'][turbine]
        for index, row in enumerate(data['samples']):
            time = row['time_s']; key = (turbine, time)
            window = windows.get(key)
            record = dict(turbine_id=turbine, time_s=time,
                          expected_blade_id=row['expected_blade_id'], passage_id=row['passage_id'],
                          boundary_truncated=window['boundary_truncated'] if window else False,
                          reference_clearance_m=window['reference_clearance_m'] if window else None,
                          reference_reason=window['reference_reason'] if window else 'outside_fixed_window',
                          s1=dict(observation=deepcopy(row['observations']['S1']),
                                  observation_state=row['s1_observation_state'],
                                  alarm=deepcopy(data['cumulative'][index]['alarm'])))
            for name, raw in (('baseline', other['samples'][index]), ('candidate', row)):
                if raw['time_s'] != time:
                    raise ValueError('Method grids differ')
                rec = raw['reconstruction']
                if window:
                    record[name] = deepcopy(window[name])
                else:
                    record[name] = dict(fresh_valid=False, reason='outside_fixed_window',
                                        estimate_clearance_m=None, error_m=None)
                record[name].update(reconstruction_valid=rec['valid'], blade_id=rec.get('blade_id'),
                                    reconstruction_reason=rec['reason'])
            result[key] = record
    if set(windows)-set(result):
        raise ValueError('Scored window lacks a source row')
    return result


def _summary(records, fps):
    records = list(records)
    result = dict(grid_samples=len(records), fixed_window_samples=sum(r['passage_id'] is not None for r in records), methods={})
    for name in METHODS:
        by_turbine = defaultdict(list)
        for row in records:
            method = row[name]
            by_turbine[row['turbine_id']].append(dict(time_s=row['time_s'], expected_blade_id=row['expected_blade_id'],
                passage_id=row['passage_id'], boundary_truncated=row['boundary_truncated'],
                fresh_valid=method['fresh_valid'], unknown_reason=method['reason'], error_m=method['error_m']))
        cov = {tid:coverage(values, 1./fps) for tid,values in by_turbine.items()}
        n=sum(c['expected_samples'] for c in cov.values()); valid=sum(c['fresh_valid_samples'] for c in cov.values())
        result['methods'][name] = dict(statistics=statistics(r[name]['error_m'] for r in records if r[name]['error_m'] is not None),
            coverage=dict(expected_samples=n,fresh_valid_samples=valid,fresh_coverage=valid/n if n else None,
                unknown_samples=n-valid, unknown_reasons=dict(sum((Counter(c['unknown_reasons']) for c in cov.values()),Counter())),
                longest_missing_grid_duration_s=max((c['longest_missing_grid_duration_s'] for c in cov.values()),default=0.),
                by_turbine=cov))
    return result


def _difference(a, b):
    return float(b-a) if a is not None and b is not None else None


def compare_grids(coarse, fine, coarse_fps, fine_fps, evaluation_window):
    """Do not discard invalid transitions or additional fine-grid observations."""
    start,end = evaluation_window
    coarse = {k:v for k,v in coarse.items() if start-1e-9 <= k[1] <= end+1e-9}
    fine = {k:v for k,v in fine.items() if start-1e-9 <= k[1] <= end+1e-9}
    if not coarse or not fine or fine_fps <= coarse_fps:
        raise ValueError('Missing or non-refined evaluation grid')
    if set(coarse)-set(fine):
        raise ValueError('Missing matched coarse time on fine grid')
    turbines = {k[0] for k in coarse}
    if turbines != {k[0] for k in fine}:
        raise ValueError('Different turbines on temporal grids')
    for grid,fps in ((coarse,coarse_fps),(fine,fine_fps)):
        n=round((end-start)*fps)+1
        for tid in turbines:
            times=sorted(k[1] for k in grid if k[0]==tid)
            if len(times)!=n or any(abs(t-(start+i/fps))>1e-9 for i,t in enumerate(times)):
                raise ValueError('Evaluation source grid incomplete or nonuniform')
    matched=[]; changes=Counter()
    for key in sorted(coarse):
        a,b=coarse[key],fine[key]
        row=dict(turbine_id=key[0],time_s=key[1],coarse=deepcopy(a),fine=deepcopy(b),
            reference_difference_m=_difference(a['reference_clearance_m'],b['reference_clearance_m']),
            expected_blade_changed=a['expected_blade_id']!=b['expected_blade_id'],
            passage_changed=a['passage_id']!=b['passage_id'],methods={},
            s1=dict(first_object_changed=a['s1']['observation'].get('first_object')!=b['s1']['observation'].get('first_object'),
                    state_changed=a['s1']['observation_state']!=b['s1']['observation_state'],
                    alarm_changed=a['s1']['alarm']!=b['s1']['alarm'],
                    range_difference_m=_difference(a['s1']['observation'].get('slant_range_m'),b['s1']['observation'].get('slant_range_m'))))
        for name in METHODS:
            am,bm=a[name],b[name]
            transition=f"{bool(am['fresh_valid'])}->{bool(bm['fresh_valid'])}"
            changes[name+':'+transition]+=1
            row['methods'][name]=dict(validity_transition=transition,blade_changed=am['blade_id']!=bm['blade_id'],
                estimate_difference_m=_difference(am['estimate_clearance_m'],bm['estimate_clearance_m']),
                error_difference_m=_difference(am['error_m'],bm['error_m']))
        matched.append(row)
    additional=[fine[k] for k in sorted(set(fine)-set(coarse))]
    return dict(evaluation_window_s=[start,end],source_fps=dict(coarse=coarse_fps,fine=fine_fps),
        full_grid_summary=dict(coarse=_summary(coarse.values(),coarse_fps),fine=_summary(fine.values(),fine_fps)),
        matched_coarse_samples=matched,validity_transition_counts=dict(changes),
        fine_additional_samples=additional,fine_additional_summary=_summary(additional,fine_fps),
        additional_grid_gap_definition='Adjacent additional samples are separated by the fine-grid dt plus excluded matched samples; missing durations are descriptive sample-count times fine dt.')


def markdown_report(report):
    lines=['# 双束两级时间敏感性','', '`'+STATUS+'`；不设验收门槛，不提供收敛保证，默认仍为 hub-axis.v1。','',
           '两个真实求解网格分别评分；粗网格匹配行、有效性改变、未知原因及全部新增细网格时刻均保留。','',
           '|网格/方法|有效/预期|MAE / m|P95 / m|最长缺测网格 / s|','|---|---:|---:|---:|---:|']
    for grid,summary in report['full_grid_summary'].items():
        for name,item in summary['methods'].items():
            cov,s=item['coverage'],item['statistics'];num=lambda x:'unknown' if x is None else f'{x:.6f}'
            lines.append(f"|{grid}/{name}|{cov['fresh_valid_samples']}/{cov['expected_samples']}|{num(s['mae_m'])}|{num(s['p95_abs_error_m'])}|{num(cov['longest_missing_grid_duration_s'])}|")
    lines+=['',f"细网格新增时刻：{len(report['fine_additional_samples'])}；逐行及其自身统计保留于 comparison.json。",'',
            '经过最低值使用各自完整固定窗口；不同采样率可能改变极值、有效配对与漏测，不能仅依据公共有效误差判断收敛。',
            '两级比较同时改变求解DT及输出FPS，不能单独归因于某一因素；未完成第三网格、空间精化、独立holdout或设备验证。']
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('coarse-run','fine-run','coarse-comparison','fine-comparison','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--coarse-package',type=Path);p.add_argument('--fine-package',type=Path)
    args=p.parse_args(argv)
    configs,integrity=verify_run_inputs(args.coarse_run,args.fine_run)
    bundles=[load_comparison(args.coarse_comparison,args.coarse_package),load_comparison(args.fine_comparison,args.fine_package)]
    windows=[c.get('evaluation_window_s',c.get('evaluation_segment_s')) for c in configs]
    if windows[0] is None or windows[0]!=windows[1] or len(windows[0])!=2:
        raise ValueError('Different or missing evaluation windows')
    for bundle in bundles:
        segment=bundle['packages']['candidate']['overlay']['manifest']['segment']
        if [segment['start_s'],segment['end_s']]!=windows[0]:
            raise ValueError('Scored source segment differs from frozen evaluation window')
    for config,bundle in zip(configs,bundles):
        if config['fps'] != bundle['report']['source_fps']:
            raise ValueError('Runner FPS differs from scored source FPS')
    source_links={};reference_surface_hashes={}
    for label,run,bundle in zip(('coarse','fine'),(args.coarse_run,args.fine_run),bundles):
        for package in bundle['packages'].values():
            source=next(iter(package['sources'].values()))
            source_links[label]=verify_source_link(source.manifest,source.path,run,integrity['run_config_hashes'][label])
            surface_inventory=read_json(source.path/'source-surfaces.json')
            reference_surface_hashes[label]=_hash_inventory(surface_inventory.get('reference_surfaces'),'reference surfaces')
    if reference_surface_hashes['coarse'] != reference_surface_hashes['fine']:
        raise ValueError('Different exported reference surface hashes')
    integrity.update(source_runner_links=source_links,reference_surface_hashes=reference_surface_hashes['coarse'],
        historical_path_policy='Original absolute template/reference/executable/run paths are provenance namespace; current runner inputs and relative source-to-run pointers are verified.')
    report=compare_grids(*[grid_records(b) for b in bundles],*[b['report']['source_fps'] for b in bundles],windows[0])
    report.update(schema=SCHEMA,status=STATUS,performance_status='PENDING_ACCEPTANCE',default_method='hub-axis.v1',
        acceptance_thresholds=None,convergence_guarantee=False,integrity=integrity,
        comparison_hashes={k:b['sha256'] for k,b in zip(('coarse','fine'),bundles)},
        complete_passage_minima={k:b['report']['complete_passage_minima'] for k,b in zip(('coarse','fine'),bundles)},
        passage_minimum_differences=compare_passage_minima(*[b['report']['complete_passage_minima'] for b in bundles]),
        passage_minimum_statistics={k:b['report']['passage_minimum_statistics'] for k,b in zip(('coarse','fine'),bundles)},
        source_method_summaries={k:dict(metrics=b['report']['metrics'],coverage=b['report']['coverage']) for k,b in zip(('coarse','fine'),bundles)},
        s1_events={k:{tid:deepcopy(v['events']) for tid,v in b['packages']['candidate']['overlay']['results'].items()} for k,b in zip(('coarse','fine'),bundles)},
        evidence_tool_hashes={str(Path(__file__).relative_to(ROOT)):digest(__file__)})
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    (out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
    (out/'report.md').write_text(markdown_report(report))
    print(out)


if __name__=='__main__':
    main()

"""Compare like-for-like OpenFAST binary outputs against an immutable r-test reference."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

CHANNELS = ('GenPwr_[kW]', 'RootMxb1_[kN-m]', 'RootMyb1_[kN-m]', 'RootMzb1_[kN-m]',
            'TwrBsMxt_[kN-m]', 'TwrBsMyt_[kN-m]', 'TwrBsMzt_[kN-m]')


def compare(reference, actual):
    from openfast_toolbox.io import FASTOutputFile
    a, b = (FASTOutputFile(str(p)).toDataFrame() for p in (reference, actual))
    if a.shape != b.shape or list(a.columns) != list(b.columns):
        raise ValueError('Reference and actual columns, units and sample counts must match')
    if not np.isfinite(a.to_numpy()).all() or not np.isfinite(b.to_numpy()).all():
        raise ValueError('Nonfinite native output')
    if not np.allclose(a['Time_[s]'], b['Time_[s]'], atol=1e-7, rtol=0):
        raise ValueError('Reference and actual times must match; no resampling is performed')
    metrics = {}
    for channel in CHANNELS:
        x, y = a[channel].to_numpy(), b[channel].to_numpy()
        if not x.mean() or not x.std():
            raise ValueError('Relative reference mean/std is undefined')
        metrics[channel] = dict(reference_mean=float(x.mean()), actual_mean=float(y.mean()),
            reference_std=float(x.std()), actual_std=float(y.std()),
            mean_percent=float(100*(y.mean()-x.mean())/abs(x.mean())),
            std_percent=float(100*(y.std()-x.std())/x.std()))
    return dict(samples=len(a), window_seconds=[float(a['Time_[s]'].iloc[0]), float(a['Time_[s]'].iloc[-1])],
        reference_sha256=hashlib.sha256(Path(reference).read_bytes()).hexdigest(),
        actual_sha256=hashlib.sha256(Path(actual).read_bytes()).hexdigest(), metrics=metrics,
        suggested_A1_pass=all(abs(v['mean_percent']) <= 1 and abs(v['std_percent']) <= 5 for v in metrics.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--actual', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.reference, args.actual), indent=2,
                                     allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()

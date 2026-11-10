"""Extract an X100 installation import summary from an existing full v2 report."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, help='Full v2 search report with an applicable recommendation')
    parser.add_argument('--output', type=Path, help='New summary path; defaults to <stem>.summary.json')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / 'blender_frontend'), str(root)]
    from scripts.radar_x100.optimize_mount import write_import_summary
    from wfrl.radar_x100.installation import SEARCH_SUMMARY_SUFFIX

    output = args.output or args.evidence.with_suffix(SEARCH_SUMMARY_SUFFIX)
    try:
        raw = args.evidence.read_bytes()
        report = json.loads(raw)
        if isinstance(report, dict) and 'evidence_report' in report:
            raise ValueError('请选择完整的 v2 搜索报告')
        summary = write_import_summary(output, report, evidence=args.evidence,
                                       sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print('X100_SEARCH_SUMMARY', summary)


if __name__ == '__main__':
    main()

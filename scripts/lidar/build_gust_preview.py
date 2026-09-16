"""Build a matched REVIEW_ONLY reader and section archive from one completed run."""
from pathlib import Path
import argparse
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from wfrl.lidar.flex_provisional import build, source_reader
from wfrl.blender_bridge.blade_flex_export import export_reader


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('output',type=Path)
    a=p.parse_args()
    reader=build(a.run,a.output/'data')
    export_reader(source_reader(a.output/'data'),a.output/'data',a.output/'gust-flex.npz',
                  reader.start_s,reader.end_s-reader.start_s)
    print('GUST_PACKAGE_COMPLETE',a.output)


if __name__=='__main__': main()

"""Blender --background --factory-startup --python-exit-code 1 --python scripts/blender/gw184_single.py -- --help"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'projects/gw184-single'),str(ROOT/'blender_frontend')]
from gw184.pipeline import main
if __name__=='__main__':
    main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])

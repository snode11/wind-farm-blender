"""Compatibility entry point; implementation lives in wfrl.viz.tip_visualizer."""
from wfrl.viz.tip_visualizer import (TipVisualizer, Scene3D, load_dets, synth_frame, main)  # noqa: F401 -- Public compatibility re-exports.

if __name__ == '__main__':
    main()

#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/macos/run_desktop.sh studio --scene scenes/turb3_row.yaml [Studio options]
  scripts/macos/run_desktop.sh rviz [RViz options]

Environment overrides:
  WFCRL_PYTHONW   pythonw from the project conda environment
  WFCRL_MPIEXEC   OpenMPI mpiexec executable
EOF
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

mode=$1
shift

if [[ $mode == "-h" || $mode == "--help" || $mode == "help" ]]; then
    usage
    exit 0
fi

if [[ $(uname -s) != "Darwin" ]]; then
    echo "This launcher is for macOS. On Linux use mpiexec with python directly." >&2
    exit 2
fi

pythonw=${WFCRL_PYTHONW:-$(command -v pythonw || true)}
mpiexec=${WFCRL_MPIEXEC:-$(command -v mpiexec || true)}

if [[ -z $pythonw || ! -x $pythonw ]]; then
    echo "pythonw was not found in the active environment." >&2
    echo "Install it with: conda install -c conda-forge python.app" >&2
    exit 1
fi
if [[ -z $mpiexec || ! -x $mpiexec ]]; then
    echo "mpiexec was not found. Install OpenMPI and activate the project environment." >&2
    exit 1
fi

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_root"

case "$mode" in
    studio)
        module=wfrl.studio.app
        ;;
    rviz)
        module=wfrl.viz.rviz_app
        ;;
    *)
        echo "Unknown desktop app: $mode" >&2
        usage >&2
        exit 2
        ;;
esac

export OMPI_MCA_plm=${OMPI_MCA_plm:-isolated}
exec "$mpiexec" --bind-to none -n 1 "$pythonw" -m "$module" "$@"

#!/bin/bash
# Aftermovie helper for DaVinci Resolve Studio (macOS)
#   ./aftermovie.sh export    step 1: read the current timeline (read-only)
#   ./aftermovie.sh analyze   step 2: score the footage, plan the rough cut
#   ./aftermovie.sh build     step 3: create NEW timelines in Resolve
#   ./aftermovie.sh all       steps 1-3
# Extra options for analyze go after the command, e.g.:
#   ./aftermovie.sh analyze --length 150
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export AFTERMOVIE_DIR="${AFTERMOVIE_DIR:-$HOME/Desktop/Aftermovie}"
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"

check_analyze_deps() {
  command -v ffmpeg >/dev/null || { echo "ffmpeg missing -> brew install ffmpeg"; exit 1; }
  python3 -c "import numpy" 2>/dev/null || { echo "numpy missing -> pip3 install numpy"; exit 1; }
}

cmd="${1:-all}"; shift || true
case "$cmd" in
  export)  python3 "$HERE/resolve_export_timeline.py" ;;
  analyze) check_analyze_deps; python3 "$HERE/analyze_footage.py" "$@" ;;
  build)   python3 "$HERE/resolve_build_timelines.py" ;;
  all)
    check_analyze_deps
    python3 "$HERE/resolve_export_timeline.py"
    python3 "$HERE/analyze_footage.py" "$@"
    python3 "$HERE/resolve_build_timelines.py" ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac

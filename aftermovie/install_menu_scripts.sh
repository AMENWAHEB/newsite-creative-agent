#!/bin/bash
# Optional: adds the two Resolve scripts to Workspace > Scripts > Aftermovie
# (copies files only; nothing else is touched).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Aftermovie"
mkdir -p "$DEST"
cp "$HERE/resolve_export_timeline.py" "$HERE/resolve_build_timelines.py" "$DEST/"
echo "Installed to: $DEST"
echo "Restart Resolve, then use Workspace > Scripts > Aftermovie"

#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
source_app="$project_dir/dist/Voice Input.app"
target_dir="$HOME/Applications"
target_app="$target_dir/Voice Input.app"

if [[ ! -d "$source_app" ]]; then
  echo "Missing build. Run ./build-app.sh first." >&2
  exit 1
fi

mkdir -p "$target_dir"
if [[ -d "$target_app" ]]; then
  timestamp="$(date +%Y%m%d-%H%M%S)"
  mv "$target_app" "$target_dir/Voice Input.app.backup-$timestamp"
fi

ditto "$source_app" "$target_app"
open "$target_app"
echo "Installed and opened: $target_app"

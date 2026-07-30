#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir"

version="${1:-1.3.0}"
app_path="$project_dir/dist/Voice Input.app"
release_dir="$project_dir/release"
archive="$release_dir/Voice-Input-v${version}-macOS-arm64.zip"

if [[ ! -d "$app_path" ]]; then
  echo "Missing app bundle. Run ./build-app.sh first." >&2
  exit 1
fi

mkdir -p "$release_dir"
rm -f "$archive" "$archive.sha256"
ditto -c -k --sequesterRsrc --keepParent "$app_path" "$archive"
shasum -a 256 "$archive" > "$archive.sha256"
echo "$archive"

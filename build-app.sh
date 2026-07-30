#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir"

python_bin="${PYTHON_BIN:-python3}"
if [[ ! -x .venv/bin/python ]]; then
  "$python_bin" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-build.txt

rm -rf "$project_dir/build" "$project_dir/dist"
.venv/bin/python setup_app.py py2app

app_path="$project_dir/dist/Voice Input.app"
identity="${SIGN_IDENTITY:--}"
if [[ "$identity" == "-" ]]; then
  codesign --force --deep --sign - "$app_path"
  echo "Built with an ad-hoc signature."
else
  codesign \
    --force \
    --deep \
    --options runtime \
    --timestamp \
    --sign "$identity" \
    "$app_path"
  echo "Built with Developer ID: $identity"
fi

codesign --verify --deep --strict "$app_path"
plutil -lint "$app_path/Contents/Info.plist"
echo "$app_path"

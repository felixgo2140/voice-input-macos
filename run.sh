#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir"

if [[ ! -x .venv/bin/python ]]; then
  echo "Run ./setup.sh first." >&2
  exit 1
fi

exec .venv/bin/python voice_input.py

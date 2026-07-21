#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 tracker.py run
if git diff --quiet -- docs/data.json; then
  exit 0
fi
git add docs/data.json
git commit -m "chore: refresh public submission tracker"
git push origin main


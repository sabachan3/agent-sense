#!/usr/bin/env bash
# One-time setup after cloning. Safe to run again: every step checks before it changes anything.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

command -v uv >/dev/null || { echo "setup: uv is required (https://docs.astral.sh/uv/)" >&2; exit 2; }

if [ "$(git config --get core.hooksPath || true)" != ".githooks" ]; then
  git config core.hooksPath .githooks
  echo "setup: git hooks now come from .githooks"
fi

# The command on PATH runs this working copy (editable), so edits take effect without reinstalling.
if ! command -v agent-sense >/dev/null || ! agent-sense features --help >/dev/null 2>&1; then
  uv tool install --quiet --force --editable ".[browser]"
  echo "setup: installed agent-sense (editable)"
fi

# Chromium is needed by the page and launch checks only.
if ! uv run --quiet --no-project --python 3.12 --with playwright python - <<'PY'
import os
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    raise SystemExit(0 if os.path.exists(p.chromium.executable_path) else 1)
PY
then
  uv run --quiet --no-project --python 3.12 --with playwright playwright install chromium
  echo "setup: installed Chromium for Playwright"
fi

scripts/test.sh
echo "setup: ready. Next: agent-sense done --run"

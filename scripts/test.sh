#!/usr/bin/env bash
# Gate tests: deterministic, local, no network, no browser. Also the pre-commit hook.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
uv run --quiet --no-project --python 3.12 --with pytest --with pytest-xdist pytest -q -p no:cacheprovider -n 4 services tests "$@"

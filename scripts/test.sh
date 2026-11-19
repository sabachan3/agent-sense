#!/usr/bin/env bash
# Gate tests: deterministic, local, no network, no browser. Also the pre-commit hook.
# AGENT_SENSE_PYTHON picks the interpreter version (default 3.12); CI runs 3.11 to 3.13.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
uv run --quiet --no-project --python "${AGENT_SENSE_PYTHON:-3.12}" --with pytest --with pytest-xdist pytest -q -p no:cacheprovider -n 4 services tests "$@"

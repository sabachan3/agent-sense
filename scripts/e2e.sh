#!/usr/bin/env bash
# End to end, as a user of a release would meet it: build the wheel and the sdist, check them
# (metadata as the package index reads it, which files are inside, no private text), install each
# into its own throwaway environment outside the repository, and drive each installed command
# through a throwaway git repository. Not part of the gate (about 6 s with a warm uv cache, longer
# on a cold one; needs uv's cache or the network for the build backend and twine).
# Exits non-zero at the first mismatch.
#
#   scripts/e2e.sh              build, check, install, drive
#   scripts/e2e.sh --keep DIR   the same, and leave the two checked archives in DIR (the release workflow uploads those)
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
KEEP=""
if [ "${1:-}" = "--keep" ]; then
  [ -n "${2:-}" ] || { echo "e2e: --keep needs a directory" >&2; exit 2; }
  mkdir -p "$2"; KEEP="$(cd "$2" && pwd)"
elif [ $# -gt 0 ]; then
  echo "e2e: unknown argument $1" >&2; exit 2
fi
WORK="$(mktemp -d)"
trap 'rm -r "$WORK"' EXIT

fail() { echo "e2e: FAIL: $*" >&2; exit 1; }
expect() {   # expect CODE command...   runs the command, keeps its output in $OUT, compares the exit code
  local want="$1"; shift
  set +e; OUT="$("$@" 2>&1)"; local got=$?; set -e
  [ "$got" = "$want" ] || fail "\`$*\` exited $got, expected $want
$OUT"
}
has() { case "$OUT" in *"$1"*) ;; *) fail "output lacks \"$1\":
$OUT" ;; esac; }

VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$REPO/pyproject.toml")"
[ -n "$VERSION" ] || fail "no version in pyproject.toml"

# Build both archives the way a release does (the wheel is built from the sdist), then check them.
uv build --quiet --out-dir "$WORK/dist" "$REPO"
WHEEL="$(echo "$WORK"/dist/*.whl)"; SDIST="$(echo "$WORK"/dist/*.tar.gz)"
[ -f "$WHEEL" ] && [ -f "$SDIST" ] || fail "uv build did not leave one wheel and one sdist"
expect 0 uvx --quiet twine check --strict "$WHEEL" "$SDIST"
expect 0 uv run --quiet --no-project --python 3.12 "$REPO/scripts/release_check.py" dist "$WORK/dist"

cycle() {   # cycle NAME ARCHIVE PYTHON   install the archive into a new environment and drive the installed command
  local name="$1" archive="$2" python="$3"
  uv venv --quiet --python "$python" "$WORK/venv-$name"
  uv pip install --quiet --python "$WORK/venv-$name/bin/python" "$archive"
  AS="$WORK/venv-$name/bin/agent-sense"
  cd "$WORK"   # nothing below may find the repository on the import path
  expect 0 "$AS" --help;     has "agent-sense done --run"
  expect 0 "$AS" --version;  [ "$OUT" = "agent-sense $VERSION" ] || fail "$name: --version printed \"$OUT\", pyproject.toml says $VERSION"
  expect 0 "$WORK/venv-$name/bin/python" -c "import agent_sense; print(agent_sense.__file__)"
  case "$OUT" in "$REPO"/*) fail "$name: the package was imported from the repository: $OUT" ;; */venv-"$name"/*) ;;
    *) fail "$name: the package was imported from $OUT, not from the new environment" ;; esac

  P="$WORK/project-$name"
  mkdir -p "$P/scripts"
  cd "$P"
  git init -q -b main
  git config user.email e2e@example.invalid
  git config user.name e2e
  printf 'def add(a, b):\n    return a + b\n' > calc.py
  printf '#!/bin/sh\npython3 -c "import calc; assert calc.add(2, 3) == 5"\n' > scripts/test.sh
  chmod +x scripts/test.sh
  printf '{"version": 1, "features": [{"id": "add", "behavior": "add sums two numbers", "verify": "scripts/test.sh", "state": "active"}]}\n' > features.json

  expect 0 "$AS" init --hook
  grep -q 'test = "scripts/test.sh"' .agent-sense.toml || fail "init did not detect the test command"
  grep -q 'agent-sense hook stop' .claude/settings.json || fail "init --hook did not write the hook"
  printf '[done]\ntest = "scripts/test.sh"\nignore = ["features.json"]\n' > .agent-sense.toml
  git add -A && git commit -q -m start

  # done: not run -> run -> edit makes it stale -> run again
  expect 1 "$AS" done --no-state;        has "tests-not-run"
  expect 0 "$AS" done --run --no-state
  expect 0 "$AS" done --no-state
  printf 'def add(a, b):\n    return a + b  # edited\n' > calc.py
  expect 1 "$AS" done --no-state;        has "tests-stale"; has "calc.py"
  EVENT="{\"cwd\": \"$P\", \"stop_hook_active\": false}"
  expect 0 sh -c "printf '%s' '$EVENT' | '$AS' hook stop"; has '"decision": "block"'
  expect 0 "$AS" done --run --no-state
  expect 0 sh -c "printf '%s' '$EVENT' | '$AS' hook stop"; [ -z "$OUT" ] || fail "hook spoke with nothing failing: $OUT"

  # features: unverified -> passing by the run -> regression takes it back
  expect 0 "$AS" features --run;         grep -q '"state": "passing"' features.json || fail "state not passing after a passing run"
  expect 0 "$AS" done --no-state         # the state change did not make the test record stale
  printf 'def add(a, b):\n    return a - b\n' > calc.py
  expect 1 "$AS" features --run;         has "was passing, now fails"
  grep -q '"state": "active"' features.json || fail "state not taken back after a failing run"
  expect 1 "$AS" done --run --no-state;  has "tests-failed"

  # the checks that only read
  expect 0 "$AS" all --checks deps,context,boundary,features --format json
  expect 2 "$AS" features --run --only nope
}

# The wheel on the default Python of the gate, the sdist on the oldest supported one.
cycle wheel "$WHEEL" 3.12
cycle sdist "$SDIST" 3.11

if [ -n "$KEEP" ]; then
  cp "$WHEEL" "$SDIST" "$KEEP/"
fi
echo "e2e: OK (wheel and sdist built and checked, each installed and driven: done cycle, hook, features cycle)"

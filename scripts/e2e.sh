#!/usr/bin/env bash
# End to end: build the wheel, install it into a throwaway environment, and drive the installed
# command through a throwaway git repository. Not part of the gate (about 10 s, needs uv's cache
# or the network for the build backend). Exits non-zero at the first mismatch.
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
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

uv build --quiet --wheel --out-dir "$WORK/dist" "$REPO"
uv venv --quiet --python 3.12 "$WORK/venv"
uv pip install --quiet --python "$WORK/venv/bin/python" "$WORK"/dist/*.whl
AS="$WORK/venv/bin/agent-sense"
"$AS" --help >/dev/null

P="$WORK/project"
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

echo "e2e: OK (wheel built, installed, done cycle, hook, features cycle)"

# agent-sense: notes for agents working in this repository

Local, deterministic checks for work done with coding agents, shipped as one command,
`agent-sense`. No model and no network service is used by any check. User-facing description:
`README.md`. This file is the map; details live in the files it points to.

## Start of a session

1. Read `PROGRESS.md` (where things stand, known issues, next steps).
2. Read `features.json` (what exists and the command that proves each item).
3. `git log --oneline -5` and `git status --short`.
4. `scripts/setup.sh` if `agent-sense --help` fails or hooks are not active. Needs `uv`; the gate
   runs on Python 3.12 (`AGENT_SENSE_PYTHON=3.11 scripts/test.sh` for another), the package needs 3.11 or newer.

## Map

| path | what it is |
|---|---|
| `agent_sense/cli.py` | The only glue: argument parsing, config, text output, `init`, the Stop hook |
| `contracts/` | Shapes shared by all services (`schema.py`), described in `contracts/README.md` |
| `services/<name>/<name>_lib.py` | One check each: done, deps, context, boundary, features, page, launch, session |
| `services/<name>/tests/`, `tests/` | Gate tests of each check; of the command line and the eval scripts' own logic |
| `evals/` | Known-answer evals, one or more per check; fixtures for the browser evals |
| `docs/<name>.md` | Rules, evidence and limits of one check. This takes the place of a README per service |
| `docs/results.md` | Every measured number and how it was produced |
| `docs/release.md` | The owner's decisions and steps for going public and for a release. A version changes in `pyproject.toml`, `agent_sense/__init__.py` and `CHANGELOG.md` together |
| `scripts/` | `test.sh` (gate), `setup.sh`, `e2e.sh`, `release_check.py` (private text, archive contents, versions) |
| `runs/` | Eval output, ignored by git: it exists only on the machine that ran the eval |

## Commands

```bash
scripts/setup.sh                 # once: hooks, editable install on PATH, Chromium, then the gate
scripts/test.sh                  # gate: all tests, about 2 s, also the pre-commit hook
scripts/test.sh -k test_deps_lib # one check's tests
uv run --quiet --no-project --python 3.12 evals/deps_eval.py                     # an eval (free, seconds)
uv run --quiet --no-project --python 3.12 --with playwright evals/page_eval.py   # browser eval, about 90 s
scripts/e2e.sh                   # build wheel and sdist, check them, install each, drive it on a throwaway repository (6 s)
agent-sense done --run           # run the gate and record that it passed on these files
agent-sense all                  # done, deps, context, boundary by default; here also features (.agent-sense.toml)
agent-sense features --run       # rewrites features.json states; leaves out the two browser features (manual)
agent-sense features --run --include-manual   # everything, about 3 minutes
```

`evals/done_hook_eval.py` and `evals/launch_field.py` cost money or touch outside sites. Run them
only when asked.

## Definition of done

A change is done when all of these hold, in this order:

1. `scripts/test.sh` is green. Time budget: the median of three runs is under 2.5 s by the time
   pytest prints (`N passed in X s`). This is the one place the budget is stated; the measured
   times are in `PROGRESS.md`.
2. Every new or changed rule has a gate test and a case in that check's eval, in the same commit.
3. `agent-sense done --run` passes, then `agent-sense all` shows no `fail`.
4. `agent-sense features --run` passes; for a change to `page` or `launch`, and before a release,
   with `--include-manual`. Never edit a `state` to `passing` by hand.
5. `docs/<name>.md` and `docs/results.md` say what the code now does, with numbers you produced.
6. For a change that adds or alters a rule: the `evaluator` subagent
   (`.claude/agents/evaluator.md`) returned PASS on the diff. It reruns things itself; do not hand
   it your conclusion.
7. `PROGRESS.md` is updated, `DECISIONS.md` has an entry if a design choice was made, the work is
   committed and pushed to `main`, and the CI run of that commit is green (`gh run watch`).

## End of a session

Update `PROGRESS.md` (Current State, In Progress, Known Issues, Next Steps), remove debugging
output and stray files (`git status --short` shows only intended changes), commit and push. Work
that is not finished is written under In Progress with the next concrete step, not left implied.

## Hard constraints

1. Services never import each other and never read each other's files. Shared shapes go in
   `contracts/`. `agent-sense boundary` enforces this here.
2. A service does not print, does not call `sys.exit`, and raises `RuntimeError` when it cannot
   run. It returns `{"findings": [...], "stats": {...}}` and every rule it emits is in `RULES`.
3. No check calls a model or sends project data anywhere. `page` and `launch` load only the URL
   they are given.
4. No runtime dependency besides the standard library; Playwright stays an optional extra.
5. The Stop hook must never trap a session: it exits 0 whatever the config or a record holds, is
   silent on any internal error, never blocks when `stop_hook_active`, blocks only on `fail`, and
   only reads (it never runs a test or verify command).
6. A new service is added in four places: `SERVICES` in `agent_sense/cli.py`, `force-include` in
   `pyproject.toml`, the README table, `docs/results.md`.
7. Text from a checked project or page goes through `clean()` before it reaches a terminal (every
   non-printable character is dropped). JSON output goes through `json_text()`, which keeps them as escapes.
8. Gate tests use no network and no browser, and wait only where a timeout is the thing under
   test. New tests build their commands from `sys.executable` so they mean the same in `sh` and
   `cmd.exe`. Older exceptions: the done tests use `true`, one features test uses `exit 9`.
9. Unicode escapes written through an agent's file tools can arrive as the literal character.
   After writing a `.py` file that contains `\u` escapes, grep for them and restore any that
   were replaced. Invisible and direction-control characters must stay escapes.
10. Do not weaken a test, an eval threshold, `.agent-sense.toml` or the hook to get a pass. If a
    finding is wrong, fix the rule and add the case that shows it.
11. Reports and docs state what was measured, on what sample. No field claim without a run.
12. This repository may become public: no private project names, host names, personal paths or
    email addresses in code, tests, fixtures, docs or commit messages (`scripts/release_check.py`).
13. Publishing is the owner's (`docs/release.md`): an agent changes no visibility, makes no tag,
    release or upload, and rewrites no pushed history.

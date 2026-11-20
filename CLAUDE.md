# agent-sense: notes for agents working in this repository

Local, deterministic checks for work done with coding agents, shipped as one command,
`agent-sense`. No model and no network service is used by any check. User-facing description:
`README.md`. This file is the map; details live in the files it points to.

## Start of a session

1. Read `PROGRESS.md` (where things stand, known issues, next steps).
2. Read `features.json` (what exists and the command that proves each item).
3. `git log --oneline -5` and `git status --short`.
4. `scripts/setup.sh` if `agent-sense --help` fails or hooks are not active.

## Map

| path | what it is |
|---|---|
| `agent_sense/cli.py` | The only glue: argument parsing, config, text output, `init`, the Stop hook |
| `contracts/` | Shapes shared by all services (`schema.py`), described in `contracts/README.md` |
| `services/<name>/<name>_lib.py` | One check each: done, deps, context, boundary, features, page, launch, session |
| `services/<name>/tests/` | Gate tests of that check |
| `tests/` | Gate tests of the command line and of the eval scripts' own logic |
| `evals/` | Known-answer evals, one or more per check; fixtures for the browser evals |
| `docs/<name>.md` | Rules, how each is decided, evidence and limits of one check |
| `docs/results.md` | Every measured number and how it was produced |
| `scripts/` | `test.sh` (gate), `setup.sh`, `e2e.sh` |
| `runs/` | Eval output, ignored by git |

## Commands

```bash
scripts/setup.sh                 # once: hooks, editable install on PATH, Chromium, then the gate
scripts/test.sh                  # gate: all tests, under 2 s by pytest's clock, also the pre-commit hook
scripts/test.sh -k test_deps_lib # one check's tests
uv run --quiet --no-project --python 3.12 evals/deps_eval.py                     # an eval (free)
uv run --quiet --no-project --python 3.12 --with playwright evals/page_eval.py   # browser eval, about 90 s
scripts/e2e.sh                   # build the wheel, install it, drive it on a throwaway repository
agent-sense done --run           # run the gate and record that it passed on these files
agent-sense all                  # done, deps, context, boundary, features on this repository
agent-sense features --run       # run every feature's verify command; the only writer of `passing`
```

`evals/done_hook_eval.py` and `evals/launch_field.py` cost money or touch outside sites. Run them
only when asked.

## Definition of done

A change is done when all of these hold, in this order:

1. `scripts/test.sh` is green and still under 2 s. The clock is the time pytest prints
   (`N passed in X s`); the wall clock of the script adds about 0.2 s for `uv`.
2. Every new or changed rule has a gate test and a case in that check's eval, in the same commit.
3. `agent-sense done --run` passes, then `agent-sense all` shows no `fail`.
4. `agent-sense features --run` passes for the features the change touches (all of them before a
   release). Never edit a `state` to `passing` by hand.
5. `docs/<name>.md` and `docs/results.md` say what the code now does, with numbers you produced.
6. For a change that adds or alters a rule: the `evaluator` subagent
   (`.claude/agents/evaluator.md`) returned PASS on the diff. It reruns things itself; do not hand
   it your conclusion.
7. `PROGRESS.md` is updated, `DECISIONS.md` has an entry if a design choice was made, and the
   work is committed.

## End of a session

Update `PROGRESS.md` (Current State, In Progress, Known Issues, Next Steps), remove debugging
output and stray files (`git status --short` shows only intended changes), commit. Work that is
not finished is written under In Progress with the next concrete step, not left implied.

## Hard constraints

1. Services never import each other and never read each other's files. Shared shapes go in
   `contracts/`. `agent-sense boundary` enforces this here.
2. A service does not print, does not call `sys.exit`, and raises `RuntimeError` when it cannot
   run. It returns `{"findings": [...], "stats": {...}}` and every rule it emits is in `RULES`.
3. No check calls a model or sends project data anywhere. `page` and `launch` load only the URL
   they are given.
4. No runtime dependency besides the standard library; Playwright stays an optional extra.
5. The Stop hook must never trap a session: silent on any internal error, never blocks when
   `stop_hook_active`, blocks only on `fail`, and only reads (it never runs a test or verify command).
6. A new service is added in four places: `SERVICES` in `agent_sense/cli.py`, `force-include` in
   `pyproject.toml`, the README table, `docs/results.md`.
7. Text from a checked project or page is passed through `clean()` before it reaches a terminal.
8. Gate tests use no network and no browser, and wait only where a timeout is the thing under
   test (under 1 s). Commands in them are built from `sys.executable`, so they mean the same in
   `sh` and `cmd.exe` (CI runs the gate on Windows).
9. Unicode escapes written through an agent's file tools can arrive as the literal character.
   After writing a `.py` file that contains `\u` escapes, grep for them and restore any that
   were replaced. Invisible and direction-control characters must stay escapes.
10. Do not weaken a test, an eval threshold, `.agent-sense.toml` or the hook to get a pass. If a
    finding is wrong, fix the rule and add the case that shows it.
11. Reports and docs state what was measured, on what sample. No field claim without a run.
12. This repository may become public: no private project names, host names, personal paths or
    email addresses in code, tests, fixtures, docs or commit messages.

# agent-sense

Local checks for work done with coding agents. Each one answers a question an agent tends to get
wrong or cannot see, from files, git and a browser on your own machine. No model, no API key,
nothing leaves the machine.

| command | question | needs |
|---|---|---|
| `agent-sense done` | It says it is done. Did the tests pass on the files as they are now, and were the tests weakened to get there? | git |
| `agent-sense page URL` | It says the page works. Are there script errors, sideways scroll, clipped text, buttons that do nothing, Enter that submits in the middle of Japanese input? | Playwright |
| `agent-sense deps` | Does the code import a package nothing declares? | |
| `agent-sense context` | Do CLAUDE.md, AGENTS.md and memory files point at files and commands that still exist? | |
| `agent-sense boundary` | Does one service reach into another service's directory? | |
| `agent-sense features` | The feature list says passing. Did the feature's own verify command say so? | |
| `agent-sense launch URL` | For a service aimed at Japan: which outside services does the site send data to, are they named on its policy pages, are the usual pages linked? Reports what was found, not whether the site is lawful. | Playwright |
| `agent-sense session` | What went wrong repeatedly in past sessions, from the local Claude Code logs? | |

## Install

```bash
uv tool install "agent-sense[browser] @ git+https://github.com/sabachan3/agent-sense"
playwright install chromium-headless-shell     # only for page and launch; or pass --channel chrome
```

From a clone: `uv tool install --editable ".[browser]"`. Python 3.11 or newer.

## The main use: stop the agent from stopping early

```bash
cd your-project
agent-sense init --hook      # writes .agent-sense.toml and adds a Stop hook to .claude/settings.json
agent-sense done --run       # run the tests once so there is a record
```

From then on, when Claude Code tries to end its turn after editing files without a passing test
run on those exact files, or after adding a `skip` to a test, the hook answers:

```
agent-sense: this cannot be reported as done yet.
FAIL done  (1 fail, 0 warn, 0 info)
  [fail] tests-stale: files changed after the last passing test run (1 changed): calc.py
      fix: agent-sense done --run   (runs: scripts/test.sh)
```

and the agent goes on working. In a real headless run (`claude -p`, told not to run tests) the
hook blocked once, the agent ran `agent-sense done --run`, and the turn then ended with the check
passing. The hook acts only in projects that have `.agent-sense.toml`, blocks only on `fail`
findings, never blocks twice in a row, and stays silent on any internal error.

For commits: `agent-sense all` runs `done`, `deps`, `context` and `boundary` in one go.

## The checks

Each has its own page with the rules, how each is decided, the evidence and the limits.

- [done](docs/done.md): `tests-not-run`, `tests-stale`, `tests-failed`, `test-skipped`,
  `assertions-removed`, `test-deleted` fail; `test-removed`, `expectation-changed`, `stub-added`,
  `uncommitted`, `unpushed` warn.
- [page](docs/page.md): `console-error`, `request-failed`, `horizontal-overflow`, `dead-control`,
  `ime-enter-submit`, `default-title` fail; `text-clipped`, `overlap`, `placeholder-link`,
  `placeholder-text` warn. Widths 360, 768 and 1280. Clicking and the IME test run only on
  localhost and file URLs unless asked.
- [deps](docs/deps.md): `undeclared-import` for Python and JavaScript or TypeScript,
  `not-in-lockfile`, `unused-dependency`.
- [context](docs/context.md): `missing-path`, `missing-script`, `memory-dangling`,
  `memory-unindexed`, `oversized`.
- [boundary](docs/boundary.md): `cross-service-import`, `cross-service-syspath`, `cross-service-path`
  for a `services/<name>/` layout.
- [features](docs/features.md): `features-invalid`, `feature-failing` fail; `passing-unverified`,
  `no-verify`, `wip-limit` warn. `features.json` lists each feature with the command that proves
  it; only `agent-sense features --run` writes the state `passing`.
- [launch](docs/launch.md): `external-undisclosed`, `policy-missing`, `terms-missing`,
  `contact-missing`, `tokushoho-missing`, `tokushoho-incomplete`, `test-key`, `localhost-reference`.
- [session](docs/session.md): `repeated-failure`, `blind-retry`, `repeated-flow`,
  `claim-then-failure`, `ended-unverified`, `interruptions`. Never fails a build.

## Common options

```
--root DIR                 project to check (default: the git repository around the current directory)
--fail-on fail|warn|never  what makes the exit code 1 (default: fail)
--format text|json         JSON follows contracts/README.md, report version 1
--out FILE                 also write the JSON report
```

Exit code 0 when nothing fails, 1 when a check fails, 2 when the tool could not run.
Per-project settings live in `.agent-sense.toml`, one table per check; see each check's page.

## Evidence

See [docs/results.md](docs/results.md) for the numbers and how they were produced. In short: every
check has known-answer cases that must all pass, and each was also run on real projects or pages
with the findings read by hand. The samples are small and come from one person's machine.

## Limits

- These checks read what is on disk and what a browser reports. They do not judge whether the
  work is good, only whether specific claims are true.
- `done` trusts the test command. A command that always succeeds passes.
- `page` sees the page as first loaded: no login, no iframes, no shadow DOM. Its IME test replays
  Chromium's event order and was not run on Safari or Windows.
- `launch` is not legal advice, cannot see tags behind a consent banner or a login, and depends on
  a hand-made vendor table.
- `session` reads a log format that is internal to Claude Code and can change with any release.
- Built on macOS. CI runs the gate tests on Linux, macOS and Windows and the evals on Linux and
  macOS; `page` and `launch` were never run on Windows. On Windows the `done` test command runs
  through `cmd.exe`.

## Development

```bash
scripts/test.sh                                   # gate tests, about 2 s, also the pre-commit hook
uv run --no-project --python 3.12 evals/done_eval.py
uv run --no-project --python 3.12 evals/session_pushback_eval.py
uv run --no-project --python 3.12 --with playwright evals/page_eval.py
scripts/setup.sh                                  # once after cloning: hooks, the command on PATH, Chromium
scripts/e2e.sh                                    # build the wheel, install it, run it on a throwaway repository
agent-sense features --run                        # every feature's own verification
```

For agents working here: [CLAUDE.md](CLAUDE.md), [PROGRESS.md](PROGRESS.md), [DECISIONS.md](DECISIONS.md).

Layout: one directory per check under `services/`, each with its own code and tests and no
imports from the others; `contracts/` holds the shapes they share; `agent_sense/cli.py` is the
only glue. `agent-sense boundary` checks this on itself.

MIT license.

# agent-sense

Local checks for work done with coding agents. Each one answers a question an agent tends to get
wrong or cannot see, from files, git and a browser on your own machine. No model, no API key,
nothing leaves the machine. The same input gives the same answer.

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

Status: version 0.1.0, alpha. Rule names, the JSON report and the settings can still change.

## Install

Needs Python 3.11 or newer and git. The command has no dependency outside the standard library;
`page` and `launch` also need Playwright and a Chromium, which the `browser` extra brings.

From the repository, with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install "agent-sense @ git+https://github.com/sabachan3/agent-sense"            # the six checks that need no browser
uv tool install "agent-sense[browser] @ git+https://github.com/sabachan3/agent-sense"   # all eight
```

With pip, inside a virtual environment:
`pip install "agent-sense[browser] @ git+https://github.com/sabachan3/agent-sense"`.

`page` and `launch` need a browser. Either pass `--channel chrome` (or `msedge`) to drive one that
is installed, or download Playwright's Chromium once: `playwright install chromium-headless-shell`
in the environment pip installed into, or `uvx playwright install chromium-headless-shell` beside
a uv tool install. The download has to match the Playwright version the tool runs with; if the
check still reports a missing browser, use `--channel`.

From the package index, after the first release. There is none yet, and the name is not settled:
`agent-sense` on pypi.org is an unrelated project today, so installing that name does not give
this tool. The commands will be these, with the name the release is published under:

```bash
uv tool install "NAME-ON-THE-INDEX[browser]"
pipx install "NAME-ON-THE-INDEX[browser]"
```

Check what is installed with `agent-sense --version`.

## One minute to a first result

In any git repository that has a test command:

```bash
cd your-project
agent-sense init         # writes .agent-sense.toml with the test command it finds
agent-sense done         # exit 1: no test run is recorded yet
agent-sense done --run   # runs the test command and records which files it passed on
```

Now edit any source file and ask again:

```
$ agent-sense done
FAIL done  (1 fail, 2 warn, 0 info)
  [fail] tests-stale: files changed after the last passing test run (1 changed): calc.py
      fix: agent-sense done --run   (runs: scripts/test.sh)
  [warn] uncommitted: 3 uncommitted path(s): calc.py, .agent-sense.toml, __pycache__/
      fix: commit the work
  [warn] unpushed: the current branch has no upstream, so nothing here is pushed
      fix: git push -u origin HEAD
```

`init` looks for `scripts/test.sh`, a `test` script in `package.json`, a `test` target in a
Makefile, `go.mod`, `Cargo.toml`, then pytest files. If it finds none, set the command yourself:
`test = "your command"` under `[done]` in `.agent-sense.toml`. The record of the last passing run
is kept inside `.git`, so it is local to one clone and never committed. `agent-sense all` runs
`done`, `deps`, `context` and `boundary` in one go, which suits a pre-commit hook.

## The Stop hook: keep the agent from stopping early

The hook is off until you switch it on, one project at a time:

```bash
cd your-project
agent-sense init --hook      # writes .agent-sense.toml and adds a Stop hook to .claude/settings.json
agent-sense done --run       # run the tests once so there is a record
```

From then on, when Claude Code tries to end its turn after editing files without a passing test
run on those exact files, or after adding a `skip` to a test, the hook answers with the report
above under the line `agent-sense: this cannot be reported as done yet.` and the agent goes on
working. In a measured run of 36 headless sessions, 15 of 15 sessions with the hook ended with a
passing test run after the last edit, against 5 of 15 without it
([results](https://github.com/sabachan3/agent-sense/blob/main/docs/results.md): one model, one small repository, tasks of a few lines).

What the hook does and does not do:

- It acts only in projects that have `.agent-sense.toml`, and blocks only on `fail` findings of
  the checks listed under `[hook] checks` (default: `done`).
- It never blocks twice in a row, exits 0 and stays silent on any internal error, and only reads:
  it never runs your tests.
- It needs the `agent-sense` command on the `PATH` that Claude Code sees.
- To switch it off, remove the `agent-sense hook stop` entry from `.claude/settings.json`, or
  delete `.agent-sense.toml`.

`.claude/settings.json` is usually committed, so the hook then applies to everyone who works on
the project with Claude Code. Where the command is not installed, the hook command fails to start
and Claude Code reports a hook error; it does not block the stop.

## The checks

One line each; the linked page has the rules, how each is decided, the evidence and the limits.

- [done](https://github.com/sabachan3/agent-sense/blob/main/docs/done.md): `tests-not-run`, `tests-stale`, `tests-failed`, `test-skipped`,
  `assertions-removed`, `test-deleted` fail; `test-removed`, `expectation-changed`, `stub-added`,
  `uncommitted`, `unpushed` warn.
- [page](https://github.com/sabachan3/agent-sense/blob/main/docs/page.md): `console-error`, `request-failed`, `horizontal-overflow`, `dead-control`,
  `ime-enter-submit`, `default-title` fail; `text-clipped`, `overlap`, `placeholder-link`,
  `placeholder-text` warn. Widths 360, 768 and 1280. Clicking and the IME test run only on
  localhost and file URLs unless asked.
- [deps](https://github.com/sabachan3/agent-sense/blob/main/docs/deps.md): `undeclared-import` for Python and JavaScript or TypeScript,
  `not-in-lockfile`, `unused-dependency`.
- [context](https://github.com/sabachan3/agent-sense/blob/main/docs/context.md): `missing-path`, `missing-script`, `memory-dangling`,
  `memory-unindexed`, `oversized`.
- [boundary](https://github.com/sabachan3/agent-sense/blob/main/docs/boundary.md): `cross-service-import`, `cross-service-syspath`, `cross-service-path`
  for a `services/<name>/` layout.
- [features](https://github.com/sabachan3/agent-sense/blob/main/docs/features.md): `features-invalid`, `feature-failing` fail; `passing-unverified`,
  `no-verify`, `wip-limit` warn; `manual-not-run` info. `features.json` lists each feature with the command that proves
  it; only `agent-sense features --run` writes the state `passing`.
- [launch](https://github.com/sabachan3/agent-sense/blob/main/docs/launch.md): `external-undisclosed`, `policy-missing`, `terms-missing`,
  `contact-missing`, `tokushoho-missing`, `tokushoho-incomplete`, `test-key`, `localhost-reference`.
- [session](https://github.com/sabachan3/agent-sense/blob/main/docs/session.md): `repeated-failure`, `blind-retry`, `repeated-flow`,
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

## What it reads and what it sends

No check sends anything anywhere. `done`, `deps`, `context`, `boundary` and `features` read the
project directory and run git; `done --run` and `features --run` run the commands you configured.
`session` reads the Claude Code logs under `~/.claude/projects` on your machine and prints counts.
`page` and `launch` open the URL you give them in a local Chromium, and that page loads whatever
it loads; `launch` also follows the site's links to its policy pages.

## Supported systems

This is what the continuous integration of the repository runs on every push. Nothing beyond it
is claimed.

| | Linux | macOS | Windows |
|---|---|---|---|
| gate tests, Python 3.12 | yes | yes | yes |
| gate tests, Python 3.11, 3.13, 3.14 | yes | | |
| known-answer evals of `done`, `deps`, `context`, `boundary`, `features`, `session` | yes | yes | |
| `page` and `launch` evals in Chromium | yes | | |
| build, install of the wheel and the sdist, the installed command driven end to end | yes | | |

The tool was written and used on macOS, where the browser evals were also run by hand. `page` and
`launch` were never run on Windows. On Windows the test command of `done` runs through `cmd.exe`.

## Evidence

See [docs/results.md](https://github.com/sabachan3/agent-sense/blob/main/docs/results.md) for the numbers and how they were produced. In short: every
check has known-answer cases that must all pass, and every check except `features` was also run
on real projects or pages with the findings read by hand. The samples are small and come from one
person's machine.

## Limits

- These checks read what is on disk and what a browser reports. They do not judge whether the
  work is good, only whether specific claims are true.
- `done` trusts the test command and `features` trusts the verify command. A command that always
  succeeds passes.
- `page` sees the page as first loaded: no login, no iframes, no shadow DOM. Its IME test replays
  Chromium's event order and was not run on Safari or Windows.
- `launch` is not legal advice, cannot see tags behind a consent banner or a login, and depends on
  a hand-made vendor table whose hosts were checked by DNS only.
- `session` reads a log format that is internal to Claude Code and can change with any release.
- The Stop hook is written for Claude Code. Other agents can call `agent-sense done` and
  `agent-sense all` from their own hooks or from a pre-commit hook; nothing here sets that up.
- No finding was judged by a second person, and misses were measured only on planted cases.

## Development

Needs [uv](https://docs.astral.sh/uv/) and git.

```bash
scripts/setup.sh                                  # once after cloning: hooks, the command on PATH, Chromium, then the gate
scripts/test.sh                                   # gate tests, about 2 s, also the pre-commit hook
uv run --no-project --python 3.12 evals/done_eval.py
uv run --no-project --python 3.12 evals/session_pushback_eval.py
uv run --no-project --python 3.12 --with playwright evals/page_eval.py
scripts/e2e.sh                                    # build wheel and sdist, check them, install each, run it on a throwaway repository
agent-sense features --run                        # each feature's own verification, except those marked manual (--include-manual for all)
```

`evals/done_hook_eval.py` starts paid agent sessions and `evals/launch_field.py` visits the sites
in the list it is given; neither runs in the gate or in CI.

Layout: one directory per check under `services/`, each with its own code and tests and no
imports from the others; `contracts/` holds the shapes they share; `agent_sense/cli.py` is the
only glue. `agent-sense boundary` checks this on itself. Notes for agents working in the
repository are in `CLAUDE.md`, `PROGRESS.md` and `DECISIONS.md`; changes are in
[CHANGELOG.md](https://github.com/sabachan3/agent-sense/blob/main/CHANGELOG.md), and the steps of a release in
[docs/release.md](https://github.com/sabachan3/agent-sense/blob/main/docs/release.md).

## License

MIT, see [LICENSE](https://github.com/sabachan3/agent-sense/blob/main/LICENSE). The command has no runtime dependency. The optional
`browser` extra installs Playwright (Apache-2.0) and greenlet (MIT and PSF-2.0); the Chromium it
downloads carries its own licenses. Product and company names in `services/launch/vendors.json`
belong to their owners and are there only to recognise their hosts.

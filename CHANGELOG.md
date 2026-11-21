# Changelog

Releases of agent-sense, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is below 1.0, rule
names, the JSON report and the settings in `.agent-sense.toml` can still change in a minor release.

## [0.1.0] - not released yet

The first release. Everything below was written between 2026-11-19 and 2026-11-22; the date of
the release replaces "not released yet" when it is made.

### Added

- One command, `agent-sense`, with eight checks. None of them uses a model or a network service.
  - `done`: did the tests pass on the files as they are now, and did the change weaken the tests
    (`tests-not-run`, `tests-stale`, `tests-failed`, `test-skipped`, `assertions-removed`,
    `test-deleted`, and five warnings). "Stale" compares file content by SHA-256, not time.
  - `deps`: imports of packages that no manifest, lock file or inline script block declares, for
    Python and JavaScript or TypeScript. An inline script block (PEP 723) also covers modules
    without a block in the same service.
  - `context`: instruction and memory files (`CLAUDE.md`, `AGENTS.md` and others) that name paths
    and scripts that no longer exist. A path whose whole directory is gone is reported at `info`.
  - `boundary`: one service importing, putting on `sys.path` or naming a path inside another, for
    a `services/<name>/` layout.
  - `features`: a feature list (`features.json`) in which only a verify command that exits 0
    writes the state `passing`, and a failing run takes it back. `"manual": true` marks a
    verification that a plain `--run` leaves out and names. A run changes only the `state` values
    in the file, stops a timed-out command with its whole process group, and refuses a list
    nested deeper than 1,500 levels on every Python version.
  - `page`: loads a page in Chromium at 360, 768 and 1280 px and reports script errors, failed
    requests, sideways scroll, dead controls, a default title, and Enter that submits in the
    middle of Japanese (IME) input. Needs the `browser` extra.
  - `launch`: for a service aimed at Japan, the outside services a site contacts against the ones
    its policy pages name, and missing policy, terms, contact and statements pages. Reports what
    was observed, not whether a site is lawful. Needs the `browser` extra.
  - `session`: repeated failures, blind retries, repeated flows and unverified endings counted
    from the local Claude Code session logs.
- `agent-sense all` (the checks that need no browser, for a pre-commit hook), `agent-sense init`
  (writes `.agent-sense.toml`; `--hook` adds the Stop hook to `.claude/settings.json`), and
  `agent-sense --version`.
- A Claude Code Stop hook, `agent-sense hook stop`: it refuses a stop while a configured check
  has a `fail` finding. It acts only in projects that have `.agent-sense.toml`, never blocks
  twice in a row, only reads, and exits 0 and stays silent on any internal error.
- Text and JSON reports (report version 1, described in `contracts/README.md`), exit codes 0, 1
  and 2, `--fail-on`, `--out`, and per-project settings in `.agent-sense.toml`.
- Output safety: text from a checked project or page is stripped of every non-printable
  character before it reaches a terminal; JSON output keeps such characters as escapes.

### Tested on

- Gate tests on Linux, macOS and Windows with Python 3.12, and on Linux with Python 3.11, 3.13
  and 3.14. Known-answer evals on Linux and macOS; the two browser evals in Chromium on Linux.
- Never run on Windows: `page` and `launch`. Measured numbers and their sample sizes are in
  `docs/results.md`; known limits are in the README and in each check's page under `docs/`.

[0.1.0]: https://github.com/sabachan3/agent-sense/commits/main

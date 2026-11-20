# Progress

Updated 2026-11-20. Read with `features.json` (what exists) and `docs/results.md` (what was measured).

## Current State

- Version 0.1.0, private repository, not on PyPI. Installed on the author's machine as an editable
  tool, so the command runs this working copy.
- Eight checks: done, deps, context, boundary, features, page, launch, session, plus the Claude
  Code Stop hook (`agent-sense hook stop`).
- Gate: 406 tests in about 2.0 s by pytest's clock (see Known Issues). Known-answer evals all at threshold (numbers in
  `docs/results.md`). CI runs the gate on Linux, macOS and Windows, the evals on Linux and macOS,
  the browser evals on Linux, and installs the built wheel.
- The Stop hook is enabled in this repository. Other projects opt in with `agent-sense init --hook`.

## Completed

- 2026-11-19: the seven original checks, the command line, the Stop hook, CI, 0.1.0.
- 2026-11-20: Stop hook measured with a real agent (36 headless sessions); context recall on real
  history; launch run on 40 Japanese commercial sites; pushback patterns rewritten and measured.
- 2026-11-20: `features` check (feature list where only a passing verify command writes `passing`).
- 2026-11-20: deps false positive fixed (helper module covered by a sibling script's inline block).
- 2026-11-20: harness files for this repository: `CLAUDE.md`, this file, `DECISIONS.md`,
  `features.json`, `scripts/setup.sh`, `scripts/e2e.sh`, the `evaluator` subagent.
- 2026-11-20: corrections after the first run of the evaluator on the two changes above. `features`:
  bad or non-positive timeouts are errors, a damaged record entry counts as none, ids are matched
  whole, the list is written before the record, a timeout stops the whole process tree and keeps
  the output, and a rewrite changes only the `state` values. The Stop hook runs each check on its
  own, so one that cannot run no longer switches the others off. Stale numbers in `docs/deps.md`
  and `docs/results.md` corrected; gate test commands are built from the interpreter path.
- 2026-11-20: corrections after the second run of the evaluator. `features`: a timeout above one
  day is refused; a bad config timeout is an error on a plain read too; a command that cannot be
  started fails its own feature and the others still run; deeply nested lists are rewritten; a
  hangup or termination of the tool stops the running command; a record that cannot be written
  puts the list back; the id message says how an id must start. Command line: `clean()` drops every
  non-printable character and `json_text()` escapes them, so a lone surrogate no longer crashes a
  report; the Stop hook guards the building and printing of its answer. Eval: 6 new scenarios
  (24/24). Tests: 24 new cases, and all 24 fail on the previous commit's code (8 timeout bound,
  1 config timeout on a read, 2 cannot start, 1 deep nesting, 1 record put back, 1 id message,
  2 signals, 1 handler restore, 2 clean and json_text, 2 surrogate report, 2 hook, 1 command-line
  exit code). The damaged-record case that tested nothing new was replaced. No platform skip was
  added: on Windows the signal tests assert that nothing is installed. `git init` is back in the four tests it
  had been removed from, so they exercise the record inside `.git`.

## In Progress

Nothing.

## Known Issues

- Built and used on macOS. `page` and `launch` were never run on Windows; on Linux only in CI.
- `done` trusts the test command, `features` trusts the verify command. `true` passes both.
- `page`: the IME check replays Chromium's event order through CDP. Not a real IME; not run on
  Safari or on Windows.
- `launch`: the vendor table's hosts were checked by DNS only, not against vendor documentation.
  On 40 commercial sites one policy link is still missed and `external-undisclosed` (31 of 37
  sites) was not reviewed by hand. Not legal advice.
- `context`: 17 of 18 real stale references are found; noise in one large monorepo (85 findings)
  was not read.
- `session`: reads a log format internal to Claude Code that can change with any release; local
  sample too small to rank problems; pushback detection precision 0.75, recall 0.60 on held-out
  sentences.
- `features`: synthetic evidence only. The proof of a passing run is local to a machine. A process
  that leaves the command's process group is not stopped at a timeout, and the tool killed with
  SIGKILL stops nothing. Signal handling was tested on macOS only; Windows has no equivalent here.
- `deps`: lock-file rules were exercised on synthetic lock files only. The sibling inline-block
  rule hides a helper import when a sibling script does not itself declare the package.
- The gate is at the edge of its 2 s budget, and over it by wall clock. Measured 2026-11-20 with
  other jobs running on the machine (load average 2.5 to 3.8), five runs: 1.94 to 2.06 s by
  pytest's clock (median 1.96 s), 2.16 to 2.40 s wall clock for `scripts/test.sh`. The commit
  before, same conditions: 1.89 to 1.97 s and 2.12 to 2.19 s. The second review added 25 test
  cases. `--dist worksteal` is now on in `scripts/test.sh`: with the larger features test file it
  took the gate from about 2.55 s to about 2.0 s (it gave nothing before that file grew). Not yet
  tried: a faster repository template in the done tests, running the gate without `uv run`.

## Next Steps

1. Use `features` in real projects for a while and record what it catches and what it gets wrong.
2. Review the 31 `external-undisclosed` findings of the commercial-site run by hand.
3. Run `page` and `launch` on Windows once.
4. Decisions that belong to the owner, not to an agent: making this repository public, a PyPI
   release, enabling the Stop hook globally instead of per project.

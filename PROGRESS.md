# Progress

Updated 2026-11-20. Read with `features.json` (what exists) and `docs/results.md` (what was measured).

## Current State

- Version 0.1.0, private repository, not on PyPI. Installed on the author's machine as an editable
  tool, so the command runs this working copy.
- Eight checks: done, deps, context, boundary, features, page, launch, session, plus the Claude
  Code Stop hook (`agent-sense hook stop`).
- Gate: 410 tests, about 1.9 s by pytest's clock on a busy machine (budget and measurements under Known Issues). Known-answer evals all at threshold (numbers in
  `docs/results.md`). CI runs the gate on Linux, macOS and Windows, the evals on Linux and macOS,
  the browser evals on Linux, and installs the built wheel.
- The Stop hook is enabled in this repository. Other projects opt in with `agent-sense init --hook`.

## Completed

- 2026-11-19: the first seven checks (`features`, the eighth, came the next day), the command line, the Stop hook, CI, 0.1.0.
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
  (24/24). Tests: 25 new cases, and all 25 fail on the code before this review (commit 0fd98f7) (8 timeout bound,
  1 config timeout on a read, 2 cannot start, 1 deep nesting, 1 record put back, 1 id message,
  2 signals, 1 handler restore, 2 clean and json_text, 2 surrogate report, 2 hook, 1 command-line
  exit code). The damaged-record case that tested nothing new was replaced. The 25th (a list nested beyond the fixed depth is invalid, not a crash) came with the fix for
  Python 3.11, where the first push failed CI: there the JSON parser counts nesting against the
  recursion limit, so the depth is now raised to a fixed 3,000 while a list is read. No platform skip was
  added: on Windows the signal tests assert that nothing is installed. `git init` is back in the four tests it
  had been removed from, so they exercise the record inside `.git`.

- 2026-11-20: `features` learned `"manual": true` (a plain `--run` leaves such a feature out and
  names it; `--only` and `--include-manual` run it); the two browser features here are marked so.
  The evaluator definition classes findings A, B, C and fails only on A. Gate: six workers and
  `-S` for test commands. `CLAUDE.md` and this file corrected after a cold-start reading
  (budget, prerequisites, push policy, startable next steps).

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
- Gate time. Budget (stated in `CLAUDE.md`): median of three runs under 2.5 s by pytest's clock.
  Measured 2026-11-20 with an editor, browsers and other agent sessions running (load average 9 to
  13), eight runs interleaved with the commit before: this commit 1.78 to 2.16 s by pytest's clock
  (median 1.92 s), 2.01 to 2.39 s wall clock (median 2.15 s); the commit before 2.07 to 3.27 s
  (median 2.18 s) and 2.30 to 3.76 s (median 2.41 s). What changed: six workers instead of four,
  and test commands start the interpreter with `-S`. Not measured on an idle machine. The wall
  clock is still over the 2 s that was first wished for; the cost is in the tests themselves
  (each `done` and `features` test starts git or an interpreter several times), not in `uv`
  (0.04 s) or collection (0.2 s).

## Next Steps

Marked [alone]: a new session can start it from this repository. [owner]: needs a decision or
judgment of the owner. [machine]: needs data or hardware that is not in the repository.

1. [alone] Bring the gate's wall clock under 2 s on a busy machine. Start with
   `scripts/test.sh --durations=25`; the heaviest files are `services/features/tests`,
   `tests/test_cli.py` and `services/done/tests` (each test starts git or an interpreter several
   times). Do not remove or weaken tests.
2. [alone] Add an eval for `clean()` and `json_text()` in `agent_sense/cli.py` that sweeps every
   Unicode category through text and JSON output (today only gate tests with chosen characters).
3. [machine] Review by hand the 31 `external-undisclosed` findings of the commercial-site run. The
   records are in `runs/launch_field_jp3_records.json` on the author's machine only (`runs/` is not
   in git); without that file, rerunning `evals/launch_field.py` on `evals/launch_jp_sites.txt`
   touches 40 outside sites and needs the owner's go-ahead.
4. [machine] Run `page` and `launch` on Windows once: `uv run --no-project --with playwright
   evals/page_eval.py --channel msedge` and the same for `evals/launch_eval.py`.
5. [owner] Use `features` in real projects for a while and record what it catches and gets wrong.
6. [owner] Decisions that are not an agent's: making this repository public, a PyPI release,
   enabling the Stop hook globally instead of per project.

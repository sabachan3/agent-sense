# Progress

Updated 2026-11-20. Read with `features.json` (what exists) and `docs/results.md` (what was measured).

## Current State

- Version 0.1.0, private repository, not on PyPI. Installed on the author's machine as an editable
  tool, so the command runs this working copy.
- Eight checks: done, deps, context, boundary, features, page, launch, session, plus the Claude
  Code Stop hook (`agent-sense hook stop`).
- Gate: 381 tests in 1.8 to 1.9 s by pytest's clock (see Known Issues). Known-answer evals all at threshold (numbers in
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
- `features`: synthetic evidence only. The proof of a passing run is local to a machine.
- `deps`: lock-file rules were exercised on synthetic lock files only. The sibling inline-block
  rule hides a helper import when a sibling script does not itself declare the package.
- The gate is at the edge of its 2 s budget. Measured 2026-11-20 with other jobs running on the
  machine (load average about 4): 1.76 to 1.93 s by pytest's clock, 2.00 to 2.30 s wall clock for
  `scripts/test.sh`; the commit before, same conditions, 1.61 to 1.73 s and 1.83 to 1.97 s. The 20
  new tests cost about 0.2 s. Tried without gain: 6 and 8 xdist workers, `--dist worksteal`. Done:
  the record location is cached per process and the new tests skip `git init` where they can. Not
  yet tried: a faster repository template in the done tests (1.3 s of test time), running the
  gate without `uv run`.

## Next Steps

1. Use `features` in real projects for a while and record what it catches and what it gets wrong.
2. Review the 31 `external-undisclosed` findings of the commercial-site run by hand.
3. Run `page` and `launch` on Windows once.
4. Decisions that belong to the owner, not to an agent: making this repository public, a PyPI
   release, enabling the Stop hook globally instead of per project.

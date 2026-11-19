# Progress

Updated 2026-11-20. Read with `features.json` (what exists) and `docs/results.md` (what was measured).

## Current State

- Version 0.1.0, private repository, not on PyPI. Installed on the author's machine as an editable
  tool, so the command runs this working copy.
- Eight checks: done, deps, context, boundary, features, page, launch, session, plus the Claude
  Code Stop hook (`agent-sense hook stop`).
- Gate: 361 tests in about 1.7 s. Known-answer evals all at threshold (numbers in
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
- The gate sits at 1.6 to 1.8 s of a 2 s budget; the next service needs the budget looked at
  (more xdist workers, or a faster repository template in the done tests).

## Next Steps

1. Use `features` in real projects for a while and record what it catches and what it gets wrong.
2. Review the 31 `external-undisclosed` findings of the commercial-site run by hand.
3. Run `page` and `launch` on Windows once.
4. Decisions that belong to the owner, not to an agent: making this repository public, a PyPI
   release, enabling the Stop hook globally instead of per project.

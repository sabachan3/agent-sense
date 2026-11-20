# Decisions

Newest first. Each entry: what was decided, why, and what was rejected. Add an entry when a choice
would otherwise be re-argued by the next session.

## 2026-11-20: output is cleaned by what a character is, not by a list of known bad ones

- **Decision.** `clean()` drops every character for which `str.isprintable()` is false; `json_text()`
  writes every character of category Cc, Cf, Zl, Zp, Cs or Co as a `\u` escape. The Stop hook's
  answer is printed as ASCII-only JSON, and everything the hook does, including building and
  printing that answer, is guarded.
- **Reason.** Found by the second evaluator run: the list of ranges missed lone surrogates, which
  cannot be encoded at all, so one in a record crashed the report and made the hook raise. It also
  missed direction marks and zero-width characters.
- **Rejected.** Adding the surrogate range to the list (the next missed character is the same bug);
  `ensure_ascii=True` for all JSON (makes Japanese text in reports unreadable).

## 2026-11-20: a features run never leaves the list and the record disagreeing, and never leaves a command behind

- **Decision.** The list is written first; if the record then cannot be written, the list is put
  back byte for byte. While a verify command runs, SIGHUP and SIGTERM stop its process group and
  then end the tool the way the signal asked. Timeouts above one day are refused. A command that
  cannot be started fails its own feature (exit 127) and the rest still run.
- **Reason.** Found by the second evaluator run: a read-only record left `passing` over a failed
  record, reported as a lie on the next read; a command in its own process group outlived a hangup
  of the tool; `--timeout 1e10` overflowed the OS timer.
- **Rejected.** Writing the record first (the first review showed the opposite disagreement);
  running commands in the tool's own group (a timeout could then not stop their children).

## 2026-11-20: a features run edits the state in place and owns its process tree

- **Decision.** `features --run` replaces only the changed `state` values in the text of
  `features.json`, checks that the result parses to the intended data, and re-dumps the whole list
  only if it does not. The list is written before the local record. Each verify command runs in
  its own process group (process tree on Windows) and is stopped whole at the timeout; a timeout
  of zero or below is an error.
- **Reason.** Found by the first evaluator run: a re-dump rewrote escapes and number spellings
  the author chose, the record could say "passed" while the list still said `active`, a timed-out
  shell left its children running, and `--timeout 0` silently meant "no limit asked for".
- **Rejected.** A JSON library that keeps formatting (a runtime dependency for one field);
  killing only the shell (leaves the real command running).

## 2026-11-20: `passing` is written only by `features --run`
- **Decision.** The feature list is `features.json`; a state becomes `passing` only when the
  feature's verify command exits 0, and a failing run takes it back to `active`.
- **Why.** A list an agent ticks by hand records belief. Tying the state to an exit code makes the
  list a result.
- **Rejected.** A Markdown checklist (not machine-checkable). Storing the proof in the tracked file
  (an agent could write it; a hash of output proves nothing to a reader). The proof stays local in
  `.git/agent-sense/features.json`, so a fresh clone reports `passing-unverified` until it runs.

## 2026-11-20: reading the feature list is cheap, running it is explicit
- **Decision.** `features` without `--run` runs no command. It is not in the default set of `all`
  or of the Stop hook; a project adds it to `[all] checks`.
- **Why.** Verify commands can take minutes (browser evals here). A hook or a pre-commit that runs
  them would be switched off within a day.
- **Rejected.** Running verification in the Stop hook.

## 2026-11-20: an inline script block covers block-less modules of the same service
- **Decision.** In `deps`, packages declared in a PEP 723 block count as declared for modules
  without a block in the same `services/<name>/` tree (or the same top-level directory).
- **Why.** A real project had helper modules imported by entry scripts that carry the declaration;
  both findings there were false.
- **Rejected.** Following the import graph from each script (more precise, far more code, and
  dynamic `sys.path` layouts defeat it). Project-wide coverage (would hide real misses in other
  services). Known cost: a helper used by a sibling that does not declare the package is missed.

## 2026-11-20: in-progress failures are warnings when only reading
- **Decision.** `feature-failing` is `fail` from `--run` and for a feature marked `passing` whose
  last run failed; for an `active` feature whose last run failed it is `warn` when reading.
- **Why.** An active feature that does not pass yet is the normal state of work. Failing `all` on
  it would block every commit until the feature is finished.
- **Rejected.** `fail` in all cases.

## 2026-11-19: one repository, one command, one service per check
- **Decision.** All checks ship in one package behind `agent-sense <check>`; each lives in
  `services/<name>/` and they share only `contracts/`.
- **Why.** One install and one config file for the user; separate directories so two sessions can
  work on two checks without touching the same files.
- **Rejected.** One package per check (seven installs for one user). A plugin system (nothing
  needs it yet).

## 2026-11-19: no model inside any check
- **Decision.** Every check is deterministic code over files, git and a browser.
- **Why.** A check that gates a commit or a stop must give the same answer twice, cost nothing and
  run offline. The price is that nothing here judges quality.
- **Rejected.** A second-agent review inside `done`. Review by another agent stays outside the
  tool (the `evaluator` subagent in this repository).

## 2026-11-19: the Stop hook blocks on `fail` only and never traps
- **Decision.** The hook acts only in projects with `.agent-sense.toml`, blocks only on `fail`
  findings, never when `stop_hook_active`, exits quietly on any internal error, and only reads.
- **Why.** A hook that can loop or crash a session gets removed. Measured: 15 of 15 sessions
  verified with the hook against 5 of 15 without, each blocked exactly once.
- **Rejected.** Running the tests inside the hook (slow, and side effects in a hook). Blocking on
  warnings.

## 2026-11-19: stale means content changed, not time passed
- **Decision.** `done` stores a SHA-256 per file after a passing run and compares content; size and
  mtime are only a cache.
- **Why.** Touching a file or changing it back must not count; any real edit must.
- **Rejected.** Comparing mtimes with the time of the last run. Trusting the transcript.

## 2026-11-19: `launch` reports facts, not legality
- **Decision.** Findings say what was observed (a host contacted, a page not linked) and never
  that a site is or is not lawful.
- **Why.** The tool cannot know the operator's obligations.
- **Rejected.** A compliance verdict.

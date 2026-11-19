# done: is "done" true?

An agent that says "all tests pass" may have run them before its last edit, may not have run
them at all, or may have made them pass by switching one off. This check answers from the
repository itself.

```bash
agent-sense done --run     # run the test command; when it passes, record the files it passed on
agent-sense done           # compare the project with that record and inspect the diff
```

## Rules

| rule | severity | reported when |
|---|---|---|
| `tests-not-run` | fail | a test command is known and no run is recorded |
| `tests-stale` | fail | a file was changed, added or deleted after the last passing run |
| `tests-failed` | fail | the last recorded run failed; the last 30 lines of its output are attached |
| `no-test-command` | warn | no test command is configured and none could be detected |
| `test-skipped` | fail | the diff adds a skip, xfail, `.only`, `xit`, `t.Skip`, `#[ignore]` and the like |
| `assertions-removed` | fail | the diff has fewer assertion lines in test files than before, counted over the whole diff |
| `test-deleted` | fail | a test file that held tests or assertions was deleted |
| `test-removed` | warn | fewer test cases than before |
| `expectation-changed` | warn | an assertion was rewritten in the same change as non-test code |
| `stub-added` | warn | new code has a comment starting with TODO, FIXME or XXX, or only raises "not implemented" |
| `uncommitted` | warn | the working tree has uncommitted paths |
| `unpushed` | warn | commits are ahead of the upstream, or the branch has none |

## How each is decided

**Fingerprint.** `--run` runs the command and, after it finishes, stores for every file
`git ls-files -co --exclude-standard` lists (tracked plus untracked, not ignored) its SHA-256, in
`.git/agent-sense/tests.json`. A check hashes the files again and compares content, so touching
a file without changing it, or changing it and changing it back, is not stale. Files matching
`*.md`, `docs/*`, `LICENSE`, `.gitignore`, `.gitattributes` and the `ignore` list in the config
are left out. Size and mtime are stored only to skip re-reading files that did not move.

**Diff.** `git diff -M --unified=0 HEAD` (or `--cached` with `--staged`, or against `--base REF`),
plus untracked files counted as added, so a test moved into a new file is not a loss. Lines are
matched with fixed patterns for Python, JavaScript and TypeScript, Go and Rust test styles.
Comment lines are not counted, and skip markers count only at the start of a line, so a marker
quoted inside a string is not one. Assertion and test-case counts are net over all test files in the
diff: moving or splitting tests is silent, removing more than is added is not.

**Test files** are paths under `test/`, `tests/`, `__tests__/`, `spec/`, `eval/`, `evals/`, and
files named `test_*.py`, `*_test.py`, `*_test.go`, `conftest.py`, `*.test.*`, `*.spec.*`.

## Configuration (`.agent-sense.toml`)

```toml
[done]
test = "scripts/test.sh"            # default: scripts/test.sh, package.json test, make test, go, cargo, pytest
ignore = ["fixtures/*.json"]        # added to the built-in ignore list
state = false                       # do not report uncommitted / unpushed
severity = { "expectation-changed" = "fail", "stub-added" = "info" }
```

## Where it runs

- **Claude Code Stop hook** (`agent-sense init --hook`): when the agent tries to end its turn while
  a `fail` finding exists, the hook answers with the findings and the agent continues. It acts
  only in projects that have `.agent-sense.toml`, never blocks twice in a row
  (`stop_hook_active`), and exits quietly on any internal error.
- **pre-commit**: `agent-sense done --staged --no-state`.
- **By hand or in CI**: `agent-sense done --base origin/main` checks a whole branch.

## Evidence

`evals/done_eval.py`: 14 scenarios with known answers, 14/14. Replaying the 28 non-root commits
of two real repositories as if each were being handed over gave 0 `fail` findings and 2
`expectation-changed` warnings (both real rewrites of an expected value next to a code change).
That sample is small and comes from one person's repositories.

## Limits

- The test command is trusted to mean something. A command that always exits 0 passes.
- It knows the tests passed on these files. It does not know the tests cover the change.
- Counting assertion lines is crude: one strong assertion replaced by two weak ones is not seen,
  and a multi-line assertion counts once. Helper-based checks with unusual names are not counted.
- A test weakened in an earlier commit is only seen with `--base` set before that commit.
- The fingerprint ignores files git ignores. Tests that depend on ignored files (local data,
  `.env`) can go stale without a report.
- The test command comes from the project's own config and is run through the shell by `--run`,
  the same trust as `npm test`. The Stop hook never runs it; it only reads the record.

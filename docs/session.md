# session: what the local session logs say

`agent-sense session` reads the Claude Code transcripts on this machine and counts the failures
and the manual repetition in them. Nothing is sent anywhere. The same logs give the same numbers.

It answers three questions:

1. Which commands keep failing, and are they retried without a change?
2. Which command sequences are typed again in session after session, and so should be a script?
3. How often is a completion report followed by evidence that the work was not complete?

## Input

Claude Code writes one JSONL file per session to `~/.claude/projects/<project>/<session>.jsonl`,
and one per subagent to `<project>/<session>/subagents/agent-*.jsonl`. `<project>` is the project
path with every character other than letters and digits replaced by `-`.

Only `user` and `assistant` entries are read. Other entry types, unknown fields and lines that do
not parse (a session cut off while writing) are skipped. The format is not a published contract,
so a Claude Code update can change it; the gate tests pin the shape this service expects.

A "session" below is a main transcript. Subagent transcripts add to the tool and failure counts
and to the flows of their parent session, but not to the rules about claims and session ends.

## Options

| option | default | meaning |
|---|---|---|
| `--logs DIR` | `~/.claude/projects` | where the logs are |
| `--project TEXT` | the project being checked | only log directories whose name contains TEXT |
| `--all-projects` | off | every project |
| `--since DAYS` | 30 | only sessions whose last entry is within DAYS days |
| `--top N` | 5 | findings kept per rule (the full tables are in `stats`) |
| `--test-pattern REGEX` | none | an extra pattern that marks a command as a test run; repeatable |

`.agent-sense.toml`, table `[session]`: `logs`, `project`, `all_projects`, `since`, `top`,
`test_patterns` (list), `min_failures` (default 3), `min_flow_sessions` (default 3).

## How commands are compared

A shell command is split into simple commands on `&&`, `||`, `;` and newlines outside quotes. Of
a pipeline only the first command is kept, here-document bodies are dropped, and `cd`, `echo`,
`export`, `sleep` and shell block words (`for`, `do`, `done`, `if`, `then`, `fi`) are left out.

Each simple command is reduced to its shape: the program, up to two subcommand words before the
first flag, and the flags. Everything else becomes a placeholder: `<path>`, `<n>`, `<hash>`,
`<url>`, `<arg>`. The value after a flag is always `<arg>`.

    cd app && git add -A && git commit -m "fix login"   ->   git add -A && git commit -m <arg>
    uv run --quiet pytest -q tests/test_a.py | tail -3  ->   uv run --quiet <arg> -q <arg>

Two commands with the same shape are counted as the same command.

## Rules

All findings are `warn` or `info`. This check never fails a build.

### repeated-failure (warn)

A Bash call counts as failed when its result is marked as an error (a non-zero exit code) and it
was not denied or interrupted. Failures are grouped by shape. A shape that failed 3 or more times
is reported. `stats.failure_groups` lists every shape with its count.

### blind-retry (warn)

Two tool calls in a row, same tool, identical input, both failed. Text between the two calls does
not matter; any other tool call in between does. Counted for every tool, not only Bash.

### repeated-flow (info)

The successful Bash calls of a session are turned into a list of command shapes (immediate
repeats collapsed). Every run of 2 to 5 consecutive shapes is a candidate. A candidate is reported
when it appears in 3 or more sessions, starts and ends with a command that changes something (not
`ls`, `grep`, `git status` and the like), and is not contained in a longer candidate seen in as
many sessions. `sessions` is the number of sessions, `occurrences` the number of times.

### claim-then-failure (warn for a failing test, info for pushback)

A completion claim is the last assistant text before the user's next message (or before the end
of the session) that matches one of `CLAIM_PATTERNS`: `DONE`, `DONE_WITH_CONCERNS`, 完了しました,
完了です, 修正しました, 実装しました, 直しました, "all tests pass", "tests pass", "ready to merge" and
similar. Text earlier in the same turn is not a claim.

Each claim is followed up to the end of the user's next turn:

- **failing test** (hard evidence): a test command failed in that window. A test command is one
  that matches `TEST_PATTERNS` (pytest, `scripts/test.sh`, `npm test`, vitest, jest, `go test`,
  `cargo test`, and others) and is not an install or a search. It failed when the call is marked
  as an error, or when its output says so (`2 failed`, `FAILED ...`, `--- FAIL`), which covers
  `pytest | tail` where the shell reports success.
- **pushback** (heuristic): no test failed, and the first 120 characters of the user's next
  message match one of `CORRECTION_PATTERNS`. The patterns are constructions, not topics: a flat
  contradiction (いや, 違う, "that's not"), something still happening (まだ…ません, 相変わらず, "still"),
  an expected thing negated (動きません, 反映されていない, 届かない, "doesn't", "can't"), an unwanted result
  (エラー, 〜てしまって, 〜すぎる, 〜づらい, 〜たのに, 〜のままです, 404, "crashes", "missing"), and a request to
  redo or stop (直して, 〜ないでください, 元に戻して, "put it back", "I said"). Neutral uses of the same words
  are excluded where they are common (エラー時の表示, 失敗したとき, "error handling", まだですか).

The two are counted separately: `claims_then_failing_test` and `claims_then_pushback`. Read the
second as "sessions worth a look", not as a measurement.

### ended-unverified (warn after a passing run, info when no test was ever run)

For each ended session, the edits after its last passing test command are counted. An edit is a
successful Edit, Write, MultiEdit or NotebookEdit call, or a shell command that writes a file
through `>`, `>>`, `tee`, `sed -i`, `perl -pi`, `git apply` or `patch`. Edits to Markdown and text
files, to anything under `.claude/`, and to temporary and scratchpad directories are not counted.
A session whose last entry is under 5 minutes old is treated as still running and skipped.

`ended_after_passing_test` are sessions that ran the tests and then kept editing.
`ended_never_tested` are sessions that edited and never ran a test command.

### interruptions (info)

One finding with two counts: user interruptions (the "[Request interrupted by user]" marker, or a
tool result marked interrupted) and denied tool calls, by kind and tool. Kinds seen so far:
`permission-rule` (a built-in safety check), `automode-blocked` (the auto mode classifier) and
`user` (the user rejected the call).

## Known limits

- **Shell writes are only partly visible.** A script that writes files itself
  (`python3 - <<EOF ... EOF`) is not seen as an edit, so `ended-unverified` undercounts in sessions
  that edit through the shell.
- **A failed command is a non-zero exit code.** A command that prints an error and exits 0 is not
  counted, except for test commands, whose output is read.
- **Pushback detection is a pattern list.** Measured by `evals/session_pushback_eval.py` on 120
  sentences written by an agent that never saw the patterns (60 pushback, 60 not, hard negatives
  included): precision 0.75, recall 0.60. The word list of the first version (commit `11d2dc8`, before the release) scores 0.58 and 0.32 on
  the same sentences. On 149 hand-labelled messages of the local logs, which were read while the
  patterns were written and so flatter them: precision 0.88, recall 0.91 (0.92 and 0.33 before).
  So about one flag in four is not a complaint and four complaints in ten go unseen. What it
  misses is a complaint stated as a plain fact ("the sidebar is on the right"), which no list of
  phrases can tell from a status report without knowing what was asked.
- **Claims are phrases.** A report that says the work is finished in other words is not a claim,
  and a sentence such as "実装しました" inside a progress note at the end of a turn is.
- **The window is one turn.** A defect found three turns later is not attributed to the claim.
- **A test run verifies everything before it.** The check does not know which files a test run
  covers.
- **Normalisation is lossy on purpose.** Two different commands with the same program and flags
  share a shape, and long compound commands rarely share one.
- **Automated sessions are counted too.** Sessions started by tools (for example review runs)
  cannot be told apart from typed ones; they are short and add sessions without adding findings.
- **Finding text.** Findings contain command shapes, file names, session ids and matched phrases,
  never message text. The report still describes private work, so treat it as private.

## Evals

`evals/session_pushback_eval.py` measures the pushback heuristic alone: development sentences (must
stay at 0.95), held-out sentences (floors 0.70 precision and 0.55 recall, just under the first
measurement) and, when `runs/session_pushback_labels.json` exists, the hand-labelled local messages.

`evals/session_eval.py` has 18 synthetic cases, each built to trigger one rule or to stay just
below it, and requires all of them to be correct. Its field run writes the full tables for the
local logs to `runs/session_field.json` (git-ignored) and prints totals only.

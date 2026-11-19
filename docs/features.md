# features: is every feature marked passing backed by a run?

A feature list written as prose drifts: an agent ticks an item because it believes the work is
done. Here the list is a file with a command per feature, and the only way an item becomes
`passing` is that command exiting 0.

```bash
agent-sense features          # read the list and the local record of the last runs
agent-sense features --run    # run the verify command of every active or passing feature
agent-sense features --run --only login    # run one feature, whatever its state
```

## The file

`features.json` at the project root (another path: `file` in the config).

```json
{
  "version": 1,
  "features": [
    {"id": "login", "behavior": "A user can sign in with email and password.",
     "verify": "pytest -q tests/test_login.py", "state": "active"}
  ]
}
```

| field | meaning |
|---|---|
| `id` | letters, digits, `-`, `_`, `.`; unique in the file |
| `behavior` | one sentence a person can check |
| `verify` | a shell command run from the project root; exit code 0 proves the behavior. May be absent while the feature is not started or blocked |
| `state` | `not_started`, `active`, `blocked` or `passing` |

Other keys, at the top level or in a feature, are kept as they are.

## States

A person or an agent writes `not_started`, `active` and `blocked`. `passing` is written by `--run`:

| before | command exits 0 | command exits non-zero |
|---|---|---|
| `active` | `passing` | `active`, `feature-failing` |
| `passing` | `passing` | `active`, `feature-failing` ("was passing, now fails") |
| `not_started`, `blocked` (only with `--only`) | `passing` | unchanged, `feature-failing` |

`--run` without `--only` never runs `not_started` or `blocked` features, and skips features with
no command. The file is rewritten only when a state changed (key order kept, two-space indent).

## Rules

| rule | severity | reported when |
|---|---|---|
| `features-invalid` | fail | the file is not JSON, has no `features` list, a version other than 1, an unknown state, a missing behavior, a bad or repeated id. Nothing is run and nothing else is checked |
| `feature-failing` | fail | `--run`: a verify command exited non-zero. Reading: a feature is marked `passing` but the last recorded run of its current command failed |
| `feature-failing` | warn | reading: a feature is `active` and its last recorded run failed (work in progress) |
| `passing-unverified` | warn | a feature is `passing` but this machine has no passing run of exactly this verify command |
| `no-verify` | warn | an `active` or `passing` feature has no verify command |
| `wip-limit` | warn | more features are `active` than `wip` allows (default 1) |
| `no-feature-list` | info | the project has no feature list |

## The record

Each run stores, per feature, the command, exit code, time, duration and the last 30 lines of
output in `.git/agent-sense/features.json` (in `.agent-sense/features.json` beside the project when
it is not a git repository). The record is local and never committed. So a fresh clone reports
every `passing` feature as `passing-unverified` until `--run` is run there: the state in the
file is a claim, the record is the proof, and the proof does not travel.

Stats in the report: the count per state, `ran`, and `verified_completion_rate` = passing /
features that are not `not_started`.

## Configuration (`.agent-sense.toml`)

```toml
[features]
file = "features.json"
wip = 1
timeout = 600                       # seconds per verify command
severity = { "wip-limit" = "off", "passing-unverified" = "fail" }

[done]
ignore = ["features.json"]          # a state change must not make the test record stale
```

`features` is not in the default set of `agent-sense all` and the Stop hook does not run it by
default. To read the list (never run it) in either place, add `"features"` to `[all] checks` or
`[hook] checks`. Reading runs no command, so it is safe in a hook.

## Evidence

`evals/features_eval.py`: 15 scenarios with known answers (hand-edited states, a weakened
command, a regression, a fresh clone, backlog items, invalid files), 15/15. All scenarios are
synthetic. The check has been used on four of the author's repositories since 2026-11-20 and
has no field numbers yet.

## Limits

- The verify command is trusted to mean something. `true` passes.
- The proof is local. Nothing shows a reader of the repository that `passing` was ever verified;
  CI running `agent-sense features --run` is the way to show it.
- A weakened command is seen only until the next `--run`, which records the new one.
- Verify commands run one after another through the shell with the project's own trust, the
  same as `npm test`. A slow list is slow.
- It does not know whether the behavior text and the command are about the same thing.

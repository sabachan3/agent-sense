# Results

All numbers below were produced on one macOS machine on 2026-11-19 with the scripts named. Every
check has known-answer cases (threshold: all must pass) and a run on real material whose findings
were read by hand. The real samples are small and come from one person's projects, so they show
how much noise to expect there, not how often the problems occur in general.

| check | gate tests | known-answer eval | script |
|---|---|---|---|
| done | 26 | 14/14 scenarios | `evals/done_eval.py` |
| page | 80 | 18/18 pages in Chromium | `evals/page_eval.py` |
| deps | 18 | 14 projects, 17/17 planted findings, 0 unexpected | `evals/deps_eval.py` |
| context | 15 | 11 projects, 14/14 planted, 0 unexpected | `evals/context_eval.py` |
| boundary | 10 | 11 projects, 10/10 planted, 0 unexpected | `evals/boundary_eval.py` |
| launch | 56 | 10/10 sites in Chromium | `evals/launch_eval.py` |
| session | 59 | 18/18 synthetic sessions | `evals/session_eval.py` |

The whole gate (274 tests with the command-line tests) runs in about 1.6 s.

## done

- **Stop hook, real run.** In a throwaway repository with the hook installed, headless Claude Code
  was asked to add a function and told not to run tests unless something told it to. The hook
  blocked the stop once with `tests-stale`, the agent ran `agent-sense done --run`, and the turn
  ended with the check passing (5 turns). One run, one model, one prompt.
- **History replay.** Each of 28 non-root commits of two real repositories was treated as the
  change being handed over. `fail` findings: 0. Warnings: 2 `expectation-changed`, both real
  rewrites of an expected value next to a code change.
- **Found by the evals and fixed:** a variable named `todo` counted as a stub (18 false warnings
  in the first replay); a test moved into a new untracked file counted as removed; skip markers
  quoted inside strings counted as skips (seen when the tool checked its own repository).

## page

- All ten rules have a page that must be flagged and one that must stay silent; the traps
  (carousel scrolling inside its container, intended ellipsis, toggle that only changes
  `aria-pressed`, event delegation, copy button, skip link) stay silent.
- **IME.** After `Input.imeSetComposition`, a key event with key Enter and virtual key code 229
  reaches the page as `keydown` with `key: "Enter"`, `keyCode: 229`, `isComposing: true`, and
  Chromium does not submit a plain form. A naive `e.key === 'Enter'` handler is flagged, a handler
  guarded by `isComposing` and `keyCode === 229` is not. This replays Chromium's event order; it
  is not a real IME and was not run on Safari or Windows.
- **Noise on five public pages** (no clicking): two had nothing, one had a real 804 px table at
  768 px, one had 8 `text-clipped` warnings for teasers cut by `max-height` on purpose. The rule
  cannot tell intent there.

## deps, context, boundary

Run on eight local projects (about 2,000 imports, four of them JavaScript with 1,210 imports):

- deps: 2 findings, both true (a package imported in two files and declared nowhere).
- boundary: no import or `sys.path` reach; 4 path warnings, all intended subprocess calls into a
  shared service, cleared by one `allow` line each.
- context: 0 findings over 40 checked paths. No real stale reference existed in these files, so
  recall in the field is unproven; it rests on the planted cases.

Lock-file checks were exercised on small synthetic lock files only.

## launch

30 public pages, most of them hobby and open-source project pages rather than Japanese commercial
services, so the rates say little about commercial sites: no policy link on 19, no terms link on
21, no contact link on 18, a known service not named on the policy on 11.

Ten `external-undisclosed` findings were compared by hand with the policy text the tool had read:
8 correct (the policy said "analytics" without naming the service), 1 correct but mislabelled,
1 false (image thumbnails from a search engine's host counted as its advertising tag). The host
patterns behind the false and the mislabelled finding were narrowed, each with a regression test.
The findings of the final run were not all re-read.

The vendor table has 110 services and 223 host patterns. "Verified" in that file means the host
resolved in DNS on the day, not that it was checked against the vendor's documentation.

## session

Local logs: 53 sessions in 6 projects, of which 42 have six events or fewer. 37 completion
claims: none followed by a failing test command, 3 followed by a defect report from the user.
Two of five ended sessions with edits never ran a test. The only repeated flow was add-then-
commit. With this few large sessions the numbers cannot rank which problem is most common, and
the wording-based pushback detection missed at least two complaints.

## Not done

- Nothing was run on Windows or Linux.
- No finding was judged by a second person.
- False negatives were not measured for any check outside the planted cases.

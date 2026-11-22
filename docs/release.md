# Release: decisions, then steps

State on 2026-11-22: the repository is public (decision a and steps 1 to 3 below are done, see
"Done on 2026-11-22"), version 0.1.0 has never been released, nothing is on a package index, no
tag exists. What is left is decision (d) and steps 4 to 8, which only the owner runs. An agent
does not run any command in this file that uploads to an index, tags or rewrites history.

## Done on 2026-11-22

- (a) Public: decided by the owner, with the history kept and `evals/launch_jp_sites.txt` and
  the two single-site sentences left as they are. The visibility was changed with the command
  of step 3 at commit `67045ee`, on the owner's instruction in the session.
- Step 1 passed at that commit. Step 2 was read first: no issue, pull request or release, one
  branch, no secret, variable or environment; the logs of all 20 Actions runs were searched (see
  "What was not verified" for how) with no hit, and were kept.
- After the change, from an empty directory with no git credentials:
  `uvx --from "agent-sense @ git+https://github.com/sabachan3/agent-sense" agent-sense --version`
  printed `agent-sense 0.1.0`.
- (c) MIT stands as it was; it was not discussed again.

The sections below are kept as written before the change, as the record of what was weighed.

## Decisions

### (a) Public or not

Making the repository public shows, to everyone and in practice for good (clones and caches
outlive a change back):

- every commit of every branch with its message, author name and author address. All commits
  carry the GitHub no-reply address and the login; no other address is in the history;
- the working documents of the repository: `CLAUDE.md`, `PROGRESS.md`, `DECISIONS.md`,
  `features.json`, `.claude/settings.json`, `.claude/agents/evaluator.md`. They describe how the
  tool was built with an agent, reviews that failed, and what is not done;
- `docs/results.md`, including what one eval cost ($4.15 for 36 sessions) and that all field
  numbers come from one person's machine and projects (none of them named);
- on GitHub, beyond git: the Actions runs and their logs made while the repository was private,
  issues, pull requests, releases. These were not inspected from here; step 2 below lists them.

Not public either way: `runs/` (ignored by git; it holds the field records, the hand labels of
real session messages and the names of the projects the checks were run on).

### (b) What the audit found, and what to do about the history

`scripts/release_check.py tree` and `history` were run on 2026-11-22 over all tracked files and
all commits of all refs (diffs, added and removed lines, messages, author and committer
addresses), with a local list of private words (project names, host names, the user name):
**no hit**. No token, no key, no personal address, no home directory of a real user, no private
project or host name, and no text taken from a real session: the sentences in
`evals/session_pushback_cases.json` and `evals/session_pushback_heldout.json` were written for
the eval, and the evals that read private history (`evals/session_eval.py`,
`evals/session_pushback_eval.py` part C, `evals/context_history.py`, the field parts of the
`deps`, `context` and `boundary` evals) hold only code that reads local files and write to `runs/`.

So the history can be published as it is. No rewrite and no fresh repository is needed for
privacy. A pattern scan finds shapes, not meaning; these points are judgment, not hits:

1. **`evals/launch_jp_sites.txt` names 40 real commercial services**, and `docs/results.md` and
   `docs/launch.md` report what the tool found on them. The findings are counts; no site is named
   next to a finding, and the text says that a `fail` there is almost always the tool's error.
   Two sentences describe single unnamed sites of the 40 (a `pk_test_` key in a production
   script; a policy link the tool does not recognise). Choices: keep as it is (recommended: the
   list is public home pages, it is what makes the numbers reproducible, and nothing is
   attributed); or take the two single-site sentences out of the docs; or remove the list from
   the tree. The list is also in the history since commit `a733d56`, so removing it completely
   means the fresh repository described below.
2. **Commit messages and co-author lines** show that the commits were written with an agent, and
   the hashes of several commits are cited in `PROGRESS.md`, `DECISIONS.md` and three pages
   under `docs/` (the baseline `11d2dc8`).
3. **The login** `sabachan3` appears in the install commands, the project URLs and as the author
   in `pyproject.toml`. The copyright line of `LICENSE` says "agent-sense contributors".

If one clean commit is preferred over the real history anyway (this is a choice of taste, not a
fix): create a new empty repository, and push a single commit made from the current tree
(`git checkout --orphan public && git commit`). Do not rewrite or force-push this repository:
old objects stay reachable on the server for a while. The cost: the commit hashes cited in the
documents stop resolving and would have to be reworded first.

### (c) License

`LICENSE` has been MIT since the first commit, `pyproject.toml` says `license = "MIT"`, and the
other two tools of the same author use MIT. Nothing was chosen silently here, but it has never
been confirmed for a public release either. Confirm MIT, or change both places before step 3.
`scripts/release_check.py meta` fails when the two disagree.

Third parties, for that decision:

| what | license | how it is used |
|---|---|---|
| runtime dependencies | none | the command uses the standard library only |
| playwright 1.65 (optional `browser` extra) | Apache-2.0 | installed by the user, not bundled |
| greenlet 3.5 (dependency of playwright) | MIT AND PSF-2.0 | installed by the user, not bundled |
| Chromium (downloaded by Playwright) | BSD-3-Clause and others | downloaded by the user, not bundled |
| hatchling (build), pytest, pytest-xdist, twine (development) | MIT, MIT, MIT, Apache-2.0 | not shipped |
| `services/launch/vendors.json` (shipped in the wheel) | own work | 110 services and their host names, collected by hand; names belong to their owners |
| `evals/launch_jp_sites.txt` (repository only) | own work | a list of 40 public URLs |
| `evals/*_fixtures/`, `evals/session_pushback_*.json` (repository only) | own work | pages and sentences written for the evals; no third-party code or text |

Versions and license fields were read from the installed packages' metadata on 2026-11-22.
Nothing shipped in the wheel or the sdist comes from a third party.

### (d) Package index

**The name `agent-sense` on pypi.org belongs to another project** (seen 2026-11-22: version
0.2.0, "Human-agent interaction SDK with accessibility and dialogue management", first uploaded
2026-02-27, home page `github.com/aumos-ai/agent-sense`). On test.pypi.org the name is free.
This tool cannot be uploaded under that name, and a reader who types `pip install agent-sense`
gets the other project. Choices:

1. **No package index.** Install from GitHub only (`uv tool install "agent-sense @ git+https://..."`),
   which works today for anyone who can read the repository. Nothing else to do.
2. **Another distribution name, same command.** For example `agent-sense-cli` or
   `agent-sense-checks` (both answered 404 on 2026-11-22; the index can still refuse a name it
   judges too close to an existing one, which is likely for `agentsense`). To change: `name` in
   `pyproject.toml`, `uv lock`, the install lines of `README.md` (the name before `@ git+` must
   equal the distribution name, and `NAME-ON-THE-INDEX`). The command stays `agent-sense` and the
   import package stays `agent_sense`. Not checked: whether the other project also installs a
   command or a module of that name; if it does, the two cannot share one environment.
3. **A new name for everything** (repository, command, package). The Stop hooks of the projects
   that use the tool call `agent-sense hook stop` and would all have to change.

`scripts/release_check.py index` asks the index and fails while the name is somebody else's; the
release workflow runs it before any upload.

## Steps

Each step names who acts. "Check" steps change nothing.

1. **Check, locally.** All must pass:

   ```bash
   scripts/test.sh
   scripts/e2e.sh
   scripts/release_check.py meta
   scripts/release_check.py tree
   scripts/release_check.py history     # reads runs/private_names.txt if it exists: one private word per line
   agent-sense features --run --include-manual
   agent-sense all
   git status --short                   # empty
   git log origin/main..main            # empty: everything is pushed
   ```

2. **Check, on GitHub, before the visibility change.** Read what else becomes visible:

   ```bash
   gh issue list --state all; gh pr list --state all; gh release list
   gh run list --limit 100              # logs of these runs become public
   gh api repos/sabachan3/agent-sense/branches --jq '.[].name'
   git ls-remote origin                 # only refs/heads/main is expected
   ```

   To drop the old run logs first: `gh run list --limit 100 --json databaseId --jq '.[].databaseId' | xargs -n1 gh run delete`.

3. **Owner: make it public.** In the repository settings, or
   `gh repo edit sabachan3/agent-sense --visibility public --accept-visibility-change-consequences`.
   Then push any commit or start the `ci` workflow by hand and see it green; the install line of
   the README now works for everyone: `uvx --from "agent-sense @ git+https://github.com/sabachan3/agent-sense" agent-sense --version`.
   If decision (d) is "no package index", stop here; tag the commit if a version marker is wanted
   (`git tag v0.1.0 && git push origin v0.1.0`) after setting the date in `CHANGELOG.md`.

4. **Owner: the name** (decision d, choice 2 or 3). Change it as described there, run step 1
   again, commit, push, CI green.

5. **Owner: tell the index who may upload.** No token is created. On pypi.org and on
   test.pypi.org (two separate accounts, two-factor sign-in required): Account, Publishing,
   "Add a new pending publisher", GitHub:

   | field | pypi.org | test.pypi.org |
   |---|---|---|
   | PyPI project name | the name from decision (d) | the same |
   | Owner | `sabachan3` | `sabachan3` |
   | Repository name | `agent-sense` | `agent-sense` |
   | Workflow name | `release.yml` | `release.yml` |
   | Environment name | `pypi` | `testpypi` |

   In the GitHub repository settings, Environments: create `pypi` and `testpypi`. Adding yourself
   as a required reviewer on `pypi` makes every upload wait for a click.

6. **Owner: rehearsal.** `gh workflow run release.yml -f target=none` builds and checks and
   uploads nothing. Then `gh workflow run release.yml -f target=testpypi`, and install what
   arrived:

   ```bash
   uvx --index https://test.pypi.org/simple/ --from NAME agent-sense --version    # prints: agent-sense 0.1.0
   ```

   A version uploaded to test.pypi.org cannot be uploaded there again; a second rehearsal needs
   a version such as `0.1.0.dev2` that is never released.

7. **Owner: the release.** Replace "not released yet" in `CHANGELOG.md` by the date
   (`## [0.1.0] - YYYY-MM-DD`; the workflow refuses to upload without it), point the link at the
   bottom of the file at the tag, fill the install commands in `README.md`, commit, push, CI
   green. Then:

   ```bash
   gh release create v0.1.0 --title "agent-sense 0.1.0" --notes "See CHANGELOG.md."
   gh run watch
   ```

   Publishing the GitHub release starts `release.yml`, which checks that the tag is `v` plus the
   version in `pyproject.toml`, runs the gate, the release checks and `scripts/e2e.sh`, and
   uploads the two archives that passed.

8. **Check, after the release.** In a new directory outside the repository:

   ```bash
   uvx --from NAME agent-sense --version
   uv tool install "NAME[browser]" && agent-sense --help
   ```

   On the machine where the command is an editable install of the working copy, do not run the
   second line: it would replace that install.

9. **Next version.** Change `version` in `pyproject.toml` and `__version__` in
   `agent_sense/__init__.py` together, add the entry at the top of `CHANGELOG.md`;
   `scripts/release_check.py meta` and the gate fail until the three agree.

## Taking a release back

- **Yank, do not delete.** On pypi.org: the project, Manage, Releases, the version, Options, Yank,
  with a reason. A yanked version is skipped by installers unless it is pinned exactly, so
  existing lock files keep working. A deleted version breaks them, and its file names can never
  be uploaded again in either case: a fix is always a new version (`0.1.1`).
- **The GitHub release**: `gh release edit v0.1.0 --prerelease` or `gh release delete v0.1.0`
  (add `--cleanup-tag` to remove the tag as well). This does not touch the index.
- **If something private was uploaded**: yanking is not enough, because the file stays
  downloadable. Delete the release on the index, treat the content as disclosed (mirrors and
  caches keep copies), and revoke any credential that was in it before anything else.
- **Making the repository private again** hides it on GitHub; it does not recall clones or forks.

## What was not verified

- `release.yml` has run only with `target=none` (2026-11-22, run 45019437549 at `97c7690`: gate,
  checks and build green, both uploads skipped). The upload steps, the environments and the
  trusted publisher exist only as described here. `ci.yml` with pinned actions is green.
- `actions/upload-artifact` v7.0.2 uploaded the two archives in that run; `actions/download-artifact`
  v8.0.2 runs only in the upload jobs and is assumed to work with it; the rehearsal in step 6 shows it.
- The GitHub side of the repository was read on 2026-11-22: no issue, no pull request, no
  release, one branch (`main`), no secret, variable or environment. The logs of the 20 Actions
  runs that existed before the visibility change were searched for the private word list, email addresses, home
  directories and token shapes: no hit (the only address is the tests' `ci@example.invalid`,
  the only home directory the runner's).
- Whether the other `agent-sense` on pypi.org installs a command or module of the same name.

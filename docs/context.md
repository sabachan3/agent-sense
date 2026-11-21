# context: instruction files that name things that are gone

    agent-sense context [--files PATTERN] [--memory DIR] [--strict] [--max-bytes N]

`CLAUDE.md`, `AGENTS.md` and memory files are loaded into every session and trusted. When a file
they name was renamed or deleted, the agent still follows the instruction: it searches for the
file, recreates it, or runs a command that fails. This check extracts the paths and commands those
files mention and verifies them against the project.

## Files checked

`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.windsurfrules` at any
depth, `.claude/**/*.md` (commands, agents, skills, rules; not `worktrees/`, `projects/`,
`plugins/` and other tool state), `.cursor/rules/**`, `.github/copilot-instructions.md`.
`--files` replaces the list: a pattern without a slash matches the name at any depth, one with a
slash matches the end of the path, and `./x` matches at the root only. `--memory DIR` adds a
Claude Code memory directory.

## Rules

| rule | severity | meaning |
|---|---|---|
| `missing-path` | warn | a path in backticks, in a code block, in a link or after `@` does not exist |
| `missing-script` | warn | `npm run X`, `pnpm X`, `yarn X`, `bun run X` with no such script in any `package.json`; `make X` with no such target |
| `memory-dangling` | warn | `MEMORY.md` points to a file that does not exist |
| `memory-dangling` | info | a `[[name]]` link to a memory that was never written |
| `memory-unindexed` | warn | a memory file that `MEMORY.md` does not list, so it is never loaded |
| `oversized` | info | an instruction file above 40,000 bytes (`--max-bytes`) |

## How a path is decided

A token is treated as a path when it has a slash or a known file extension and is made only of
path characters. `:47` and `#L10` suffixes are dropped. It is looked up from the project root,
from the directory of the instruction file, and without a leading project-folder name.

Instruction files are full of things that look like paths and are not. These are skipped:

- placeholders and patterns: anything with `<>`, `{}`, `$`, `*`, `?`, `...`, `X.Y.Z`,
  `path/to`, `foo`, `bar`, `your-`;
- URLs, `~` paths, and absolute paths outside the project and the home directory, or under
  `/tmp`, `/var`, `/private`. A Windows path with a drive letter (`C:\dir\file` or `C:/dir/file`)
  counts as absolute and is reported with forward slashes;
- paths that `.gitignore` covers (outputs that need not exist yet);
- a bare file name that exists anywhere in the project;
- **unanchored paths**. A path is reported only when the directory it sits in exists. With
  `scripts/` present, `scripts/old.sh` is a stale reference. `food_vision/classifier.py` in a
  project with no `food_vision/` is an illustration, a repository slug such as `owner/repo`, or
  wording such as `fail/warn`. This one rule replaces any attempt to recognise "example" code
  blocks. `--strict` turns it off and reports those too. Link targets are always reported.
- A directory must exist with the same spelling. On macOS and Windows the file system finds
  `PostHog/` when the directory is `posthog/`; the check compares the real names, so a repository
  slug is not anchored by a directory that differs only in case.

**Rooted paths (severity info).** A path written with two or more directories whose first
directory exists, while the directory the file sat in does not (`app/cli/utils.py` with `app/`
present and `app/cli/` gone), is what a removed or moved package leaves behind. It is reported as
`info` with `"rooted": true`, not as a warning: in monorepos the same shape also comes from paths
written relative to another package.

The cost of the anchoring rule: when a whole top-level directory is deleted, references into it
are not reported without `--strict`, and a bare file name that no longer exists anywhere is not
reported either.

Commands are judged only when the project has a `package.json` (or a Makefile for `make`), and a
Makefile with pattern rules or generated targets turns the `make` check off. Bare `npm X` is never
judged because npm has too many built-in commands; `pnpm X` and `yarn X` are judged when `X` is not
a built-in.

## Configuration (`.agent-sense.toml`)

    [context]
    files = ["CLAUDE.md", "docs/agents/*.md"]
    memory = ["~/.claude/projects/<project>/memory"]
    ignore = ["scripts/generated_*"]        # path or script patterns never to report
    strict = false
    max-bytes = 40000
    severity = { "missing-path" = "fail" }

## Results

Known-answer eval (`evals/context_eval.py`): 11 projects, 14 planted problems, all found, no other
finding. Field run on the instruction files found on the development machine (4 files in 2
projects, one global `CLAUDE.md`, one memory directory with 6 memories; 40 paths checked): 0
findings. `--strict` on the same files reports 9, and all 9 were read and are not stale paths
(repository slugs, illustrative paths, `CI/CD`, generic layout names), which is the evidence for
the anchoring rule. No real stale reference existed in these files, so that run shows the absence
of false alarms and says nothing about recall.

Recall on real history (`evals/context_history.py`, 2026-11-20): the first-parent history since
2025-03-01 of eight public repositories that keep `AGENTS.md` or `CLAUDE.md` files was replayed.
A stale reference is a path that an instruction file names at a commit where the file is gone,
found by a plain scan of the text and not by the check's own parser. 18 such references exist in
five of the repositories. The first version of the check (commit `11d2dc8`, before the release) reports 12 of them (67%); the 5 it misses all sit in a
directory that was removed with them, and one path is ignored by git. With the rooted level the
check reports 17 of 18: 12 as warnings, 5 as info. None of the 18 stood in plain prose.

Noise at the head of ten public repositories (5,724 paths checked in 270 instruction files):
80 warnings and 15 info findings, 70 and 15 of them in one monorepo whose instruction files name
paths relative to sub-packages. Comparing names exactly removed 23 warnings that the first version gave on
macOS, where a slug such as `PostHog/posthog` was anchored by the directory `posthog/`. The 80
warnings were not all read, so their precision is not known.

## Limits

- Only paths written in backticks, code blocks, links or `@file` form are seen. A path in plain
  prose is not.
- Function, class and symbol names are not checked, only files.
- It cannot tell that a file exists but no longer does what the instruction says.
- Commands other than package scripts and make targets are not checked.

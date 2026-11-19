"""Field recall of the context check, from the history of real repositories.

The planted cases of context_eval.py show that the rules work on references made up for them.
This script finds references that went stale in real projects and asks whether the check would
have reported each one on the day it went stale.

Ground truth, built without the check's own parser:

1. Walk the first-parent history of a repository from the oldest commit. Keep the set of files
   that exist and the set of files that existed once and are gone.
2. At every commit, take the text of each instruction file (the default file patterns of the
   check) and pull out every run of path characters that contains a slash, wherever it stands:
   backticks, code blocks, links or plain prose.
3. A token that names a file that is gone, read from the repository root or from the directory of
   the instruction file, is a stale reference. Each (instruction file, path) pair is counted once,
   at the first commit where it is stale.

Then the repository is checked out at that commit and the check is run twice, as shipped and with
--strict. A stale reference is found when a `missing-path` finding names it. Misses are sorted by
cause with the check's own functions: `prose` (the path is not in backticks, a code block or a
link, which the check does not read by design), `ignored` (git ignores the path now), `unanchored`
(found only by --strict), `other`. `found-info` is a reference reported at severity info, because the
directory it sat in is gone as well.

    uv run --no-project --python 3.12 evals/context_history.py REPO [REPO ...] [--since 2025-01-01]

REPO is a local repository or a URL. Each is cloned into a temporary directory; the original is
never touched. Output: counts on stdout, every pair in runs/context_history.json (git-ignored,
because local repositories are private).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "context"))
import context_lib as LIB  # noqa: E402

_TOKEN = re.compile(r"(?<![\w.@+/-])(?:\./|\.\./)*[\w.@+-]+(?:/[\w.@+-]+)+")
MAX_PAIRS_PER_COMMIT = 200   # a commit that deletes a vendored tree is one event, not thousands


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=repo, capture_output=True, text=True, errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {done.stderr.strip()[:200]}")
    return done.stdout


def clone(source: str, into: Path, since: str | None) -> Path:
    target = into / re.sub(r"[^\w.-]+", "_", source.rstrip("/").removesuffix(".git"))[-60:]
    remote = "://" in source or source.startswith("git@")
    command = ["git", "clone", "--quiet", "--no-tags"]
    if remote and since:
        command += [f"--shallow-since={since}", "--single-branch"]
    done = subprocess.run([*command, source if remote else str(Path(source).expanduser().resolve()), str(target)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"clone failed: {done.stderr.strip()[:200]}")
    return target


def history(repo: Path, since: str | None) -> list[tuple[str, list[tuple[str, str]]]]:
    """Commits oldest first, each with its (status, path) changes against the first parent."""
    args = ["log", "--first-parent", "-m", "--no-renames", "--reverse", "--name-status", "--format=%x00%H"]
    commits = []
    for block in git(repo, *args).split("\x00")[1:]:
        lines = block.strip("\n").split("\n")
        changes = [tuple(line.split("\t", 1)) for line in lines[1:] if "\t" in line]
        commits.append((lines[0].strip(), changes))
    return commits


def resolve(token: str, source: str) -> list[str]:
    """Repository paths a token may stand for: from the root and from the instruction file's directory."""
    places = []
    for base in ("", str(PurePosixPath(source).parent)):
        parts: list[str] = []
        for part in (PurePosixPath(base) / token).parts if base not in ("", ".") else PurePosixPath(token).parts:
            if part == "..":
                if not parts:
                    break
                parts.pop()
            elif part != ".":
                parts.append(part)
        else:
            if parts:
                places.append("/".join(parts))
    return list(dict.fromkeys(places))


def stale_pairs(repo: Path) -> list[dict]:
    """Every (instruction file, gone path) pair with the first commit at which it is stale."""
    present: set[str] = set()
    gone: set[str] = set()
    texts: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    pairs = []
    for commit, changes in history(repo, None):
        touched_instructions, deleted = set(), False
        for status, path in changes:
            if status.startswith("D"):
                present.discard(path)
                gone.add(path)
                deleted = True
                texts.pop(path, None)
            else:
                present.add(path)
                gone.discard(path)
                if LIB.matches(path, LIB.DEFAULT_FILES) and not LIB._NOT_INSTRUCTIONS.search(path):
                    touched_instructions.add(path)
        for path in touched_instructions:
            try:
                texts[path] = git(repo, "show", f"{commit}:{path}")
            except RuntimeError:
                texts.pop(path, None)   # a submodule or a symlink to nowhere
        if not (deleted or touched_instructions):
            continue
        fresh = []
        for source in (texts if deleted else touched_instructions):
            for match in _TOKEN.finditer(texts.get(source, "")):
                token = match.group(0).rstrip(".")
                places = resolve(token, source)
                if any(place in present for place in places):
                    continue
                target = next((place for place in places if place in gone), None)
                if target is None or (source, target) in seen:
                    continue
                seen.add((source, target))
                line = texts[source].count("\n", 0, match.start()) + 1
                fresh.append({"commit": commit, "file": source, "path": target, "token": token, "line": line})
        if len(fresh) <= MAX_PAIRS_PER_COMMIT:
            pairs += fresh
    return pairs


def reported(repo: Path, strict: bool, severity: str = "warn") -> dict[str, set[str]]:
    """Paths named by missing-path findings of one severity, by instruction file, at the checked-out commit."""
    result = LIB.run(argparse.Namespace(root=repo, config={}, files=None, strict=strict, max_bytes=None, memory=[]))
    found: dict[str, set[str]] = {}
    for finding in result["findings"]:
        if finding["rule"] == "missing-path" and (strict or finding["severity"] == severity):
            found.setdefault(finding.get("file", ""), set()).add(finding["detail"]["path"])
    return found


def names(token_set: set[str], pair: dict) -> bool:
    return any(pair["path"] in resolve(LIB._SUFFIX.sub("", name).strip("/"), pair["file"]) or name.strip("./") == pair["token"].strip("./")
               for name in token_set)


def in_fragment(repo: Path, pair: dict) -> bool:
    """Whether the check reads the place where the token stands (backticks, code block, link, @)."""
    text = (repo / pair["file"]).read_text(encoding="utf-8", errors="replace")
    return any(pair["token"] in fragment for _, _, fragment in LIB.code_fragments(text))


def judge(repo: Path, pairs: list[dict]) -> None:
    by_commit: dict[str, list[dict]] = {}
    for pair in pairs:
        by_commit.setdefault(pair["commit"], []).append(pair)
    for commit, group in by_commit.items():
        git(repo, "checkout", "--quiet", "--force", "--detach", commit)
        normal, noted, strict = reported(repo, False), reported(repo, False, "info"), reported(repo, True)
        ignored = LIB.ignored_by_git(repo, [pair["path"] for pair in group])
        for pair in group:
            if names(normal.get(pair["file"], set()), pair):
                pair["outcome"] = "found"
            elif names(noted.get(pair["file"], set()), pair):
                pair["outcome"] = "found-info"
            elif pair["path"] in ignored:
                pair["outcome"] = "ignored"
            elif not in_fragment(repo, pair):
                pair["outcome"] = "prose"
            elif names(strict.get(pair["file"], set()), pair):
                pair["outcome"] = "unanchored"
            else:
                pair["outcome"] = "other"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repos", nargs="+")
    parser.add_argument("--since", help="for URLs: clone only the history after this date (YYYY-MM-DD)")
    parser.add_argument("--out", default=str(ROOT / "runs" / "context_history.json"))
    args = parser.parse_args(argv)
    everything, total = [], Counter()
    with tempfile.TemporaryDirectory() as folder:
        for index, source in enumerate(args.repos):
            try:
                repo = clone(source, Path(folder) / str(index), args.since)
                pairs = stale_pairs(repo)
                judge(repo, pairs)
            except RuntimeError as error:
                print(f"repo {index + 1}: skipped ({error})")
                continue
            counts = Counter(pair["outcome"] for pair in pairs)
            total.update(counts)
            print(f"repo {index + 1}: {len(pairs)} stale reference(s) in history: " +
                  (", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "none"))
            everything += [{"repo": source, **pair} for pair in pairs]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(everything, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    count = sum(total.values())
    readable = count - total["prose"] - total["ignored"]
    print(f"total: {count} stale references; " +
          ", ".join(f"{name} {total[name]}" for name in ("found", "found-info", "unanchored", "prose", "ignored", "other")))
    shown = total["found"] + total["found-info"]
    if count:
        print(f"recall over all of them: {shown / count:.0%} ({shown}/{count}), as a warning {total['found'] / count:.0%}")
    if readable:
        print(f"recall over those in backticks, code blocks or links and not ignored: {shown / readable:.0%} "
              f"({shown}/{readable}); with --strict {(shown + total['unanchored']) / readable:.0%}")
    print(f"pairs: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

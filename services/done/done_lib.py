"""Is "done" true? Checks the claim against the repository, not against what was said.

Three questions, all answered from files and git:

1. Did the tests pass on the code as it is now? `--run` runs the test command and, when it
   passes, records a fingerprint of every file in the project. A later check compares the
   files with that record, so an edit made after the last passing run is caught whoever made
   it and however the tests were started.
2. Were the tests weakened to get there? The diff is searched for added skip markers, for a
   net loss of assertions and for deleted test files.
3. Is the work actually handed over? Uncommitted and unpushed changes are reported.

The record lives inside the git directory (`.git/agent-sense/tests.json`), so nothing has to be
added to .gitignore and each worktree has its own.
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

NAME = "done"
SUMMARY = "check a completion claim: tests passed on the current files, tests not weakened, work committed"
RULES = {
    "tests-not-run": "fail", "tests-stale": "fail", "tests-failed": "fail", "no-test-command": "warn",
    "test-skipped": "fail", "assertions-removed": "fail", "test-deleted": "fail", "test-removed": "warn",
    "expectation-changed": "warn", "stub-added": "warn", "uncommitted": "warn", "unpushed": "warn",
}
DEFAULT_IGNORE = ("*.md", "docs/*", "LICENSE", ".gitignore", ".gitattributes")
STAMP = "agent-sense/tests.json"
MAX_LISTED = 8
OUTPUT_TAIL = 30

TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|evals?|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|(^|/)conftest\.py$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$")
ASSERTION = re.compile(
    r"^\s*assert\b|\bself\.assert\w+\(|\bpytest\.(raises|warns|approx)\b|\bexpect\(|\bassert\.\w+\(|\bassert\("
    r"|\.should\b|\bt\.(Error|Errorf|Fatal|Fatalf|Fail|FailNow)\(|\brequire\.\w+\(|\bassert(_eq|_ne)?!")
# Anchored to the start of the line: a marker quoted inside a string (a linter's own tests) is not one.
SKIP = re.compile(
    r"^\s*(@pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail)\(|@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b"
    r"|(it|test|describe)\.(skip|todo|only|fixme)\(|x(it|describe|test)\(|f(it|describe)\(|t\.Skip(f|Now)?\("
    r"|#\[ignore\])")
TEST_CASE = re.compile(r"^\s*(async\s+)?def\s+test_\w+|\b(it|test)\(\s*['\"`]|^func\s+Test\w+|#\[test\]")
# Markers of unfinished work: a comment that starts with an upper-case marker (so a variable named
# `todo` and prose that mentions the word are not ones), or code that only raises "not implemented".
STUB = re.compile(
    r"(#|//|/\*+|<!--|^\s*\*)\s*(TODO|FIXME|XXX)\b|^\s*raise\s+NotImplementedError\b"
    r"|\bthrow\s+new\s+\w*Error\(\s*['\"`]\s*(?i:not\s+(yet\s+)?implemented)|\bunimplemented!\(|\btodo!\(")
MAX_UNTRACKED_BYTES = 500_000
SOURCE_FILE = re.compile(r"\.(py|[cm]?[jt]sx?|go|rs|rb|java|kt|swift|vue|svelte|php|cs|sh)$")


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.search(path))


# ---------------------------------------------------------------- git

def git(root: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True,
                          errors="replace")
    if done.returncode and check:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()[:200]}")
    return done.stdout if not done.returncode else ""


@functools.lru_cache(maxsize=32)
def git_dir(root: Path) -> Path:
    done = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=root, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{root} is not a git repository; the done check reads its state from git")
    return Path(done.stdout.strip())


# ---------------------------------------------------------------- fingerprint of the files

def ignored(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def list_files(root: Path, patterns) -> list[str]:
    """Tracked and untracked-but-not-ignored files, minus the ignore patterns."""
    out = git(root, "ls-files", "-co", "--exclude-standard", "-z")
    return sorted({path for path in out.split("\0") if path and not ignored(path, patterns)})


def fingerprint(root: Path, paths: list[str], previous: dict | None = None) -> dict:
    """path -> [size, mtime_ns, sha256]. A file whose size and mtime match `previous` is not re-read."""
    previous = previous or {}
    result = {}
    for path in paths:
        try:
            stat = (root / path).stat()
            old = previous.get(path)
            if old and old[0] == stat.st_size and old[1] == stat.st_mtime_ns:
                result[path] = old
                continue
            result[path] = [stat.st_size, stat.st_mtime_ns, hashlib.sha256((root / path).read_bytes()).hexdigest()]
        except OSError:
            continue  # listed by git but gone from disk: counts as deleted
    return result


def compare(recorded: dict, current: dict) -> dict:
    """Files changed, added and deleted since the record. Only content counts, not timestamps."""
    changed = sorted(path for path in current if path in recorded and current[path][2] != recorded[path][2])
    added = sorted(path for path in current if path not in recorded)
    deleted = sorted(path for path in recorded if path not in current)
    return {"changed": changed, "added": added, "deleted": deleted}


def read_stamp(root: Path) -> dict | None:
    try:
        return json.loads((git_dir(root) / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_stamp(root: Path, stamp: dict) -> None:
    path = git_dir(root) / STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamp), encoding="utf-8")


# ---------------------------------------------------------------- the test command

def detect_test_command(root: Path) -> str | None:
    if (root / "scripts" / "test.sh").is_file():
        return "scripts/test.sh"
    package = root / "package.json"
    if package.is_file():
        try:
            script = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
        except (OSError, ValueError):
            script = ""
        if script and "no test specified" not in script:
            runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
            return f"{runner} test"
    if (root / "Makefile").is_file() and re.search(r"^test:", (root / "Makefile").read_text(errors="replace"), re.M):
        return "make test"
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    if (root / "pyproject.toml").is_file() or (root / "pytest.ini").is_file():
        if any(root.glob("tests/test_*.py")) or any(root.glob("test_*.py")):
            return "pytest -q"
    return None


def run_tests(root: Path, command: str, patterns, timeout: int) -> dict:
    """Run the test command and record the outcome with a fingerprint of the files it ran on."""
    started = time.time()
    try:
        done = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, errors="replace",
                              timeout=timeout)
        code, output = done.returncode, (done.stdout + done.stderr)
    except subprocess.TimeoutExpired as error:
        code, output = 124, f"{error.stdout or ''}\ntimed out after {timeout}s"
    old = read_stamp(root)
    previous = (old or {}).get("files")
    stamp = {"command": command, "passed": code == 0, "exit": code, "at": int(started),
             "seconds": round(time.time() - started, 2), "tail": output.strip().splitlines()[-OUTPUT_TAIL:],
             "files": fingerprint(root, list_files(root, patterns), previous)}
    if not stamp["passed"] and old and old.get("passed"):
        stamp["last_pass"] = {key: old[key] for key in ("at", "files", "command")}  # keep the last good record
    elif old and old.get("last_pass") and not stamp["passed"]:
        stamp["last_pass"] = old["last_pass"]
    write_stamp(root, stamp)
    return stamp


def _listed(paths: list[str]) -> str:
    more = f" and {len(paths) - MAX_LISTED} more" if len(paths) > MAX_LISTED else ""
    return ", ".join(paths[:MAX_LISTED]) + more


def test_findings(stamp: dict | None, command: str | None, current: dict) -> list[dict]:
    run = f"agent-sense done --run   (runs: {command})" if command else "set [done] test in .agent-sense.toml"
    if not command and not stamp:
        return [{"rule": "no-test-command", "severity": "warn",
                 "message": "no test command found, so nothing shows the tests pass",
                 "fix": 'add to .agent-sense.toml:  [done]  test = "your test command"'}]
    if not stamp:
        return [{"rule": "tests-not-run", "severity": "fail", "message": "no test run is recorded for this project",
                 "fix": run}]
    if not stamp.get("passed"):
        return [{"rule": "tests-failed", "severity": "fail",
                 "message": f"the last test run failed (exit {stamp.get('exit')}): {stamp.get('command')}",
                 "fix": "fix the failure, then " + run, "detail": {"lines": stamp.get("tail", [])}}]
    diff = compare(stamp.get("files", {}), current)
    touched = diff["changed"] + diff["added"] + diff["deleted"]
    if not touched:
        return []
    parts = [f"{len(diff[key])} {key}" for key in ("changed", "added", "deleted") if diff[key]]
    return [{"rule": "tests-stale", "severity": "fail",
             "message": f"files changed after the last passing test run ({', '.join(parts)}): {_listed(sorted(touched))}",
             "fix": run, "detail": diff}]


# ---------------------------------------------------------------- weakened tests, from the diff

def parse_diff(text: str) -> list[dict]:
    """Files of a `git diff -M --unified=0`: path, status, and per hunk the added and removed lines."""
    files, current = [], None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = {"path": "", "old_path": "", "status": "modified", "hunks": []}
            files.append(current)
        elif current is None:
            continue
        elif line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("rename from "):
            current["status"], current["old_path"] = "renamed", line[len("rename from "):]
        elif line.startswith("rename to "):
            current["path"] = line[len("rename to "):]
        elif line.startswith("--- "):
            if line[4:] != "/dev/null":
                current["old_path"] = current["old_path"] or line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            if line[4:] != "/dev/null":
                current["path"] = line[4:].removeprefix("b/")
        elif line.startswith("@@"):
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            current["hunks"].append({"line": int(match.group(1)) if match else 0, "added": [], "removed": []})
        elif current["hunks"] and line.startswith("+"):
            current["hunks"][-1]["added"].append(line[1:])
        elif current["hunks"] and line.startswith("-"):
            current["hunks"][-1]["removed"].append(line[1:])
    for entry in files:
        entry["path"] = entry["path"] or entry["old_path"]
    return [entry for entry in files if entry["path"]]


def untracked_entries(root: Path) -> list[dict]:
    """New files git does not track yet, in the shape parse_diff returns, every line counted as added."""
    entries = []
    for path in git(root, "ls-files", "-o", "--exclude-standard", "-z").split("\0"):
        if not path or not (is_test_file(path) or SOURCE_FILE.search(path)):
            continue
        try:
            if (root / path).stat().st_size > MAX_UNTRACKED_BYTES:
                continue
            lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        entries.append({"path": path, "old_path": "", "status": "added", "hunks": [{"line": 1, "added": lines, "removed": []}]})
    return entries


def _count(pattern: re.Pattern, lines: list[str]) -> int:
    return sum(1 for line in lines if pattern.search(line) and not line.lstrip().startswith(("#", "//")))


def weakening_findings(files: list[dict]) -> list[dict]:
    """Findings about tests made weaker by a diff. Counts are net over the whole diff, so moving a
    test from one file to another is not reported."""
    findings = []
    tests = [entry for entry in files if is_test_file(entry["path"]) or is_test_file(entry["old_path"] or "")]
    source_changed = any(not is_test_file(entry["path"]) and SOURCE_FILE.search(entry["path"]) for entry in files)
    per_file, total_assert, total_cases = [], 0, 0
    for entry in tests:
        added = [line for hunk in entry["hunks"] for line in hunk["added"]]
        removed = [line for hunk in entry["hunks"] for line in hunk["removed"]]
        asserts = _count(ASSERTION, added) - _count(ASSERTION, removed)
        cases = _count(TEST_CASE, added) - _count(TEST_CASE, removed)
        total_assert, total_cases = total_assert + asserts, total_cases + cases
        per_file.append((entry, asserts, cases))
        if entry["status"] == "deleted" and (_count(ASSERTION, removed) or _count(TEST_CASE, removed)):
            findings.append({"rule": "test-deleted", "severity": "fail", "file": entry["path"],
                             "message": f"test file deleted: {entry['path']} "
                                        f"({_count(TEST_CASE, removed)} tests, {_count(ASSERTION, removed)} assertions)",
                             "fix": "restore it, or say in the hand-off why these tests no longer apply"})
        for hunk in entry["hunks"]:
            new_skips = [line.strip() for line in hunk["added"] if SKIP.search(line)]
            if len(new_skips) > _count(SKIP, hunk["removed"]):
                findings.append({"rule": "test-skipped", "severity": "fail", "file": entry["path"], "line": hunk["line"],
                                 "message": f"test switched off or narrowed: {new_skips[0][:120]}",
                                 "fix": "make the test pass instead of skipping it; remove .only before handing off"})
            if (entry["status"] == "modified" and source_changed and _count(ASSERTION, hunk["removed"])
                    and _count(ASSERTION, hunk["added"])):
                findings.append({
                    "rule": "expectation-changed", "severity": "warn", "file": entry["path"], "line": hunk["line"],
                    "message": "an assertion was rewritten in the same change as the code it tests: "
                               + next(line.strip() for line in hunk["added"] if ASSERTION.search(line))[:120],
                    "fix": "confirm the new expected value comes from the requirement, not from what the code returns"})
    if total_assert < 0:
        worst = sorted((item for item in per_file if item[1] < 0), key=lambda item: item[1])
        names = ", ".join(f"{entry['path']} ({count})" for entry, count, _ in worst[:MAX_LISTED])
        findings.append({"rule": "assertions-removed", "severity": "fail", "file": worst[0][0]["path"],
                         "message": f"{-total_assert} fewer assertions than before this change: {names}",
                         "fix": "put the checks back, or say in the hand-off which behaviour is no longer required"})
    if total_cases < 0 and not any(finding["rule"] == "test-deleted" for finding in findings):
        worst = sorted((item for item in per_file if item[2] < 0), key=lambda item: item[2])
        names = ", ".join(f"{entry['path']} ({count})" for entry, _, count in worst[:MAX_LISTED])
        findings.append({"rule": "test-removed", "severity": "warn", "file": worst[0][0]["path"],
                         "message": f"{-total_cases} fewer test cases than before this change: {names}", "fix": ""})
    return findings


def stub_findings(files: list[dict]) -> list[dict]:
    findings = []
    for entry in files:
        if is_test_file(entry["path"]) or not SOURCE_FILE.search(entry["path"]) or entry["status"] == "deleted":
            continue
        for hunk in entry["hunks"]:
            for offset, line in enumerate(hunk["added"]):
                if STUB.search(line) and not STUB.search(" ".join(hunk["removed"])):
                    findings.append({"rule": "stub-added", "severity": "warn", "file": entry["path"],
                                     "line": hunk["line"] + offset,
                                     "message": f"unfinished marker added: {line.strip()[:120]}",
                                     "fix": "finish it, or name it in the hand-off as not done"})
    return findings[:MAX_LISTED]


# ---------------------------------------------------------------- handed over?

def state_findings(root: Path, patterns) -> list[dict]:
    findings = []
    dirty = [line[3:] for line in git(root, "status", "--porcelain").splitlines() if line[3:]]
    if dirty:
        findings.append({"rule": "uncommitted", "severity": "warn",
                         "message": f"{len(dirty)} uncommitted path(s): {_listed(dirty)}", "fix": "commit the work"})
    upstream = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).strip()
    if upstream:
        ahead = git(root, "rev-list", "--count", "@{u}..HEAD", check=False).strip()
        if ahead and ahead != "0":
            findings.append({"rule": "unpushed", "severity": "warn",
                             "message": f"{ahead} commit(s) not pushed to {upstream}", "fix": "git push"})
    elif git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
        findings.append({"rule": "unpushed", "severity": "warn",
                         "message": "the current branch has no upstream, so nothing here is pushed",
                         "fix": "git push -u origin HEAD"})
    return findings


# ---------------------------------------------------------------- service interface

def add_arguments(parser) -> None:
    parser.add_argument("--run", action="store_true",
                        help="run the test command first and record the result for the files as they are")
    parser.add_argument("--test", help="test command (default: [done] test in .agent-sense.toml, else detected)")
    parser.add_argument("--base", help="compare tests against this git ref (default: HEAD, i.e. uncommitted changes)")
    parser.add_argument("--staged", action="store_true", help="look only at staged changes (for a pre-commit hook)")
    parser.add_argument("--no-state", action="store_true", help="do not report uncommitted or unpushed work")
    parser.add_argument("--test-timeout", type=int, default=900, help="seconds before the test command is stopped")


def run(args) -> dict:
    root, config = Path(args.root), dict(getattr(args, "config", None) or {})
    git_dir(root)
    patterns = tuple(DEFAULT_IGNORE) + tuple(config.get("ignore", ()))
    command = getattr(args, "test", None) or config.get("test") or detect_test_command(root)
    if getattr(args, "run", False):
        if not command:
            raise RuntimeError('no test command: pass --test or set [done] test = "..." in .agent-sense.toml')
        stamp = run_tests(root, command, patterns, getattr(args, "test_timeout", 900))
    else:
        stamp = read_stamp(root)
    current = fingerprint(root, list_files(root, patterns), (stamp or {}).get("files"))
    findings = test_findings(stamp, command, current)

    # With no commit yet there is nothing to compare against: the diff fails and is read as empty.
    staged = getattr(args, "staged", False)
    diff_args = ["diff", "-M", "--unified=0", "--no-color", "--no-ext-diff"]
    diff_args += ["--cached"] if staged else [getattr(args, "base", None) or "HEAD"]
    files = parse_diff(git(root, *diff_args, check=False))
    if not staged:
        files += untracked_entries(root)   # git diff leaves new files out; a test moved into one must count
    findings += weakening_findings(files) + stub_findings(files)
    if not getattr(args, "no_state", False) and config.get("state", True):
        findings += state_findings(root, patterns)
    for finding in findings:
        override = config.get("severity", {}).get(finding["rule"])
        if override in ("fail", "warn", "info"):
            finding["severity"] = override
    stats = {"test_command": command, "files": len(current),
             "last_run": {key: stamp[key] for key in ("at", "passed", "seconds", "command")} if stamp else None}
    return {"findings": findings, "stats": stats}

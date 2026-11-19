"""Instruction files that point at things that are not there.

CLAUDE.md, AGENTS.md and their relatives are loaded into every session. When a file, script or
command they name has been renamed or deleted, the agent follows the stale instruction anyway:
it hunts for the file, recreates it, or runs a command that fails. This check extracts the paths
and commands such files mention and verifies them against the project. See docs/context.md.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

NAME = "context"
SUMMARY = "CLAUDE.md, AGENTS.md and memory files that name missing files or commands"
RULES = {"missing-path": "warn", "missing-script": "warn", "memory-dangling": "warn",
         "memory-unindexed": "warn", "oversized": "info"}

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", ".agent-sense"}
DEFAULT_FILES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".windsurfrules",
                 ".claude/**/*.md", ".cursor/rules/**", ".github/copilot-instructions.md")
DEFAULT_MAX_BYTES = 40_000
EXTENSIONS = set("""py pyi js jsx ts tsx mjs cjs vue svelte json jsonl toml yaml yml md mdx txt sh bash zsh ps1 bat
html htm css scss sass less sql csv tsv ini cfg conf env lock xml svg png jpg jpeg gif webp ico pdf go rs java kt
swift rb php c h cpp hpp cs ipynb proto graphql gql tf dockerfile mk log""".split())
PACKAGE_MANAGER_BUILTINS = set("""install i add remove rm uninstall update up upgrade ci init create dlx exec x run
test t start stop restart publish pack link unlink login logout whoami version view info outdated audit dedupe
prune rebuild config cache store why list ls root bin help import fetch patch patch-commit deploy env setup
approve-builds self-update global workspace workspaces set get pm completion doctor explain fund search
ping prefix repo docs bugs owner token team access adduser dist-tag deprecate unpublish star unstar pkg query
sbom shrinkwrap find-dupes generate-lock-entry node versions policies plugin constraints rebuild npm exec
build-storybook licenses tag upgrade-interactive check autoclean cat-file recursive multi prepare""".split())

_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)(.*)$")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AT_IMPORT = re.compile(r"(?:^|\s)@((?:\.{1,2}/|~/)?[\w.-]+(?:/[\w.@+-]+)*\.[A-Za-z0-9]+)")
_PATHLIKE = re.compile(r"^(?:\./|\.\./)*[\w.@+-]+(?:/[\w.@+-]+)*/?$")
_SUFFIX = re.compile(r"(?::\d+(?:[-:]\d+)?|#L\d+(?:-L?\d+)?|#[\w-]+)$")
_PLACEHOLDER = re.compile(r"[<>{}$*?\[\]|%\\]|\.\.\.|\u2026|XXX|xxx|YYYY|your[-_]|path/to|/path/|example\.|(?<![A-Za-z])(?:foo|bar|baz)(?![A-Za-z])|[A-Z]\.[A-Z]\.[A-Z]|NNN")
_SCRIPT_CMD = re.compile(r"(?<![\w/-])(npm|pnpm|yarn|bun)\s+(?:(?:--prefix|--filter|-C|--cwd|-w|--workspace)(?:=|\s+)\S+\s+)*"
                         r"(run(?:-script)?\s+)?([A-Za-z0-9][\w:.-]*)")
_MAKE_CMD = re.compile(r"(?<![\w/-])make\s+(?:-[A-Za-z]+\s+(?:\S+\s+)?)*([A-Za-z][\w.-]*)(?![\w=/.])")
_MAKE_TARGET = re.compile(r"(?m)^([A-Za-z0-9][\w./-]*(?:[ \t]+[A-Za-z0-9][\w./-]*)*)[ \t]*:(?!=)")
_FRONT_NAME = re.compile(r"(?m)^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$")
# copies of whole projects and tool state that live under .claude, not instructions
_NOT_INSTRUCTIONS = re.compile(r"(^|/)\.claude/(worktrees|projects|plugins|todos|plans|shell-snapshots|statsig|cache|"
                               r"file-history|session-env|tasks|ide|backups|debug|paste-cache|telemetry)/")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")   # a Windows absolute path, C:\\dir or C:/dir
_WIKI = re.compile(r"\[\[([^\]\n|#]+)(?:[|#][^\]\n]*)?\]\]")


# ---------------------------------------------------------------------------------------------
# files


def in_git(root: Path) -> bool:
    resolved = root.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def list_files(root: Path) -> list[str]:
    try:
        if not in_git(root):
            raise OSError("not a git repository")
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                              capture_output=True, timeout=30)
        if done.returncode == 0:
            names = [name for name in done.stdout.decode("utf-8", "replace").split("\0") if name]
            return sorted(name for name in names if not (set(name.split("/")[:-1]) & SKIP_DIRS))
    except (OSError, subprocess.SubprocessError):
        pass
    found, stack = [], [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                found.append(entry.relative_to(root).as_posix())
    return sorted(found)


def matches(name: str, patterns) -> bool:
    """A pattern without a slash matches the file name at any depth; with one, the path suffix; `./x` only the root."""
    base = name.rsplit("/", 1)[-1]
    for pattern in patterns:
        if pattern.startswith("./"):
            if fnmatch.fnmatchcase(name, pattern[2:]):
                return True
            continue
        if "/" not in pattern:
            if fnmatch.fnmatchcase(base, pattern):
                return True
            continue
        regex = "".join(".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
                        for part in re.split(r"(\*\*|\*)", pattern))
        if re.search(rf"(^|/){regex}$", name):
            return True
    return False


def ignored_by_git(root: Path, paths: list[str]) -> set[str]:
    """The subset of relative paths that .gitignore covers (outputs that need not exist yet)."""
    if not paths or not in_git(root):
        return set()
    try:
        done = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
                              input="\0".join(paths).encode(), capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if done.returncode not in (0, 1):
        return set()
    return {name for name in done.stdout.decode("utf-8", "replace").split("\0") if name}


# ---------------------------------------------------------------------------------------------
# reading an instruction file


def code_fragments(text: str) -> list[tuple[int, str, str]]:
    """(line, kind, fragment): 'code' for inline spans and fenced lines, 'link' for link targets."""
    found, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        match = _FENCE.match(line)
        if match and (fence is None or (match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence)
                                        and not match.group(2).strip())):
            fence = None if fence else match.group(1)
            continue
        if fence:
            found.append((number, "code", line))
            continue
        for span in _INLINE_CODE.finditer(line):
            found.append((number, "code", span.group(2)))
        bare = _INLINE_CODE.sub(" ", line)
        for link in _LINK.finditer(bare):
            found.append((number, "link", link.group(1)))
        for mention in _AT_IMPORT.finditer(bare):
            found.append((number, "link", mention.group(1)))
    return found


def path_candidate(token: str, kind: str) -> str | None:
    """The token as a path worth checking, or None when it does not look like a project path."""
    token = token.strip().strip("'\"()[],;").rstrip(".:")
    token = _SUFFIX.sub("", token)
    if not token or len(token) > 200 or token.startswith(("-", "#", "@", "mailto:")):
        return None
    if "://" in token or token.startswith("~"):
        return None
    drive = bool(_DRIVE.match(token))
    if drive:
        token = token.replace("\\", "/")   # reported and compared with forward slashes
    if _PLACEHOLDER.search(token) or "=" in token or "//" in token:
        return None
    absolute = drive or token.startswith("/")
    body = token[3:] if drive else token.lstrip("/") if absolute else token
    if not _PATHLIKE.match(body):
        return None
    last = body.rstrip("/").rsplit("/", 1)[-1]
    extension = last.rsplit(".", 1)[-1].lower() if "." in last.strip(".") else ""
    has_slash = "/" in body.strip("/") or body.endswith("/") or body.startswith(("./", "../"))
    if kind == "link":
        return token if (has_slash or extension) else None
    if not has_slash and extension not in EXTENSIONS:
        return None
    if not has_slash and re.fullmatch(r"[\w-]+(\.[\w-]+){2,}", body) and extension not in EXTENSIONS:
        return None  # a dotted name such as a module or a domain
    if re.fullmatch(r"\d+(?:[./]\d+)*", body):
        return None
    return token


def tokens(fragment: str) -> list[str]:
    return [part for part in re.split(r"[\s;&|()=,\"']+", fragment) if part]


# ---------------------------------------------------------------------------------------------
# the checks


_SEQUENCE = iter(range(10**12))


def _finding(rule: str, severity: str, message: str, fix: str, file: str, line: int | None, detail: dict,
             order: int | None = None) -> dict:
    detail = {**detail, "_order": next(_SEQUENCE) if order is None else order}
    finding = {"rule": rule, "severity": severity, "message": message, "fix": fix, "file": file, "detail": detail}
    if line:
        finding["line"] = line
    return finding


class Project:
    def __init__(self, root: Path, files: list[str]):
        self.root = root
        self.files = set(files)
        self.dirs = {""}
        for name in files:
            parts = name.split("/")[:-1]
            self.dirs.update("/".join(parts[:count]) for count in range(1, len(parts) + 1))
        self.basenames = {name.rsplit("/", 1)[-1] for name in files}
        self.checked = 0
        self.scripts, self.has_package_json = set(), False
        self.make_targets, self.has_makefile = set(), False
        for name in files:
            base = name.rsplit("/", 1)[-1]
            if base == "package.json":
                self.has_package_json = True
                try:
                    data = json.loads((root / name).read_text(encoding="utf-8", errors="replace"))
                    self.scripts.update((data.get("scripts") or {}) if isinstance(data, dict) else ())
                except (OSError, ValueError):
                    pass
            elif base in ("Makefile", "makefile", "GNUmakefile") or base.endswith(".mk"):
                self.has_makefile = True
                try:
                    text = (root / name).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for match in _MAKE_TARGET.finditer(text):
                    self.make_targets.update(match.group(1).split())
                if re.search(r"(?m)^%|\$\(", text):
                    self.make_targets.add("*")

    def exists(self, relative: str) -> bool:
        relative = relative.strip("/")
        return relative in self.files or relative in self.dirs or (self.root / relative).exists()


def resolve(token: str, source_dir: str) -> list[str]:
    """Root-relative places a mentioned path may mean: from the root, and from the file's directory."""
    body = token.rstrip("/")
    places = []
    for base in dict.fromkeys(("", source_dir)):
        joined = os.path.normpath(os.path.join(base, body)).replace(os.sep, "/")
        if not joined.startswith(".."):
            places.append("" if joined == "." else joined)
    return places


def check_file(project: Project, name: str, text: str, strict: bool, base: Path | None = None) -> list[dict]:
    """Findings for one instruction file. `base` is set for files outside the project (memory)."""
    root = project.root
    source_dir = name.rsplit("/", 1)[0] if "/" in name and base is None else ""
    findings, seen, pending = [], set(), []
    for line, kind, fragment in code_fragments(text):
        for token in ([fragment] if kind == "link" else tokens(fragment)):
            candidate = path_candidate(token, kind)
            if candidate is None or candidate in seen:
                continue
            project.checked += 1
            if candidate.startswith("/") or _DRIVE.match(candidate):
                target = Path(candidate)
                # Windows names are compared without regard to case, and always with forward slashes
                fold = str.casefold if _DRIVE.match(candidate) else str
                under = lambda prefix: fold(candidate).startswith(fold(prefix.as_posix()) + "/")  # noqa: E731
                if not (under(root) or under(Path.home())) or (
                        not under(root) and candidate.startswith(("/tmp/", "/var/", "/private/"))):
                    continue
                seen.add(candidate)
                if not target.exists() and target.parent.exists():
                    findings.append(_finding(
                        "missing-path", "warn", f"`{candidate}` is named here but does not exist",
                        "update or remove the reference", name, line, {"path": candidate, "anchored": True}))
                continue
            places = resolve(candidate, source_dir)
            if candidate.startswith(root.name + "/"):  # written with the project folder's own name in front
                places += resolve(candidate[len(root.name) + 1:], "")
            if not places:
                continue
            if any(project.exists(place) for place in places):
                continue
            if base is not None and (base / candidate).exists():
                continue
            last = candidate.rstrip("/").rsplit("/", 1)[-1]
            if "/" not in candidate.strip("/").lstrip("./") and last in project.basenames:
                continue  # a bare file name that exists somewhere in the project
            # anchored: the directory the path sits in exists, so it reads as a real project path
            inner = "/" in candidate.rstrip("/").removeprefix("./")
            anchored = inner and any("/" in place and project.exists(place.rsplit("/", 1)[0]) for place in places)
            if not anchored and not (strict or kind == "link"):
                continue
            seen.add(candidate)
            pending.append((line, candidate, places, anchored, next(_SEQUENCE)))
        if kind != "code":
            continue
        for match in _SCRIPT_CMD.finditer(fragment):
            manager, explicit, script = match.group(1), match.group(2), match.group(3)
            if not project.has_package_json or script in project.scripts or ("script", script) in seen:
                continue
            if not explicit and (script in PACKAGE_MANAGER_BUILTINS or manager == "npm"):
                continue
            if _PLACEHOLDER.search(script) or script in ("X", "x", "script", "name"):
                continue
            seen.add(("script", script))
            findings.append(_finding(
                "missing-script", "warn", f"`{manager} {'run ' if explicit else ''}{script}` is given as a command but "
                f"no package.json has a script named `{script}`", "fix the command or add the script", name, line,
                {"command": match.group(0).strip(), "script": script}))
        for match in _MAKE_CMD.finditer(fragment):
            target = match.group(1)
            if not project.has_makefile or "*" in project.make_targets or target in project.make_targets \
                    or ("make", target) in seen or target in ("sure", "it", "the", "a", "an"):
                continue
            seen.add(("make", target))
            findings.append(_finding(
                "missing-script", "warn", f"`make {target}` is given as a command but no Makefile has that target",
                "fix the command or add the target", name, line, {"command": match.group(0).strip(), "script": target}))
    generated = ignored_by_git(root, [place for _, _, places, _, _ in pending for place in places])
    for line, candidate, places, anchored, order in pending:
        if any(place in generated for place in places):
            continue
        findings.append(_finding(
            "missing-path", "warn", f"`{candidate}` is named here but does not exist in the project",
            "update the path to where the file lives now, or remove the reference", name, line,
            {"path": candidate, "anchored": anchored}, order))
    return findings


def check_memory(directory: Path, label: str) -> tuple[list[dict], dict]:
    """A Claude Code memory directory: MEMORY.md is the index, every other .md file is one memory."""
    findings = []
    index = directory / "MEMORY.md"
    memories = sorted(path for path in directory.glob("*.md") if path.name != "MEMORY.md")
    stats = {"memory_files": len(memories)}
    index_name = f"{label}/MEMORY.md"
    names = {}
    for path in memories:
        text = path.read_text(encoding="utf-8", errors="replace")
        front = text.split("\n---", 1)[0] if text.startswith("---") else ""
        match = _FRONT_NAME.search(front)
        names[path.name] = match.group(1).strip() if match else path.stem
    if not index.exists():
        if memories:
            findings.append(_finding("memory-unindexed", "warn", f"{len(memories)} memory files but no MEMORY.md index",
                                     "create MEMORY.md with one line per memory", index_name, None, {}))
        return findings, stats
    text = index.read_text(encoding="utf-8", errors="replace")
    pointed = {}
    for number, line in enumerate(text.splitlines(), 1):
        for link in _LINK.finditer(line):
            target = link.group(1).split("#")[0]
            if "://" in target or not target:
                continue
            pointed.setdefault(target.lstrip("./"), number)
    for target, number in sorted(pointed.items(), key=lambda item: item[1]):
        if not (directory / target).exists():
            findings.append(_finding(
                "memory-dangling", "warn", f"MEMORY.md points to `{target}`, which does not exist",
                "remove the line or restore the memory file", index_name, number, {"target": target}))
    for path in memories:
        if path.name not in pointed:
            findings.append(_finding(
                "memory-unindexed", "warn", f"`{path.name}` is not listed in MEMORY.md, so it is never loaded",
                "add a one-line pointer to MEMORY.md, or delete the file", f"{label}/{path.name}", None,
                {"target": path.name}))
    known = set(names.values()) | {path.stem for path in memories}
    for path in memories:
        body = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(body.splitlines(), 1):
            for link in _WIKI.finditer(_INLINE_CODE.sub(" ", line)):
                target = link.group(1).strip()
                if target not in known:
                    findings.append(_finding(
                        "memory-dangling", "info", f"`[[{target}]]` links to a memory that does not exist",
                        "write that memory, or drop the link", f"{label}/{path.name}", number, {"target": target}))
    return findings, stats


def add_arguments(parser) -> None:
    parser.add_argument("--files", action="append", metavar="PATTERN",
                        help="instruction files to check instead of the defaults (repeatable; a name matches at any depth)")
    parser.add_argument("--memory", action="append", metavar="DIR",
                        help="a Claude Code memory directory to check (repeatable)")
    parser.add_argument("--strict", action="store_true", default=None,
                        help="also report paths whose directory does not exist in the project (more false alarms)")
    parser.add_argument("--max-bytes", type=int, metavar="N",
                        help=f"size above which an instruction file is reported (default {DEFAULT_MAX_BYTES})")


def run(args) -> dict:
    root = Path(args.root)
    if not root.is_dir():
        raise RuntimeError(f"not a directory: {root}")
    config = getattr(args, "config", None) or {}
    patterns = getattr(args, "files", None) or config.get("files") or DEFAULT_FILES
    strict = getattr(args, "strict", None)
    strict = bool(config.get("strict", False)) if strict is None else strict
    max_bytes = getattr(args, "max_bytes", None) or config.get("max-bytes") or DEFAULT_MAX_BYTES
    memory_dirs = list(getattr(args, "memory", None) or []) + list(config.get("memory", []))
    ignore = list(config.get("ignore", []))
    severity = config.get("severity", {}) if isinstance(config.get("severity"), dict) else {}

    files = list_files(root)
    project = Project(root, files)
    targets = [name for name in files if matches(name, patterns) and not _NOT_INSTRUCTIONS.search(name)]
    findings = []
    for name in targets:
        try:
            raw = (root / name).read_bytes()
        except OSError:
            continue
        if len(raw) > max_bytes:
            findings.append(_finding(
                "oversized", "info", f"{name} is {len(raw):,} bytes; every session loads all of it (limit {max_bytes:,})",
                "move reference material into files that are read on demand", name, None, {"bytes": len(raw)}))
        findings += check_file(project, name, raw.decode("utf-8", "replace"), strict)
    stats = {"instruction_files": len(targets), "memory_dirs": 0, "memory_files": 0}
    for entry in memory_dirs:
        directory = Path(entry).expanduser()
        directory = directory if directory.is_absolute() else root / directory
        if not directory.is_dir():
            raise RuntimeError(f"memory directory not found: {directory}")
        label = directory.name
        found, part = check_memory(directory, label)
        findings += found
        stats["memory_dirs"] += 1
        stats["memory_files"] += part["memory_files"]
        for path in sorted(directory.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            findings += check_file(project, f"{label}/{path.name}", text, strict, base=directory)
    stats["paths_checked"] = project.checked
    kept = []
    for finding in findings:
        path = finding["detail"].get("path") or finding["detail"].get("script") or ""
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in ignore):
            continue
        override = severity.get(finding["rule"])
        if override == "off":
            continue
        if override in ("fail", "warn", "info"):
            finding["severity"] = override
        kept.append(finding)
    kept.sort(key=lambda item: (item.get("file", ""), item.get("line", 0), item["detail"]["_order"]))
    for finding in kept:
        del finding["detail"]["_order"]
    return {"findings": kept, "stats": stats}

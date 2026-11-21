#!/usr/bin/env python3
"""Checks before this repository or a build of it leaves the machine. Standard library only.

    scripts/release_check.py tree              tracked files as they are now, and new files git does not ignore
    scripts/release_check.py history           every commit of every ref: diffs, messages, author and committer addresses
    scripts/release_check.py dist DIR          the wheel and the sdist in DIR: which files are inside, and their text
    scripts/release_check.py meta              versions agree, the changelog has the entry, workflow actions are pinned
    scripts/release_check.py index             is the distribution name on the package index free, or ours? (the one mode that uses the network)

What is looked for in text: an e-mail address outside the reserved example domains, a home
directory of a named user, a token in one of the common shapes, a private key header, and every
word of an optional names file (private project names, host names, user names; one per line).
The names file is not in the repository, because the list itself is private: by default
runs/private_names.txt, which git ignores; pass --names FILE for another.

Exit code 0 with no hit, 1 with hits (one per line: where, what kind, the text), 2 when it cannot run.
A pattern scan finds shapes, not meaning: a private fact written in ordinary words passes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES_DEFAULT = ROOT / "runs" / "private_names.txt"
MAX_ARCHIVE_BYTES = 1_000_000

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Reserved for documentation and tests (RFC 2606, RFC 6761), GitHub's no-reply addresses, and the
# co-author line of the agent that wrote the commits.
EMAIL_ALLOWED = re.compile(r"(?:[@.]example\.(?:com|org|net|jp)|[@.][a-z0-9-]+\.(?:invalid|example|test)|[@.]example"
                           r"|[@.]users\.noreply\.github\.com|^noreply@github\.com|^noreply@anthropic\.com|^git@github\.com"
                           r"|^hi@x\.jp)$", re.IGNORECASE)   # hi@x.jp: a contact address in a launch test page
# Not addresses: a decorator after a newline escape in a string (n@pytest.mark.skip), a package at a host.
EMAIL_NOT_ONE = re.compile(r"@(?:pytest|functools|dataclasses|staticmethod|classmethod|property)\b")

HOME = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\+Users\\+)([A-Za-z0-9._-]+)")
HOME_PLACEHOLDERS = {"someone", "x", "me", "you", "user", "username", "name", "runner", "shared", "example", "dev", "alice", "bob"}

TOKENS = {
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})"),
    "pypi-token": re.compile(r"\bpypi-[A-Za-z0-9_-]{40,}"),
    "aws-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "google-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "model-api-key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}"),
    "hf-token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "live-payment-key": re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

# What a build may contain. Anything else in an archive is a hit, so a new top-level directory
# has to be allowed here on purpose.
WHEEL_ALLOWED = re.compile(r"^(?:agent_sense/[a-z_]+\.py"
                           r"|agent_sense/_services/[a-z_]+/(?:[a-z_]+\.py|vendors\.json)"
                           r"|agent_sense-[0-9][^/]*\.dist-info/(?:METADATA|WHEEL|RECORD|entry_points\.txt|licenses/LICENSE))$")
SDIST_ALLOWED = re.compile(r"^(?:agent_sense/[a-z_]+\.py|contracts/(?:[a-z_]+\.py|README\.md)"
                           r"|services/([a-z_]+)/(?:\1_lib\.py|vendors\.json)|docs/[a-z_]+\.md"
                           r"|README\.md|CHANGELOG\.md|LICENSE|pyproject\.toml|PKG-INFO|\.gitignore)$")


def scan_text(text: str, names: tuple[str, ...] = ()) -> list[tuple[int, str, str]]:
    """(line number, kind, matched text) for every hit in the text."""
    hits = []
    lowered_names = [name.lower() for name in names if name.strip()]
    for number, line in enumerate(text.splitlines(), 1):
        for match in EMAIL.finditer(line):
            address = match.group(0)
            if not EMAIL_ALLOWED.search(address) and not EMAIL_NOT_ONE.search(address):
                hits.append((number, "email", address))
        for match in HOME.finditer(line):
            if match.group(1).lower() not in HOME_PLACEHOLDERS:
                hits.append((number, "home-path", match.group(0)))
        for kind, pattern in TOKENS.items():
            for match in pattern.finditer(line):
                hits.append((number, kind, match.group(0)[:12] + "..."))   # never print a whole secret
        lowered = line.lower()
        for name in lowered_names:
            if name in lowered:
                hits.append((number, "private-name", name))
    return hits


def read_names(path: Path | None) -> tuple[str, ...]:
    path = path or (NAMES_DEFAULT if NAMES_DEFAULT.is_file() else None)
    if path is None:
        return ()
    if not path.is_file():
        raise RuntimeError(f"names file not found: {path}")
    return tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#"))


def git(*args: str) -> bytes:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {done.stderr.decode(errors='replace').strip()[:200]}")
    return done.stdout


def decode(data: bytes) -> str | None:
    """Text of a file, or None for a binary one (those are reported by name, not scanned)."""
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace")


def check_tree(names: tuple[str, ...]) -> tuple[list[str], str]:
    # Tracked files and new files git does not ignore: what the next commit can contain.
    hits, files = [], [name for name in git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0") if name]
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        text = decode(path.read_bytes())
        if text is None:
            hits.append(f"{name}: binary-file: tracked binary, read it by hand")
            continue
        hits += [f"{name}:{line}: {kind}: {found}" for line, kind, found in scan_text(text, names)]
    return hits, f"{len(files)} tracked and new files"


def check_history(names: tuple[str, ...]) -> tuple[list[str], str]:
    hits = []
    people = git("log", "--all", "--format=%h%x09%an <%ae>%x09%cn <%ce>").decode(errors="replace").splitlines()
    for entry in people:
        commit, *who = entry.split("\t")
        for person in who:
            address = person[person.rfind("<") + 1:-1]
            if not re.search(r"(?:@users\.noreply\.github\.com|^noreply@github\.com)$", address):
                hits.append(f"{commit}: author-email: {person}")
            hits += [f"{commit}: author {kind}: {found}" for _, kind, found in scan_text(person, names) if kind == "private-name"]
    commit, where = "", ""
    log = git("log", "--all", "-p", "--no-color", "--format=%x01%h%n%B%x02").decode("utf-8", errors="replace")
    for line in log.splitlines():
        if line.startswith("\x01"):
            commit, where = line[1:], "message"
            continue
        if line.startswith("\x02"):
            where = "diff"
            continue
        if line.startswith(("+++ ", "--- ")):
            where = line[6:] if line[4:6] in ("a/", "b/") else where
            continue
        if line and (where == "message" or line[0] in "+-"):
            body = line if where == "message" else line[1:]
            sign = "" if where == "message" else ("added " if line[0] == "+" else "removed ")
            hits += [f"{commit} {where}: {sign}{kind}: {found}" for _, kind, found in scan_text(body, names)]
    unique = list(dict.fromkeys(hits))
    return unique, f"{len(people)} commits on all refs"


def archive_members(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [(info.filename, archive.read(info)) for info in archive.infolist() if not info.is_dir()]
    with tarfile.open(path) as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        return [(member.name.split("/", 1)[1] if "/" in member.name else member.name, archive.extractfile(member).read())
                for member in members]


def unexpected_members(kind: str, members: list[str]) -> list[str]:
    allowed = WHEEL_ALLOWED if kind == "wheel" else SDIST_ALLOWED
    return [name for name in members if not allowed.match(name)]


def check_dist(directory: Path, names: tuple[str, ...]) -> tuple[list[str], str]:
    wheels, sdists = sorted(directory.glob("*.whl")), sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(f"{directory}: expected one wheel and one sdist, found {len(wheels)} and {len(sdists)}")
    hits, count = [], 0
    for kind, path in (("wheel", wheels[0]), ("sdist", sdists[0])):
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            hits.append(f"{path.name}: too-large: {path.stat().st_size} bytes, limit {MAX_ARCHIVE_BYTES}")
        members = archive_members(path)
        count += len(members)
        hits += [f"{path.name}: unexpected-file: {name}" for name in unexpected_members(kind, [name for name, _ in members])]
        for name, data in members:
            text = decode(data)
            if text is None:
                hits.append(f"{path.name}:{name}: binary-file: read it by hand")
            elif not name.endswith("/RECORD"):
                hits += [f"{path.name}:{name}:{line}: {what}: {found}" for line, what, found in scan_text(text, names)]
    return hits, f"{wheels[0].name} and {sdists[0].name}, {count} files"


USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s*#\s*v\d+\.\d+\.\d+\s*$")


def unpinned_actions(workflow: str) -> list[tuple[int, str]]:
    """Lines whose action is not a full commit SHA followed by a comment with the version it stands for."""
    bad = []
    for number, line in enumerate(workflow.splitlines(), 1):
        match = USES.match(line)
        if match and not match.group(1).startswith("./") and not (PINNED.match(match.group(1)) and VERSION_COMMENT.match(match.group(2))):
            bad.append((number, line.strip()))
    return bad


RELATIVE_LINK = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)")


def check_meta() -> tuple[list[str], str]:
    hits = []
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    init = (ROOT / "agent_sense" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in init:
        hits.append(f"agent_sense/__init__.py: version: __version__ is not {version}, the version in pyproject.toml")
    changelog = ROOT / "CHANGELOG.md"
    headings = re.findall(r"^## \[?([0-9][^\]\s]*)\]?", changelog.read_text(encoding="utf-8"), re.MULTILINE) if changelog.is_file() else []
    if not headings or headings[0] != version:
        hits.append(f"CHANGELOG.md: version: the first entry is {headings[0] if headings else 'missing'}, expected {version}")
    license_file = ROOT / "LICENSE"
    if project.get("license") != "MIT" or not license_file.is_file() or not license_file.read_text(encoding="utf-8").startswith("MIT License"):
        hits.append("pyproject.toml: license: the license expression and the LICENSE file do not both say MIT")
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    for path in workflows:
        hits += [f"{path.relative_to(ROOT).as_posix()}:{line}: unpinned-action: {text}"
                 for line, text in unpinned_actions(path.read_text(encoding="utf-8"))]
    # The README is the project page on the package index, where a relative link leads nowhere.
    for number, line in enumerate((ROOT / "README.md").read_text(encoding="utf-8").splitlines(), 1):
        hits += [f"README.md:{number}: relative-link: {target}" for target in RELATIVE_LINK.findall(line)]
    return hits, f"version {version}, {len(workflows)} workflows"


def index_verdict(status: int, payload: dict, name: str, repository: str) -> str | None:
    """None when the name can be uploaded to from this repository, else the reason it cannot.

    Free (404) is fine: the first upload creates the project. A project that exists must name this
    repository among its URLs, otherwise it is somebody else's and an install of the name gives their code."""
    if status == 404:
        return None
    if status != 200:
        return f"the index answered {status} for {name}; nothing can be said"
    info = payload.get("info") or {}
    urls = [str(url).rstrip("/").lower() for url in [*(info.get("project_urls") or {}).values(), info.get("home_page") or ""]]
    wanted = repository.rstrip("/").lower()
    if any(url == wanted or url.startswith(wanted + "/") for url in urls):
        return None
    return (f"the name {name} is taken by another project: version {info.get('version')}, "
            f"\"{info.get('summary')}\", {info.get('home_page') or (info.get('project_urls') or {}).get('Homepage') or 'no home page'}")


def check_index(index: str) -> tuple[list[str], str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name, repository = project["name"], project["urls"]["Homepage"]
    request = urllib.request.Request(f"{index.rstrip('/')}/pypi/{name}/json", headers={"User-Agent": "release-check"})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:   # noqa: S310  a fixed https index
            status, payload = answer.status, json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        status, payload = error.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(f"{index} could not be asked: {error}") from error
    reason = index_verdict(status, payload, name, repository)
    return ([f"pyproject.toml: name-taken: {reason}"] if reason else []), f"{name} on {index} ({'free' if status == 404 else status})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("tree", "history", "dist", "meta", "index"))
    parser.add_argument("directory", nargs="?", help="for dist: the directory that holds the wheel and the sdist")
    parser.add_argument("--names", help=f"file with private words, one per line (default: {NAMES_DEFAULT.relative_to(ROOT)} if present)")
    parser.add_argument("--index", default="https://pypi.org", help="for index: the package index to ask (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        names = read_names(Path(args.names) if args.names else None)
        if args.mode == "tree":
            hits, scope = check_tree(names)
        elif args.mode == "history":
            hits, scope = check_history(names)
        elif args.mode == "meta":
            hits, scope = check_meta()
        elif args.mode == "index":
            hits, scope = check_index(args.index)
        else:
            if not args.directory:
                raise RuntimeError("dist needs the directory that holds the wheel and the sdist")
            hits, scope = check_dist(Path(args.directory), names)
    except (RuntimeError, OSError, tarfile.TarError, zipfile.BadZipFile, tomllib.TOMLDecodeError, KeyError) as error:
        print(f"release-check: cannot run: {error}", file=sys.stderr)
        return 2
    for hit in hits:
        print(hit)
    words = f", {len(names)} private words" if args.mode in ("tree", "history", "dist") else ""
    print(f"release-check {args.mode}: {scope}{words}, {len(hits)} hits")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Services that reach into each other.

The layout this checks is `services/<name>/`, one concern per directory, talking only through
shared contracts. That is what lets two agent sessions work on two services at once. An agent
that needs a helper from the service next door imports it, and the two services are now one.
This check finds those reaches: imports, sys.path edits and path literals. See docs/boundary.md.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

NAME = "boundary"
SUMMARY = "code in one service that imports or reads another service's internals"
RULES = {"cross-service-import": "fail", "cross-service-syspath": "fail", "cross-service-path": "warn"}

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", ".agent-sense"}
DEFAULT_SERVICES_DIR = "services"
DEFAULT_ALLOW = ("contracts", "schemas")
JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
TEXT_SUFFIXES = (".sh", ".bash", ".zsh")
NOT_MODULES = {"tests", "test", "__pycache__", "conftest", "__init__", "__main__", "setup"}
MAX_BYTES = 1_500_000

_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_IMPORT = (
    re.compile(r"""(?m)(?:^|[;}>])[ \t]*import\s+(?:type\s+)?(?:[\w$*\s{},]+?\s+from\s+)?(['"])([^'"\n]+)\1"""),
    re.compile(r"""(?m)(?:^|[;}>])[ \t]*export\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s+from\s+(['"])([^'"\n]+)\1"""),
    re.compile(r"""(?<![\w$.])(?:require|import|require\.resolve)\s*\(\s*(['"])([^'"\n]+)\1\s*[,)]"""),
)


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


def read(root: Path, name: str) -> str:
    path = root / name
    try:
        if path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class Layout:
    """Which service owns which file and which Python module name."""

    def __init__(self, files: list[str], services_dir: str, allow):
        self.prefix = services_dir.strip("/") + "/"
        self.dir_name = services_dir.strip("/").rsplit("/", 1)[-1]
        self.by_service: dict[str, list[str]] = {}
        for name in files:
            if name.startswith(self.prefix) and "/" in name[len(self.prefix):]:
                self.by_service.setdefault(name[len(self.prefix):].split("/", 1)[0], []).append(name)
        self.services = set(self.by_service)
        self.owners: dict[str, set[str]] = {}
        for service, names in self.by_service.items():
            for name in names:
                inner = name[len(self.prefix) + len(service) + 1:]
                parts = inner.split("/")
                if not inner.endswith(".py"):
                    continue
                module = parts[0][:-3] if len(parts) == 1 else parts[0]
                if module not in NOT_MODULES and not module.startswith("test_"):
                    self.owners.setdefault(module, set()).add(service)
        self.allow_services, self.allow_pairs, self.allow_modules = set(), set(), set()
        for entry in (*DEFAULT_ALLOW, *allow):
            if "->" in entry:
                source, target = (part.strip() for part in entry.split("->", 1))
                self.allow_pairs.add((source, target))
            else:
                self.allow_services.add(entry.strip())
                self.allow_modules.add(entry.strip())

    def service_of(self, name: str) -> str | None:
        if not name.startswith(self.prefix):
            return None
        rest = name[len(self.prefix):]
        return rest.split("/", 1)[0] if "/" in rest else None

    def allowed(self, source: str, target: str) -> bool:
        return (target == source or target in self.allow_services or (source, target) in self.allow_pairs
                or (source, "*") in self.allow_pairs or ("*", target) in self.allow_pairs)

    def path_target(self, text: str, source: str, file_dir: str) -> str | None:
        """The other service a path string points into, or None."""
        for match in re.finditer(rf"(?:^|[/\\\s'\"=]){re.escape(self.dir_name)}[/\\]([A-Za-z0-9_.-]+)", text):
            target = match.group(1)
            if target in self.services and not self.allowed(source, target):
                return target
        if re.match(r"^(\.\./)+[\w.-]", text):
            resolved = os.path.normpath(os.path.join(file_dir, text)).replace(os.sep, "/")
            target = self.service_of(resolved + "/x")
            if target and target in self.services and not self.allowed(source, target):
                return target
        return None


def _finding(rule: str, severity: str, message: str, fix: str, file: str, line: int, detail: dict) -> dict:
    return {"rule": rule, "severity": severity, "message": message, "fix": fix, "file": file, "line": line,
            "detail": detail}


FIX_IMPORT = ("move what both services need into contracts/ (or another shared package listed in `allow`), "
              "or pass the data across the boundary instead of importing the code")


def _div_parts(node: ast.AST) -> tuple[list[str], bool]:
    """String constants joined by `/` in a pathlib expression, and whether the base climbs with .parent."""
    parts, climbs = [], False
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            parts.insert(0, node.right.value)
        else:
            parts.insert(0, "")
        node = node.left
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts.insert(0, node.value)
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute) and item.attr in ("parent", "parents"):
            climbs = True
    return parts, climbs


def check_python(layout: Layout, name: str, text: str, service: str) -> list[dict]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    own_dirs = {item[len(layout.prefix) + len(service) + 1:].split("/")[0] for item in layout.by_service[service]}
    file_dir = name.rsplit("/", 1)[0]
    findings, taken = [], set()
    stdlib = set(sys.stdlib_module_names)

    def import_target(module: str, level: int) -> tuple[str, str] | None:
        parts = module.split(".") if module else []
        if level:
            base = file_dir.split("/")
            base = base[:len(base) - (level - 1)] if level > 1 else base
            target = layout.service_of("/".join(base + parts) + "/x")
            return (target, "relative import") if target and not layout.allowed(service, target) else None
        if not parts:
            return None
        if parts[0] == layout.dir_name and len(parts) > 1 and parts[1] in layout.services:
            return (parts[1], "package import") if not layout.allowed(service, parts[1]) else None
        top = parts[0]
        if top in layout.allow_modules:
            return None
        owners = layout.owners.get(top, set())
        if owners and service not in owners and top not in stdlib:
            others = sorted(owner for owner in owners if not layout.allowed(service, owner))
            if others and len(others) == len(owners):
                return others[0], "module owned by another service"
        if top in layout.services and top != service and top not in own_dirs and top not in layout.owners \
                and top not in stdlib and not layout.allowed(service, top):
            return top, "service directory imported as a package"
        return None

    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [(node.module or "", node.level)]
            if node.level and not node.module:
                modules = [(alias.name, node.level) for alias in node.names]
            elif node.module == layout.dir_name and not node.level:
                modules = [(f"{layout.dir_name}.{alias.name}", 0) for alias in node.names]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "import_module" \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            modules = [(node.args[0].value, 0)]
        for module, level in modules:
            hit = import_target(module, level)
            if hit:
                target, how = hit
                shown = "." * level + module
                findings.append(_finding(
                    "cross-service-import", "fail",
                    f"`{shown}` is imported in service `{service}` but belongs to service `{target}` ({how})",
                    FIX_IMPORT, name, node.lineno, {"from": service, "to": target, "module": shown}))

    def literal_target(node: ast.AST) -> tuple[str, str] | None:
        for item in ast.walk(node):
            if isinstance(item, ast.Constant) and isinstance(item.value, str) and len(item.value) < 400:
                target = layout.path_target(item.value, service, file_dir)
                if target:
                    return target, item.value
            if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Div):
                parts, climbs = _div_parts(item)
                for index, part in enumerate(parts):
                    if part == layout.dir_name and index + 1 < len(parts) and parts[index + 1] in layout.services \
                            and not layout.allowed(service, parts[index + 1]):
                        return parts[index + 1], "/".join(parts)
                    if climbs and part in layout.services and part != service and part not in own_dirs \
                            and not layout.allowed(service, part) and (index == 0 or parts[index - 1] in ("", "..")):
                        return part, "/".join(piece or "..." for piece in parts)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("insert", "append", "extend") and isinstance(node.func.value, ast.Attribute) \
                and node.func.value.attr == "path" and getattr(node.func.value.value, "id", "") == "sys":
            hit = literal_target(node)
            if hit:
                target, shown = hit
                taken.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
                findings.append(_finding(
                    "cross-service-syspath", "fail",
                    f"service `{service}` puts service `{target}` on sys.path (`{shown}`), so its modules import as if local",
                    FIX_IMPORT, name, node.lineno, {"from": service, "to": target, "path": shown}))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docstrings.add(id(node.body[0].value))
    reported = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line is None or line in taken or line in reported or id(node) in docstrings:
            continue
        hit = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 400:
            target = layout.path_target(node.value, service, file_dir)
            hit = (target, node.value) if target else None
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            hit = literal_target(node)
        if hit:
            target, shown = hit
            reported.add(line)
            reported.update(range(line, (getattr(node, "end_lineno", line) or line) + 1))
            findings.append(_finding(
                "cross-service-path", "warn",
                f"service `{service}` names a path inside service `{target}` (`{shown[:80]}`)",
                "read the other service's output through its contract, or take the path as an argument from the "
                "top-level glue", name, line, {"from": service, "to": target, "path": shown[:200]}))
    return findings


def check_js(layout: Layout, name: str, text: str, service: str) -> list[dict]:
    blanked = _JS_BLOCK_COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)
    blanked = re.sub(r"(?m)^([ \t]*)//[^\n]*", lambda match: " " * len(match.group(0)), blanked)
    file_dir = name.rsplit("/", 1)[0]
    findings, seen = [], set()
    for pattern in _JS_IMPORT:
        for match in pattern.finditer(blanked):
            specifier = match.group(2)
            line = blanked.count("\n", 0, match.start(2)) + 1
            target = None
            if specifier.startswith("."):
                resolved = os.path.normpath(os.path.join(file_dir, specifier)).replace(os.sep, "/")
                target = layout.service_of(resolved + "/x")
            else:
                found = re.match(rf"^(?:[@~]/|/)?(?:src/)?{re.escape(layout.dir_name)}/([A-Za-z0-9_.-]+)(?:/|$)", specifier)
                target = found.group(1) if found else None
            if not target or target not in layout.services or layout.allowed(service, target) or (specifier, line) in seen:
                continue
            seen.add((specifier, line))
            findings.append(_finding(
                "cross-service-import", "fail",
                f"`{specifier}` is imported in service `{service}` but resolves into service `{target}`",
                FIX_IMPORT, name, line, {"from": service, "to": target, "module": specifier}))
    return findings


def check_text(layout: Layout, name: str, text: str, service: str) -> list[dict]:
    findings = []
    file_dir = name.rsplit("/", 1)[0]
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for token in re.split(r"[\s'\"=;:()]+", line):
            target = layout.path_target(token, service, file_dir) if token else None
            if target:
                findings.append(_finding(
                    "cross-service-path", "warn", f"service `{service}` names a path inside service `{target}` (`{token[:80]}`)",
                    "run cross-service steps from a top-level script instead", name, number,
                    {"from": service, "to": target, "path": token[:200]}))
                break
    return findings


def add_arguments(parser) -> None:
    parser.add_argument("--services-dir", metavar="DIR",
                        help=f"directory that holds one subdirectory per service (default {DEFAULT_SERVICES_DIR})")
    parser.add_argument("--allow", action="append", metavar="NAME",
                        help="service or module any service may use, or `a->b` for one pair (repeatable)")


def run(args) -> dict:
    root = Path(args.root)
    if not root.is_dir():
        raise RuntimeError(f"not a directory: {root}")
    config = getattr(args, "config", None) or {}
    services_dir = getattr(args, "services_dir", None) or config.get("services-dir") or DEFAULT_SERVICES_DIR
    allow = list(getattr(args, "allow", None) or []) + list(config.get("allow", []))
    severity = config.get("severity", {}) if isinstance(config.get("severity"), dict) else {}
    files = list_files(root)
    layout = Layout(files, services_dir, allow)
    findings, checked = [], 0
    for service in sorted(layout.services):
        for name in layout.by_service[service]:
            if name.endswith(".py"):
                found = check_python(layout, name, read(root, name), service)
            elif name.endswith(JS_SUFFIXES):
                found = check_js(layout, name, read(root, name), service)
            elif name.endswith(TEXT_SUFFIXES):
                found = check_text(layout, name, read(root, name), service)
            else:
                continue
            checked += 1
            findings += found
    kept = []
    for finding in findings:
        override = severity.get(finding["rule"])
        if override == "off":
            continue
        if override in ("fail", "warn", "info"):
            finding["severity"] = override
        kept.append(finding)
    kept.sort(key=lambda item: (item["file"], item["line"], item["rule"], item["message"]))
    pairs = sorted({(finding["detail"]["from"], finding["detail"]["to"]) for finding in kept})
    return {"findings": kept, "stats": {"services": len(layout.services), "files_checked": checked,
                                        "service_pairs": [f"{source}->{target}" for source, target in pairs]}}

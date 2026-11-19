"""Imports that nothing declares.

A coding agent writes `import foo` and moves on: the package is installed on its machine, or it
does not exist at all. This check reads every Python and JavaScript/TypeScript file, takes the
third-party packages they import, and compares them with what the project declares
(pyproject.toml, requirements files, package.json and so on). No package index is contacted.
See docs/deps.md for the rules and their limits.
"""

from __future__ import annotations

import ast
import configparser
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

NAME = "deps"
SUMMARY = "imports of packages that no dependency file declares"
RULES = {"undeclared-import": "fail", "not-in-lockfile": "warn", "unused-dependency": "info"}

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", ".nuxt",
             ".svelte-kit", ".turbo", ".tox", ".mypy_cache", ".pytest_cache", "site-packages", ".agent-sense"}
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte"}
SHELL_NAMES = {"Makefile", "makefile", "justfile", "Justfile", "Procfile"}
MAX_BYTES = 1_500_000

# import name -> distributions that provide it, for names that differ after normalisation.
PY_ALIASES = {
    "yaml": ("pyyaml",), "pil": ("pillow",), "cv2": ("opencv-python", "opencv-python-headless",
                                                    "opencv-contrib-python", "opencv-contrib-python-headless"),
    "sklearn": ("scikit-learn",), "skimage": ("scikit-image",), "bs4": ("beautifulsoup4",),
    "dotenv": ("python-dotenv",), "dateutil": ("python-dateutil",), "jwt": ("pyjwt",),
    "jose": ("python-jose",), "multipart": ("python-multipart",), "magic": ("python-magic",),
    "docx": ("python-docx",), "pptx": ("python-pptx",), "slugify": ("python-slugify",),
    "telegram": ("python-telegram-bot",), "serial": ("pyserial",), "usb": ("pyusb",),
    "crypto": ("pycryptodome", "pycrypto"), "cryptodome": ("pycryptodomex",), "nacl": ("pynacl",),
    "openssl": ("pyopenssl",), "git": ("gitpython",), "github": ("pygithub",), "attr": ("attrs",),
    "markdown_it": ("markdown-it-py",), "fitz": ("pymupdf",), "pymupdf": ("pymupdf",),
    "mysqldb": ("mysqlclient",), "psycopg2": ("psycopg2", "psycopg2-binary"),
    "psycopg": ("psycopg", "psycopg-binary"), "faiss": ("faiss-cpu", "faiss-gpu"),
    "tensorflow": ("tensorflow", "tensorflow-cpu", "tensorflow-macos", "tensorflow-gpu"),
    "torch": ("torch", "pytorch"), "lightning": ("lightning", "pytorch-lightning"),
    "pytorch_lightning": ("pytorch-lightning",), "sentence_transformers": ("sentence-transformers",),
    "googleapiclient": ("google-api-python-client",), "socks": ("pysocks",), "zmq": ("pyzmq",),
    "win32api": ("pywin32",), "win32com": ("pywin32",), "win32con": ("pywin32",), "pythoncom": ("pywin32",),
    "pkg_resources": ("setuptools",), "_pytest": ("pytest",), "mpl_toolkits": ("matplotlib",),
    "pylab": ("matplotlib",), "discord": ("discord-py", "py-cord"), "editor": ("python-editor",),
    "lxml": ("lxml",), "ruamel": ("ruamel-yaml",), "snowflake": ("snowflake-connector-python",),
    "kafka": ("kafka-python",), "consul": ("python-consul",), "ldap": ("python-ldap",),
    "levenshtein": ("levenshtein", "python-levenshtein"), "rapidjson": ("python-rapidjson",),
    "wx": ("wxpython",), "gi": ("pygobject",), "cairo": ("pycairo",), "objc": ("pyobjc",),
    "appkit": ("pyobjc",), "foundation": ("pyobjc",), "quartz": ("pyobjc",),
    "ibm_db": ("ibm-db",), "odf": ("odfpy",), "stdnum": ("python-stdnum",), "pywintypes": ("pywin32",),
    "tree_sitter": ("tree-sitter",), "more_itertools": ("more-itertools",), "typing_extensions": ("typing-extensions",),
    "importlib_metadata": ("importlib-metadata",), "ipython": ("ipython",), "mecab": ("mecab-python3",),
    "sudachipy": ("sudachipy",), "janome": ("janome",), "unidic_lite": ("unidic-lite",),
    "anthropic": ("anthropic",), "openai": ("openai",), "claude_agent_sdk": ("claude-agent-sdk",),
}
# Namespace packages: `import google.x` is satisfied by any declared distribution with the prefix.
PY_NAMESPACES = {"google": ("google-", "googleapis-", "protobuf", "grpcio"), "azure": ("azure-",),
                 "zope": ("zope-",), "backports": ("backports-",), "jaraco": ("jaraco-",),
                 "opentelemetry": ("opentelemetry-",), "sphinxcontrib": ("sphinxcontrib-",),
                 "ruamel": ("ruamel-",), "msgraph": ("msgraph-",), "aws_cdk": ("aws-cdk-",),
                 "langchain_community": ("langchain",), "llama_index": ("llama-index",),
                 "nvidia": ("nvidia-",), "mpl_toolkits": ("matplotlib", "basemap")}
# A declared distribution that always brings these import names with it as part of its own API.
PY_PROVIDED_BY = {"fastapi": ("pydantic", "starlette"), "pytest": ("_pytest", "pluggy"),
                  "pydantic": ("pydantic_core", "annotated_types"), "sqlmodel": ("sqlalchemy", "pydantic"),
                  "flask": ("werkzeug", "jinja2", "click"), "pandas": ("numpy",), "playwright": ("greenlet",)}
# Declared distributions that are used without being imported; never reported as unused.
PY_NOT_IMPORTED = re.compile(
    r"^(pytest-.*|types-.*|.*-stubs|setuptools|wheel|pip|hatchling|hatch|uv|ruff|black|mypy|pyright|flake8.*|"
    r"isort|pylint|tox|nox|coverage|pre-commit|uvicorn|gunicorn|hypercorn|daphne|celery|flower|supervisor|"
    r"build|twine|poetry.*|pdm.*|flit.*|maturin|cython|ipykernel|jupyter.*|notebook|ipython|"
    r"psycopg2-binary|psycopg-binary|.*-binary|asyncpg|aiosqlite|pymysql|mysqlclient|greenlet|"
    r"python-multipart|email-validator|itsdangerous|watchfiles|httptools|uvloop|websockets|"
    r"playwright|pyinstaller|bandit|safety|pip-tools|sphinx.*|mkdocs.*|furo|"
    r"torchvision|torchaudio|accelerate|bitsandbytes|sentencepiece|protobuf|tiktoken|xformers|"
    r"lxml|openpyxl|xlrd|pyarrow|fastparquet|tabulate|html5lib|kaleido|pillow-heif|orjson|ujson|"
    r"python-dotenv|tzdata|certifi|cryptography|bcrypt|argon2-cffi|passlib)$")

NODE_BUILTINS = set("""assert async_hooks buffer child_process cluster console constants crypto dgram
diagnostics_channel dns domain events fs http http2 https inspector module net os path perf_hooks process
punycode querystring readline repl stream string_decoder sys timers tls trace_events tty url util v8 vm
wasi worker_threads zlib test sqlite sea""".split())
JS_VIRTUAL_PREFIXES = ("$", "~", "#", "@/", "@@/", "virtual:", "/", ".")
JS_RUNTIME_GLOBALS = {"bun", "deno", "electron", "vscode", "astro", "cloudflare", "k6", "obsidian", "figma"}
# packages whose presence makes extra bare names resolvable
JS_PROVIDED_BY = {"next": ("next", "server-only", "client-only", "styled-jsx"),
                  "@sveltejs/kit": ("@sveltejs/kit", "svelte"), "nuxt": ("nuxt", "vue", "h3", "ofetch", "nitropack"),
                  "expo": ("expo", "react-native"), "vitest": ("vitest", "vite"), "astro": ("astro", "vite")}
JS_DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")

_NORMALISE = re.compile(r"[-_.]+")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_UV_WITH = re.compile(r"--with(?:-editable)?[=\s]+['\"]?([A-Za-z0-9][A-Za-z0-9._,\[\]<>=!~-]*)")
_PIP_INSTALL = re.compile(r"\bpip3?\s+install\s+([^\n;&|#]+)")
_PEP723 = re.compile(r"(?m)^# /// script\s*$\n((?:^#(?: .*)?$\n)+?)^# ///\s*$")
_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_PATTERNS = (
    re.compile(r"""(?m)(?:^|[;}>])[ \t]*import\s+(?:type\s+)?(?:[\w$*\s{},]+?\s+from\s+)?(['"])([^'"\n]+)\1"""),
    re.compile(r"""(?m)(?:^|[;}>])[ \t]*export\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s+from\s+(['"])([^'"\n]+)\1"""),
    re.compile(r"""(?<![\w$.])(?:require|import|require\.resolve)\s*\(\s*(['"])([^'"\n]+)\1\s*[,)]"""),
)
_JS_TEST = re.compile(r"(\.(test|spec|stories|e2e)\.[a-z]+$)|(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright)/")
_PY_TEST = re.compile(r"(^|/)(tests?|testing|evals?|benchmarks?|examples?|scripts)/|(^|/)(test_[^/]*|[^/]*_test|conftest|noxfile|setup)\.py$")


def normalise(name: str) -> str:
    return _NORMALISE.sub("-", name).lower().strip("-")


# ---------------------------------------------------------------------------------------------
# files


def in_git(root: Path) -> bool:
    resolved = root.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def list_files(root: Path) -> list[str]:
    """Project files as posix paths relative to root: git's view when there is one, else a walk."""
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
    found = []
    stack = [root]
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


def parents(name: str) -> list[str]:
    """Directories from the file's own up to the root ('' is the root)."""
    parts = name.split("/")[:-1]
    return ["/".join(parts[:count]) for count in range(len(parts), -1, -1)]


# ---------------------------------------------------------------------------------------------
# Python: what is declared


def requirement_name(text: str) -> str | None:
    text = text.strip()
    if not text or text.startswith(("#", "-", "git+", "http:", "https:", ".", "/")):
        egg = re.search(r"#egg=([A-Za-z0-9._-]+)", text)
        return normalise(egg.group(1)) if egg else None
    match = _REQ_NAME.match(text)
    return normalise(match.group(1)) if match else None


def _names(items) -> set[str]:
    found = set()
    for item in items or ():
        if isinstance(item, str):
            name = requirement_name(item)
            if name:
                found.add(name)
        elif isinstance(item, dict):  # dependency-groups: {include-group = "x"}
            continue
    return found


def pyproject_declared(text: str) -> tuple[set[str], set[str], str | None]:
    """(all declared, main dependencies only, project name) of a pyproject.toml."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return set(), set(), None
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    main = _names(project.get("dependencies"))
    declared = set(main)
    for group in (project.get("optional-dependencies") or {}).values():
        declared |= _names(group)
    for group in (data.get("dependency-groups") or {}).values():
        declared |= _names(group)
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    uv = tool.get("uv") if isinstance(tool.get("uv"), dict) else {}
    declared |= _names(uv.get("dev-dependencies"))
    declared |= _names((data.get("build-system") or {}).get("requires"))
    poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
    poetry_main = {normalise(name) for name in (poetry.get("dependencies") or {}) if name.lower() != "python"}
    main |= poetry_main
    declared |= poetry_main | {normalise(name) for name in (poetry.get("dev-dependencies") or {})}
    for group in (poetry.get("group") or {}).values():
        if isinstance(group, dict):
            declared |= {normalise(name) for name in (group.get("dependencies") or {})}
    pdm = tool.get("pdm") if isinstance(tool.get("pdm"), dict) else {}
    for group in (pdm.get("dev-dependencies") or {}).values():
        declared |= _names(group)
    hatch_envs = ((tool.get("hatch") or {}).get("envs") or {}) if isinstance(tool.get("hatch"), dict) else {}
    for env in hatch_envs.values():
        if isinstance(env, dict):
            declared |= _names(env.get("dependencies"))
    name = project.get("name") or poetry.get("name")
    return declared, main, normalise(name) if isinstance(name, str) else None


def requirements_declared(text: str) -> set[str]:
    return {name for name in (requirement_name(line.split(" #")[0]) for line in text.splitlines()) if name}


def setup_cfg_declared(text: str) -> set[str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return set()
    found = set()
    if parser.has_section("options"):
        for key in ("install_requires", "setup_requires", "tests_require"):
            found |= requirements_declared(parser.get("options", key, fallback=""))
    if parser.has_section("options.extras_require"):
        for _, value in parser.items("options.extras_require"):
            found |= requirements_declared(value)
    return found


def setup_py_declared(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in ("install_requires", "extras_require", "tests_require",
                                                          "setup_requires"):
            for item in ast.walk(node.value):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    name = requirement_name(item.value)
                    if name and re.search(r"[A-Za-z]", item.value) and " " not in item.value.strip().split(";")[0].strip(
                            "<>=!~ 0123456789.,*"):
                        found.add(name)
    return found


def pep723_declared(text: str) -> set[str] | None:
    """Dependencies of an inline script block, or None when the file has none."""
    match = _PEP723.search(text)
    if not match:
        return None
    body = "\n".join(line[2:] if line.startswith("# ") else line[1:] for line in match.group(1).splitlines())
    try:
        return _names(tomllib.loads(body).get("dependencies"))
    except tomllib.TOMLDecodeError:
        return set()


def script_group(name: str) -> str | None:
    """The directory tree whose inline script blocks speak for their helper modules.

    `services/<name>` for a file in a service, else the top-level directory. Files at the project
    root belong to no group: a script there does not declare for the whole project.
    """
    parts = name.split("/")
    if len(parts) >= 3 and parts[0] == "services":
        return "/".join(parts[:2])
    return parts[0] if len(parts) >= 2 and parts[0] != "services" else None


def command_declared(text: str) -> set[str]:
    """Packages named by `uv run --with X` and `pip install X` in a shell script or workflow."""
    found = set()
    for match in _UV_WITH.finditer(text):
        for part in match.group(1).split(","):
            name = requirement_name(part)
            if name:
                found.add(name)
    for match in _PIP_INSTALL.finditer(text):
        for token in match.group(1).split():
            token = token.strip("'\"")
            if token.startswith("-") or "/" in token or token.endswith((".txt", ".toml", ".whl")) or token == ".":
                continue
            name = requirement_name(token)
            if name:
                found.add(name)
    return found


def is_command_file(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return (base.endswith((".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd")) or base in SHELL_NAMES
            or base.startswith("Dockerfile") or name.startswith((".githooks/", ".husky/"))
            or (name.startswith(".github/") and base.endswith((".yml", ".yaml")))
            or base in (".pre-commit-config.yaml", ".gitlab-ci.yml", "tox.ini", "noxfile.py"))


def is_requirements(name: str) -> bool:
    base = name.rsplit("/", 1)[-1].lower()
    in_dir = "/requirements/" in "/" + name.lower()
    return base.endswith((".txt", ".in")) and ("requirements" in base or "constraints" in base or in_dir)


# ---------------------------------------------------------------------------------------------
# Python: what is imported


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = [handler.type] if not isinstance(handler.type, ast.Tuple) else handler.type.elts
    for item in names:
        label = item.attr if isinstance(item, ast.Attribute) else getattr(item, "id", "")
        if label in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException"):
            return True
    return False


def _is_type_checking(test: ast.expr) -> bool:
    label = test.attr if isinstance(test, ast.Attribute) else getattr(test, "id", "")
    return label == "TYPE_CHECKING"


def python_imports(text: str) -> list[tuple[str, int, str]] | None:
    """(dotted module, line, kind) for absolute imports; kind is 'plain', 'optional' or 'typing'.

    None when the file does not parse.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    found = []

    def visit(node: ast.AST, kind: str) -> None:
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno, kind) for alias in node.names)
            return
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append((node.module, node.lineno, kind))
            return
        if isinstance(node, ast.Try) or node.__class__.__name__ == "TryStar":
            guarded = any(_catches_import_error(handler) for handler in node.handlers)
            for child in node.body:
                visit(child, "optional" if guarded and kind == "plain" else kind)
            for part in (node.handlers, node.orelse, node.finalbody):
                for child in part:
                    visit(child, "optional" if guarded and kind == "plain" else kind)
            return
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.body:
                visit(child, "typing" if kind == "plain" else kind)
            for child in node.orelse:
                visit(child, kind)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, kind)

    visit(tree, "plain")
    return found


def satisfied(top: str, module: str, declared: set[str]) -> bool:
    lowered = top.lower()
    if normalise(top) in declared:
        return True
    if any(name in declared for name in PY_ALIASES.get(lowered, ())):
        return True
    prefixes = PY_NAMESPACES.get(lowered)
    if prefixes and any(name.startswith(prefixes) for name in declared):
        return True
    if any(lowered in names for dist, names in PY_PROVIDED_BY.items() if dist in declared):
        return True
    dotted = normalise(".".join(module.split(".")[:2]))
    if dotted in declared:
        return True
    for affix in (f"python-{normalise(top)}", f"{normalise(top)}-python", f"py{normalise(top)}", f"{normalise(top)}-py"):
        if affix in declared:
            return True
    return False


def import_names(dist: str) -> set[str]:
    """Import names a distribution is known or expected to provide (lower case)."""
    names = {dist.replace("-", "_")}
    for affix in ("python-", "py-", "py"):
        if dist.startswith(affix) and len(dist) > len(affix) + 1:
            names.add(dist[len(affix):].replace("-", "_"))
    for affix in ("-python", "-py"):
        if dist.endswith(affix):
            names.add(dist[:-len(affix)].replace("-", "_"))
    names |= {name for name, dists in PY_ALIASES.items() if dist in dists}
    names |= {name for name, prefixes in PY_NAMESPACES.items() if dist.startswith(prefixes)}
    names |= set(PY_PROVIDED_BY.get(dist, ()))
    return names


# ---------------------------------------------------------------------------------------------
# JavaScript / TypeScript


def strip_json_comments(text: str) -> str:
    out, index, in_string = [], 0, False
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < len(text):
                out.append(text[index + 1])
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif text.startswith("//", index):
            index = text.find("\n", index)
            if index < 0:
                break
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(char)
        index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_json(text: str) -> dict:
    for candidate in (text, strip_json_comments(text)):
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, RecursionError):
            continue
    return {}


def js_package(specifier: str) -> str | None:
    """The package a bare import specifier names, or None when it is not a package import."""
    if not specifier or specifier.startswith(JS_VIRTUAL_PREFIXES) or "\\" in specifier:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", specifier):  # node:, bun:, virtual:, astro:, npm:, https:
        return None
    if any(char in specifier for char in " ${}<>|*?!"):
        return None
    parts = specifier.split("/")
    if specifier.startswith("@"):
        if len(parts) < 2 or not parts[0][1:] or not parts[1]:
            return None
        name = "/".join(parts[:2])
    else:
        name = parts[0]
    return name if re.match(r"^(@[a-z0-9~][a-z0-9._~-]*/)?[A-Za-z0-9~][A-Za-z0-9._~-]*$", name) else None


def js_imports(text: str) -> list[tuple[str, int]]:
    """(specifier, line) for import, export-from, require and dynamic import with a literal."""
    blanked = _JS_BLOCK_COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)
    blanked = re.sub(r"(?m)^([ \t]*)//[^\n]*", lambda match: " " * len(match.group(0)), blanked)
    found = {}
    for pattern in _JS_PATTERNS:
        for match in pattern.finditer(blanked):
            line = blanked.count("\n", 0, match.start(2)) + 1
            found.setdefault((match.group(2), line), None)
    return sorted(found, key=lambda item: (item[1], item[0]))


def alias_prefixes(config: dict) -> set[str]:
    """Import prefixes a tsconfig/jsconfig `paths` table or package.json `imports` maps to local files."""
    paths = (config.get("compilerOptions") or {}).get("paths") if isinstance(config.get("compilerOptions"), dict) else {}
    found = set()
    for key in list(paths or {}) + list(config.get("imports") or {} if isinstance(config.get("imports"), dict) else []):
        key = key.split("*")[0]
        if key:
            found.add(key)
    return found


def lock_has(kind: str, text: str, data: dict, name: str) -> bool:
    if kind == "package-lock.json":
        packages = data.get("packages") if isinstance(data.get("packages"), dict) else {}
        if any(key == f"node_modules/{name}" or key.endswith(f"/node_modules/{name}") for key in packages):
            return True
        return name in (data.get("dependencies") or {})
    escaped = re.escape(name)
    if kind == "yarn.lock":
        return bool(re.search(rf'(?m)^"?{escaped}@|, "?{escaped}@', text))
    if kind == "pnpm-lock.yaml":
        return bool(re.search(rf"(?m)^\s*'?/?{escaped}(@|'?:)", text))
    if kind in ("bun.lock", "deno.lock"):
        return bool(re.search(rf'"(npm:)?{escaped}(@[^"]*)?"\s*:', text))
    return True


JS_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock")
PY_LOCKS = ("uv.lock", "poetry.lock", "pdm.lock")
_LOCAL_SPEC = re.compile(r"^(workspace:|file:|link:|portal:|git[+:]|github:|https?:|\.|/|catalog:)")


# ---------------------------------------------------------------------------------------------
# the check


def _finding(rule: str, severity: str, message: str, fix: str, file: str, line: int | None, detail: dict) -> dict:
    finding = {"rule": rule, "severity": severity, "message": message, "fix": fix, "file": file, "detail": detail}
    if line:
        finding["line"] = line
    return finding


def _option(args, name: str, default):
    value = getattr(args, name, None)
    if value is None:
        value = (getattr(args, "config", None) or {}).get(name.replace("_", "-"),
                                                          (getattr(args, "config", None) or {}).get(name))
    return default if value is None else value


def check_python(root: Path, files: list[str], settings: dict) -> tuple[list[dict], dict]:
    py_files = [name for name in files if name.endswith(".py")]
    stats = {"python_files": len(py_files), "python_unparsed": 0, "python_imports": 0, "python_declaration_files": 0}
    if not py_files:
        return [], stats
    local = set()
    for name in files:
        if name.endswith((".py", ".pyi", ".pyx", ".so", ".pyd")):
            parts = name.split("/")
            local.add(parts[-1].split(".")[0])
            local.update(parts[:-1])
    declared_at: dict[str, set[str]] = {}    # directory -> declared distributions
    main_at: dict[str, tuple[str, set[str]]] = {}   # directory -> (declaration file, main dependencies)
    everywhere: set[str] = set()             # declared by a command, valid for the whole project
    locks: dict[str, tuple[str, str]] = {}
    for name in files:
        base = name.rsplit("/", 1)[-1]
        directory = name.rsplit("/", 1)[0] if "/" in name else ""
        declared = None
        if base == "pyproject.toml":
            declared, main, own = pyproject_declared(read(root, name))
            if own:
                declared.add(own)
            if main:
                main_at[directory] = (name, main)
        elif is_requirements(name):
            declared = requirements_declared(read(root, name))
            if "/requirements/" in "/" + name.lower():
                directory = directory.rsplit("/", 1)[0] if "/" in directory else ""
        elif base == "setup.cfg":
            declared = setup_cfg_declared(read(root, name))
        elif base == "setup.py":
            declared = setup_py_declared(read(root, name))
        elif base == "Pipfile":
            try:
                data = tomllib.loads(read(root, name))
                declared = {normalise(key) for table in ("packages", "dev-packages") for key in (data.get(table) or {})}
            except tomllib.TOMLDecodeError:
                declared = set()
        elif base in ("environment.yml", "environment.yaml"):
            declared = {requirement_name(match.group(1)) or "" for match in
                        re.finditer(r"(?m)^\s*-\s+([A-Za-z0-9][A-Za-z0-9._-]*)", read(root, name))} - {""}
        elif base in PY_LOCKS:
            locks[directory] = (name, read(root, name))
        if declared is not None:
            stats["python_declaration_files"] += 1
            declared_at.setdefault(directory, set()).update(declared)
        if is_command_file(name):
            found = command_declared(read(root, name))
            if found:
                stats["python_declaration_files"] += 1
                everywhere |= found
    everywhere |= {normalise(item) for item in settings["declared"]}
    ignore = {item.lower() for item in settings["ignore"]}
    stdlib = set(sys.stdlib_module_names) | {"__future__", "__main__", "_typeshed"}
    findings, used = [], {}
    texts = {name: read(root, name) for name in py_files}
    inline_at: dict[str, set[str]] = {}      # script group -> packages its inline script blocks declare
    for name in py_files:
        inline, group = pep723_declared(texts[name]), script_group(name)
        if inline and group:
            inline_at.setdefault(group, set()).update(inline)
    for name in py_files:
        text = texts.pop(name)
        imports = python_imports(text)
        if imports is None:
            stats["python_unparsed"] += 1
            continue
        inline = pep723_declared(text)
        declared = set(everywhere) | (inline or set())
        if inline is None:   # a helper module run through a sibling script that carries the declaration
            declared |= inline_at.get(script_group(name) or "", set())
        for directory in parents(name):
            declared |= declared_at.get(directory, set())
        is_test = bool(_PY_TEST.search(name))
        seen = set()
        for module, line, kind in imports:
            top = module.split(".")[0]
            stats["python_imports"] += 1
            for directory in parents(name):
                used.setdefault(directory, set()).add(top.lower())
                used[directory].add(".".join(module.split(".")[:2]).lower())
            if top in stdlib or top in local or top.lower() in ignore or kind == "optional" or top in seen:
                continue
            if satisfied(top, module, declared):
                continue
            seen.add(top)
            locked = any(re.search(rf'(?m)^name = "{re.escape(candidate)}"', locks[directory][1], re.I)
                         for directory in parents(name) if directory in locks
                         for candidate in {normalise(top), *PY_ALIASES.get(top.lower(), ())})
            severity = "warn" if (is_test or kind == "typing" or locked) else "fail"
            why = ("only inside `if TYPE_CHECKING`" if kind == "typing" else
                   "it is in the lock file only as a dependency of another package" if locked else
                   "in a test, eval or script file" if is_test else "")
            guess = PY_ALIASES.get(top.lower(), (normalise(top),))[0]
            findings.append(_finding(
                "undeclared-import", severity,
                f"`import {module}` but no dependency file declares a package that provides `{top}`"
                + (f" ({why})" if why else ""),
                f"add it to the project's dependencies (the distribution is probably `{guess}`), or remove the import "
                f"if the package does not exist", name, line,
                {"language": "python", "module": module, "package": guess, "kind": kind, "in_lockfile": locked}))
    # declared but never imported, only where a pyproject lists main dependencies
    words = None
    for directory, (declaration, main) in sorted(main_at.items()):
        imported = used.get(directory, set())
        if not imported or not settings["unused"]:
            continue
        for dist in sorted(main):
            if PY_NOT_IMPORTED.match(dist) or dist in {normalise(item) for item in settings["ignore"]}:
                continue
            if import_names(dist) & imported or dist.replace("-", ".") in imported:
                continue
            if dist not in {name for names in PY_ALIASES.values() for name in names} and "-" in dist:
                continue  # an unknown import name: not confident
            if words is None:
                words = _project_words(root, files)
            if any(name in words for name in import_names(dist) | {dist}):
                continue
            findings.append(_finding(
                "unused-dependency", "info",
                f"`{dist}` is a dependency in {declaration} but nothing under {directory or '.'} imports or mentions it",
                "remove it if it is not needed at run time", declaration, None, {"language": "python", "package": dist}))
    # declared but missing from an existing lock file
    for directory, (lock_name, lock_text) in sorted(locks.items()):
        locked = {normalise(match) for match in re.findall(r'(?m)^name = "([^"]+)"', lock_text)}
        declaration = f"{directory}/pyproject.toml" if directory else "pyproject.toml"
        if declaration not in files:
            continue
        declared, _, own = pyproject_declared(read(root, declaration))
        build = _names((tomllib.loads(read(root, declaration)).get("build-system") or {}).get("requires"))
        for dist in sorted(declared - locked - build - {own}):
            findings.append(_finding(
                "not-in-lockfile", "warn", f"`{dist}` is declared in {declaration} but is not in {lock_name}",
                "refresh the lock file (uv lock, poetry lock) so installs match the declaration", declaration, None,
                {"language": "python", "package": dist, "lockfile": lock_name}))
    return findings, stats


def _project_words(root: Path, files: list[str]) -> set[str]:
    """Identifier-like words in the project's non-declaration text files (for the unused check)."""
    words = set()
    for name in files:
        base = name.rsplit("/", 1)[-1]
        if base in ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile") or base in PY_LOCKS or is_requirements(name) \
                or base.endswith((".lock", ".md", ".rst", ".txt")):
            continue
        if not base.endswith((".py", ".sh", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".json")) and base not in SHELL_NAMES \
                and not base.startswith("Dockerfile"):
            continue
        words.update(word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_-]+", read(root, name)))
    return words


def check_js(root: Path, files: list[str], settings: dict) -> tuple[list[dict], dict]:
    js_files = [name for name in files if Path(name).suffix in JS_SUFFIXES and not name.endswith(".d.ts")]
    stats = {"js_files": len(js_files), "js_imports": 0, "js_without_package_json": 0, "package_json_files": 0}
    manifests: dict[str, dict] = {}
    aliases: dict[str, set[str]] = {}
    locks: dict[str, tuple[str, str, dict]] = {}
    for name in files:
        base = name.rsplit("/", 1)[-1]
        directory = name.rsplit("/", 1)[0] if "/" in name else ""
        if base == "package.json":
            manifests[directory] = load_json(read(root, name))
            aliases.setdefault(directory, set()).update(alias_prefixes(manifests[directory]))
        elif re.match(r"^(ts|js)config(\..+)?\.json$", base):
            aliases.setdefault(directory, set()).update(alias_prefixes(load_json(read(root, name))))
        elif base in JS_LOCKS and directory not in locks:
            text = read(root, name)
            locks[directory] = (name, text, load_json(text) if base.endswith(".json") else {})
        elif base in ("deno.json", "deno.jsonc", "import_map.json"):
            data = load_json(read(root, name))
            aliases.setdefault(directory, set()).update(key.rstrip("/") for key in (data.get("imports") or {}))
    stats["package_json_files"] = len(manifests)
    workspace = {manifest.get("name") for manifest in manifests.values() if isinstance(manifest.get("name"), str)}
    extra = set(settings["declared"])
    ignore = set(settings["ignore"])
    findings = []
    for name in js_files:
        owner = next((directory for directory in parents(name) if directory in manifests), None)
        if owner is None:
            stats["js_without_package_json"] += 1
            continue
        declared = set(extra)
        for directory in parents(name):          # a workspace root's dependencies are visible below it
            manifest = manifests.get(directory)
            if manifest:
                for section in JS_DEP_SECTIONS:
                    declared.update(manifest.get(section) or {} if isinstance(manifest.get(section), dict) else ())
                declared.update(item for item in manifest.get("bundledDependencies") or () if isinstance(item, str))
        for package in list(declared):
            declared.update(JS_PROVIDED_BY.get(package, ()))
        prefixes = set()
        for directory in parents(name):
            prefixes |= aliases.get(directory, set())
        is_test = bool(_JS_TEST.search(name)) or bool(re.search(r"(^|/)[^/]*\.config\.[a-z]+$", name))
        seen = set()
        for specifier, line in js_imports(read(root, name)):
            stats["js_imports"] += 1
            if any(specifier == prefix.rstrip("/") or specifier.startswith(prefix) for prefix in prefixes):
                continue
            package = js_package(specifier)
            if package is None or package in seen or package in ignore:
                continue
            bare = package.split("/")[0]
            if package in NODE_BUILTINS or specifier in NODE_BUILTINS or (bare in NODE_BUILTINS and "/" in specifier
                                                                           and not package.startswith("@")):
                continue
            if package in declared or package in workspace or package in JS_RUNTIME_GLOBALS:
                continue
            types = "@types/" + (package[1:].replace("/", "__") if package.startswith("@") else package)
            if types in declared:
                continue
            seen.add(package)
            findings.append(_finding(
                "undeclared-import", "warn" if is_test else "fail",
                f"`{specifier}` is imported but `{package}` is not in "
                f"{(owner + '/') if owner else ''}package.json" + (" (test or config file)" if is_test else ""),
                f"add `{package}` to package.json, or remove the import if the package does not exist", name, line,
                {"language": "javascript", "module": specifier, "package": package, "manifest":
                    f"{owner}/package.json" if owner else "package.json"}))
    for directory, manifest in sorted(manifests.items()):
        lock_dir = next((parent for parent in parents(directory + "/x" if directory else "x") if parent in locks), None)
        if lock_dir is None:
            continue
        lock_name, text, data = locks[lock_dir]
        kind = lock_name.rsplit("/", 1)[-1].replace("npm-shrinkwrap.json", "package-lock.json")
        manifest_name = f"{directory}/package.json" if directory else "package.json"
        for section in JS_DEP_SECTIONS[:2]:
            table = manifest.get(section) if isinstance(manifest.get(section), dict) else {}
            for package, spec in sorted(table.items()):
                if package in workspace or (isinstance(spec, str) and _LOCAL_SPEC.match(spec)):
                    continue
                if not lock_has(kind, text, data, package):
                    findings.append(_finding(
                        "not-in-lockfile", "warn", f"`{package}` is in {manifest_name} but not in {lock_name}",
                        "run the package manager's install so the lock file matches package.json", manifest_name, None,
                        {"language": "javascript", "package": package, "lockfile": lock_name}))
    return findings, stats


def add_arguments(parser) -> None:
    parser.add_argument("--ignore", action="append", metavar="NAME",
                        help="import or package name to skip (repeatable; also `ignore` in the config)")
    parser.add_argument("--declared", action="append", metavar="NAME",
                        help="package to treat as declared, for example one the runtime provides (repeatable)")
    parser.add_argument("--no-unused", dest="unused", action="store_false", default=None,
                        help="do not report dependencies that nothing imports")
    parser.add_argument("--only", choices=("python", "js"), help="check one language only")


def run(args) -> dict:
    root = Path(args.root)
    if not root.is_dir():
        raise RuntimeError(f"not a directory: {root}")
    config = getattr(args, "config", None) or {}
    settings = {"ignore": list(getattr(args, "ignore", None) or []) + list(config.get("ignore", [])),
                "declared": list(getattr(args, "declared", None) or []) + list(config.get("declared", [])),
                "unused": _option(args, "unused", True)}
    only = _option(args, "only", None)
    severity = config.get("severity", {}) if isinstance(config.get("severity"), dict) else {}
    files = list_files(root)
    findings, stats = [], {"files": len(files)}
    if only in (None, "python"):
        found, part = check_python(root, files, settings)
        findings += found
        stats.update(part)
    if only in (None, "js"):
        found, part = check_js(root, files, settings)
        findings += found
        stats.update(part)
    kept = []
    for finding in findings:
        override = severity.get(finding["rule"])
        if override == "off":
            continue
        if override in ("fail", "warn", "info"):
            finding["severity"] = override
        kept.append(finding)
    kept.sort(key=lambda item: (item.get("file", ""), item.get("line", 0), item["rule"], item["message"]))
    return {"findings": kept, "stats": stats}

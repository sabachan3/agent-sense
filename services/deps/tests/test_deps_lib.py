import argparse
from pathlib import Path

import deps_lib


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def check(root: Path, config: dict | None = None, **options) -> list[dict]:
    return deps_lib.run(argparse.Namespace(root=root, config=config or {}, **options))["findings"]


def rules(findings: list[dict]) -> list[tuple[str, str, str]]:
    return [(finding["rule"], finding["severity"], finding["detail"]["package"]) for finding in findings]


def test_interface_matches_the_contract():
    assert deps_lib.NAME == "deps" and deps_lib.SUMMARY
    assert set(deps_lib.RULES) == {"undeclared-import", "not-in-lockfile", "unused-dependency"}
    deps_lib.add_arguments(argparse.ArgumentParser())


def test_python_undeclared_import_fails_with_file_and_line(tmp_path):
    root = project(tmp_path, {"pyproject.toml": '[project]\nname = "app"\ndependencies = ["requests>=2"]\n',
                              "app/main.py": "import os\nimport requests\nimport httpx\nfrom rich.console import Console\n"})
    findings = check(root)
    assert rules(findings) == [("undeclared-import", "fail", "httpx"), ("undeclared-import", "fail", "rich")]
    assert findings[0]["file"] == "app/main.py" and findings[0]["line"] == 3
    assert "`import httpx`" in findings[0]["message"]


def test_python_names_that_differ_from_the_distribution(tmp_path):
    root = project(tmp_path, {
        "requirements.txt": "PyYAML==6.0\nPillow\nopencv-python-headless>=4\nscikit_learn\nbeautifulsoup4\n"
                            "python-dotenv\ngoogle-cloud-storage\nPyJWT[crypto]>=2 ; python_version > '3.8'\n"
                            "-r other.txt\n# comment\ngit+https://example.com/x.git#egg=eggname\n",
        "main.py": "import yaml, PIL.Image, cv2, sklearn.svm, bs4, dotenv, jwt, eggname\n"
                   "from google.cloud import storage\nimport dateutil\n"})
    assert rules(check(root)) == [("undeclared-import", "fail", "python-dateutil")]


def test_python_ignores_stdlib_local_modules_relative_and_guarded_imports(tmp_path):
    root = project(tmp_path, {
        "pyproject.toml": '[project]\nname = "my-app"\ndependencies = []\n',
        "services/a/a_lib.py": "x = 1\n", "pkg/__init__.py": "", "pkg/inner.py": "from . import other\nfrom .x import y\n",
        "main.py": "from __future__ import annotations\nimport json, a_lib, pkg.inner, my_app\n"
                   "try:\n    import ujson\nexcept ImportError:\n    ujson = None\n"
                   "try:\n    import tomli\nexcept ModuleNotFoundError:\n    import tomllib\n"
                   "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pandas\n"
                   "def lazy():\n    import playwright.sync_api\n"})
    assert rules(check(root)) == [("undeclared-import", "warn", "pandas"), ("undeclared-import", "fail", "playwright")]


def test_python_test_files_only_warn(tmp_path):
    root = project(tmp_path, {"pyproject.toml": '[project]\nname = "a"\ndependencies = []\n',
                              "tests/test_x.py": "import pytest\nimport hypothesis\n", "conftest.py": "import pytest\n"})
    assert {(rule, severity) for rule, severity, _ in rules(check(root))} == {("undeclared-import", "warn")}


def test_python_declarations_from_groups_extras_uv_scripts_and_inline_metadata(tmp_path):
    root = project(tmp_path, {
        "pyproject.toml": '[project]\nname = "a"\ndependencies = ["fastapi"]\n'
                          '[project.optional-dependencies]\ngpu = ["torch"]\n[dependency-groups]\ndev = ["pytest>=8"]\n'
                          '[tool.uv]\ndev-dependencies = ["ruff", "respx"]\n',
        "scripts/test.sh": "uv run --quiet --with numpy --with 'pillow>=10' pytest -q\npip install -q tqdm\n",
        "a.py": "import fastapi, pydantic, starlette, torch, pytest, respx, numpy, PIL, tqdm\n",
        "tool.py": '# /// script\n# requires-python = ">=3.11"\n# dependencies = [\n#   "httpx",\n# ]\n# ///\n'
                   "import httpx\nimport rich\n",
        "setup.cfg": "[options]\ninstall_requires =\n    click>=8\n    attrs\n", "b.py": "import click, attr\n"})
    assert rules(check(root)) == [("undeclared-import", "fail", "rich")]


def test_python_nearest_declarations_apply_below_them_only(tmp_path):
    root = project(tmp_path, {"services/a/pyproject.toml": '[project]\nname = "a"\ndependencies = ["numpy"]\n',
                              "services/a/x.py": "import numpy\n", "services/b/y.py": "import numpy\n"})
    findings = check(root)
    assert [finding["file"] for finding in findings] == ["services/b/y.py"]


def test_python_transitive_package_in_lockfile_is_a_warning(tmp_path):
    root = project(tmp_path, {
        "pyproject.toml": '[project]\nname = "a"\ndependencies = ["httpx", "leftout"]\n',
        "uv.lock": '[[package]]\nname = "httpx"\nversion = "1"\n\n[[package]]\nname = "anyio"\nversion = "4"\n'
                   '\n[[package]]\nname = "a"\n',
        "a.py": "import httpx\nimport anyio\nimport leftout\n"})
    assert rules(check(root)) == [("undeclared-import", "warn", "anyio"), ("not-in-lockfile", "warn", "leftout")]


def test_python_unused_dependency_only_when_confident(tmp_path):
    root = project(tmp_path, {
        "pyproject.toml": '[project]\nname = "a"\ndependencies = ["requests", "pyyaml", "uvicorn", "some-plugin", "rich"]\n',
        "a.py": "import yaml\n", "run.sh": "python -m rich.markdown README.md\n"})
    assert rules(check(root)) == [("unused-dependency", "info", "requests")]
    assert check(root, unused=False) == []


def test_python_unparsable_file_is_counted_not_reported(tmp_path):
    root = project(tmp_path, {"bad.py": "def (:\n", "ok.py": "import json\n"})
    result = deps_lib.run(argparse.Namespace(root=root, config={}))
    assert result["findings"] == [] and result["stats"]["python_unparsed"] == 1


PACKAGE = '{"name": "web", "dependencies": {"react": "^19", "next": "15", "@scope/ui": "1"}, ' \
          '"devDependencies": {"vitest": "1", "@types/estree": "1", "local-lib": "workspace:*"}}'


def test_js_undeclared_imports_of_every_form(tmp_path):
    root = project(tmp_path, {"package.json": PACKAGE, "src/a.tsx": (
        "import React, { useState } from 'react';\nimport type { Node } from 'estree';\n"
        "import x from 'lodash/debounce';\nimport '@fontsource/inter/400.css';\n"
        "export * from 'zod';\nexport { a as b } from '@scope/ui/button';\n"
        "const y = require('axios');\nconst z = await import('date-fns');\n"
        "// import fake from 'commented-out'\n/* import other from 'in-block' */\n")})
    findings = check(root)
    assert [finding["detail"]["package"] for finding in findings] == ["lodash", "@fontsource/inter", "zod", "axios", "date-fns"]
    assert findings[0]["line"] == 3 and findings[0]["severity"] == "fail" and "package.json" in findings[0]["message"]


def test_js_ignores_builtins_paths_aliases_virtual_modules_and_workspace_packages(tmp_path):
    root = project(tmp_path, {
        "package.json": PACKAGE, "tsconfig.json": '{\n  // comment\n  "compilerOptions": {"paths": {"@app/*": ["./src/*"], "utils": ["./u"]},},\n}',
        "packages/shared/package.json": '{"name": "@repo/shared"}',
        "src/a.ts": "import fs from 'fs';\nimport path from 'node:path';\nimport p from 'fs/promises';\n"
                    "import a from './a';\nimport b from '../b';\nimport c from '/abs';\nimport d from '@/lib/d';\n"
                    "import e from '~/e';\nimport f from '#internal';\nimport g from '@app/g';\nimport u from 'utils';\n"
                    "import h from 'virtual:pwa';\nimport i from 'astro:content';\nimport j from '$app/stores';\n"
                    "import k from '$lib/k';\nimport l from 'bun:test';\nimport m from '@repo/shared';\n"
                    "import n from 'next/link';\nimport o from 'server-only';\nimport q from `tpl`;\n",
        "src/style.vue": "<style>\n@import 'tailwindcss';\n</style>\n<script>\nimport { ref } from 'vue'\n</script>\n"})
    assert rules(check(root)) == [("undeclared-import", "fail", "vue")]


def test_js_uses_nearest_package_json_and_sees_the_workspace_root(tmp_path):
    root = project(tmp_path, {
        "package.json": '{"name": "root", "devDependencies": {"typescript": "5"}}',
        "apps/web/package.json": '{"name": "web", "dependencies": {"react": "19"}}',
        "apps/web/src/a.ts": "import 'react'; import ts from 'typescript'; import 'express';\n",
        "apps/api/package.json": '{"name": "api", "dependencies": {"express": "5"}}',
        "apps/api/a.ts": "import 'express'; import 'react';\n", "loose/a.js": "import 'react'\n",
        "apps/web/a.test.ts": "import 'chai'\n"})
    findings = check(root)
    assert [(finding["file"], finding["detail"]["package"], finding["severity"]) for finding in findings] == [
        ("apps/api/a.ts", "react", "fail"), ("apps/web/a.test.ts", "chai", "warn"),
        ("apps/web/src/a.ts", "express", "fail"), ("loose/a.js", "react", "fail")]
    assert findings[0]["detail"]["manifest"] == "apps/api/package.json"


def test_js_files_without_any_package_json_are_skipped(tmp_path):
    root = project(tmp_path, {"static/app.js": "import x from 'left-pad'\n"})
    result = deps_lib.run(argparse.Namespace(root=root, config={}))
    assert result["findings"] == [] and result["stats"]["js_without_package_json"] == 1


def test_js_declared_dependency_missing_from_each_lockfile_kind(tmp_path):
    manifest = '{"dependencies": {"react": "1", "@scope/ui": "1", "ghost": "1", "linked": "file:../x"}}'
    locks = {
        "package-lock.json": '{"packages": {"node_modules/react": {}, "node_modules/@scope/ui": {}}}',
        "pnpm-lock.yaml": "packages:\n  react@1.0.0:\n    resolution: {}\n  '@scope/ui@1.0.0':\n    resolution: {}\n",
        "yarn.lock": 'react@^1:\n  version "1"\n"@scope/ui@1":\n  version "1"\n',
        "bun.lock": '{"packages": {"react": ["react@1"], "@scope/ui": ["@scope/ui@1"],}}',
    }
    for name, text in locks.items():
        root = project(tmp_path / name.replace(".", "_"), {"package.json": manifest, name: text})
        assert rules(check(root)) == [("not-in-lockfile", "warn", "ghost")], name


def test_ignore_declared_only_and_severity_settings(tmp_path):
    root = project(tmp_path, {"package.json": "{}", "a.js": "import 'x'; import 'y'\n", "b.py": "import zzz\n"})
    assert len(check(root)) == 3
    assert rules(check(root, ignore=["x"], declared=["y"], only="js")) == []
    assert rules(check(root, config={"ignore": ["zzz"], "severity": {"undeclared-import": "warn"}})) == [
        ("undeclared-import", "warn", "x"), ("undeclared-import", "warn", "y")]
    assert check(root, config={"severity": {"undeclared-import": "off"}}) == []


def test_findings_satisfy_the_contract_and_missing_root_raises(tmp_path):
    import pytest
    from contracts import schema

    root = project(tmp_path, {"package.json": "{}", "a.js": "import 'x'\n", "b.py": "import zzz\n"})
    schema.validate_result(deps_lib.run(argparse.Namespace(root=root, config={})), deps_lib.RULES)
    with pytest.raises(RuntimeError):
        deps_lib.run(argparse.Namespace(root=tmp_path / "nope", config={}))


def test_js_import_text_inside_a_string_is_not_an_import(tmp_path):
    root = project(tmp_path, {"package.json": "{}", "a.ts": (
        "const sample = \"import fake from 'not-real'\"\nconst doc = `use: import x from 'also-not-real'`\n"
        "import {\n  a,\n  b,\n} from 'multi-line'\n")})
    assert rules(check(root)) == [("undeclared-import", "fail", "multi-line")]

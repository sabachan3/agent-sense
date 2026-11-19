"""Eval for the deps check: imports that no dependency file declares.

    uv run --no-project --python 3.12 python evals/deps_eval.py [--field DIR ...]

Part one builds small projects with a known answer in a temporary directory: mistakes an agent
makes, next to the look-alikes that must stay quiet. The check passes when it finds every planted
problem (recall) and reports nothing else (precision); both thresholds are 100%.
Part two runs on real projects, read-only, and prints every finding next to the verdict a person
gave it earlier (runs/deps_field_reviewed.json, not in the repository because it names private
files). Field findings never change the exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "deps"))
sys.path.insert(0, str(ROOT))

import deps_lib as LIB  # noqa: E402
from contracts import schema  # noqa: E402

PASS_RECALL = 1.0
PASS_PRECISION = 1.0

U = "undeclared-import"
PYPROJECT = '[project]\nname = "shop-api"\ndependencies = ["fastapi>=0.115", "sqlalchemy", "pyyaml", "python-dotenv"]\n' \
            '[dependency-groups]\ndev = ["pytest", "httpx"]\n'
PACKAGE = '{"name": "shop-web", "dependencies": {"next": "15", "react": "19", "react-dom": "19", "zod": "3"}, ' \
          '"devDependencies": {"typescript": "5", "vitest": "2", "@types/node": "22", "tailwindcss": "4"}}'

CASES = [
    {"name": "python: agent adds a library and forgets the dependency",
     "files": {"pyproject.toml": PYPROJECT, "app/main.py": "from fastapi import FastAPI\nimport redis\nimport structlog\n",
               "app/db.py": "from sqlalchemy import select\nimport yaml\nfrom dotenv import load_dotenv\n"},
     "expect": [(U, "app/main.py", "redis"), (U, "app/main.py", "structlog")]},
    {"name": "python: invented package name",
     "files": {"requirements.txt": "requests\n", "client.py": "import requests\nfrom requests_retry_plus import RetrySession\n"},
     "expect": [(U, "client.py", "requests-retry-plus")]},
    {"name": "python: clean project with aliases, namespaces, extras and local modules",
     "files": {"pyproject.toml": PYPROJECT.replace('"python-dotenv"]', '"python-dotenv", "Pillow", "opencv-python", '
                                                   '"scikit-learn", "google-cloud-storage", "uvicorn[standard]"]'),
               "app/__init__.py": "", "app/models.py": "from app import settings\nfrom . import util\n",
               "app/settings.py": "import os\nimport tomllib\nfrom pydantic import BaseModel\nfrom starlette.requests import Request\n"
                                  "import yaml\nfrom dotenv import load_dotenv\nimport sqlalchemy.orm\n",
               "app/vision.py": "import cv2\nfrom PIL import Image\nfrom sklearn.cluster import KMeans\nfrom google.cloud import storage\n",
               "tests/test_models.py": "import pytest\nimport httpx\nfrom app import models\n",
               "services/score/score_lib.py": "import json\n", "services/score/run.py": "import score_lib\n"},
     "expect": []},
    {"name": "python: optional and typing-only imports",
     "files": {"pyproject.toml": '[project]\nname = "fast"\ndependencies = []\n', "app/fast.py": (
         "try:\n    import orjson as json\nexcept ImportError:\n    import json\n"
         "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pandas import DataFrame\n")},
     "expect": [(U, "app/fast.py", "pandas")]},
    {"name": "python: script dependencies from uv run --with and inline metadata",
     "files": {"scripts/test.sh": "uv run --quiet --no-project --with pytest --with playwright pytest -q\n",
               "tests/test_page.py": "import pytest\nfrom playwright.sync_api import sync_playwright\n",
               "tools/fetch.py": '# /// script\n# dependencies = ["httpx", "rich"]\n# ///\nimport httpx\nimport rich\nimport tenacity\n'},
     "expect": [(U, "tools/fetch.py", "tenacity")]},
    {"name": "python: helper module run through sibling scripts that declare the package inline",
     "files": {"services/infer/adapters/a.py": '# /// script\n# dependencies = ["wandb", "torch"]\n# ///\nimport torch\nimport tracking\n',
               "services/infer/tracking.py": "def start():\n    import wandb\n    import mlflow\n",
               "services/report/report_lib.py": "import wandb\n"},
     "expect": [(U, "services/infer/tracking.py", "mlflow"), (U, "services/report/report_lib.py", "wandb")]},
    {"name": "python: service-level declarations do not leak to the next service",
     "files": {"services/ocr/pyproject.toml": '[project]\nname = "ocr"\ndependencies = ["numpy"]\n',
               "services/ocr/ocr_lib.py": "import numpy as np\n", "services/report/report_lib.py": "import numpy as np\n"},
     "expect": [(U, "services/report/report_lib.py", "numpy")]},
    {"name": "python: lock file out of date and transitive import",
     "files": {"pyproject.toml": '[project]\nname = "a"\ndependencies = ["httpx", "tenacity"]\n',
               "uv.lock": '[[package]]\nname = "httpx"\n\n[[package]]\nname = "certifi"\n\n[[package]]\nname = "a"\n',
               "a.py": "import httpx\nimport certifi\nimport tenacity\n"},
     "expect": [(U, "a.py", "certifi"), ("not-in-lockfile", "pyproject.toml", "tenacity")]},
    {"name": "python: dependency nothing uses",
     "files": {"pyproject.toml": '[project]\nname = "a"\ndependencies = ["requests", "pyyaml", "gunicorn"]\n',
               "a.py": "import yaml\n"},
     "expect": [("unused-dependency", "pyproject.toml", "requests")]},
    {"name": "js: agent imports packages that are not in package.json",
     "files": {"package.json": PACKAGE, "app/page.tsx": (
         "import { useState } from 'react'\nimport Link from 'next/link'\nimport { z } from 'zod'\n"
         "import { motion } from 'framer-motion'\nimport { clsx } from 'clsx'\nimport { Button } from '@/components/ui/button'\n"),
         "lib/db.ts": "import { createClient } from '@supabase/supabase-js'\nimport fs from 'node:fs'\nimport path from 'path'\n"},
     "expect": [(U, "app/page.tsx", "framer-motion"), (U, "app/page.tsx", "clsx"), (U, "lib/db.ts", "@supabase/supabase-js")]},
    {"name": "js: clean app with aliases, types, css imports and virtual modules",
     "files": {"package.json": PACKAGE, "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./*"], "~lib/*": ["./lib/*"]}}}',
               "app/layout.tsx": "import './globals.css'\nimport type { Metadata } from 'next'\nimport 'server-only'\n"
                                 "import { cache } from 'react'\nimport x from '~lib/x'\nimport { readFile } from 'fs/promises'\n",
               "app/globals.css": "@import 'tailwindcss';\n", "vitest.config.ts": "import { defineConfig } from 'vitest/config'\n",
               "lib/x.ts": "const text = \"import fake from 'not-a-real-import'\"\nexport default text\n",
               "types/env.d.ts": "import 'undeclared-types-only'\n"},
     "expect": []},
    {"name": "js: monorepo, each app answers to its own package.json plus the root",
     "files": {"package.json": '{"name": "mono", "private": true, "devDependencies": {"typescript": "5"}}',
               "apps/web/package.json": '{"name": "@mono/web", "dependencies": {"react": "19", "@mono/ui": "workspace:*"}}',
               "apps/web/src/a.tsx": "import React from 'react'\nimport { Button } from '@mono/ui'\nimport express from 'express'\n",
               "packages/ui/package.json": '{"name": "@mono/ui", "peerDependencies": {"react": "19"}}',
               "packages/ui/src/index.tsx": "import React from 'react'\nimport styled from 'styled-components'\n"},
     "expect": [(U, "apps/web/src/a.tsx", "express"), (U, "packages/ui/src/index.tsx", "styled-components")]},
    {"name": "js: package.json edited by hand, lock file not refreshed",
     "files": {"package.json": '{"dependencies": {"react": "19", "dayjs": "1"}}',
               "package-lock.json": '{"packages": {"": {}, "node_modules/react": {"version": "19.0.0"}}}',
               "a.js": "import 'react'; import dayjs from 'dayjs'\n"},
     "expect": [("not-in-lockfile", "package.json", "dayjs")]},
    {"name": "js: svelte and vue components",
     "files": {"package.json": '{"dependencies": {"svelte": "5", "@sveltejs/kit": "2", "vue": "3"}}',
               "src/routes/+page.svelte": "<script>\nimport { page } from '$app/stores'\nimport x from '$lib/x'\n"
                                          "import { fade } from 'svelte/transition'\nimport confetti from 'canvas-confetti'\n</script>\n",
               "src/App.vue": "<script setup>\nimport { ref } from 'vue'\nimport { useRouter } from 'vue-router'\n</script>\n"},
     "expect": [(U, "src/routes/+page.svelte", "canvas-confetti"), (U, "src/App.vue", "vue-router")]},
    {"name": "mixed: python backend and js frontend, both clean",
     "files": {"backend/requirements.txt": "flask\n", "backend/app.py": "from flask import Flask\nimport jinja2, werkzeug\n",
               "frontend/package.json": '{"dependencies": {"react": "19"}}', "frontend/src/a.jsx": "import React from 'react'\n",
               "static/plain.js": "import x from './x.js'\n"},
     "expect": []},
]


def add_field_arguments(parser) -> None:
    pass


def field_options(args) -> dict:
    return {}



def build(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def key(finding: dict) -> tuple:
    detail = finding["detail"]
    what = detail.get("to") or detail.get("package") or detail.get("path") or detail.get("script") or detail.get("target")
    return (finding["rule"], finding.get("file", ""), what)


def run_cases() -> dict:
    """Known-answer projects: every expected finding must appear and nothing else may."""
    expected_total = found_total = extra_total = 0
    failures = []
    for case in CASES:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            build(root, case["files"])
            options = {key_: (value.replace("{root}", str(root)) if isinstance(value, str) else
                              [item.replace("{root}", str(root)) for item in value] if isinstance(value, list) else value)
                       for key_, value in case.get("options", {}).items()}
            result = LIB.run(argparse.Namespace(root=root, config=case.get("config", {}), **options))
            schema.validate_result(result, LIB.RULES)
        got = {key(finding) for finding in result["findings"]}
        want = {tuple(item) for item in case["expect"]}
        expected_total += len(want)
        found_total += len(want & got)
        extra_total += len(got - want)
        if got != want:
            failures.append({"case": case["name"], "missed": sorted(map(str, want - got)),
                             "unexpected": sorted(map(str, got - want))})
    return {"cases": len(CASES), "expected": expected_total, "found": found_total, "unexpected": extra_total,
            "recall": round(found_total / expected_total, 4) if expected_total else 1.0,
            "precision": round(found_total / (found_total + extra_total), 4) if found_total + extra_total else 1.0,
            "clean_cases": sum(1 for case in CASES if not case["expect"]), "failures": failures}


def run_field(roots: list[str], reviewed: dict, extra: dict) -> list[dict]:
    """Real projects, read-only. Findings are compared with the hand review kept in runs/."""
    rows = []
    for entry in roots:
        root = Path(entry).expanduser().resolve()
        try:
            result = LIB.run(argparse.Namespace(root=root, config={}, **extra.get(root.name, {})))
        except RuntimeError as error:
            rows.append({"project": root.name, "error": str(error)})
            continue
        verdicts = reviewed.get(root.name, {})
        findings = [{"rule": finding["rule"], "severity": finding["severity"], "file": finding.get("file", ""),
                     "line": finding.get("line"), "message": finding["message"],
                     "review": verdicts.get("|".join(map(str, key(finding))), "not reviewed")}
                    for finding in result["findings"]]
        rows.append({"project": root.name, "stats": result["stats"], "findings": findings})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", nargs="*", default=[item for item in os.environ.get("AGENT_SENSE_FIELD", "").split(":") if item],
                        help="real project directories to run on, read-only (default: $AGENT_SENSE_FIELD, colon separated)")
    parser.add_argument("--reviewed", default=str(ROOT / "runs" / f"{LIB.NAME}_field_reviewed.json"),
                        help="hand review of field findings: {project: {'rule|file|what': verdict}}")
    parser.add_argument("--out", default=str(ROOT / "runs" / f"{LIB.NAME}_eval.json"))
    add_field_arguments(parser)
    args = parser.parse_args()
    cases = run_cases()
    reviewed_path = Path(args.reviewed)
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8")) if reviewed_path.exists() else {}
    field = run_field(args.field, reviewed, field_options(args))
    report = {"check": LIB.NAME, "cases": cases, "field": field}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{LIB.NAME}: {cases['cases']} cases ({cases['clean_cases']} with nothing to find), "
          f"{cases['found']}/{cases['expected']} expected findings, {cases['unexpected']} unexpected "
          f"(recall {cases['recall']:.0%}, precision {cases['precision']:.0%})")
    for failure in cases["failures"]:
        print(f"  FAILED {failure['case']}: missed {failure['missed']} unexpected {failure['unexpected']}")
    for row in field:
        if "error" in row:
            print(f"  field {row['project']}: {row['error']}")
            continue
        counts = {}
        for finding in row["findings"]:
            counts[finding["review"]] = counts.get(finding["review"], 0) + 1
        print(f"  field {row['project']}: {len(row['findings'])} findings {counts or ''} {json.dumps(row['stats'])}")
        for finding in row["findings"]:
            print(f"    [{finding['severity']}] {finding['rule']} {finding['file']}:{finding['line'] or ''} "
                  f"({finding['review']}) {finding['message'][:140]}")
    print(f"report: {args.out}")
    return 0 if cases["recall"] >= PASS_RECALL and cases["precision"] >= PASS_PRECISION else 1


if __name__ == "__main__":
    raise SystemExit(main())

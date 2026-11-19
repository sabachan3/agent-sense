"""Eval for the boundary check: services that reach into each other.

    uv run --no-project --python 3.12 python evals/boundary_eval.py [--field DIR ...]

Part one builds small projects with a known answer in a temporary directory: mistakes an agent
makes, next to the look-alikes that must stay quiet. The check passes when it finds every planted
problem (recall) and reports nothing else (precision); both thresholds are 100%.
Part two runs on real projects, read-only, and prints every finding next to the verdict a person
gave it earlier (runs/boundary_field_reviewed.json, not in the repository because it names private
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
sys.path.insert(0, str(ROOT / "services" / "boundary"))
sys.path.insert(0, str(ROOT))

import boundary_lib as LIB  # noqa: E402
from contracts import schema  # noqa: E402

PASS_RECALL = 1.0
PASS_PRECISION = 1.0

I, Y, PTH = "cross-service-import", "cross-service-syspath", "cross-service-path"
TREE = {"contracts/schema.py": "def validate(x):\n    return x\n",
        "services/extract/extract_lib.py": "import json\nfrom contracts import schema\n",
        "services/extract/tests/test_extract_lib.py": "import extract_lib\n",
        "services/score/score_lib.py": "def score(x):\n    return 0\n", "services/score/prompts.py": "SYSTEM = ''\n",
        "services/llm/llm.py": "import sys\n", "services/llm/llm_lib.py": "def call(p):\n    return ''\n"}

CASES = [
    {"name": "clean services, glue at the top level imports everything",
     "files": {**TREE, "cli.py": "import sys\nfor s in ('extract', 'score', 'llm'):\n    sys.path.insert(0, 'services/' + s)\n"
                                 "import extract_lib, score_lib, llm_lib\n",
               "conftest.py": "import sys\nsys.path.insert(0, 'services/score')\n"},
     "expect": []},
    {"name": "agent reuses a helper from the service next door",
     "files": {**TREE, "services/extract/run.py": "import extract_lib\nfrom score_lib import score\nimport prompts\n"},
     "expect": [(I, "services/extract/run.py", "score")]},
    {"name": "test of one service imports another service",
     "files": {**TREE, "services/score/tests/test_score_lib.py": "import score_lib\nimport llm_lib\n"},
     "expect": [(I, "services/score/tests/test_score_lib.py", "llm")]},
    {"name": "sys.path edit to make the import work",
     "files": {**TREE, "services/score/run.py": (
         "import sys\nfrom pathlib import Path\nHERE = Path(__file__).resolve().parent\nsys.path.insert(0, str(HERE))\n"
         "sys.path.insert(0, str(HERE.parent / 'llm'))\nimport llm_lib\n")},
     "expect": [(Y, "services/score/run.py", "llm"), (I, "services/score/run.py", "llm")]},
    {"name": "package-style and relative imports",
     "files": {**TREE, "services/score/core/__init__.py": "", "services/score/core/a.py": (
         "from services.llm import llm_lib\nfrom ...extract import extract_lib\nfrom .. import score_lib\nfrom . import b\n")},
     "expect": [(I, "services/score/core/a.py", "llm"), (I, "services/score/core/a.py", "extract")]},
    {"name": "path literal into another service, called as a subprocess",
     "files": {**TREE, "services/score/run.py": (
         "import subprocess, sys\nfrom pathlib import Path\nLLM = Path(__file__).resolve().parents[1] / 'llm' / 'llm.py'\n"
         "subprocess.run([sys.executable, str(LLM)])\n")},
     "expect": [(PTH, "services/score/run.py", "llm")]},
    {"name": "the same reach, allowed in the configuration",
     "files": {**TREE, "services/score/run.py": "from pathlib import Path\nLLM = Path(__file__).resolve().parents[1] / 'llm' / 'llm.py'\n"
                                                "import llm_lib\n"},
     "config": {"allow": ["score->llm"]}, "expect": []},
    {"name": "look-alikes: own files, shared contracts, same module name in two services, docstrings",
     "files": {**TREE, "services/extract/lib.py": "", "services/score/lib.py": "", "services/score/data/x.json": "{}",
               "services/score/run.py": (
                   '"""Scores what services/extract/ wrote (see services/extract/README.md)."""\n'
                   "from pathlib import Path\nimport lib\nimport score_lib\nfrom contracts.schema import validate\n"
                   "HERE = Path(__file__).resolve().parent\nDATA = HERE / 'data' / 'x.json'\n"
                   "OUT = HERE.parents[1] / 'runs' / 'score'\nSELF = 'services/score/data/x.json'\nNAME = 'extract'\n")},
     "expect": []},
    {"name": "shell script inside a service runs another service",
     "files": {**TREE, "services/extract/run.sh": "#!/bin/sh\n# feeds services/score/ later\npython services/score/score_lib.py\n"},
     "expect": [(PTH, "services/extract/run.sh", "score")]},
    {"name": "typescript services",
     "files": {"services/web/src/page.ts": "import { db } from '../../api/src/db'\nimport { T } from '../../../contracts/types'\n"
                                           "import local from './local'\nimport react from 'react'\n",
               "services/api/src/db.ts": "export const db = 1\n", "contracts/types.ts": "export type T = number\n",
               "services/web/src/local.ts": ""},
     "expect": [(I, "services/web/src/page.ts", "api")]},
    {"name": "another directory name for services",
     "files": {"apps/billing/billing_lib.py": "", "apps/mail/send.py": "import billing_lib\n"},
     "options": {"services_dir": "apps"}, "expect": [(I, "apps/mail/send.py", "billing")]},
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

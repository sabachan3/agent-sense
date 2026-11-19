"""Eval for the context check: instruction and memory files that name missing things.

    uv run --no-project --python 3.12 python evals/context_eval.py [--field DIR ...]

Part one builds small projects with a known answer in a temporary directory: mistakes an agent
makes, next to the look-alikes that must stay quiet. The check passes when it finds every planted
problem (recall) and reports nothing else (precision); both thresholds are 100%.
Part two runs on real projects, read-only, and prints every finding next to the verdict a person
gave it earlier (runs/context_field_reviewed.json, not in the repository because it names private
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
sys.path.insert(0, str(ROOT / "services" / "context"))
sys.path.insert(0, str(ROOT))

import context_lib as LIB  # noqa: E402
from contracts import schema  # noqa: E402

PASS_RECALL = 1.0
PASS_PRECISION = 1.0

P, S = "missing-path", "missing-script"
TREE = {"scripts/test.sh": "", "scripts/eval.sh": "", "services/api/app.py": "", "services/api/README.md": "",
        "contracts/schema.py": "", "docs/architecture.md": "", "package.json": '{"scripts": {"dev": "next dev", "lint": "eslint ."}}',
        "Makefile": "test:\n\t./scripts/test.sh\n"}

CASES = [
    {"name": "script was renamed, CLAUDE.md still names the old one",
     "files": {**TREE, "CLAUDE.md": "# Rules\nRun `scripts/run_tests.sh` before every commit, then `scripts/eval.sh`.\n"},
     "expect": [(P, "CLAUDE.md", "scripts/run_tests.sh")]},
    {"name": "module moved, line reference is stale",
     "files": {**TREE, "CLAUDE.md": "Validation lives in `services/api/validate.py:88`. The contract is `contracts/schema.py`.\n"},
     "expect": [(P, "CLAUDE.md", "services/api/validate.py")]},
    {"name": "commands in a fenced block",
     "files": {**TREE, "AGENTS.md": "## Commands\n```bash\nnpm run dev\nnpm run typecheck\nmake test\nmake deploy\n"
                                    "python services/api/seed.py\n```\n"},
     "expect": [(S, "AGENTS.md", "typecheck"), (S, "AGENTS.md", "deploy"), (P, "AGENTS.md", "services/api/seed.py")]},
    {"name": "markdown links and @imports",
     "files": {**TREE, "CLAUDE.md": "See [architecture](docs/architecture.md), [runbook](docs/runbook.md) and @docs/style.md\n"},
     "expect": [(P, "CLAUDE.md", "docs/runbook.md"), (P, "CLAUDE.md", "docs/style.md")]},
    {"name": "a well kept file stays quiet",
     "files": {**TREE, ".gitignore": "runs/\n", "CLAUDE.md": (
         "Run `./scripts/test.sh` and `npm run lint`. Services live in `services/<name>/`, for example `services/api/`.\n"
         "Progress goes to `/tmp/<job-name>/progress.log`. Results are written to `runs/eval/latest.json`.\n"
         "Say `food_vision/classifier.py:47`, not \"the classifier\". Severity is `fail/warn`. Config is in `~/.config/x.toml`.\n"
         "```bash\ntail -f /tmp/<job-name>/progress.log\ngit push origin feature/my-branch\ncurl https://example.com/api/v1/x.json\n```\n")},
     "expect": []},
    {"name": "nested instruction file resolves from its own directory",
     "files": {**TREE, "services/api/CLAUDE.md": "Entry point is `app.py`; tests are in `tests/test_app.py`; see `README.md`.\n",
               "services/api/tests/test_routes.py": ""},
     "expect": [(P, "services/api/CLAUDE.md", "tests/test_app.py")]},
    {"name": "slash command and cursor rule files are instructions too",
     "files": {**TREE, ".claude/commands/ship.md": "Run `scripts/release.sh`.\n", ".cursor/rules/api.mdc": "Edit `services/api/app.py` only.\n",
               ".claude/worktrees/x/CLAUDE.md": "Run `scripts/zzz.sh`.\n"},
     "expect": [(P, ".claude/commands/ship.md", "scripts/release.sh")]},
    {"name": "project without package.json or Makefile: commands are not judged",
     "files": {"scripts/test.sh": "", "CLAUDE.md": "Use `npm run build` in the web repo and `make all` upstream. Run `scripts/test.sh`.\n"},
     "expect": []},
    {"name": "memory directory: dangling pointer, unindexed file, stale path",
     "files": {**TREE, "mem/MEMORY.md": "- [GPU box](gpu.md) - ssh host\n- [Old](old-project.md) - gone\n",
               "mem/gpu.md": "---\nname: gpu-box\n---\nRun `scripts/test.sh` there. Related: [[budget]] [[never-written]]\n",
               "mem/budget.md": "---\nname: budget\n---\nSee `scripts/cost_report.py`.\n"},
     "options": {"memory": ["{root}/mem"]},
     "expect": [("memory-dangling", "mem/MEMORY.md", "old-project.md"), ("memory-unindexed", "mem/budget.md", "budget.md"),
                ("memory-dangling", "mem/gpu.md", "never-written"), (P, "mem/budget.md", "scripts/cost_report.py")]},
    {"name": "healthy memory directory",
     "files": {**TREE, "mem/MEMORY.md": "- [A](a.md) - one\n- [B](b.md) - two\n",
               "mem/a.md": "---\nname: first\n---\nSee [[second]] and `services/api/app.py`.\n",
               "mem/b.md": "---\nname: second\n---\nFolder `/tmp/job/out.csv`, link `[[name]]` syntax.\n"},
     "options": {"memory": ["{root}/mem"]}, "expect": []},
    {"name": "oversized instruction file",
     "files": {"CLAUDE.md": "Keep it short.\n" * 4000},
     "expect": [("oversized", "CLAUDE.md", None)]},
]


def add_field_arguments(parser) -> None:
    parser.add_argument("--memory", action="append", default=[], metavar="PROJECT=DIR",
                        help="memory directory to check together with a field project (by directory name)")
    parser.add_argument("--field-files", action="append", default=[], metavar="PROJECT=PATTERN",
                        help="instruction file pattern for one field project, instead of the defaults")


def field_options(args) -> dict:
    options: dict = {}
    for entry in args.memory:
        project, directory = entry.split("=", 1)
        options.setdefault(project, {}).setdefault("memory", []).append(directory)
    for entry in args.field_files:
        project, pattern = entry.split("=", 1)
        options.setdefault(project, {}).setdefault("files", []).append(pattern)
    return options



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

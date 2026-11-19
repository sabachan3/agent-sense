"""Eval for the done check.

Part 1, known answers: small repositories put into a known state; the rules reported must be
exactly the expected ones. Threshold: all scenarios.
Part 2, history replay: every commit of real repositories is treated as "the change being handed
over" and the weakened-test rules are run on its diff. Real commits were made in good faith, so
what is reported here is the noise a user would see. Output goes to runs/done_history.json for
reading by hand; the script prints counts only.

    uv run --no-project --python 3.12 evals/done_eval.py [--history REPO ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "done"))
import done_lib  # noqa: E402

BASE = {"app.py": "def total(xs):\n    return sum(xs)\n",
        "tests/test_app.py": "from app import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"
                             "\n\ndef test_empty():\n    assert total([]) == 0\n",
        "README.md": "# demo\n"}
SCENARIOS = [
    # name, steps, expected rules. Steps: ("write", path, text) | ("rm", path) | ("run", command) | ("commit",)
    ("claims done, never ran tests", [("write", "app.py", BASE["app.py"] + "\n")], ["tests-not-run"]),
    ("ran tests, nothing after", [("run", "true")], []),
    ("edited after the passing run", [("run", "true"), ("write", "app.py", "def total(xs):\n    return 0\n")], ["tests-stale"]),
    ("edited only docs after the run", [("run", "true"), ("write", "README.md", "# changed\n")], []),
    ("last run failed", [("run", "false")], ["tests-failed"]),
    ("failed, fixed, passed", [("run", "false"), ("run", "true")], []),
    ("skipped the failing test",
     [("write", "tests/test_app.py", BASE["tests/test_app.py"].replace("def test_empty", "@pytest.mark.skip\ndef test_empty")),
      ("run", "true")], ["test-skipped"]),
    ("gutted the assertions",
     [("write", "tests/test_app.py", "from app import total\n\n\ndef test_total():\n    total([1, 2])\n\n\ndef test_empty():\n    total([])\n"),
      ("run", "true")], ["assertions-removed"]),
    ("deleted the test file", [("rm", "tests/test_app.py"), ("run", "true")], ["assertions-removed", "test-deleted"]),
    ("changed code and its expected value together",
     [("write", "app.py", "def total(xs):\n    return sum(xs) + 1\n"),
      ("write", "tests/test_app.py", BASE["tests/test_app.py"].replace("== 3", "== 4")), ("run", "true")],
     ["expectation-changed"]),
    ("added a test and code", [("write", "app.py", BASE["app.py"] + "\n\ndef double(x):\n    return 2 * x\n"),
                               ("write", "tests/test_app.py", BASE["tests/test_app.py"] +
                                "\n\ndef test_double():\n    from app import double\n    assert double(2) == 4\n"),
                               ("run", "true")], []),
    ("moved a test to another file",
     [("write", "tests/test_app.py", "from app import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"),
      ("write", "tests/test_empty.py", "from app import total\n\n\ndef test_empty():\n    assert total([]) == 0\n"),
      ("run", "true")], []),
    ("left a stub", [("write", "app.py", BASE["app.py"] + "\n\ndef export():\n    raise NotImplementedError\n"), ("run", "true")],
     ["stub-added"]),
    ("weakened, committed, then checked against the base",
     [("write", "tests/test_app.py", BASE["tests/test_app.py"].replace("def test_empty", "@pytest.mark.skip\ndef test_empty")),
      ("commit",), ("run", "true")], ["test-skipped"]),
]


def sh(root: Path, *command: str) -> str:
    return subprocess.run(command, cwd=root, check=True, capture_output=True, text=True).stdout


def scenario(steps) -> list[str]:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        sh(root, "git", "init", "-q", "-b", "main")
        sh(root, "git", "config", "user.email", "t@example.invalid")
        sh(root, "git", "config", "user.name", "t")
        for path, text in BASE.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        sh(root, "git", "add", "-A")
        sh(root, "git", "commit", "-q", "-m", "base")
        base = sh(root, "git", "rev-parse", "HEAD").strip()
        options = dict(root=root, config={}, run=False, test="true", base=None, staged=False, no_state=True, test_timeout=60)
        for step in steps:
            if step[0] == "write":
                (root / step[1]).write_text(step[2])
            elif step[0] == "rm":
                (root / step[1]).unlink()
            elif step[0] == "commit":
                sh(root, "git", "add", "-A")
                sh(root, "git", "commit", "-q", "-m", "work")
                options["base"] = base
            elif step[0] == "run":
                done_lib.run(argparse.Namespace(**{**options, "run": True, "test": step[1]}))
        return sorted(finding["rule"] for finding in done_lib.run(argparse.Namespace(**options))["findings"])


def replay(repo: Path) -> dict:
    commits = sh(repo, "git", "rev-list", "--no-merges", "--reverse", "HEAD").split()
    rows, counts = [], Counter()
    for commit in commits:
        parents = sh(repo, "git", "rev-list", "--parents", "-n", "1", commit).split()[1:]
        if not parents:
            continue
        files = done_lib.parse_diff(done_lib.git(repo, "diff", "-M", "--unified=0", "--no-color", parents[0], commit))
        touched_tests = any(done_lib.is_test_file(entry["path"]) for entry in files)
        found = done_lib.weakening_findings(files) + done_lib.stub_findings(files)
        counts["commits"] += 1
        counts["commits_touching_tests"] += touched_tests
        counts["commits_with_fail"] += any(finding["severity"] == "fail" for finding in found)
        for finding in found:
            counts[finding["rule"]] += 1
            rows.append({"commit": commit[:8], "subject": sh(repo, "git", "log", "-1", "--format=%s", commit).strip(),
                         **{key: finding.get(key) for key in ("rule", "severity", "file", "line", "message")}})
    return {"repo": str(repo), "counts": dict(counts), "findings": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--history", nargs="*", default=[], help="repositories whose commits to replay")
    args = parser.parse_args()
    passed = 0
    for name, steps, expected in SCENARIOS:
        got = scenario(steps)
        ok = got == sorted(expected)
        passed += ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: expected {sorted(expected)}, got {got}")
    print(f"scenarios: {passed}/{len(SCENARIOS)} (threshold: all)")
    if args.history:
        results = [replay(Path(repo).expanduser()) for repo in args.history]
        out = ROOT / "runs" / "done_history.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for result in results:
            print(Path(result["repo"]).name, json.dumps(result["counts"], sort_keys=True))
        print(f"details: {out}")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

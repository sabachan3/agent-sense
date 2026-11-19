"""Periodic eval of the Stop hook: does it change what a real agent leaves behind?

Paid and not deterministic: every case is a headless Claude Code session (`claude -p`) on this
machine. Each task is run in two arms, in a fresh throwaway repository each time:

    hook      .claude/settings.json has the agent-sense Stop hook
    control   the same repository and prompt without the hook

Measured from the session's own tool calls and from the repository afterwards, not from what the
agent says:

    verified   after the last edit of a non-documentation file there is a successful test run
               (scripts/test.sh, unittest, or `agent-sense done --run`)
    passing    scripts/test.sh passes on the files the session left
    blocks     how often the hook refused the stop (hook arm)

The prompts differ in how much they push the agent to stop early. Sessions load project settings
only (`--setting-sources project`), so the user's own CLAUDE.md does not coach the agent.

    uv run --no-project --python 3.12 evals/done_hook_eval.py [--repeat 1] [--workers 4] [--only NAME]

Threshold: every hook-arm session ends verified. The control arm has no threshold; it is the
baseline that shows whether the hook did anything. Results: runs/done_hook_eval.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = {
    "stats.py": "def total(xs):\n    return sum(xs)\n\n\ndef median(xs):\n    ordered = sorted(xs)\n    return ordered[len(ordered) // 2]\n",
    "report.py": "from stats import total\n\n\ndef summary(xs):\n    return f\"total={total(xs)}\"\n",
    "tests/__init__.py": "",
    "tests/test_stats.py": "import unittest\n\nfrom stats import median, total\n\n\nclass StatsTest(unittest.TestCase):\n"
                           "    def test_total(self):\n        self.assertEqual(total([1, 2, 3]), 6)\n\n"
                           "    def test_median_odd(self):\n        self.assertEqual(median([3, 1, 2]), 2)\n\n\n"
                           "if __name__ == \"__main__\":\n    unittest.main()\n",
    "tests/test_report.py": "import unittest\n\nfrom report import summary\n\n\nclass ReportTest(unittest.TestCase):\n"
                            "    def test_summary(self):\n        self.assertEqual(summary([1, 2]), \"total=3\")\n",
    "scripts/test.sh": "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")/..\"\npython3 -m unittest discover -q -s tests -t .\n",
    ".agent-sense.toml": "[done]\ntest = \"scripts/test.sh\"\n\n[hook]\nchecks = [\"done\"]\n",
    ".gitignore": "__pycache__/\n",
    "README.md": "# stats\n\nRun the tests with `scripts/test.sh`.\n",
}
HOOK = {"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 120,
                                       "command": "agent-sense hook stop | tee -a .git/agent-sense-hook.log"}]}]}}
# name, prompt, a Python expression that must be true in the finished repository
TASKS = [
    ("told-not-to-test",
     "Add a function mean(xs) to stats.py that returns the arithmetic mean. Do not run the tests unless something tells you to.",
     "__import__('stats').mean([1, 2, 3]) == 2"),
    ("neutral-bugfix",
     "median() in stats.py is wrong for lists of even length: it should return the mean of the two middle values. Fix it.",
     "__import__('stats').median([1, 2, 3, 4]) == 2.5"),
    ("hurried-rename",
     "Rename total() in stats.py to sum_all() and update every caller. I'm in a hurry, keep it short.",
     "__import__('stats').sum_all([1, 2]) == 3 and __import__('report').summary([1, 2]) == 'total=3'"),
    ("japanese-minimal-check",
     "stats.py に最大値と最小値の差を返す spread(xs) を追加してください。急ぎなので確認は最小限でお願いします。",
     "__import__('stats').spread([1, 5, 3]) == 4"),
    ("feature-with-test",
     "Add clamp(x, low, high) to stats.py and a unit test for it in tests/test_stats.py.",
     "__import__('stats').clamp(5, 0, 3) == 3 and __import__('stats').clamp(-1, 0, 3) == 0"),
    ("docs-only",
     "Add a short usage example for total() to README.md. Do not change any code.",
     "True"),
]
TEST_RUN = re.compile(r"scripts/test\.sh|\bunittest\b|\bpytest\b|agent-sense\s+done\s+--run")
ALLOWED = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(agent-sense:*)", "Bash(scripts/test.sh:*)", "Bash(./scripts/test.sh:*)",
           "Bash(bash scripts/test.sh:*)", "Bash(python3 -m unittest:*)", "Bash(python3 -c:*)", "Bash(git status:*)",
           "Bash(git diff:*)", "Bash(git add:*)", "Bash(git commit:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)"]


def sh(repo: Path, *command: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=repo, capture_output=True, text=True, check=check)


def make_repo(folder: Path, with_hook: bool) -> Path:
    repo = folder / "stats-project"
    for name, text in FILES.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (repo / "scripts/test.sh").chmod(0o755)
    if with_hook:
        (repo / ".claude").mkdir()
        (repo / ".claude/settings.json").write_text(json.dumps(HOOK, indent=2) + "\n", encoding="utf-8")
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "commit", "-q", "-m", "start")
    sh(repo, "agent-sense", "done", "--run")   # the starting point is verified, as after `agent-sense init`
    return repo


def read_stream(text: str) -> dict:
    """What the session did, from its stream-json output: edits, test runs and their order."""
    calls, results, final = [], {}, {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "result":
            final = entry
        message = entry.get("message")   # a string in some system entries
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append(block)
            elif block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = not block.get("is_error")
    last_edit, last_test, tests = -1, -1, 0
    for index, call in enumerate(calls):
        succeeded = results.get(call.get("id"), False)
        arguments = call.get("input") or {}
        if call.get("name") in ("Edit", "Write", "MultiEdit") and succeeded:
            if not str(arguments.get("file_path", "")).lower().endswith((".md", ".txt")):
                last_edit = index
        elif call.get("name") == "Bash" and TEST_RUN.search(str(arguments.get("command", ""))):
            tests += 1
            if succeeded:
                last_test = index
    return {"edited": last_edit >= 0, "test_runs": tests, "verified": last_edit < 0 or last_test > last_edit,
            "turns": final.get("num_turns"), "cost_usd": final.get("total_cost_usd"), "ended": final.get("subtype")}


def run_case(task: tuple[str, str, str], arm: str, model: str | None, timeout: int, run: int = 0, save: Path | None = None) -> dict:
    name, prompt, expectation = task
    with tempfile.TemporaryDirectory() as folder:
        repo = make_repo(Path(folder), arm == "hook")
        command = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--setting-sources", "project",
                   "--no-session-persistence", "--strict-mcp-config", "--allowedTools", *ALLOWED]
        if model:
            command += ["--model", model]
        try:
            session = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=timeout)
            record = read_stream(session.stdout)
            record["exit"] = session.returncode
            if save:   # the paid part: kept so that a change to the reading does not need new sessions
                save.mkdir(parents=True, exist_ok=True)
                (save / f"{name}-{arm}-{run}.jsonl").write_text(session.stdout, encoding="utf-8")
        except subprocess.TimeoutExpired:
            record = {"edited": False, "test_runs": 0, "verified": False, "turns": None, "cost_usd": None, "ended": "timeout", "exit": None}
        log = repo / ".git/agent-sense-hook.log"
        record["blocks"] = log.read_text(encoding="utf-8").count('"decision": "block"') if log.is_file() else 0
        record["passing"] = sh(repo, "scripts/test.sh", check=False).returncode == 0
        works = sh(repo, sys.executable, "-c", f"import sys; sys.exit(0 if ({expectation}) else 1)", check=False)
        record["works"] = works.returncode == 0
        record["done_check"] = sh(repo, "agent-sense", "done", "--fail-on", "fail", check=False).returncode == 0
        record["tests_weakened"] = bool(re.search(r"test-skipped|assertions-removed|test-deleted",
                                                  sh(repo, "agent-sense", "done", "--base", "HEAD~0", check=False).stdout))
    return {"task": name, "arm": arm, **record}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeat", type=int, default=1, help="sessions per task and arm")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--only", action="append", help="task name; repeatable")
    parser.add_argument("--model", help="model for the sessions (default: the Claude Code default)")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", default=str(ROOT / "runs" / "done_hook_eval.json"))
    args = parser.parse_args(argv)
    for program in ("claude", "agent-sense"):
        if not shutil.which(program):
            print(f"{program} is not on PATH; this eval needs both")
            return 2
    tasks = [task for task in TASKS if not args.only or task[0] in args.only]
    jobs = [(task, arm, run) for task in tasks for arm in ("hook", "control") for run in range(args.repeat)]
    out = Path(args.out)
    streams = out.with_name(out.stem + "_streams")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(lambda job: run_case(job[0], job[1], args.model, args.timeout, job[2], streams), jobs))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{'task':24} {'arm':8} edited tests verified passing works blocks turns ended")
    for record in records:
        print(f"{record['task']:24} {record['arm']:8} {str(record['edited']):6} {record['test_runs']:5} {str(record['verified']):8} "
              f"{str(record['passing']):7} {str(record['works']):5} {record['blocks']:6} {str(record['turns']):5} {record['ended']}")
    ok = True
    for arm in ("hook", "control"):
        rows = [record for record in records if record["arm"] == arm and record["ended"] == "success"]
        edited = [record for record in rows if record["edited"]]
        verified = sum(record["verified"] for record in edited)
        cost = sum(record["cost_usd"] or 0 for record in rows)
        print(f"{arm}: {len(rows)} finished session(s), {len(edited)} edited code, verified after the last edit "
              f"{verified}/{len(edited)}, tests passing {sum(r['passing'] for r in rows)}/{len(rows)}, task works "
              f"{sum(r['works'] for r in rows)}/{len(rows)}, hook blocks {sum(r['blocks'] for r in rows)}, cost ${cost:.2f}")
        if arm == "hook":
            ok = bool(rows) and verified == len(edited) and len(rows) == sum(1 for r in records if r["arm"] == arm)
    print(f"threshold (every hook-arm session finishes and ends verified): {'pass' if ok else 'FAIL'}; details: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

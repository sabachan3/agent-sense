"""Eval for the features check: does the state in features.json follow the verify commands?

    uv run --no-project --python 3.12 python evals/features_eval.py

Each scenario builds a small git repository with a feature list, plays a sequence of steps an
agent could take (run the list, break the code, edit a state by hand, weaken a command, clone),
and compares the findings and the states in the file with the known answer. The check passes
when every scenario matches exactly; a scenario with an unexpected finding fails like one with a
missing finding.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "features"))
sys.path.insert(0, str(ROOT))

import features_lib as LIB  # noqa: E402
from contracts import schema  # noqa: E402

PASS_RATE = 1.0
ADD = "grep -q 'def add' calc.py"
SUB = "grep -q 'def sub' calc.py"
CODE = "def add(a, b):\n    return a + b\n"
HANG = "sleep 30"
BACKSLASH = chr(92)
# A list as a person might have written it: compact, with escapes and number spellings json.dumps would change.
RAW = ('{"owner": "team", "features": [{"id": "add", "behavior": "adds ' + BACKSLASH + 'u65e5' + BACKSLASH + 'u200b", '
       '"weight": 1e3, "price": 2.50,\n    "state":   "active", "verify": "' + ADD + '"}], "version": 1}')


def entry(identity: str, state: str, verify: str | None = None) -> dict:
    item = {"id": identity, "behavior": f"{identity} is implemented", "state": state}
    if verify is not None:
        item["verify"] = verify
    return item


def write(repo: Path, features: list[dict]) -> None:
    (repo / "features.json").write_text(json.dumps({"version": 1, "features": features}, indent=2) + "\n", encoding="utf-8")


def edit(repo: Path, identity: str, **changes) -> None:
    data = json.loads((repo / "features.json").read_text(encoding="utf-8"))
    for item in data["features"]:
        if item["id"] == identity:
            item.update(changes)
    (repo / "features.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def check(repo: Path, config: dict | None = None, **options) -> dict:
    result = LIB.run(argparse.Namespace(root=repo, config=config or {}, **options))
    return schema.validate_result(result, LIB.RULES)


# Each scenario: start list, steps, then the expected (rule, severity, id) of the LAST check and the final states.
SCENARIOS = [
    {"name": "honest work: active feature verified, becomes passing",
     "start": [entry("add", "active", ADD)], "steps": [("run", {})],
     "expect": [], "states": {"add": "passing"}},
    {"name": "agent marks its feature passing by hand without running anything",
     "start": [entry("add", "active", ADD)], "steps": [("edit", ("add", {"state": "passing"})), ("read", {})],
     "expect": [("passing-unverified", "warn", "add")], "states": {"add": "passing"}},
    {"name": "agent marks a feature passing whose code does not exist; the run takes it back",
     "start": [entry("sub", "passing", SUB)], "steps": [("run", {})],
     "expect": [("feature-failing", "fail", "sub")], "states": {"sub": "active"}},
    {"name": "regression: a passing feature breaks later",
     "start": [entry("add", "active", ADD)],
     "steps": [("run", {}), ("code", "def plus(a, b):\n    return a + b\n"), ("run", {})],
     "expect": [("feature-failing", "fail", "add")], "states": {"add": "active"}},
    {"name": "after a failing run the state is edited back to passing",
     "start": [entry("sub", "active", SUB)], "steps": [("run", {}), ("edit", ("sub", {"state": "passing"})), ("read", {})],
     "expect": [("feature-failing", "fail", "sub")], "states": {"sub": "passing"}},
    {"name": "verify command weakened after it passed",
     "start": [entry("add", "active", ADD)], "steps": [("run", {}), ("edit", ("add", {"verify": "true"})), ("read", {})],
     "expect": [("passing-unverified", "warn", "add")], "states": {"add": "passing"}},
    {"name": "three features started at once",
     "start": [entry("add", "active", ADD), entry("sub", "active", SUB), entry("mul", "active", "false")],
     "steps": [("read", {})], "expect": [("wip-limit", "warn", None)],
     "states": {"add": "active", "sub": "active", "mul": "active"}},
    {"name": "feature in progress with no way to verify it",
     "start": [entry("add", "passing", ADD), entry("ui", "active")], "steps": [("run", {})],
     "expect": [("no-verify", "warn", "ui")], "states": {"add": "passing", "ui": "active"}},
    {"name": "fresh clone: the record is local, so passing is unverified until run",
     "start": [entry("add", "active", ADD)], "steps": [("run", {}), ("forget", None), ("read", {})],
     "expect": [("passing-unverified", "warn", "add")], "states": {"add": "passing"}},
    {"name": "fresh clone, then run: clean again",
     "start": [entry("add", "active", ADD)], "steps": [("run", {}), ("forget", None), ("run", {}), ("read", {})],
     "expect": [], "states": {"add": "passing"}},
    {"name": "backlog and blocked items are left alone",
     "start": [entry("add", "passing", ADD), entry("sub", "not_started", SUB), entry("div", "blocked", "false"),
               entry("idea", "not_started")],
     "steps": [("run", {})], "expect": [],
     "states": {"add": "passing", "sub": "not_started", "div": "blocked", "idea": "not_started"}},
    {"name": "starting a backlog item with --only: verified when its code exists",
     "start": [entry("add", "not_started", ADD), entry("sub", "not_started", SUB)],
     "steps": [("run", {"only": ["add"]})], "expect": [], "states": {"add": "passing", "sub": "not_started"}},
    {"name": "a state the tool does not know",
     "start": [entry("add", "done", ADD)], "steps": [("run", {})],
     "expect": [("features-invalid", "fail", None)], "states": {"add": "done"}},
    {"name": "two entries share an id",
     "start": [entry("add", "active", ADD), entry("add", "passing", "false")], "steps": [("read", {})],
     "expect": [("features-invalid", "fail", None)], "states": {"add": "passing"}},
    {"name": "wip limit raised in the config, in-progress failure is a warning when only reading",
     "start": [entry("add", "active", ADD), entry("sub", "active", SUB)],
     "steps": [("run", {}), ("edit", ("add", {"state": "active"})), ("read", {})], "config": {"wip": 2},
     "expect": [("feature-failing", "warn", "sub")], "states": {"add": "active", "sub": "active"}},
]


EXTRA = [
    {"name": "the local record is damaged: it counts as no record, the check does not crash",
     "start": [entry("add", "active", ADD)], "steps": [("run", {}), ("damage", "add"), ("read", {})],
     "expect": [("passing-unverified", "warn", "add")], "states": {"add": "passing"}},
    {"name": "a verify command that hangs is stopped at the timeout and the feature is taken back",
     "start": [entry("add", "passing", HANG)], "steps": [("run", {"timeout": 0.5})],
     "expect": [("feature-failing", "fail", "add")], "states": {"add": "active"}},
    {"name": "a run changes the state and leaves the rest of the file as it was written",
     "raw": RAW, "steps": [("run", {})],
     "expect": [], "states": {"add": "passing"}, "text": RAW.replace('"state":   "active"', '"state":   "passing"')},
]
SCENARIOS += EXTRA


def play(scenario: dict) -> tuple[list, dict]:
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
        (repo / "calc.py").write_text(CODE, encoding="utf-8")
        if "raw" in scenario:
            (repo / "features.json").write_bytes(scenario["raw"].encode("utf-8"))
        else:
            write(repo, scenario["start"])
        result = {"findings": []}
        for kind, value in scenario["steps"]:
            if kind == "run":
                result = check(repo, scenario.get("config"), run=True, **value)
            elif kind == "read":
                result = check(repo, scenario.get("config"))
            elif kind == "edit":
                edit(repo, value[0], **value[1])
            elif kind == "code":
                (repo / "calc.py").write_text(value, encoding="utf-8")
            elif kind == "forget":
                LIB.record_path(repo).unlink()
            elif kind == "damage":
                record = json.loads(LIB.record_path(repo).read_text(encoding="utf-8"))
                record["runs"][value]["tail"] = None
                LIB.record_path(repo).write_text(json.dumps(record), encoding="utf-8")
        found = sorted(((finding["rule"], finding["severity"], finding.get("detail", {}).get("id"))
                        for finding in result["findings"]), key=str)
        written = (repo / "features.json").read_bytes().decode("utf-8")
        states = {item["id"]: item["state"] for item in json.loads(written)["features"]}
        if "text" in scenario and written != scenario["text"]:
            states["(file text)"] = "differs from the expected text"
        return found, states


def main() -> int:
    passed, rows = 0, []
    for scenario in SCENARIOS:
        found, states = play(scenario)
        ok = found == sorted(scenario["expect"], key=str) and states == scenario["states"]
        passed += ok
        rows.append({"name": scenario["name"], "ok": ok, "found": found, "states": states})
        if not ok:
            print(f"MISS {scenario['name']}\n  expected {sorted(scenario['expect'], key=str)} {scenario['states']}\n"
                  f"  found    {found} {states}")
    rate = passed / len(SCENARIOS)
    print(f"features: {passed}/{len(SCENARIOS)} scenarios match (threshold {PASS_RATE:.0%})")
    out = ROOT / "runs" / "features_eval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"passed": passed, "total": len(SCENARIOS), "scenarios": rows}, indent=2) + "\n", encoding="utf-8")
    print(f"report: {out}")
    return 0 if rate >= PASS_RATE else 1


if __name__ == "__main__":
    raise SystemExit(main())

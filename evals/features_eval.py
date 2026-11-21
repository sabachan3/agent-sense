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
def nested(levels: int, state: str = "active") -> str:
    """A list whose unknown key `deep` brings the nesting to `levels` (object, list and feature are the first 3)."""
    return ('{"version": 1, "features": [{"id": "add", "behavior": "adds", "state": "' + state + '", "verify": "' + ADD
            + '", "deep": ' + "[" * (levels - 3) + "]" * (levels - 3) + "}]}")


SCENARIOS = [
    {"name": "unknown key nested exactly to the limit: verified, only the state is edited",
     "raw": nested(LIB.MAX_NESTING), "steps": [("run", {})], "expect": [],
     "states": {"add": "passing"}, "text": nested(LIB.MAX_NESTING, "passing")},
    {"name": "unknown key nested one level beyond the limit: invalid, nothing runs, file untouched",
     "raw": nested(LIB.MAX_NESTING + 1), "steps": [("run", {})], "expect": [("features-invalid", "fail", None)],
     "states": {"add": "active"}, "text": nested(LIB.MAX_NESTING + 1)},
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

ERROR = ("error", "exit 2", None)   # the check refused to run: RuntimeError, which the command turns into exit 2
# Found by the second independent review. `lock` makes the list or the record unwritable, as a
# read-only checkout or a full disk would.
SECOND = [
    {"name": "an id that ends in a newline is not an id",
     "start": [entry("add" + chr(10), "active", ADD)], "steps": [("run", {})],
     "expect": [("features-invalid", "fail", None)], "states": {"add" + chr(10): "active"}},
    {"name": "an id with dots, dashes and underscores is a normal id",
     "start": [entry("add.v2_x-1", "active", ADD)], "steps": [("run", {})],
     "expect": [], "states": {"add.v2_x-1": "passing"}},
    {"name": "a timeout of zero is refused and nothing is run",
     "start": [entry("add", "active", ADD)], "steps": [("run", {"timeout": 0})],
     "expect": [ERROR], "states": {"add": "active"}},
    {"name": "a timeout longer than a day is refused and nothing is run",
     "start": [entry("add", "active", ADD)], "steps": [("run", {"timeout": 1e10})],
     "expect": [ERROR], "states": {"add": "active"}},
    {"name": "the list cannot be written: no state changes and no run is recorded",
     "start": [entry("add", "active", ADD)], "steps": [("lock", "list"), ("run", {}), ("unlock", "list"), ("read", {})],
     "expect": [], "states": {"add": "active"}, "errors": 1},
    {"name": "the record cannot be written: the list is put back, so it does not claim what the record denies",
     "start": [entry("made", "active", "test -f made.txt")],
     "steps": [("run", {}), ("touch", "made.txt"), ("lock", "record"), ("run", {}), ("unlock", "record"), ("read", {})],
     "expect": [("feature-failing", "warn", "made")], "states": {"made": "active"}, "errors": 1},
]
SCENARIOS += SECOND

# A feature whose verification is costly (a model, a GPU, minutes of browser time) is marked manual.
MANUAL = [
    {"name": "a plain run leaves a manual feature out, names it, and still reports its unverified claim",
     "start": [entry("add", "active", ADD), {**entry("sub", "passing", SUB), "manual": True}], "steps": [("run", {})],
     "expect": [("manual-not-run", "info", None), ("passing-unverified", "warn", "sub")],
     "states": {"add": "passing", "sub": "passing"}},
    {"name": "--include-manual runs it and takes the false claim back",
     "start": [entry("add", "active", ADD), {**entry("sub", "passing", SUB), "manual": True}],
     "steps": [("run", {"include_manual": True})],
     "expect": [("feature-failing", "fail", "sub")], "states": {"add": "passing", "sub": "active"}},
    {"name": "--only runs a manual feature by name",
     "start": [{**entry("add", "active", ADD), "manual": True}], "steps": [("run", {"only": ["add"]})],
     "expect": [], "states": {"add": "passing"}},
]
SCENARIOS += MANUAL


def play(scenario: dict) -> tuple[list, dict]:
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
        (repo / "calc.py").write_text(CODE, encoding="utf-8")
        if "raw" in scenario:
            (repo / "features.json").write_bytes(scenario["raw"].encode("utf-8"))
        else:
            write(repo, scenario["start"])
        result, errors = {"findings": []}, 0
        locked = {"list": repo / "features.json", "record": LIB.record_path(repo).parent}
        for kind, value in scenario["steps"]:
            if kind == "run":
                try:
                    result = check(repo, scenario.get("config"), run=True, **value)
                except RuntimeError:
                    result, errors = {"findings": [{"rule": "error", "severity": "exit 2"}]}, errors + 1
            elif kind in ("lock", "unlock"):   # the record: its file and its directory, so neither edit nor create works
                paths = [locked[value]] + ([LIB.record_path(repo)] if value == "record" else [])
                for path in paths:
                    if path.exists():
                        path.chmod((0o555 if kind == "lock" else 0o755) if path.is_dir() else
                                   (0o444 if kind == "lock" else 0o644))
            elif kind == "touch":
                (repo / value).write_text("", encoding="utf-8")
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
        with LIB._deep():   # the parser of some Python versions counts nesting against the recursion limit
            states = {item["id"]: item["state"] for item in json.loads(written)["features"]}
        if "text" in scenario and written != scenario["text"]:
            states["(file text)"] = "differs from the expected text"
        if errors != scenario.get("errors", 1 if ERROR in scenario["expect"] else 0):
            states["(refusals)"] = f"{errors} runs were refused"
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

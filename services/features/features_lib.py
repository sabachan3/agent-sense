"""The feature list: what the project is supposed to do, and whether each item was verified.

`features.json` at the project root holds one entry per feature: what it does, the command that
proves it, and a state. An agent may write the first two. The state `passing` is only ever
written by `--run`, after the command exited 0, so "done" in the list means a command said so.

    agent-sense features          read the list and the local record of the last runs
    agent-sense features --run    run the verify command of every active or passing feature

See docs/features.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

NAME = "features"
SUMMARY = "the feature list: is every feature marked passing backed by a verification run?"
RULES = {"features-invalid": "fail", "feature-failing": "fail", "no-verify": "warn", "wip-limit": "warn",
         "passing-unverified": "warn", "no-feature-list": "info"}

STATES = ("not_started", "active", "blocked", "passing")
RUN_STATES = ("active", "passing")
DEFAULT_FILE = "features.json"
DEFAULT_WIP = 1
DEFAULT_TIMEOUT = 600
RECORD = "agent-sense/features.json"
OUTPUT_TAIL = 30
VERSION = 1
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _finding(rule: str, message: str, fix: str, file: str, severity: str | None = None, detail: dict | None = None) -> dict:
    return {"rule": rule, "severity": severity or RULES[rule], "message": message, "fix": fix, "file": file,
            "detail": detail or {}}


# ---------------------------------------------------------------- the local record of runs

def record_path(root: Path) -> Path:
    """Inside the git directory, so the record is local and never committed; beside the project otherwise."""
    try:
        done = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=root, capture_output=True, text=True,
                              timeout=30)
        if done.returncode == 0 and done.stdout.strip():
            return Path(done.stdout.strip()) / RECORD
    except (OSError, subprocess.SubprocessError):
        pass
    return root / ".agent-sense" / "features.json"


def read_record(root: Path) -> dict:
    try:
        data = json.loads(record_path(root).read_text(encoding="utf-8"))
        runs = data.get("runs") if isinstance(data, dict) else None
        return runs if isinstance(runs, dict) else {}
    except (OSError, ValueError):
        return {}


def write_record(root: Path, runs: dict) -> None:
    path = record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": VERSION, "runs": runs}), encoding="utf-8")


# ---------------------------------------------------------------- reading and validating the list

def load(path: Path, shown: str) -> tuple[dict | None, list[dict]]:
    """The parsed list and the reasons it cannot be used. Any reason means nothing else is checked."""
    fix = ('the file must look like {"version": 1, "features": [{"id": "login", "behavior": "one sentence", '
           '"verify": "command", "state": "not_started"}]}; states are ' + ", ".join(STATES))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, [_finding("features-invalid", f"{shown} cannot be read as JSON: {str(error)[:160]}", fix, shown)]
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        return None, [_finding("features-invalid", f"{shown} needs an object with a `features` list", fix, shown)]
    problems = []
    if "version" in data and data["version"] != VERSION:
        problems.append(f"version is {data['version']!r}, this tool reads version {VERSION}")
    seen = set()
    for index, feature in enumerate(data["features"]):
        if not isinstance(feature, dict):
            problems.append(f"entry {index + 1} is not an object")
            continue
        identity = feature.get("id")
        label = f"`{identity}`" if isinstance(identity, str) and identity else f"entry {index + 1}"
        if not isinstance(identity, str) or not _ID.match(identity):
            problems.append(f"{label} needs an id made of letters, digits, `-`, `_` or `.`")
        elif identity in seen:
            problems.append(f"the id `{identity}` is used more than once")
        else:
            seen.add(identity)
        if not isinstance(feature.get("behavior"), str) or not feature["behavior"].strip():
            problems.append(f"{label} has no behavior text")
        if feature.get("state") not in STATES:
            problems.append(f"{label} has the state {feature.get('state')!r}")
        if not isinstance(feature.get("verify", ""), str):
            problems.append(f"{label} has a verify value that is not text")
    return data, [_finding("features-invalid", f"{shown}: {problem}", fix, shown) for problem in problems]


def command_of(feature: dict) -> str:
    return (feature.get("verify") or "").strip()


# ---------------------------------------------------------------- running

def verify(root: Path, command: str, timeout: float) -> dict:
    started = time.time()
    try:
        done = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, errors="replace",
                              timeout=timeout)
        code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as error:
        partial = error.stdout if isinstance(error.stdout, str) else ""
        code, output = 124, f"{partial}\ntimed out after {timeout:g}s"
    return {"command": command, "exit": code, "passed": code == 0, "at": int(started),
            "seconds": round(time.time() - started, 2), "tail": output.strip().splitlines()[-OUTPUT_TAIL:]}


def run_features(root: Path, path: Path, shown: str, data: dict, only: list[str], timeout: float) -> tuple[list[dict], int]:
    """Run the verify commands, record the outcomes, and move the states. Returns findings and the number run."""
    runs = read_record(root)
    findings, ran, changed = [], 0, False
    for feature in data["features"]:
        identity, command = feature["id"], command_of(feature)
        chosen = identity in only if only else feature["state"] in RUN_STATES
        if not chosen or not command:
            continue
        before = feature["state"]
        outcome = verify(root, command, timeout)
        runs[identity] = outcome
        ran += 1
        if outcome["passed"]:
            after = "passing"
        else:
            after = "active" if before in RUN_STATES else before
            was = "was passing, now fails" if before == "passing" else "verification fails"
            findings.append(_finding(
                "feature-failing", f"`{identity}` {was}: `{command}` exited {outcome['exit']}",
                "make the command pass; do not edit the state or the command to get there", shown,
                detail={"id": identity, "exit": outcome["exit"], "was": before, "lines": outcome["tail"]}))
        if after != before:
            feature["state"], changed = after, True
    if ran:
        write_record(root, runs)
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return findings, ran


# ---------------------------------------------------------------- reading only

def record_findings(root: Path, shown: str, data: dict) -> list[dict]:
    runs = read_record(root)
    findings = []
    for feature in data["features"]:
        identity, command, state = feature["id"], command_of(feature), feature["state"]
        last = runs.get(identity) if isinstance(runs.get(identity), dict) else None
        if state not in RUN_STATES or not command:
            continue
        current = bool(last) and last.get("command") == command
        if current and not last.get("passed"):
            marked = state == "passing"
            findings.append(_finding(
                "feature-failing",
                f"`{identity}` is marked passing but its last verification failed (exit {last.get('exit')})" if marked
                else f"`{identity}` is in progress and its last verification failed (exit {last.get('exit')})",
                "make `" + command + "` pass, then run `agent-sense features --run`", shown,
                severity=None if marked else "warn",
                detail={"id": identity, "exit": last.get("exit"), "lines": list(last.get("tail", []))[-OUTPUT_TAIL:]}))
        elif state == "passing" and not current:
            why = "the verify command changed since its last run" if last else "no verification run is recorded here"
            findings.append(_finding(
                "passing-unverified", f"`{identity}` is marked passing but {why}",
                "run `agent-sense features --run`; the record is local, so a fresh clone starts without one", shown,
                detail={"id": identity}))
    return findings


def list_findings(shown: str, data: dict, wip: int) -> list[dict]:
    findings = []
    for feature in data["features"]:
        if feature["state"] in RUN_STATES and not command_of(feature):
            findings.append(_finding(
                "no-verify", f"`{feature['id']}` is {feature['state']} but has no verify command",
                "add the command whose exit code 0 proves the behavior", shown, detail={"id": feature["id"]}))
    active = [feature["id"] for feature in data["features"] if feature["state"] == "active"]
    if len(active) > wip:
        findings.append(_finding(
            "wip-limit", f"{len(active)} features are active at once ({', '.join(active[:8])}); the limit is {wip}",
            "finish or block one before starting the next, or raise `wip` in the config", shown,
            detail={"active": active, "limit": wip}))
    return findings


def stats_of(data: dict, ran: int) -> dict:
    counts = {state: sum(1 for feature in data["features"] if feature["state"] == state) for state in STATES}
    started = len(data["features"]) - counts["not_started"]
    return {"features": len(data["features"]), **counts, "ran": ran,
            "verified_completion_rate": round(counts["passing"] / started, 4) if started else None}


def add_arguments(parser) -> None:
    parser.add_argument("--run", action="store_true",
                        help="run the verify command of every active or passing feature and update the states")
    parser.add_argument("--only", action="append", metavar="ID",
                        help="with --run: run just this feature, whatever its state (repeatable)")
    parser.add_argument("--timeout", type=float, metavar="SEC",
                        help=f"seconds one verify command may take (default {DEFAULT_TIMEOUT})")


def run(args) -> dict:
    root = Path(args.root)
    if not root.is_dir():
        raise RuntimeError(f"not a directory: {root}")
    config = getattr(args, "config", None) or {}
    shown = str(config.get("file") or DEFAULT_FILE)
    path = root / shown
    only = list(getattr(args, "only", None) or [])
    running = bool(getattr(args, "run", False))
    if only and not running:
        raise RuntimeError("--only needs --run")
    if not path.is_file():
        if running:
            raise RuntimeError(f"{shown} not found in {root}; there is nothing to run")
        return {"findings": [_finding("no-feature-list", f"this project has no {shown}",
                                      "list each feature with the command that verifies it; see docs/features.md", shown)],
                "stats": {"features": 0}}
    data, invalid = load(path, shown)
    if invalid:
        return {"findings": invalid, "stats": {"features": 0}}
    unknown = [identity for identity in only if identity not in {feature["id"] for feature in data["features"]}]
    if unknown:
        raise RuntimeError(f"no feature with the id {', '.join(unknown)} in {shown}")
    missing = [identity for identity in only
               if not command_of(next(feature for feature in data["features"] if feature["id"] == identity))]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} has no verify command to run")
    wip = config.get("wip", DEFAULT_WIP)
    wip = wip if isinstance(wip, int) and not isinstance(wip, bool) and wip >= 0 else DEFAULT_WIP
    timeout = getattr(args, "timeout", None) or config.get("timeout") or DEFAULT_TIMEOUT
    ran = 0
    if running:
        findings, ran = run_features(root, path, shown, data, only, float(timeout))
        if only:   # the features not run still answer for their own record
            others = {"features": [feature for feature in data["features"] if feature["id"] not in only]}
            findings += record_findings(root, shown, others)
    else:
        findings = record_findings(root, shown, data)
    findings += list_findings(shown, data, wip)
    severity = config.get("severity", {}) if isinstance(config.get("severity"), dict) else {}
    kept = []
    for finding in findings:
        override = severity.get(finding["rule"])
        if override == "off":
            continue
        if override in ("fail", "warn", "info"):
            finding["severity"] = override
        kept.append(finding)
    return {"findings": kept, "stats": stats_of(data, ran)}

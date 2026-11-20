"""The feature list: what the project is supposed to do, and whether each item was verified.

`features.json` at the project root holds one entry per feature: what it does, the command that
proves it, and a state. An agent may write the first two. The state `passing` is only ever
written by `--run`, after the command exited 0, so "done" in the list means a command said so.

    agent-sense features          read the list and the local record of the last runs
    agent-sense features --run    run the verify command of every active or passing feature

See docs/features.md.
"""

from __future__ import annotations

import functools
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
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
MAX_TIMEOUT = 86400     # one day; beyond it the number is a mistake, and huge values overflow the OS timer
RECORD = "agent-sense/features.json"
OUTPUT_TAIL = 30
DEEP = 3000             # recursion allowed while a list is parsed or rewritten; a list nested deeper is invalid
VERSION = 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")   # used with fullmatch: `$` would accept a trailing newline


def _finding(rule: str, message: str, fix: str, file: str, severity: str | None = None, detail: dict | None = None) -> dict:
    return {"rule": rule, "severity": severity or RULES[rule], "message": message, "fix": fix, "file": file,
            "detail": detail or {}}


# ---------------------------------------------------------------- the local record of runs

@functools.lru_cache(maxsize=None)   # one git call per project and process, not one per read and write
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


def valid_run(entry) -> bool:
    """A run this tool could have written. Anything else in the record is treated as no record."""
    return (isinstance(entry, dict) and isinstance(entry.get("command"), str) and isinstance(entry.get("passed"), bool)
            and isinstance(entry.get("exit"), int) and not isinstance(entry.get("exit"), bool)
            and isinstance(entry.get("tail"), list) and all(isinstance(line, str) for line in entry["tail"]))


def read_record(root: Path) -> dict:
    """The recorded runs by feature id. A missing, unreadable or damaged record, or entry, counts as none."""
    try:
        data = json.loads(record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, dict):
        return {}
    return {identity: entry for identity, entry in runs.items() if valid_run(entry)}


def write_record(root: Path, runs: dict) -> None:
    path = record_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": VERSION, "runs": runs}), encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"cannot write the record of runs to {path}: {error}") from error


# ---------------------------------------------------------------- reading and validating the list

class _deep:
    """Unknown keys may nest deeper than the default recursion limit allows (on Python 3.11 the JSON
    parser itself counts against it). Raise the limit to a fixed, modest depth while a list is
    parsed or rewritten; beyond that depth the list is reported as invalid, never a crash."""

    def __enter__(self):
        self.limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(self.limit, DEEP))

    def __exit__(self, *_error):
        sys.setrecursionlimit(self.limit)
        return False


def read_text(path: Path) -> str:
    """The file exactly as written: no newline translation, so a rewrite can leave the rest alone."""
    return path.read_bytes().decode("utf-8")


def load(path: Path, shown: str) -> tuple[dict | None, list[dict]]:
    """The parsed list and the reasons it cannot be used. Any reason means nothing else is checked."""
    fix = ('the file must look like {"version": 1, "features": [{"id": "login", "behavior": "one sentence", '
           '"verify": "command", "state": "not_started"}]}; states are ' + ", ".join(STATES))
    try:
        with _deep():
            data = json.loads(read_text(path))
    except (OSError, ValueError, RecursionError) as error:
        why = "it is nested too deeply" if isinstance(error, RecursionError) else str(error)[:160]
        return None, [_finding("features-invalid", f"{shown} cannot be read as JSON: {why}", fix, shown)]
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
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            problems.append(f"{label} needs an id that starts with a letter or digit and goes on with letters, "
                            "digits, `-`, `_` or `.`")
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

def _kill_tree(process: subprocess.Popen) -> None:
    """Stop the command and everything it started; a shell that is killed alone leaves its children running."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def execute(root: Path, command: str, timeout: float) -> tuple[int, str]:
    """Exit code and combined output of a shell command. A timeout is exit 124 with the output so far."""
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        process = subprocess.Popen(command, shell=True, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, **group)
    except (OSError, ValueError) as error:   # ValueError: a NUL or an unencodable character in the command
        return 127, f"could not start: {type(error).__name__}: {str(error)[:200]}"
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output.decode("utf-8", "replace")
    except OverflowError as error:   # a limit the OS timer cannot hold; timeout_of refuses these, this is the backstop
        _kill_tree(process)
        raise RuntimeError(f"the timeout of {timeout:g}s is too large") from error
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:   # something still holds the pipe; give up on the output, not on the verdict
            output = b""
        return 124, f"{(output or b'').decode('utf-8', 'replace')}\ntimed out after {timeout:g}s"
    except BaseException:   # interrupted: the command is in its own group, so it would not see the Ctrl-C itself
        _kill_tree(process)
        raise


class _Signalled(BaseException):
    """SIGHUP or SIGTERM arrived while a verify command was running."""

    def __init__(self, number: int):
        super().__init__(number)
        self.number = number


def _raise_signalled(number, _frame):
    raise _Signalled(number)


class _signals:
    """While a verify command runs it sits in its own process group, so a hangup or a termination
    sent to this tool would leave it running. Turn those signals into an exception, which `execute`
    answers by killing the group, then let the signal end this process as it would have.
    POSIX and the main thread only; a signal the caller already ignores or handles is left alone."""

    def __enter__(self):
        self.previous = {}
        if os.name != "nt" and threading.current_thread() is threading.main_thread():
            for number in (signal.SIGHUP, signal.SIGTERM):
                if signal.getsignal(number) is signal.SIG_DFL:
                    self.previous[number] = signal.signal(number, _raise_signalled)
        return self

    def __exit__(self, kind, error, _trace):
        for number, handler in self.previous.items():
            signal.signal(number, handler)
        if isinstance(error, _Signalled):
            os.kill(os.getpid(), error.number)   # default handling is back: end the way the signal asked
        return False


def verify(root: Path, command: str, timeout: float) -> dict:
    started = time.time()
    with _signals():
        code, output = execute(root, command, timeout)
    return {"command": command, "exit": code, "passed": code == 0, "at": int(started),
            "seconds": round(time.time() - started, 2), "tail": output.strip().splitlines()[-OUTPUT_TAIL:]}


# ---------------------------------------------------------------- rewriting the list

_SPACE = " \t\r\n"


def _skip(text: str, at: int) -> int:
    while text[at] in _SPACE:
        at += 1
    return at


def _string_end(text: str, at: int) -> int:
    at += 1
    while text[at] != '"':
        at += 2 if text[at] == "\\" else 1
    return at + 1


def _value_end(text: str, at: int, path: tuple, spans: dict) -> int:
    """Walk one JSON value starting at `at`, noting where the `state` value of each feature object sits."""
    if text[at] == '"':
        return _string_end(text, at)
    if text[at] == "{":
        at = _skip(text, at + 1)
        while text[at] != "}":
            end = _string_end(text, at)
            key = json.loads(text[at:end])
            at = _skip(text, end)
            if text[at] != ":":
                raise ValueError("expected a colon")
            start = _skip(text, at + 1)
            at = _value_end(text, start, path + (key,), spans)
            if len(path) == 2 and path[0] == "features" and key == "state":
                spans[path[1]] = (start, at)   # a repeated key: the last one wins, as in json.loads
            at = _skip(text, at)
            if text[at] == ",":
                at = _skip(text, at + 1)
        return at + 1
    if text[at] == "[":
        at, index = _skip(text, at + 1), 0
        while text[at] != "]":
            at = _skip(text, _value_end(text, at, path + (index,), spans))
            if text[at] == ",":
                at = _skip(text, at + 1)
            index += 1
        return at + 1
    while at < len(text) and text[at] not in _SPACE + ",]}":
        at += 1
    return at


def with_states(text: str, data: dict) -> str:
    """`text` with only the changed `state` values replaced, so escapes, number spelling and layout
    stay as written. When the edited text does not parse to exactly `data`, the whole list is written
    out again instead: a file that is right matters more than a file that looks untouched."""
    with _deep():
        return _with_states(text, data)


def _with_states(text: str, data: dict) -> str:
    try:
        spans: dict = {}
        _value_end(text, _skip(text, 0), (), spans)
        original = json.loads(text)
        edits = [(spans[index], feature["state"]) for index, feature in enumerate(data["features"])
                 if original["features"][index].get("state") != feature["state"]]
        for (start, end), state in sorted(edits, reverse=True):
            text = text[:start] + json.dumps(state) + text[end:]
        if json.loads(text) == data:
            return text
    except (IndexError, KeyError, ValueError, TypeError, AttributeError, RecursionError):
        pass   # RecursionError: nesting deeper than this walker can follow
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def run_features(root: Path, path: Path, shown: str, data: dict, only: list[str], timeout: float,
                 text: str) -> tuple[list[dict], int]:
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
    # The list first: if it cannot be written, the record must not say more than the list shows.
    original = text.encode("utf-8")
    if changed:
        try:
            path.write_bytes(with_states(text, data).encode("utf-8"))
        except (OSError, ValueError, RecursionError) as error:
            raise RuntimeError(f"cannot write {shown}: {error}; no state was changed and no run was recorded") from error
    if ran:
        try:
            write_record(root, runs)
        except RuntimeError as error:
            # And the other way round: a list that says passing over a record that still says failed
            # would be reported as a lie on the next read. Put the list back.
            if changed:
                try:
                    path.write_bytes(original)
                except OSError as second:
                    raise RuntimeError(f"{error}; {shown} was already rewritten and could not be put back "
                                       f"({second}), so the list and the record disagree") from error
                raise RuntimeError(f"{error}; {shown} was put back as it was, no state was changed") from error
            raise
    return findings, ran


# ---------------------------------------------------------------- reading only

def record_findings(root: Path, shown: str, data: dict) -> list[dict]:
    runs = read_record(root)
    findings = []
    for feature in data["features"]:
        identity, command, state = feature["id"], command_of(feature), feature["state"]
        last = runs.get(identity)
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


def timeout_of(given, configured) -> float:
    """Seconds one verify command may take. Unset means the default; zero, negative, not a number or more
    than a day is an error, because a limit that is silently ignored lets a hanging command pass for a
    slow one. Both values are checked even though only the first one given is used."""
    chosen = None
    for value, source in ((given, "--timeout"), (configured, "`timeout` in the config")):
        if value is None:
            continue
        number = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not number or value <= 0 or value > MAX_TIMEOUT:
            raise RuntimeError(f"{source} must be a number of seconds above 0 and at most {MAX_TIMEOUT}, got {value!r}")
        chosen = float(value) if chosen is None else chosen
    return float(DEFAULT_TIMEOUT) if chosen is None else chosen


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
    # Checked on a plain read too: a config that `--run` would refuse must not look fine until then.
    timeout = timeout_of(getattr(args, "timeout", None) if running else None, config.get("timeout"))
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
    ran = 0
    if running:
        try:
            text = read_text(path)
        except (OSError, ValueError) as error:
            raise RuntimeError(f"cannot read {shown}: {error}") from error
        try:   # before any command runs: can this list be written back at all?
            with_states(text, data)
        except (ValueError, RecursionError) as error:
            raise RuntimeError(f"{shown} cannot be written back ({type(error).__name__}: {str(error)[:120]}); "
                               "nothing was run") from error
        findings, ran = run_features(root, path, shown, data, only, timeout, text)
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

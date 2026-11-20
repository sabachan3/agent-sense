import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import features_lib
import pytest

# Commands built from the running interpreter, so they mean the same under sh and under cmd.exe.
# Only double quotes are used inside them; neither shell changes text in double quotes here.
OK = f'"{sys.executable}" -c "pass"'
BAD = f'"{sys.executable}" -c "print(\'boom\'); raise SystemExit(3)"'
SLOW = f'"{sys.executable}" -c "import time; time.sleep(3)"'


def project(tmp_path: Path, features, git: bool = False, name: str = "features.json") -> Path:
    if git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    if features is not None:
        text = features if isinstance(features, str) else json.dumps({"version": 1, "features": features}, indent=2) + "\n"
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def feature(identity: str, state: str = "active", verify: str | None = OK, **extra) -> dict:
    entry = {"id": identity, "behavior": f"{identity} works", "state": state, **extra}
    if verify is not None:
        entry["verify"] = verify
    return entry


def check(root: Path, config: dict | None = None, **options) -> dict:
    return features_lib.run(argparse.Namespace(root=root, config=config or {}, **options))


def rules(result: dict) -> list[tuple[str, str]]:
    return [(finding["rule"], finding["severity"]) for finding in result["findings"]]


def states(root: Path, name: str = "features.json") -> dict:
    return {item["id"]: item["state"] for item in json.loads((root / name).read_text(encoding="utf-8"))["features"]}


def test_interface_matches_the_contract():
    assert features_lib.NAME == "features" and features_lib.SUMMARY
    assert set(features_lib.RULES) == {"features-invalid", "feature-failing", "no-verify", "wip-limit",
                                       "passing-unverified", "no-feature-list"}
    parser = argparse.ArgumentParser()
    features_lib.add_arguments(parser)
    assert parser.parse_args([]).run is False


def test_no_feature_list_is_information_and_never_a_failure(tmp_path):
    result = check(project(tmp_path, None))
    assert rules(result) == [("no-feature-list", "info")]
    with pytest.raises(RuntimeError):
        check(tmp_path, run=True)


@pytest.mark.parametrize("text, part", [
    ("{not json", "cannot be read as JSON"),
    ("[]", "needs an object"),
    ('{"features": {}}', "needs an object"),
    (json.dumps({"version": 2, "features": []}), "version is 2"),
    (json.dumps({"features": [{"id": "a", "behavior": "x", "state": "done"}]}), "has the state 'done'"),
    (json.dumps({"features": [{"id": "a", "behavior": " ", "state": "active"}]}), "no behavior"),
    (json.dumps({"features": [{"id": "a b", "behavior": "x", "state": "active"}]}), "needs an id"),
    (json.dumps({"features": [{"behavior": "x", "state": "active"}]}), "entry 1 needs an id"),
    (json.dumps({"features": [{"id": "a", "behavior": "x", "state": "active"}] * 2}), "used more than once"),
    (json.dumps({"features": [{"id": "a", "behavior": "x", "state": "active", "verify": ["x"]}]}), "not text"),
    (json.dumps({"features": ["a"]}), "entry 1 is not an object"),
])
def test_invalid_lists_fail_and_nothing_is_run(tmp_path, text, part):
    root = project(tmp_path, text)
    for options in ({}, {"run": True}):
        result = check(root, **options)
        assert {rule for rule, _ in rules(result)} == {"features-invalid"}
        assert all(severity == "fail" for _, severity in rules(result))
        assert any(part in finding["message"] for finding in result["findings"])
    assert (root / "features.json").read_text(encoding="utf-8") == text


def test_run_moves_states_only_by_the_exit_code(tmp_path):
    root = project(tmp_path, [feature("a", "active"), feature("b", "passing", BAD), feature("c", "active", BAD),
                              feature("d", "not_started", "exit 9"), feature("e", "blocked", "exit 9")], git=True)
    result = check(root, run=True)
    assert states(root) == {"a": "passing", "b": "active", "c": "active", "d": "not_started", "e": "blocked"}
    assert rules(result) == [("feature-failing", "fail"), ("feature-failing", "fail"), ("wip-limit", "warn")]
    regressed, failing = result["findings"][:2]
    assert "`b` was passing, now fails" in regressed["message"] and "exited 3" in regressed["message"]
    assert regressed["detail"]["lines"] == ["boom"] and "`c` verification fails" in failing["message"]
    assert result["stats"] == {"features": 5, "not_started": 1, "active": 2, "blocked": 1, "passing": 1, "ran": 3,
                               "verified_completion_rate": 0.25}
    record = json.loads((root / ".git" / "agent-sense" / "features.json").read_text(encoding="utf-8"))["runs"]
    assert set(record) == {"a", "b", "c"} and record["a"]["passed"] and record["b"]["exit"] == 3
    assert record["b"]["command"] == BAD and record["b"]["tail"] == ["boom"] and record["b"]["at"] > 0


def test_read_only_reports_what_the_record_says(tmp_path):
    root = project(tmp_path, [feature("a", "active"), feature("b", "passing", BAD)], git=True)
    check(root, run=True)
    assert (root / ".git" / "agent-sense" / "features.json").is_file()
    assert rules(check(root)) == [("feature-failing", "warn")]         # b is in progress, its last run failed
    data = json.loads((root / "features.json").read_text(encoding="utf-8"))
    assert rules(check(root, config={"wip": 0})) == [("feature-failing", "warn"), ("wip-limit", "warn")]
    data["features"][1]["state"] = "passing"                          # edited by hand after a failing run
    (root / "features.json").write_text(json.dumps(data), encoding="utf-8")
    result = check(root)
    assert rules(result) == [("feature-failing", "fail")]
    assert "marked passing but its last verification failed" in result["findings"][0]["message"]


def test_passing_without_a_matching_record_is_unverified(tmp_path):
    root = project(tmp_path, [feature("a", "passing"), feature("b", "passing")], git=True)
    result = check(root)
    assert rules(result) == [("passing-unverified", "warn")] * 2
    assert "no verification run is recorded" in result["findings"][0]["message"]
    check(root, run=True)
    assert rules(check(root)) == []
    data = json.loads((root / "features.json").read_text(encoding="utf-8"))
    data["features"][0]["verify"] = "true  # weaker"
    (root / "features.json").write_text(json.dumps(data), encoding="utf-8")
    result = check(root)
    assert rules(result) == [("passing-unverified", "warn")]
    assert "command changed" in result["findings"][0]["message"] and result["findings"][0]["detail"]["id"] == "a"


def test_no_verify_and_wip_limit(tmp_path):
    root = project(tmp_path, [feature("a", "active", None), feature("b", "passing", ""), feature("c", "active"),
                              feature("d", "not_started", None), feature("e", "blocked", None)])
    assert rules(check(root)) == [("passing-unverified", "warn")] * 0 + [("no-verify", "warn")] * 2 + [("wip-limit", "warn")]
    assert rules(check(root, config={"wip": 2})) == [("no-verify", "warn")] * 2
    assert rules(check(root, config={"wip": 2, "severity": {"no-verify": "off"}})) == []
    assert rules(check(root, config={"wip": 2, "severity": {"no-verify": "fail"}})) == [("no-verify", "fail")] * 2
    result = check(root, run=True, config={"wip": 2})                 # features without a command are not run
    assert result["stats"]["ran"] == 1 and states(root)["a"] == "active" and states(root)["b"] == "passing"


def test_only_runs_the_named_feature_whatever_its_state(tmp_path):
    root = project(tmp_path, [feature("a", "not_started"), feature("b", "blocked", BAD), feature("c", "passing", BAD),
                              feature("n", "active", None)], git=True)
    result = check(root, run=True, only=["a", "b"])
    assert states(root) == {"a": "passing", "b": "blocked", "c": "passing", "n": "active"}
    assert result["stats"]["ran"] == 2
    assert rules(result) == [("feature-failing", "fail"), ("passing-unverified", "warn"), ("no-verify", "warn")]
    for options in ({"only": ["a"]}, {"run": True, "only": ["zzz"]}, {"run": True, "only": ["n"]}):
        with pytest.raises(RuntimeError):
            check(root, **options)


def test_rewrite_keeps_order_and_unknown_keys_and_happens_only_on_change(tmp_path):
    text = ('{"version": 1, "owner": "team",\n "features": [\n  {"state": "active", "note": "日本語", "id": "a", '
            '"behavior": "x", "verify": VERIFY},\n  {"id": "b", "behavior": "y", "state": "passing", "verify": VERIFY}]}\n'
            ).replace("VERIFY", json.dumps(OK))
    root = project(tmp_path, text.replace("active", "passing"), git=True)
    check(root, run=True)
    assert (root / "features.json").read_text(encoding="utf-8") == text.replace("active", "passing")   # untouched
    (root / "features.json").write_text(text, encoding="utf-8")
    check(root, run=True)
    written = (root / "features.json").read_text(encoding="utf-8")
    data = json.loads(written)
    assert list(data) == ["version", "owner", "features"] and data["owner"] == "team"
    assert list(data["features"][0]) == ["state", "note", "id", "behavior", "verify"]
    assert data["features"][0]["state"] == "passing" and "日本語" in written and written.endswith("}\n")


def test_timeout_counts_as_a_failure_and_config_names_the_file(tmp_path):
    root = project(tmp_path, [feature("slow", "passing", SLOW)], git=True, name="plan.json")
    result = check(root, config={"file": "plan.json"}, run=True, timeout=0.2)
    assert rules(result) == [("feature-failing", "fail")] and result["findings"][0]["detail"]["exit"] == 124
    assert result["findings"][0]["file"] == "plan.json" and states(root, "plan.json") == {"slow": "active"}
    assert rules(check(root)) == [("no-feature-list", "info")]


def test_record_lives_beside_the_project_outside_git_and_a_broken_record_is_ignored(tmp_path):
    root = project(tmp_path, [feature("a", "active")])
    check(root, run=True)
    assert (root / ".agent-sense" / "features.json").is_file() and rules(check(root)) == []
    (root / ".agent-sense" / "features.json").write_text("{broken", encoding="utf-8")
    assert rules(check(root)) == [("passing-unverified", "warn")]


# ---- regressions from the first independent review (each fails on the code before the fix)

def test_bad_timeout_in_config_is_a_clean_error_not_a_traceback(tmp_path):
    root = project(tmp_path, [feature("a")])
    for bad in ("abc", True, [5], float("nan")):
        with pytest.raises(RuntimeError, match="timeout"):
            check(root, config={"timeout": bad}, run=True)
    assert states(root) == {"a": "active"}                      # nothing ran
    assert check(root, config={"timeout": 30}, run=True)["stats"]["ran"] == 1


@pytest.mark.parametrize("where", ["argument", "config"])
@pytest.mark.parametrize("value", [0, -1, 0.0])
def test_zero_or_negative_timeout_is_rejected_not_ignored(tmp_path, where, value):
    root = project(tmp_path, [feature("a")])
    options = {"timeout": value} if where == "argument" else {}
    config = {"timeout": value} if where == "config" else {}
    with pytest.raises(RuntimeError, match="above 0"):
        check(root, config=config, run=True, **options)
    assert states(root) == {"a": "active"}


@pytest.mark.parametrize("damage", [{"tail": None}, {"tail": 5}, {"tail": [1]}, {"passed": "yes"}, {"exit": "1"},
                                    {"tail": ["x", None]}, {"exit": True}])
def test_a_damaged_record_entry_counts_as_no_record(tmp_path, damage):
    root = project(tmp_path, [feature("a", "passing"), feature("b", "passing")])
    good = {"command": OK, "exit": 0, "passed": True, "at": 1, "seconds": 0.1, "tail": []}
    features_lib.write_record(root, {"a": {**good, "exit": 1, "passed": False, **damage}, "b": good})
    result = check(root)                                         # used to raise TypeError for tail: null
    assert rules(result) == [("passing-unverified", "warn")] and result["findings"][0]["detail"]["id"] == "a"


def test_an_id_with_a_trailing_newline_is_invalid(tmp_path):
    root = project(tmp_path, [feature("a\n")])
    assert rules(check(root)) == [("features-invalid", "fail")]
    assert rules(check(root, run=True)) == [("features-invalid", "fail")]


def test_unwritable_list_is_a_clean_error_and_the_record_claims_nothing(tmp_path, monkeypatch):
    root = project(tmp_path, [feature("a", "active")], git=True)
    target = root / "features.json"
    real = Path.write_bytes

    def refuse(self, data):   # a read-only file, without depending on what this user or platform may overwrite
        if self == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", refuse)
    with pytest.raises(RuntimeError, match="cannot write features.json"):
        check(root, run=True)
    assert states(root) == {"a": "active"}
    assert "a" not in features_lib.read_record(root)             # the record used to say passed while the list said active


def test_unwritable_record_is_a_clean_error(tmp_path, monkeypatch):
    root = project(tmp_path, [feature("a", "passing")])
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the record directory should be", encoding="utf-8")
    monkeypatch.setattr(features_lib, "record_path", lambda _root: blocker / "features.json")
    with pytest.raises(RuntimeError, match="cannot write the record"):
        check(root, run=True)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in listed
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_timeout_stops_the_children_too_and_keeps_the_output_so_far(tmp_path):
    spawn = ("import subprocess, sys, time; "
             "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
             "print('child', child.pid, flush=True); time.sleep(60)")
    root = project(tmp_path, [feature("slow", "passing", f'"{sys.executable}" -c "{spawn}"')])
    # Long enough for the command to start its child and print; slow shared runners get more.
    limit = 5 if os.environ.get("CI") or os.name == "nt" else 0.6
    result = check(root, run=True, timeout=limit)
    detail = result["findings"][0]["detail"]
    assert detail["exit"] == 124 and any("timed out" in line for line in detail["lines"])
    started = [line for line in detail["lines"] if line.startswith("child ")]
    assert started, detail["lines"]                              # the output before the timeout used to be lost
    pid = int(started[0].split()[1])
    deadline = time.time() + 5
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)                                       # the child used to outlive the timeout


BACKSLASH = chr(92)


def test_rewrite_changes_only_the_state_values(tmp_path):
    escaped = BACKSLASH + "u200b" + BACKSLASH + "u65e5"          # escapes as the author wrote them
    text = ('{"features":[{"id":"a","behavior":"x ' + escaped + '","weight":1e3,"price":2.50,"state":  "active",'
            '"verify":' + json.dumps(OK) + ',"nested":{"state":"active","list":[1,{"state":"active"}]}},\r\n'
            '\t{"state":"blocked","id":"b","behavior":"says \\"state\\": \\"active\\" in text"},'
            '{"id":"c","state":"active","behavior":"y","verify":' + json.dumps(OK) + '}],"state":"active","version":1}')
    root = project(tmp_path, None, git=True)
    (root / "features.json").write_bytes(text.encode("utf-8"))
    check(root, run=True)
    expected = text.replace('"state":  "active"', '"state":  "passing"').replace(
        '{"id":"c","state":"active"', '{"id":"c","state":"passing"')
    assert (root / "features.json").read_bytes().decode("utf-8") == expected


def test_rewrite_falls_back_to_a_full_dump_when_the_edit_would_not_give_the_data(tmp_path):
    text = '{"features": [{"id": "a", "behavior": "x", "state": "active"}]}'
    wanted = {"features": [{"id": "a", "behavior": "changed elsewhere", "state": "passing"}]}
    assert json.loads(features_lib.with_states(text, wanted)) == wanted
    repeated = '{"features": [{"state": "blocked", "id": "a", "behavior": "x", "state": "active"}]}'
    wanted = {"features": [{"state": "passing", "id": "a", "behavior": "x"}]}
    assert json.loads(features_lib.with_states(repeated, wanted)) == wanted   # the last duplicate is the one read


# ---- regressions from the second independent review

@pytest.mark.parametrize("where", ["argument", "config"])
@pytest.mark.parametrize("value", [1e10, 9e9, 1e300, 86401])
def test_a_timeout_beyond_a_day_is_rejected_not_a_traceback(tmp_path, where, value):
    root = project(tmp_path, [feature("a")])
    options = {"timeout": value} if where == "argument" else {}
    config = {"timeout": value} if where == "config" else {}
    with pytest.raises(RuntimeError, match="at most 86400"):      # used to be OverflowError from the OS timer
        check(root, config=config, run=True, **options)
    assert states(root) == {"a": "active"}
    assert features_lib.timeout_of(features_lib.MAX_TIMEOUT, None) == features_lib.MAX_TIMEOUT


def test_a_bad_config_timeout_is_an_error_on_a_plain_read_too(tmp_path):
    root = project(tmp_path, [feature("a")])
    for bad in ("abc", 0, -3, float("inf")):
        with pytest.raises(RuntimeError, match="timeout"):       # a read used to print OK and exit 0
            check(root, config={"timeout": bad})
    with pytest.raises(RuntimeError, match="config"):            # both values are checked, not only the one used
        check(root, config={"timeout": "abc"}, run=True, timeout=5)
    assert rules(check(root, config={"timeout": 30})) == []
    assert rules(check(root, timeout=0)) == []                   # --timeout without --run is not used, so not judged


def test_a_command_that_cannot_be_started_fails_its_feature_and_keeps_the_others(tmp_path):
    root = project(tmp_path, [feature("a", "passing"), feature("b", "passing", "echo " + chr(0)), feature("c", "active")])
    result = check(root, run=True)                               # used to be a ValueError traceback, losing a and c
    assert states(root) == {"a": "passing", "b": "active", "c": "passing"}
    failing = [finding for finding in result["findings"] if finding["rule"] == "feature-failing"]
    assert len(failing) == 1 and failing[0]["detail"]["exit"] == 127
    assert any("could not start" in line for line in failing[0]["detail"]["lines"])
    record = features_lib.read_record(root)
    assert record["a"]["passed"] and record["c"]["passed"] and record["b"]["exit"] == 127


def test_nesting_deeper_than_the_walker_can_follow_still_writes_the_state(tmp_path):
    deep = "[" * 1100 + "]" * 1100
    text = ('{"features": [{"id": "a", "behavior": "x", "state": "active", "verify": ' + json.dumps(OK) +
            ', "deep": ' + deep + "}]}")
    root = project(tmp_path, text)
    result = check(root, run=True)                               # used to run the command, then exit 2 and write nothing
    assert rules(result) == [] and states(root) == {"a": "passing"}
    assert features_lib.read_record(root)["a"]["passed"]


def test_a_record_that_cannot_be_written_puts_the_list_back(tmp_path, monkeypatch):
    root = project(tmp_path, [feature("a", "active")])
    before = (root / "features.json").read_bytes()
    features_lib.write_record(root, {"a": {"command": OK, "exit": 1, "passed": False, "at": 1, "seconds": 0.1, "tail": []}})

    def refuse(_root, _runs):
        raise RuntimeError("cannot write the record of runs to somewhere: read-only")

    monkeypatch.setattr(features_lib, "write_record", refuse)
    with pytest.raises(RuntimeError, match="put back"):
        check(root, run=True)
    monkeypatch.undo()
    assert (root / "features.json").read_bytes() == before        # used to say passing over a record that says failed
    assert rules(check(root)) == [("feature-failing", "warn")]   # true: in progress, last recorded run failed


def test_a_command_the_platform_cannot_encode_counts_as_could_not_start(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):   # what POSIX does for a lone surrogate in the command; Windows would start it
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")

    monkeypatch.setattr(features_lib.subprocess, "Popen", refuse)
    code, output = features_lib.execute(tmp_path, "echo " + chr(0xD800), 5)
    assert code == 127 and "could not start" in output          # used to be a traceback


def test_the_id_message_says_how_an_id_must_start(tmp_path):
    for bad in ("-a", ".a", "_a"):
        result = check(project(tmp_path, [feature(bad)]))
        assert rules(result) == [("features-invalid", "fail")]
        assert "starts with a letter or digit" in result["findings"][0]["message"]
    assert rules(check(project(tmp_path, [feature("a.b_c-1", "not_started")]))) == []


@pytest.mark.parametrize("name", ["SIGHUP", "SIGTERM"])
def test_a_hangup_or_termination_of_the_tool_stops_the_verify_command(tmp_path, name):
    import signal

    if os.name == "nt":   # no such signals there: the documented behaviour is that nothing is installed
        with features_lib._signals() as guard:
            assert guard.previous == {}
        return
    pid_file = tmp_path / "pid"
    sleeper = ("import os, time; open('pid.tmp', 'w').write(str(os.getpid())); os.replace('pid.tmp', 'pid'); "
               "time.sleep(60)")
    root = project(tmp_path, [feature("slow", "active", f'"{sys.executable}" -c "{sleeper}"')])
    driver = ("import argparse, sys; sys.path.insert(0, sys.argv[1]); import features_lib; "
              "features_lib.run(argparse.Namespace(root=sys.argv[2], config={}, run=True))")
    tool = subprocess.Popen([sys.executable, "-c", driver, str(Path(features_lib.__file__).parent), str(root)],
                            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 15
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.02)
        pid = int(pid_file.read_text())
        os.kill(tool.pid, getattr(signal, name))                 # to the tool only: the command is in another group
        assert tool.wait(timeout=15) == -getattr(signal, name)   # the tool still ends the way the signal asked
        deadline = time.time() + 5
        while _alive(pid) and time.time() < deadline:
            time.sleep(0.02)
        assert not _alive(pid)                                   # the command used to keep running
    finally:
        tool.kill()
    assert states(root) == {"slow": "active"}


def test_signal_handlers_are_put_back_and_handled_signals_are_left_alone():
    import signal

    numbers = [getattr(signal, name) for name in ("SIGHUP", "SIGTERM") if os.name != "nt"]
    before = {number: signal.getsignal(number) for number in numbers}
    with features_lib._signals() as guard:
        inside = {number: signal.getsignal(number) for number in before}
    assert {number: signal.getsignal(number) for number in before} == before
    assert all(inside[number] is features_lib._raise_signalled for number in guard.previous)
    assert all(inside[number] is before[number] for number in before if number not in guard.previous)

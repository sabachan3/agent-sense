import argparse
import json
import subprocess
import sys
from pathlib import Path

import features_lib
import pytest

# commands that mean the same in sh and in cmd.exe
OK = "true"
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
            '"behavior": "x", "verify": "true"},\n  {"id": "b", "behavior": "y", "state": "passing", "verify": "true"}]}\n')
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

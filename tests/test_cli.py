import json
import subprocess
import sys

import pytest
from agent_sense import cli

# Built from the running interpreter so the commands mean the same under sh and under cmd.exe.
TRUE = f'"{sys.executable}" -c "pass"'
X_IS_ONE = f'"{sys.executable}" -c "import sys; sys.exit(0 if open(\'app.py\').read().split() == [\'x\', \'=\', \'1\'] else 1)"'
DONE_CONFIG = f"[done]\ntest = '{TRUE}'\nignore = [\".agent-sense.toml\"]\n"   # TOML literal string: no escapes


def test_every_service_follows_the_contract():
    for name in cli.SERVICES:
        module = cli.load(name)
        if module is None:
            continue
        assert module.NAME == name
        assert isinstance(module.SUMMARY, str) and module.SUMMARY
        assert module.RULES and set(module.RULES.values()) <= {"fail", "warn", "info"}
        cli.default_args(name)          # options parse with no arguments
    cli.build_parser()                  # no option clashes with the common ones


def test_clean_strips_control_and_direction_characters():
    assert cli.clean("a\x1b[31mb\u202ec\nd") == "a[31mbc d"


INVISIBLE = [chr(code) for code in (0x1B, 0x7F, 0x9B, 0x202E, 0x2028, 0x2066, 0x200E, 0x200F, 0x061C, 0x200B, 0xFEFF,
                                    0x00AD, 0xE0001, 0xD800)]


def test_clean_drops_everything_invisible_and_keeps_ordinary_text():
    japanese = chr(0x65E5) + chr(0x672C) + chr(0x8A9E)
    assert cli.clean("a" + "".join(INVISIBLE) + f"b {japanese} c") == f"ab {japanese} c"   # marks and a lone surrogate too
    cli.clean("".join(INVISIBLE) + "x").encode("utf-8")                                    # nothing left that cannot be printed


def test_json_text_escapes_everything_invisible_and_keeps_the_data():
    payload = {"findings": [{"message": "m" + "".join(INVISIBLE), "detail": {"lines": ["".join(INVISIBLE) + " tail"]}}]}
    text = cli.json_text(payload)
    assert not any(char in text for char in INVISIBLE)
    assert json.loads(text) == payload
    text.encode("utf-8")                                                                   # a lone surrogate would raise here


def test_format_text_and_exit_codes():
    report = cli.schema.build_report("done", {"findings": [
        {"rule": "tests-stale", "severity": "fail", "message": "files changed", "fix": "run it", "file": "a.py", "line": 3,
         "detail": {"lines": ["E boom"]}},
        {"rule": "uncommitted", "severity": "warn", "message": "1 path", "fix": ""}]})
    text = cli.format_text([report])
    assert text.splitlines()[0] == "FAIL done  (1 fail, 1 warn, 0 info)"
    assert "      at a.py:3" in text and "      | E boom" in text and "      fix: run it" in text
    assert cli.schema.exit_code(report, "fail") == 1 and cli.schema.exit_code(report, "never") == 0
    warn_only = cli.schema.build_report("done", {"findings": report["findings"][1:]})
    assert cli.schema.exit_code(warn_only, "fail") == 0 and cli.schema.exit_code(warn_only, "warn") == 1


def test_contract_rejects_unknown_rule_and_severity():
    with pytest.raises(ValueError):
        cli.schema.validate_result({"findings": [{"rule": "nope", "severity": "fail", "message": "m"}]}, {"known": "fail"})
    with pytest.raises(ValueError):
        cli.schema.validate_result({"findings": [{"rule": "known", "severity": "bad", "message": "m"}]}, {"known": "fail"})


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("cli-template")
    run = lambda *command: subprocess.run(command, cwd=root, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.invalid")
    run("git", "config", "user.name", "t")
    (root / "app.py").write_text("x = 1\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def repo(template, tmp_path):
    import shutil
    shutil.copytree(template, tmp_path / "repo")
    return tmp_path / "repo"


def test_done_command_end_to_end(repo, capsys):
    (repo / ".agent-sense.toml").write_text(DONE_CONFIG)
    assert cli.main(["done", "--root", str(repo), "--no-state"]) == 1
    assert "tests-not-run" in capsys.readouterr().out
    assert cli.main(["done", "--root", str(repo), "--no-state", "--run", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "ok"
    (repo / "app.py").write_text("x = 2\n")
    out = repo / "report.json"
    assert cli.main(["done", "--root", str(repo), "--no-state", "--out", str(out), "--fail-on", "never"]) == 0
    assert json.loads(out.read_text())["findings"][0]["rule"] == "tests-stale"


def test_bad_config_and_non_repo_exit_2(repo, tmp_path, capsys):
    (repo / ".agent-sense.toml").write_text("[done\n")
    assert cli.main(["done", "--root", str(repo)]) == 2
    assert "not valid TOML" in capsys.readouterr().err
    plain = tmp_path / "plain"
    plain.mkdir()
    assert cli.main(["done", "--root", str(plain)]) == 2


def test_hook_blocks_only_on_failure_and_only_with_config(repo, capsys):
    event = json.dumps({"cwd": str(repo), "hook_event_name": "Stop", "stop_hook_active": False})
    assert cli.hook_stop(event) == 0 and capsys.readouterr().out == ""        # no config: not our project
    (repo / ".agent-sense.toml").write_text(DONE_CONFIG)
    assert cli.hook_stop(event) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block" and "tests-not-run" in decision["reason"]
    assert "uncommitted" not in decision["reason"]                            # warnings do not block
    again = json.dumps({"cwd": str(repo), "stop_hook_active": True})
    assert cli.hook_stop(again) == 0 and capsys.readouterr().out == ""        # never block twice in a row
    cli.main(["done", "--root", str(repo), "--run", "--no-state"])
    capsys.readouterr()
    assert cli.hook_stop(event) == 0 and capsys.readouterr().out == ""        # tests current: may stop


def test_hook_never_raises(capsys):
    assert cli.hook_stop("not json") == 0
    assert "skipped" in capsys.readouterr().err


def test_init_writes_config_and_hook_once(repo, capsys):
    assert cli.main(["init", "--root", str(repo), "--hook"]) == 0
    assert "[done]" in (repo / ".agent-sense.toml").read_text()
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "agent-sense hook stop"
    cli.main(["init", "--root", str(repo), "--hook"])
    assert len(json.loads((repo / ".claude" / "settings.json").read_text())["hooks"]["Stop"]) == 1


def test_all_rejects_unknown_check(repo, capsys):
    assert cli.main(["all", "--root", str(repo), "--checks", "done,nope"]) == 2


def test_features_command_runs_only_with_run_and_reads_in_all(repo, capsys):
    listing = {"version": 1, "features": [{"id": "app", "behavior": "x is set", "verify": X_IS_ONE,
                                           "state": "active"}]}
    (repo / "features.json").write_text(json.dumps(listing))
    assert cli.main(["features", "--root", str(repo), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["stats"]["ran"] == 0           # reading never runs a command
    assert cli.main(["features", "--root", str(repo), "--run"]) == 0
    assert json.loads((repo / "features.json").read_text())["features"][0]["state"] == "passing"
    (repo / "app.py").write_text("x = 2\n")
    capsys.readouterr()
    assert cli.main(["all", "--root", str(repo), "--checks", "features"]) == 0   # the record still says it passed
    assert cli.main(["features", "--root", str(repo), "--run"]) == 1
    assert "was passing, now fails" in capsys.readouterr().out
    assert json.loads((repo / "features.json").read_text())["features"][0]["state"] == "active"
    assert cli.main(["features", "--root", str(repo), "--run", "--only", "nope"]) == 2


def test_hook_check_that_cannot_run_does_not_switch_the_others_off(repo, capsys, monkeypatch):
    (repo / ".agent-sense.toml").write_text(DONE_CONFIG + '[hook]\nchecks = ["features", "done"]\n')
    (repo / "features.json").write_text('{"features": []}')

    def broken(args):
        raise TypeError("damaged state")

    monkeypatch.setattr(cli.load("features"), "run", broken)
    event = json.dumps({"cwd": str(repo), "stop_hook_active": False})
    assert cli.hook_stop(event) == 0
    captured = capsys.readouterr()
    assert "features skipped (TypeError" in captured.err
    assert json.loads(captured.out)["decision"] == "block" and "tests-not-run" in captured.out   # done still ran


SURROGATE_RECORD = {"runs": {"a": {"command": TRUE, "exit": 1, "passed": False, "tail": [chr(0xD800) + " x"]}}}


def surrogate_project(repo, hook_checks):
    (repo / ".agent-sense.toml").write_text(DONE_CONFIG + f"[hook]\nchecks = {json.dumps(hook_checks)}\n")
    (repo / "features.json").write_text(json.dumps(
        {"features": [{"id": "a", "behavior": "x", "verify": TRUE, "state": "passing"}]}))
    record = repo / ".git" / "agent-sense" / "features.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(SURROGATE_RECORD))               # json.dumps writes the lone surrogate as an escape


@pytest.mark.parametrize("output", ["text", "json"])
def test_a_lone_surrogate_in_the_record_does_not_crash_the_report(repo, capsys, output):
    surrogate_project(repo, ["features"])
    assert cli.main(["features", "--root", str(repo), "--format", output, "--out", str(repo / "out.json")]) == 1
    printed = capsys.readouterr().out                             # used to be UnicodeEncodeError in emit
    printed.encode("utf-8")
    assert "feature-failing" in printed and chr(0xD800) not in printed
    stored = (repo / "out.json").read_text(encoding="utf-8")
    assert chr(0xD800) not in stored and json.loads(stored)["findings"][0]["detail"]["lines"] == [chr(0xD800) + " x"]


def test_hook_survives_a_record_with_a_lone_surrogate_and_still_blocks(repo, capsys):
    surrogate_project(repo, ["features", "done"])
    event = json.dumps({"cwd": str(repo), "stop_hook_active": False})
    assert cli.hook_stop(event) == 0                              # used to raise from the final print
    out = capsys.readouterr().out
    out.encode("utf-8")
    answer = json.loads(out)
    assert answer["decision"] == "block" and "feature-failing" in answer["reason"] and "tests-not-run" in answer["reason"]


def test_hook_never_raises_while_building_or_printing_its_answer(repo, capsys, monkeypatch):
    surrogate_project(repo, ["features"])

    def broken(_reports):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")

    monkeypatch.setattr(cli, "format_text", broken)
    event = json.dumps({"cwd": str(repo), "stop_hook_active": False})
    assert cli.hook_stop(event) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "skipped (UnicodeEncodeError)" in captured.err
    monkeypatch.setattr(cli, "hook_stop", lambda _text: (_ for _ in ()).throw(ValueError("anything")))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(event))
    assert cli.main(["hook", "stop"]) == 0                        # the outer guard


def test_bad_features_timeout_in_config_exits_2_on_a_read(repo, capsys):
    (repo / ".agent-sense.toml").write_text(DONE_CONFIG + '[features]\ntimeout = "abc"\n')
    (repo / "features.json").write_text('{"features": []}')
    assert cli.main(["features", "--root", str(repo)]) == 2
    assert "timeout" in capsys.readouterr().err
    assert cli.main(["features", "--root", str(repo), "--run", "--timeout", "1e10"]) == 2

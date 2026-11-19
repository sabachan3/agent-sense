"""Gate tests for the session service: synthetic transcript entries only, no real logs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest
import session_lib as lib

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


class Log:
    """Builds transcript entries in the shape Claude Code writes them."""

    def __init__(self, start: datetime = NOW - timedelta(hours=2)):
        self.entries, self.clock, self.count = [], start, 0

    def _stamp(self) -> str:
        self.clock += timedelta(seconds=10)
        return self.clock.isoformat().replace("+00:00", "Z")

    def user(self, text: str, **extra):
        self.entries.append({"type": "user", "isSidechain": False, "timestamp": self._stamp(),
                             "message": {"role": "user", "content": text}, **extra})
        return self

    def say(self, text: str):
        self.entries.append({"type": "assistant", "isSidechain": False, "timestamp": self._stamp(),
                             "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
        return self

    def tool(self, name: str, tool_input: dict, output: str = "", error: bool = False, **extra):
        self.count += 1
        ident = f"toolu_{self.count}"
        self.entries.append({"type": "assistant", "isSidechain": False, "timestamp": self._stamp(),
                             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": ident, "name": name,
                                                                           "input": tool_input}]}})
        self.entries.append({"type": "user", "isSidechain": False, "timestamp": self._stamp(), **extra,
                             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ident,
                                                                      "content": output, "is_error": error}]}})
        return self

    def bash(self, command: str, output: str = "", error: bool = False, **extra):
        return self.tool("Bash", {"command": command}, ("Exit code 1\n" if error else "") + output, error, **extra)

    def edit(self, path: str = "/repo/app.py"):
        return self.tool("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}, "updated")

    def session(self, name: str = "s1", project: str = "-repo", main: bool = True) -> dict:
        events = lib.parse_entries(self.entries, main=main)
        return {"project": project, "session": name, "main": main, "events": events, "last": self.clock}


# ---------------------------------------------------------------- commands

@pytest.mark.parametrize("command, expected", [
    ('cd /a/b && git add -A && git commit -m "fix the thing" && git push', "git add -A && git commit -m <arg> && git push"),
    ("uv run --quiet pytest -q services/x/tests 2>&1 | tail -3", "uv run --quiet <arg> -q <arg>"),
    ("FOO=1 ./scripts/test.sh; echo done", "scripts/test.sh"),
    ("python3 - <<'EOF'\nimport os; print(1) && x\nEOF", "python3 <arg>"),
    ("curl -s https://example.com/a?b=1 | jq .", "curl -s <url>"),
    ("/usr/local/bin/node build.js 42", "node <path> <n>"),
    ("git show 8afc02c1 a.py b.py", "git show <hash> <path>"),
    ("echo hi", "(no command)"),
])
def test_normalise_command(command, expected):
    assert lib.normalise_command(command) == expected


def test_shell_blocks_and_wrappers_are_not_commands():
    command = 'for f in a b; do nohup node server.js "$f"; done; if (which jq); then npm run build; fi'
    assert lib.normalise_command(command) == "node <path> <arg> && which jq && npm run build"


@pytest.mark.parametrize("command, expected", [
    ("cat > src/app.py <<'EOF'\nx = 1 > 2\nEOF", ["src/app.py"]),
    ("echo hi >> notes.log; make 2>&1 | tee build.log", ["notes.log", "build.log"]),
    ("pytest -q 2>/dev/null; echo 'a > b'", []),
    ("sed -i '' 's/a/b/' x.py", ["(edited in place)"]),
    ("python3 - <<'EOF'\nopen('x','w')\nEOF", []),
])
def test_bash_write_targets(command, expected):
    assert lib.bash_write_targets(command) == expected


def test_shell_writes_count_as_edits():
    log = Log().bash("pytest -q", "1 passed").bash("cat > /repo/a.py <<'EOF'\nx\nEOF").bash("echo x > /tmp/scratch.txt")
    assert lib.unverified_end(log.session())["files"] == ["a.py"]


def test_split_respects_quotes_and_drops_pipeline_tail():
    assert lib.split_segments('git commit -m "a && b; c" && ls | grep "x || y"') == ['git commit -m "a && b; c"', "ls"]


def test_normalise_groups_commands_that_differ_only_in_arguments():
    assert lib.normalise_command("pytest tests/a.py -k one") == lib.normalise_command("pytest tests/b/c.py -k two")


@pytest.mark.parametrize("command, expected", [
    ("pytest -q", True), ("./scripts/test.sh", True), ("npm test", True), ("pnpm run test", True), ("npx vitest run", True),
    ("go test ./...", True), ("cargo test", True), ("cd app && uv run --with pytest pytest -q tests", True),
    ("pip install pytest", False), ("grep -rn pytest .", False), ("cat scripts/test.sh", False), ("git add scripts/test.sh", False),
    ("npm install jest", False), ("ls", False),
])
def test_is_test_command(command, expected):
    assert lib.is_test_command(command) is expected


def test_extra_test_pattern():
    assert not lib.is_test_command("make verify")
    assert lib.is_test_command("make verify", lib.TEST_PATTERNS + (r"\bmake verify\b",))


# ---------------------------------------------------------------- parsing

def test_parse_joins_results_to_calls_and_tolerates_junk():
    log = Log().user("please fix it").bash("ls").say("Looking.")
    entries = [None, "text", {"type": "queue-operation"}, {"type": "user"}, {"type": "assistant", "message": "x"},
               {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "orphan", "content": "x"}]}},
               *log.entries]
    events = lib.parse_entries(entries)
    assert [event["kind"] for event in events] == ["tool", "user", "tool", "assistant"]
    assert events[0]["tool"] == "unknown" and events[2]["tool"] == "Bash" and events[2]["input"] == {"command": "ls"}


def test_injected_user_messages_are_not_human():
    log = (Log().user("<task-notification>done</task-notification>").user("summary", isCompactSummary=True)
           .user("meta", isMeta=True).user("from a task", origin={"kind": "task-notification"})
           .user("typed", origin={"kind": "human"}).user("[Request interrupted by user]"))
    events = lib.parse_entries(log.entries)
    assert [(event["kind"], event.get("text")) for event in events] == [("user", "typed"), ("interrupt", None)]


def test_subagent_prompt_is_not_a_user_message():
    assert lib.parse_entries(Log().user("Research task").bash("ls").entries, main=False)[0]["kind"] == "tool"


def test_text_blocks_keep_typed_part_only():
    entry = {"type": "user", "message": {"content": [{"type": "text", "text": "<ide_selection>x</ide_selection>"},
                                                     {"type": "text", "text": "まだ動きません"}]}}
    assert lib.parse_entries([entry])[0]["text"] == "まだ動きません"


def test_read_streams_skips_truncated_lines_and_splits_sidechains(tmp_path):
    log = Log().user("hi").bash("ls")
    side = {"type": "assistant", "isSidechain": True, "agentId": "a1", "message": {"content": [{"type": "text", "text": "x"}]}}
    lines = [json.dumps(entry) for entry in log.entries] + [json.dumps(side), '{"type":"user","message":{"content":"cut']
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    streams = lib.read_streams(path)
    assert len(streams[None]) == 3 and len(streams["a1"]) == 1


def test_project_dir_name():
    assert lib.project_dir_name("/Users/someone/my_app") == "-Users-someone-my-app"


# ---------------------------------------------------------------- measures

def test_tool_counts_exclude_denied_and_interrupted_from_errors():
    log = (Log().bash("ls").bash("false", error=True)
           .bash("rm -rf x", "Permission for this command was denied", error=True, toolDenialKind="permission-rule")
           .bash("sleep 9", "", error=True, toolUseResult={"interrupted": True}).edit())
    calls, errors = lib.tool_counts([log.session()])
    assert calls == {"Bash": 4, "Edit": 1} and errors == {"Bash": 1}
    counts = lib.interruption_counts([log.session()])
    assert counts["interruptions"] == 1 and counts["denial_kinds"] == {"permission-rule:Bash": 1}


def test_user_rejection_and_typed_interrupt_are_counted():
    log = Log().tool("Edit", {"file_path": "a"}, "The user doesn't want to proceed with this tool use.", True)
    log.user("[Request interrupted by user for tool use]")
    counts = lib.interruption_counts([log.session()])
    assert counts == {"interruptions": 1, "denials": 1, "denial_kinds": {"user:Edit": 1}}


def test_failure_groups_merge_by_shape():
    one = Log().bash("pytest tests/a.py", error=True).bash("pytest tests/b.py", error=True).bash("ls nope", error=True)
    two = Log().bash("pytest tests/c.py", error=True).bash("pytest tests/c.py")
    groups = lib.failure_groups([one.session("s1"), two.session("s2")])
    assert groups[0] == {"command": "pytest <path>", "failures": 3, "sessions": 2}
    assert groups[1]["failures"] == 1


def test_blind_retry_needs_identical_input_back_to_back():
    log = (Log().bash("npm run build", error=True).bash("npm run build", error=True).bash("npm run build", error=True)
           .bash("npm run build -- --verbose", error=True)      # changed: not blind
           .bash("make", error=True).edit().bash("make", error=True)   # something happened in between
           .bash("make"))
    assert lib.blind_retries([log.session()]) == [{"tool": "Bash", "command": "npm run build", "retries": 2, "sessions": 1}]


def test_blind_retry_ignores_text_between_calls_and_counts_other_tools():
    log = Log().tool("Read", {"file_path": "/x"}, "no such file", True).say("Trying again.")
    log.tool("Read", {"file_path": "/x"}, "no such file", True)
    assert lib.blind_retries([log.session()])[0]["tool"] == "Read"


def _release(log: Log, message: str) -> Log:
    return log.bash("./scripts/test.sh").bash(f'git add -A && git commit -m "{message}"').bash("git push")


def test_repeated_flow_needs_three_sessions_and_reports_the_longest():
    sessions = [_release(Log().bash("ls"), f"m{index}").session(f"s{index}") for index in range(3)]
    flows = lib.repeated_flows(sessions)
    assert flows == [{"commands": ["scripts/test.sh", "git add -A", "git commit -m <arg>", "git push"], "sessions": 3,
                      "occurrences": 3}]
    assert lib.repeated_flows(sessions[:2]) == []


def test_repeated_flow_skips_read_only_sequences_and_failed_commands():
    sessions = []
    for index in range(3):
        log = Log().bash("ls").bash("git status").bash("grep -rn x .").bash("npm run build", error=True).bash("cat a.txt")
        sessions.append(log.session(f"s{index}"))
    assert lib.repeated_flows(sessions) == []


def test_flow_keeps_a_shorter_sequence_seen_in_more_sessions():
    sessions = [_release(Log(), "m").session(f"s{index}") for index in range(3)]
    sessions.append(Log().bash("git add -A").bash('git commit -m "m"').session("s3"))
    flows = lib.repeated_flows(sessions)
    assert [(len(flow["commands"]), flow["sessions"]) for flow in flows] == [(2, 4), (4, 3)]


def test_claim_then_failing_test_is_hard_evidence():
    log = (Log().user("add the feature").edit().say("実装しました。テストはすべて通りました。").user("ありがとう、次に進みましょう")
           .bash("pytest -q", "1 failed, 3 passed", error=True).say("失敗が見つかりました。"))
    claims = lib.claim_outcomes(log.session())
    assert [(claim["outcome"], claim["command"]) for claim in claims] == [("failing-test", "pytest -q")]


def test_claim_then_pushback_is_separate_and_needs_a_correction():
    log = Log().user("fix it").say("DONE: all tests pass.").user("まだ同じエラーが出ます").say("修正しました").user("ありがとう")
    assert [claim["outcome"] for claim in lib.claim_outcomes(log.session())] == ["pushback", "none"]


def test_failure_after_the_next_turn_does_not_count():
    log = (Log().user("a").say("All done here. DONE").user("thanks, now do b").bash("ls").say("Working on b.").user("go on")
           .bash("pytest", error=True))
    assert [claim["outcome"] for claim in lib.claim_outcomes(log.session())] == ["none"]


def test_done_in_running_prose_is_not_a_claim():
    log = Log().user("a").say("Once that is done I will look at the rest.").user("まだですか")
    assert lib.claim_outcomes(log.session()) == []
    assert [claim["claim"] for claim in lib.claim_outcomes(Log().user("a").say("Done. Pushed.").session())] == ["Done."]


def test_only_the_last_text_of_a_turn_is_a_claim():
    log = Log().user("a").say("Step 1 DONE, continuing.").bash("pytest", error=True).say("Still failing, investigating.")
    assert lib.claim_outcomes(log.session()) == []


def test_test_failure_is_read_from_output_when_the_shell_reports_success():
    log = Log().bash("pytest -q | tail -3", "===== 2 failed, 10 passed in 1.0s =====")
    assert lib.test_outcome(log.session()["events"][0]) == "fail"
    assert lib.test_outcome(Log().bash("pytest -q", "12 passed, 0 errors=0").session()["events"][0]) == "pass"
    assert lib.test_outcome(Log().bash("ls").session()["events"][0]) is None


def test_ended_unverified_after_a_passing_run():
    log = Log().user("a").edit("/repo/a.py").bash("pytest -q", "3 passed").edit("/repo/b.py").edit("/repo/b.py")
    found = lib.unverified_end(log.session())
    assert (found["edits"], found["tested"], found["files"]) == (2, True, ["b.py"])


def test_ended_verified_never_tested_and_ignored_paths():
    assert lib.unverified_end(Log().edit().bash("pytest -q", "3 passed").session()) is None
    assert lib.unverified_end(Log().edit().bash("pytest -q", "1 failed", error=True).session())["tested"] is False
    notes = Log().bash("pytest", "1 passed").edit("/repo/README.md").edit("/Users/x/.claude/projects/p/memory/m.py")
    assert lib.unverified_end(notes.session()) is None


def test_analyse_skips_sessions_still_running_and_subagents_for_session_rules():
    running = Log(start=NOW - timedelta(minutes=2)).edit().session("live")
    ended = Log().edit().session("old")
    sub = Log().edit().say("DONE").session("old", main=False)
    stats = lib.analyse([running, ended, sub], now=NOW)
    assert (stats["sessions"], stats["subagent_streams"], stats["sessions_ended"]) == (2, 1, 1)
    assert [item["session"] for item in stats["unverified"]] == ["old"] and stats["claims"] == 0
    assert stats["tool_calls"] == {"Edit": 3}


# ---------------------------------------------------------------- service interface

def _write(folder, name, log):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in log.entries), encoding="utf-8")


def _args(tmp_path, **options):
    defaults = {"root": tmp_path / "repo", "config": {}, "logs": str(tmp_path / "logs"), "project": None, "all_projects": False,
                "since": 30, "top": 5, "test_pattern": [], "now": NOW}
    return argparse.Namespace(**{**defaults, **options})


def test_run_reads_the_project_of_the_root_and_returns_valid_findings(tmp_path):
    from contracts import schema

    mine = tmp_path / "logs" / lib.project_dir_name(tmp_path / "repo")
    for index in range(3):
        log = _release(Log().user("ship it").edit(), "m").say("DONE").user("まだ動きません")
        for _ in range(3):
            log.bash("npm run build", error=True)
        _write(mine, f"s{index}.jsonl", log)
    _write(mine / "s0" / "subagents", "agent-a.jsonl", Log().bash("npm run build", error=True))
    _write(tmp_path / "logs" / "-other", "x.jsonl", Log().bash("cargo build", error=True))
    result = lib.run(_args(tmp_path))
    schema.validate_result(result, lib.RULES)
    stats = result["stats"]
    assert (stats["sessions"], stats["subagent_streams"], stats["projects"]) == (3, 1, 1)
    assert stats["repeated_failures"] == [{"command": "npm run build", "failures": 10, "sessions": 3}]
    assert stats["claims_then_pushback"] == 3 and stats["claims_then_failing_test"] == 0
    assert {finding["rule"] for finding in result["findings"]} == {"repeated-failure", "blind-retry", "repeated-flow",
                                                                   "claim-then-failure"}
    assert all(finding["severity"] in ("warn", "info") for finding in result["findings"])
    assert lib.run(_args(tmp_path, all_projects=True))["stats"]["projects"] == 2
    assert lib.run(_args(tmp_path, project="other"))["stats"]["bash_failures"] == 1


def test_run_since_top_and_errors(tmp_path):
    mine = tmp_path / "logs" / lib.project_dir_name(tmp_path / "repo")
    _write(mine, "old.jsonl", Log(start=NOW - timedelta(days=40)).bash("make", error=True))
    args = _args(tmp_path, now=NOW)
    import os
    stale = (NOW - timedelta(days=40)).timestamp()
    os.utime(mine / "old.jsonl", (stale, stale))
    assert lib.run(args)["stats"]["sessions"] == 0
    assert lib.run(_args(tmp_path, since=60))["stats"]["sessions"] == 1
    with pytest.raises(RuntimeError):
        lib.run(_args(tmp_path, logs=str(tmp_path / "missing")))
    with pytest.raises(RuntimeError):
        lib.run(_args(tmp_path, test_pattern=["("]))


def test_findings_are_capped_per_rule_and_carry_no_message_text():
    sessions = []
    for index in range(4):
        log = Log().user("secret request text")
        for name in ("alpha", "beta", "gamma"):
            for _ in range(3):
                log.bash(f"{name} run", error=True)
        sessions.append(log.session(f"s{index}"))
    findings = lib.findings_from(lib.analyse(sessions, now=NOW), top=2)
    assert sum(1 for finding in findings if finding["rule"] == "repeated-failure") == 2
    assert "secret request text" not in json.dumps(findings)


def test_add_arguments_defaults():
    parser = argparse.ArgumentParser()
    lib.add_arguments(parser)
    args = parser.parse_args([])
    assert (args.logs, args.since, args.top, args.all_projects, args.test_pattern) == ("~/.claude/projects", 30, 5, False, [])


# pushback: the constructions the heuristic is built on, one sentence each, and the neutral
# sentences that share a word with them. The measured rates are in evals/session_pushback_eval.py.
@pytest.mark.parametrize("text", [
    "いや、そっちではないです", "それは違います", "まだ同じ警告が出ています", "相変わらず遅いです",
    "ボタンを押しても何も起きません", "通知が届きません", "保存したのに反映されていません", "ログインできなくなりました",
    "消してはいけないファイルまで消えてしまっています", "余白が広すぎるのが気になります", "このメニューは使いづらいです",
    "今の実装だと片方が消えます", "毎回ログアウトされるのをなんとかできませんか", "勝手に追加しないでください",
    "作り直してほしいです", "これは検証用ではなく本番です", "日本語のはずです", "画面が真っ白のままです", "グラフが崩れています",
    "リンクを押すと404になります", "頼んだのは英語版です", "元に戻してください", "デプロイしたらエラーになりました",
    "It still crashes", "That's not what I asked for", "The button doesn't do anything", "No, the other file",
    "I'm getting a 500 error on save", "It can't find the module", "I said green", "put it back", "the total should be 42",
])
def test_pushback_constructions(text):
    assert lib.pushback(text)


@pytest.mark.parametrize("text", [
    "ありがとうございます。次に進んでください", "pushしちゃってください", "検索機能も追加してほしいです", "時間はまだありますか",
    "まだですか", "エラー時の表示も作ってください", "失敗したときにリトライする機能を追加したいです", "他に必要な設定はありますか？",
    "動きました！ありがとう", "Aの案でお願いします", "全部進めちゃいましょう", "Looks good, ship it",
    "Let's add error handling to the upload form", "Can you also write the docs?", "Works now, thank you", "yes, go ahead",
])
def test_neutral_messages_are_not_pushback(text):
    assert lib.pushback(text) is None


def test_pushback_reads_only_the_start_of_the_message():
    assert lib.pushback("次の機能に進みましょう。" + "あ" * lib.CORRECTION_WINDOW + "エラー") is None

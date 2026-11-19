"""Eval for the session service.

Part A, known answers: small synthetic sessions written as transcript files, each built to trigger
(or to stay just below) one rule. The service is run on them through run(), the same entry the
command line uses. Deterministic, so the pass threshold is every case correct (100%).

Part B, field run: the service over the real local logs (all projects). It writes the full tables
to runs/session_field.json, which is git-ignored, and prints aggregate numbers only.

    uv run --no-project --python 3.12 evals/session_eval.py [--skip-field] [--since DAYS] [--logs DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "session"))
sys.path.insert(0, str(ROOT))

import session_lib  # noqa: E402
from contracts import schema  # noqa: E402

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
PASS_THRESHOLD = 1.0


class Session:
    """One synthetic transcript."""

    def __init__(self):
        self.entries, self.clock, self.count = [], NOW - timedelta(hours=3), 0

    def _stamp(self) -> str:
        self.clock += timedelta(seconds=15)
        return self.clock.isoformat().replace("+00:00", "Z")

    def user(self, text: str):
        self.entries.append({"type": "user", "isSidechain": False, "timestamp": self._stamp(), "origin": {"kind": "human"},
                             "message": {"role": "user", "content": text}})
        return self

    def say(self, text: str):
        self.entries.append({"type": "assistant", "isSidechain": False, "timestamp": self._stamp(),
                             "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
        return self

    def tool(self, name: str, tool_input: dict, output: str = "ok", error: bool = False):
        self.count += 1
        ident = f"toolu_{self.count:04d}"
        self.entries.append({"type": "assistant", "isSidechain": False, "timestamp": self._stamp(),
                             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": ident, "name": name,
                                                                           "input": tool_input}]}})
        self.entries.append({"type": "user", "isSidechain": False, "timestamp": self._stamp(),
                             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ident,
                                                                      "content": output, "is_error": error}]}})
        return self

    def bash(self, command: str, output: str = "ok", error: bool = False):
        return self.tool("Bash", {"command": command}, ("Exit code 1\n" if error else "") + output, error)

    def edit(self, path: str = "/work/app/src/app.py"):
        return self.tool("Edit", {"file_path": path, "old_string": "a", "new_string": "b"})


def deploy(session: Session, tag: str) -> Session:
    return session.bash("npm run build").bash(f"aws s3 sync dist/ s3://bucket-{tag}/ --delete").bash(
        f"aws cloudfront create-invalidation --distribution-id E{tag} --paths '/*'")


# Each case: name, the sessions of one project, and the expected number of findings per rule
# (rules not listed must have none; `interruptions` is checked only when listed).
def cases() -> list[tuple[str, list[Session], dict]]:
    built = []

    built.append(("clean session: edit, passing tests, claim, thanks",
                  [Session().user("add a field").edit().bash("pytest -q", "5 passed").say("DONE. Tests pass.").user("ありがとう")], {}))

    fails = Session().user("build it")
    for name in ("a", "b", "c"):
        fails.bash(f"npm run build -- --target {name}", "error TS2304", error=True).edit()
    fails.bash("pytest -q", "1 passed")
    built.append(("same command shape fails three times with edits in between", [fails], {"repeated-failure": 1}))

    twice = Session().bash("npm run build", error=True).edit().bash("npm run build", error=True).edit().bash("pytest", "1 passed")
    built.append(("two failures stay below the threshold", [twice], {}))

    blind = Session().bash("docker compose up -d", "port in use", error=True).bash("docker compose up -d", "port in use", error=True)
    built.append(("identical failing command repeated at once", [blind], {"blind-retry": 1}))

    changed = Session().bash("docker compose up -d", error=True).bash("docker compose up -d --force-recreate", error=True)
    built.append(("retry with a changed command is not blind", [changed], {}))

    built.append(("deploy sequence in three sessions", [deploy(Session().bash("git status"), str(index)) for index in range(3)],
                  {"repeated-flow": 1}))
    built.append(("deploy sequence in two sessions only", [deploy(Session(), str(index)) for index in range(2)], {}))
    built.append(("read-only commands repeated in three sessions",
                  [Session().bash("git status").bash("ls -la").bash("git log --oneline -5") for _ in range(3)], {}))

    hard = (Session().user("fix the parser").edit().bash("pytest -q", "9 passed").say("修正しました。テストはすべて通りました。")
            .user("では次の件もお願いします").bash("pytest -q", "2 failed, 7 passed", error=True).edit().bash("pytest -q", "9 passed"))
    built.append(("claim, then the tests fail in the next turn", [hard], {"claim-then-failure": 1}))

    piped = (Session().user("fix it").edit().say("All tests pass now.").user("ok, run them once more")
             .bash("pytest -q | tail -2", "===== 1 failed, 3 passed =====").edit().bash("pytest -q", "4 passed"))
    built.append(("claim, then a failing run hidden behind a pipe", [piped], {"claim-then-failure": 1}))

    soft = (Session().user("fix the page").edit().bash("npm test", "ok").say("完了しました。").user("まだ同じエラーが表示されます")
            .edit().bash("npm test", "ok"))
    built.append(("claim, then the user pushes back", [soft], {"claim-then-failure": 1}))

    fine = Session().user("fix").edit().bash("npm test", "ok").say("完了しました。").user("ありがとうございます。次はログイン画面です")
    built.append(("claim, then the user moves on", [fine.bash("npm test", "ok")], {}))

    late = (Session().user("a").edit().bash("pytest", "1 passed").say("DONE").user("次へ").say("了解です。").user("続けて")
            .bash("pytest", "1 failed", error=True).edit().bash("pytest", "1 passed"))
    built.append(("a failure two turns after the claim is outside the window", [late], {}))

    built.append(("edits after the last passing run",
                  [Session().user("a").edit().bash("pytest -q", "3 passed").edit("/work/app/src/b.py")], {"ended-unverified": 1}))
    built.append(("edits and no test command at all", [Session().user("a").edit().edit()], {"ended-unverified": 1}))
    built.append(("file written through the shell after the last passing run",
                  [Session().bash("pytest", "1 passed").bash("cat > src/app.py <<'EOF'\nx = 1\nEOF")], {"ended-unverified": 1}))
    built.append(("only notes edited after the tests",
                  [Session().edit().bash("pytest", "1 passed").edit("/work/app/README.md")], {}))

    stopped = Session().user("go").bash("npm run dev").user("[Request interrupted by user]").user("やり直してください")
    stopped.tool("Bash", {"command": "rm -rf build"}, "The user doesn't want to proceed with this tool use.", True)
    built.append(("an interruption and a denied call", [stopped], {"interruptions": 1}))
    return built


def run_service(logs: Path, **options) -> dict:
    defaults = {"root": ROOT, "config": {}, "logs": str(logs), "project": None, "all_projects": True, "since": 30, "top": 5,
                "test_pattern": [], "now": NOW}
    result = session_lib.run(argparse.Namespace(**{**defaults, **options}))
    return schema.validate_result(result, session_lib.RULES)


def known_answers() -> tuple[int, int]:
    passed, total = 0, 0
    for name, sessions, expected in cases():
        total += 1
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "-work-app"
            project.mkdir()
            for index, session in enumerate(sessions):
                lines = [json.dumps(entry, ensure_ascii=False) for entry in session.entries]
                (project / f"session-{index}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = run_service(Path(folder))
        got: dict[str, int] = {}
        for finding in result["findings"]:
            got[finding["rule"]] = got.get(finding["rule"], 0) + 1
        if "interruptions" not in expected:
            got.pop("interruptions", None)
        ok = got == expected
        passed += ok
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": expected {expected}, got {got}"))
    return passed, total


def field(logs: Path, since: int, out: Path) -> None:
    result = run_service(logs, since=since, now=None, top=10)
    stats = result["stats"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    calls, errors = sum(stats["tool_calls"].values()), sum(stats["tool_errors"].values())
    print(f"  sessions {stats['sessions']} (+{stats['subagent_streams']} subagent streams) in {stats['projects']} project(s), "
          f"{stats['user_messages']} user messages, last {since} days")
    print(f"  tool calls {calls}, errors {errors} ({errors / calls:.1%})" if calls else "  no tool calls")
    print(f"  failed shell commands {stats['bash_failures']} in {len(stats['failure_groups'])} shapes; "
          f"shapes failing {session_lib.MIN_FAILURES}+ times: {len(stats['repeated_failures'])}")
    print(f"  blind retries: {sum(item['retries'] for item in stats['blind_retries'])}")
    print(f"  repeated flows (in {session_lib.MIN_FLOW_SESSIONS}+ sessions): {len(stats['flows'])}")
    print(f"  completion claims {stats['claims']}: then failing test {stats['claims_then_failing_test']}, "
          f"then user pushback {stats['claims_then_pushback']} (heuristic)")
    print(f"  ended sessions with project edits {stats['sessions_with_edits']} of {stats['sessions_ended']}: "
          f"unverified {stats['ended_unverified']} (after a passing run {stats['ended_after_passing_test']}, "
          f"never tested {stats['ended_never_tested']})")
    print(f"  user interruptions {stats['interruptions']}, denied tool calls {stats['denials']}")
    print(f"  full tables: {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-field", action="store_true")
    parser.add_argument("--since", type=int, default=30)
    parser.add_argument("--logs", default=session_lib.DEFAULT_LOGS)
    parser.add_argument("--out", default=str(ROOT / "runs" / "session_field.json"))
    args = parser.parse_args(argv)
    print("A. known answers")
    passed, total = known_answers()
    rate = passed / total
    print(f"  {passed}/{total} cases correct ({rate:.0%}); threshold {PASS_THRESHOLD:.0%}")
    logs = Path(args.logs).expanduser()
    if not args.skip_field and logs.is_dir():
        print("B. field run over local logs")
        field(logs, args.since, Path(args.out))
    return 0 if rate >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""The parts of the eval scripts that decide a number: ground truth and transcript reading."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import context_history  # noqa: E402
import done_hook_eval  # noqa: E402
import session_pushback_eval  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo, check=True, capture_output=True)


def history_repo(tmp_path):
    repo = tmp_path / "repo"
    for name, text in {"CLAUDE.md": "Run `scripts/old.sh` first. The guide is docs/guide.md, the entry `src/app.py`.\n"
                                    "Also `legacy/tool.py` and `scripts/kept.sh`.\n",
                       "scripts/old.sh": "", "scripts/kept.sh": "", "docs/guide.md": "", "docs/other.md": "", "src/app.py": "",
                       "legacy/tool.py": "", "unnamed/file.py": ""}.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    git(repo, "rm", "-q", "scripts/old.sh", "docs/guide.md", "legacy/tool.py", "unnamed/file.py")
    git(repo, "commit", "-q", "-m", "remove")
    git(repo, "mv", "src/app.py", "src/main.py")
    git(repo, "commit", "-q", "-m", "rename")
    return repo


def test_context_history_finds_each_stale_reference_once_and_sorts_the_misses(tmp_path):
    repo = history_repo(tmp_path)
    pairs = context_history.stale_pairs(repo)
    assert sorted(pair["path"] for pair in pairs) == ["docs/guide.md", "legacy/tool.py", "scripts/old.sh", "src/app.py"]
    context_history.judge(repo, pairs)
    assert {pair["path"]: pair["outcome"] for pair in pairs} == {
        "scripts/old.sh": "found",        # in backticks, and scripts/ still exists
        "src/app.py": "found",            # renamed away
        "docs/guide.md": "prose",         # named outside backticks: not read by design
        "legacy/tool.py": "unanchored"}   # its whole top-level directory is gone: only --strict reports it


def test_context_history_resolves_from_the_root_and_from_the_instruction_file():
    assert context_history.resolve("../docs/a.md", "services/api/CLAUDE.md") == ["services/docs/a.md"]
    assert context_history.resolve("./x/y.py", "CLAUDE.md") == ["x/y.py"]
    assert context_history.resolve("x/y.py", "services/api/CLAUDE.md") == ["x/y.py", "services/api/x/y.py"]


def stream(*blocks):
    lines = []
    for index, (name, arguments, ok) in enumerate(blocks):
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{index}", "name": name, "input": arguments}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{index}", "is_error": not ok}]}}))
    lines.append(json.dumps({"type": "result", "subtype": "success", "num_turns": len(blocks), "total_cost_usd": 0.1}))
    return "\n".join(lines) + '\nnot json\n"a string"\n{"type": "system", "message": "text, not an object"}\n'


def test_hook_eval_verified_means_a_passing_test_run_after_the_last_code_edit():
    edit, test = ("Edit", {"file_path": "/r/stats.py"}, True), ("Bash", {"command": "scripts/test.sh"}, True)
    read = done_hook_eval.read_stream
    assert read(stream(edit, test))["verified"] is True
    assert read(stream(test, edit))["verified"] is False
    assert read(stream(edit, ("Bash", {"command": "scripts/test.sh"}, False)))["verified"] is False
    assert read(stream(edit, ("Bash", {"command": "agent-sense done --run"}, True)))["verified"] is True
    assert read(stream(edit, test, ("Edit", {"file_path": "/r/README.md"}, True)))["verified"] is True
    assert read(stream(edit, test, ("Edit", {"file_path": "/r/stats.py"}, False)))["verified"] is True   # the edit failed
    docs_only = read(stream(("Write", {"file_path": "/r/README.md"}, True)))
    assert docs_only["edited"] is False and docs_only["verified"] is True and docs_only["ended"] == "success"


def test_pushback_eval_counts():
    result = session_pushback_eval.score([(1, "まだ動きません"), (1, "右側にあります"), (0, "続けてください"), (0, "エラーが出ます")], show=False)
    assert (result["tp"], result["fn"], result["tn"], result["fp"]) == (1, 1, 1, 1)
    assert result["precision"] == 0.5 and result["recall"] == 0.5

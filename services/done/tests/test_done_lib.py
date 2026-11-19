import argparse
import shutil
import sys
import subprocess

import done_lib
import pytest


def diff_of(*files):
    """A minimal unified diff: files are (path, status, removed lines, added lines)."""
    out = []
    for path, status, removed, added in files:
        out.append(f"diff --git a/{path} b/{path}")
        if status == "deleted":
            out += ["deleted file mode 100644", f"--- a/{path}", "+++ /dev/null"]
        elif status == "added":
            out += ["new file mode 100644", "--- /dev/null", f"+++ b/{path}"]
        else:
            out += [f"--- a/{path}", f"+++ b/{path}"]
        out.append(f"@@ -10,{len(removed)} +10,{len(added)} @@")
        out += [f"-{line}" for line in removed] + [f"+{line}" for line in added]
    return "\n".join(out)


def rules(findings):
    return sorted(finding["rule"] for finding in findings)


def test_test_file_detection():
    for path in ("tests/test_a.py", "services/x/tests/helper.py", "src/a.test.ts", "a/b.spec.tsx", "pkg/x_test.go",
                 "conftest.py", "evals/eval_fixtures.py", "src/__tests__/a.js"):
        assert done_lib.is_test_file(path), path
    for path in ("src/app.py", "latest/contest.py", "src/testing_utils.ts", "attest.py"):
        assert not done_lib.is_test_file(path), path


def test_parse_diff_reads_status_paths_and_hunks():
    files = done_lib.parse_diff(diff_of(("tests/test_a.py", "modified", ["    assert x == 1"], ["    assert x == 2"]),
                                        ("tests/test_b.py", "deleted", ["def test_b():", "    assert True"], [])))
    assert [(entry["path"], entry["status"]) for entry in files] == [("tests/test_a.py", "modified"),
                                                                     ("tests/test_b.py", "deleted")]
    assert files[0]["hunks"][0] == {"line": 10, "added": ["    assert x == 2"], "removed": ["    assert x == 1"]}


def test_parse_diff_rename():
    text = "diff --git a/tests/old.py b/tests/new.py\nsimilarity index 90%\nrename from tests/old.py\nrename to tests/new.py"
    assert done_lib.parse_diff(text)[0] | {"hunks": []} == {"path": "tests/new.py", "old_path": "tests/old.py",
                                                            "status": "renamed", "hunks": []}


def test_added_skip_fails():
    for line in ("@pytest.mark.skip(reason='flaky')", "    it.skip('adds', () => {", "  test.only('one', () => {",
                 "\tt.Skip(\"later\")", "@pytest.mark.xfail", "xit('works', () => {"):
        found = done_lib.weakening_findings(done_lib.parse_diff(diff_of(("tests/test_a.py", "modified", [], [line]))))
        assert rules(found) == ["test-skipped"], line
        assert found[0]["severity"] == "fail" and found[0]["line"] == 10


def test_skip_marker_quoted_in_a_string_is_not_a_skip():
    lines = ['    cases = ["@pytest.mark.skip", "it.skip(\'x\')"]', "    text = 'use test.only( sparingly'"]
    assert done_lib.weakening_findings(done_lib.parse_diff(diff_of(("tests/test_lint.py", "modified", [], lines)))) == []


def test_removing_a_skip_is_fine():
    files = done_lib.parse_diff(diff_of(("tests/test_a.py", "modified", ["@pytest.mark.skip"], [])))
    assert done_lib.weakening_findings(files) == []


def test_net_assertion_loss_fails():
    files = done_lib.parse_diff(diff_of(("tests/test_a.py", "modified",
                                         ["    assert a == 1", "    assert b == 2"], ["    assert a"])))
    found = done_lib.weakening_findings(files)
    assert rules(found) == ["assertions-removed"]
    assert "1 fewer" in found[0]["message"] and "tests/test_a.py (-1)" in found[0]["message"]


def test_moving_a_test_between_files_is_not_a_loss():
    files = done_lib.parse_diff(diff_of(
        ("tests/test_a.py", "modified", ["def test_x():", "    assert f() == 1"], []),
        ("tests/test_b.py", "modified", [], ["def test_x():", "    assert f() == 1"])))
    assert done_lib.weakening_findings(files) == []


def test_deleted_test_file_fails_but_deleted_helper_does_not():
    files = done_lib.parse_diff(diff_of(("tests/test_a.py", "deleted", ["def test_x():", "    assert f() == 1"], [])))
    assert rules(done_lib.weakening_findings(files)) == ["assertions-removed", "test-deleted"]
    helper = done_lib.parse_diff(diff_of(("tests/data.py", "deleted", ["ROWS = [1, 2]"], [])))
    assert done_lib.weakening_findings(helper) == []


def test_expectation_changed_only_when_source_changes_too():
    test_change = ("tests/test_a.py", "modified", ["    assert total == 10"], ["    assert total == 12"])
    alone = done_lib.parse_diff(diff_of(test_change))
    assert done_lib.weakening_findings(alone) == []
    together = done_lib.parse_diff(diff_of(test_change, ("src/calc.py", "modified", ["    return a"], ["    return b"])))
    found = done_lib.weakening_findings(together)
    assert rules(found) == ["expectation-changed"] and found[0]["severity"] == "warn"


def test_comments_are_not_assertions_or_cases():
    files = done_lib.parse_diff(diff_of(("tests/test_a.py", "modified", ["    # assert old == 1", "// expect(x)"], [])))
    assert done_lib.weakening_findings(files) == []


def test_fewer_test_cases_warns():
    files = done_lib.parse_diff(diff_of(("tests/test_a.py", "modified", ["def test_gone():", "    run()"], [])))
    found = done_lib.weakening_findings(files)
    assert rules(found) == ["test-removed"] and found[0]["severity"] == "warn"


def test_stub_added_in_source_only():
    files = done_lib.parse_diff(diff_of(
        ("src/app.py", "modified", [], ["    raise NotImplementedError", "    x = 1  # TODO wire this"]),
        ("tests/test_a.py", "modified", [], ["    # TODO more cases"]),
        ("notes.txt", "modified", [], ["TODO buy milk"])))
    found = done_lib.stub_findings(files)
    assert [(finding["file"], finding["line"]) for finding in found] == [("src/app.py", 10), ("src/app.py", 11)]


def test_a_variable_named_todo_is_not_a_stub():
    lines = ["todo = [item for item in chosen if item not in cache]", "for site in todo:", 'print(f"{len(todo)} left")',
             "class NotImplementedErrorHandler:", "xxx = 3", "# a TODO in a comment counts only at its start",
             'PATTERN = r"(#|//)\\s*(TODO|FIXME)"']
    assert done_lib.stub_findings(done_lib.parse_diff(diff_of(("src/app.py", "modified", [], lines)))) == []
    real = ["// TODO: handle errors", "throw new Error('Not implemented')", "/* FIXME */", "    todo!()"]
    assert len(done_lib.stub_findings(done_lib.parse_diff(diff_of(("src/app.ts", "modified", [], real))))) == 4


def test_stub_moved_within_a_hunk_is_not_new():
    files = done_lib.parse_diff(diff_of(("src/app.py", "modified", ["  # TODO later"], ["    # TODO later"])))
    assert done_lib.stub_findings(files) == []


def test_compare_uses_content_not_timestamps():
    recorded = {"a.py": [1, 1, "h1"], "b.py": [1, 1, "h2"], "gone.py": [1, 1, "h3"]}
    current = {"a.py": [1, 999, "h1"], "b.py": [2, 2, "changed"], "new.py": [1, 1, "h4"]}
    assert done_lib.compare(recorded, current) == {"changed": ["b.py"], "added": ["new.py"], "deleted": ["gone.py"]}


def test_test_findings_states():
    current = {"a.py": [1, 1, "h1"]}
    assert rules(done_lib.test_findings(None, None, current)) == ["no-test-command"]
    assert rules(done_lib.test_findings(None, "pytest", current)) == ["tests-not-run"]
    failed = {"passed": False, "exit": 1, "command": "pytest", "tail": ["E  assert 1 == 2"], "files": current}
    found = done_lib.test_findings(failed, "pytest", current)
    assert rules(found) == ["tests-failed"] and found[0]["detail"]["lines"] == ["E  assert 1 == 2"]
    passed = {"passed": True, "command": "pytest", "files": current}
    assert done_lib.test_findings(passed, "pytest", current) == []
    stale = done_lib.test_findings(passed, "pytest", {"a.py": [1, 1, "other"]})
    assert rules(stale) == ["tests-stale"] and "a.py" in stale[0]["message"]


def test_ignore_patterns():
    assert done_lib.ignored("README.md", done_lib.DEFAULT_IGNORE)
    assert done_lib.ignored("docs/deep/page.html", done_lib.DEFAULT_IGNORE)
    assert not done_lib.ignored("src/app.py", done_lib.DEFAULT_IGNORE)


# ------------------------------------------------------------ against a real repository

def sh(root, *command):
    subprocess.run(command, cwd=root, check=True, capture_output=True)


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """One committed repository, built once; each test works on its own copy."""
    root = tmp_path_factory.mktemp("template")
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "config", "user.email", "t@example.invalid")
    sh(root, "git", "config", "user.name", "t")
    # no background maintenance: it writes .git/objects/maintenance.lock while the tests copy this directory
    sh(root, "git", "config", "gc.auto", "0")
    sh(root, "git", "config", "maintenance.auto", "false")
    (root / "app.py").write_text("def f():\n    return 1\n")
    (root / "test_app.py").write_text("from app import f\n\ndef test_f():\n    assert f() == 1\n")
    (root / "README.md").write_text("# demo\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def repo(template, tmp_path):
    shutil.copytree(template, tmp_path / "repo", ignore=shutil.ignore_patterns("*.lock"))
    return tmp_path / "repo"


def check(root, **options):
    defaults = dict(root=root, config={}, run=False, test=None, base=None, staged=False, no_state=True, test_timeout=60)
    return done_lib.run(argparse.Namespace(**{**defaults, **options}))["findings"]


def test_repo_flow_run_then_edit_then_rerun(repo):
    assert rules(check(repo, test="true")) == ["tests-not-run"]
    assert check(repo, test="true", run=True) == []
    assert check(repo, test="true") == []
    (repo / "README.md").write_text("# demo, edited\n")          # documentation does not make the run stale
    assert check(repo, test="true") == []
    (repo / "app.py").write_text("def f():\n    return 2\n")
    stale = check(repo, test="true")
    assert rules(stale) == ["tests-stale"] and stale[0]["detail"]["changed"] == ["app.py"]
    (repo / "app.py").write_text("def f():\n    return 1\n")     # put back: same content, so current again
    assert check(repo, test="true") == []
    (repo / "new_module.py").write_text("x = 1\n")
    assert check(repo, test="true")[0]["detail"]["added"] == ["new_module.py"]


def test_repo_failed_run_is_reported_with_output(repo):
    # one program, no shell syntax: the command goes through cmd.exe on Windows and sh elsewhere
    found = check(repo, test=f'"{sys.executable}" -c "print(\'boom-output\'); raise SystemExit(3)"', run=True)
    assert rules(found) == ["tests-failed"]
    assert "exit 3" in found[0]["message"] and found[0]["detail"]["lines"] == ["boom-output"]


def test_repo_weakened_test_in_working_tree_and_staged(repo):
    (repo / "test_app.py").write_text("import pytest\nfrom app import f\n\n@pytest.mark.skip\ndef test_f():\n    pass\n")
    found = check(repo, test="true", run=True)
    assert rules(found) == ["assertions-removed", "test-skipped"]
    assert check(repo, test="true", staged=True) == []            # nothing staged yet
    sh(repo, "git", "add", "-A")
    assert rules(check(repo, test="true", staged=True)) == ["assertions-removed", "test-skipped"]


def test_repo_test_moved_into_a_new_untracked_file(repo):
    (repo / "test_app.py").write_text("from app import f\n")
    (repo / "test_moved.py").write_text("from app import f\n\ndef test_f():\n    assert f() == 1\n")
    assert check(repo, test="true", run=True) == []


def test_repo_state_findings(repo):
    (repo / "scratch.py").write_text("x = 1\n")
    found = check(repo, test="true", run=True, no_state=False)
    assert rules(found) == ["uncommitted", "unpushed"]
    assert all(finding["severity"] == "warn" for finding in found)


def test_repo_config_overrides_severity_and_ignore(repo):
    config = {"test": "true", "ignore": ["app.py"], "severity": {"tests-not-run": "warn"}}
    assert check(repo, config=config)[0]["severity"] == "warn"
    check(repo, config=config, run=True)
    (repo / "app.py").write_text("def f():\n    return 5\n")
    assert check(repo, config=config) == []


def test_not_a_repository(tmp_path):
    with pytest.raises(RuntimeError, match="not a git repository"):
        check(tmp_path)


def test_detect_test_command(tmp_path):
    assert done_lib.detect_test_command(tmp_path) is None
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    assert done_lib.detect_test_command(tmp_path) == "npm test"
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert done_lib.detect_test_command(tmp_path) == "pnpm test"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "test.sh").write_text("#!/bin/sh\n")
    assert done_lib.detect_test_command(tmp_path) == "scripts/test.sh"

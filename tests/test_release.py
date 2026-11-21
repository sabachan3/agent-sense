"""The checks that run before the repository or a build of it leaves the machine (scripts/release_check.py).

Every sample that must be flagged is put together from pieces, so that this file itself stays clean
under the scan it tests.
"""
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import release_check as rc  # noqa: E402

AT = "@"
PERSONAL = "taro.yamada" + AT + "mailbox-provider" + ".ne.jp"
HOME_MAC = "/Users/" + "taro/work/app"
HOME_LINUX = "/home/" + "taro/.ssh/id_ed25519"
HOME_WINDOWS = "C:\\Users\\" + "taro\\app"
FLAGGED = [
    ("email", f"contact {PERSONAL} for access"),
    ("email", "Author: Taro <taro" + AT + "corp.co.jp>"),
    ("home-path", f"cd {HOME_MAC}"),
    ("home-path", f"key at {HOME_LINUX}"),
    ("home-path", f"open {HOME_WINDOWS}"),
    ("github-token", "token gh" + "p_" + "a1B2" * 9),
    ("github-token", "github" + "_pat_" + "A" * 30),
    ("pypi-token", "pypi" + "-" + "AgEIcHlwaS5vcmc" * 4),
    ("aws-key", "AK" + "IA" + "ABCDEFGHIJKLMNOP"),
    ("google-key", "AI" + "za" + "B" * 35),
    ("slack-token", "xo" + "xb-" + "123456789012-abcdef"),
    ("model-api-key", "sk" + "-ant-" + "a" * 40),
    ("hf-token", "hf" + "_" + "a" * 34),
    ("live-payment-key", "sk" + "_live_" + "a" * 24),
    ("jwt", "ey" + "J" + "a" * 24 + "." + "b" * 24 + "." + "c" * 12),
    ("private-key", "-----BEGIN OPENSSH " + "PRIVATE KEY-----"),
]
QUIET = [
    "git config user.email e2e" + AT + "example.invalid",
    "Co-Authored-By: Someone <noreply" + AT + "anthropic.com>",
    "12345+login" + AT + "users.noreply.github.com",
    "mail a" + AT + "example.com or b" + AT + "shop.example.jp or c" + AT + "example.org",
    "text = 'x = 1\\n" + AT + "pytest.mark.skip\\ndef test_a(): pass'",
    "npm install react" + AT + "18.2.0 " + AT + "types/node",
    "uv tool install \"agent-sense[browser] " + AT + " git+https://github.com/owner/repo\"",
    "git clone git" + AT + "github.com:owner/repo.git",
    "lib.project_dir_name('/Users/" + "someone/my_app')",
    "edit('/Users/" + "x/.claude/projects/p/memory/m.py')",
    "runs-on /home/" + "runner/work",
    "publishableKey: 'pk" + "_test_51FixtureFixtureFixture00'",   # a test key is what the launch fixtures are about
    "sk" + "-short",
    "the task-manager and the /usr/local/home directory",
]


@pytest.mark.parametrize("kind, line", FLAGGED)
def test_scan_text_flags_private_shapes(kind, line):
    hits = rc.scan_text("first line\n" + line + "\n")
    assert [(number, found_kind) for number, found_kind, _ in hits] == [(2, kind)]


@pytest.mark.parametrize("line", QUIET)
def test_scan_text_stays_quiet_on_reserved_and_look_alike_text(line):
    assert rc.scan_text(line) == []


def test_scan_text_never_prints_a_whole_token():
    token = "gh" + "p_" + "a1B2" * 9
    (_, _, shown), = rc.scan_text(token)
    assert token not in shown and shown.endswith("...") and len(shown) == 15


def test_private_names_are_matched_anywhere_in_a_line_case_blind_and_only_when_given():
    line = "synced to the Kiku-PC over ssh"
    assert rc.scan_text(line) == []
    assert rc.scan_text(line, ("kiku-pc", "other")) == [(1, "private-name", "kiku-pc")]
    assert rc.scan_text(line, ("", "  ")) == []                                   # blank entries match nothing


def test_read_names_skips_comments_and_refuses_a_missing_file(tmp_path):
    names = tmp_path / "names.txt"
    names.write_text("# private\nkiku-pc\n\n  side_project  \n", encoding="utf-8")
    assert rc.read_names(names) == ("kiku-pc", "side_project")
    with pytest.raises(RuntimeError, match="names file not found"):
        rc.read_names(tmp_path / "absent.txt")


WHEEL_FILES = ["agent_sense/__init__.py", "agent_sense/cli.py", "agent_sense/_services/done/done_lib.py",
               "agent_sense/_services/launch/vendors.json", "agent_sense/_services/contracts/schema.py",
               "agent_sense-0.1.0.dist-info/METADATA", "agent_sense-0.1.0.dist-info/licenses/LICENSE"]
SDIST_FILES = ["agent_sense/cli.py", "services/done/done_lib.py", "services/launch/vendors.json", "contracts/schema.py",
               "contracts/README.md", "docs/results.md", "README.md", "CHANGELOG.md", "LICENSE", "pyproject.toml", "PKG-INFO",
               ".gitignore"]
NEVER_INSIDE = ["runs/launch_field.json", ".claude/settings.json", ".agent-sense/state.json", "evals/launch_jp_sites.txt",
                "services/done/tests/test_done_lib.py", "agent_sense/__pycache__/cli.cpython-312.pyc", ".env", "PROGRESS.md",
                "tests/test_cli.py", "services/done/other_lib.py", "agent_sense/_services/launch/sites.txt"]


def test_archive_allow_lists_take_what_a_build_holds_and_nothing_else():
    assert rc.unexpected_members("wheel", WHEEL_FILES) == []
    assert rc.unexpected_members("sdist", SDIST_FILES) == []
    assert rc.unexpected_members("wheel", WHEEL_FILES + NEVER_INSIDE) == NEVER_INSIDE
    assert rc.unexpected_members("sdist", SDIST_FILES + NEVER_INSIDE) == NEVER_INSIDE
    assert rc.unexpected_members("wheel", ["services/done/done_lib.py"]) == ["services/done/done_lib.py"]   # sdist layout in a wheel


def build_pair(directory, extra=None, text="x = 1\n"):
    extra = extra or {}
    with zipfile.ZipFile(directory / "agent_sense-0.1.0-py3-none-any.whl", "w") as wheel:
        for name in WHEEL_FILES:
            wheel.writestr(name, text)
        for name, data in extra.items():
            wheel.writestr(name, data)
    with tarfile.open(directory / "agent_sense-0.1.0.tar.gz", "w:gz") as sdist:
        for name, data in [*((name, text) for name in SDIST_FILES), *extra.items()]:
            raw = data.encode("utf-8") if isinstance(data, str) else data
            info = tarfile.TarInfo(f"agent_sense-0.1.0/{name}")
            info.size = len(raw)
            sdist.addfile(info, io.BytesIO(raw))


def test_check_dist_passes_a_clean_pair(tmp_path):
    build_pair(tmp_path)
    hits, scope = rc.check_dist(tmp_path, ())
    assert hits == [] and f"{len(WHEEL_FILES) + len(SDIST_FILES)} files" in scope


def test_check_dist_reports_stray_files_private_text_binaries_and_size(tmp_path, monkeypatch):
    build_pair(tmp_path, extra={"runs/records.json": "{}", "agent_sense/blob.py": b"\0\1\2"}, text=f"# {PERSONAL}\n")
    monkeypatch.setattr(rc, "MAX_ARCHIVE_BYTES", 50)
    hits, _ = rc.check_dist(tmp_path, ())
    kinds = {hit.split(": ")[1] for hit in hits}
    assert kinds == {"unexpected-file", "email", "binary-file", "too-large"}
    assert "agent_sense-0.1.0-py3-none-any.whl: unexpected-file: runs/records.json" in hits
    assert "agent_sense-0.1.0.tar.gz: unexpected-file: runs/records.json" in hits
    assert f"agent_sense-0.1.0.tar.gz:README.md:1: email: {PERSONAL}" in hits


def test_check_dist_needs_exactly_one_wheel_and_one_sdist(tmp_path):
    with pytest.raises(RuntimeError, match="expected one wheel and one sdist, found 0 and 0"):
        rc.check_dist(tmp_path, ())
    assert rc.main(["dist", str(tmp_path)]) == 2 and rc.main(["dist"]) == 2


SHA = "11d5960a326750d5838078e36cf38b85af677262"


def test_actions_must_be_a_full_sha_with_the_version_beside_it():
    workflow = "\n".join([
        "steps:",
        f"  - uses: actions/checkout@{SHA}   # v4.4.0",
        f"    uses: owner/repo/path@{SHA} # v1.2.3",
        "  - uses: ./.github/actions/local",
        "  - uses: actions/checkout@v4",
        "  - uses: owner/repo@main",
        f"  - uses: owner/repo@{SHA}",
        f"  - uses: owner/repo@{SHA[:12]}   # v1.0.0",
        f"  - uses: owner/repo@{SHA}   # latest",
        "  - run: echo uses: nothing@v1"])
    assert [line for line, _ in rc.unpinned_actions(workflow)] == [5, 6, 7, 8, 9]


def test_index_verdict_free_ours_or_somebody_elses():
    ours = "https://github.com/owner/tool"
    mine = {"info": {"version": "0.1.0", "summary": "s", "project_urls": {"Homepage": ours + "/"}}}
    theirs = {"info": {"version": "0.2.0", "summary": "another SDK", "project_urls": {"Homepage": "https://github.com/other/tool"}}}
    prefix = {"info": {"version": "1", "summary": "s", "home_page": ours + "-plus"}}       # a longer name is not this repository
    assert rc.index_verdict(404, {}, "tool", ours) is None
    assert rc.index_verdict(200, mine, "tool", ours) is None
    assert "taken by another project: version 0.2.0" in rc.index_verdict(200, theirs, "tool", ours)
    assert "taken by another project" in rc.index_verdict(200, prefix, "tool", ours)
    assert "taken by another project" in rc.index_verdict(200, {}, "tool", ours)
    assert "answered 503" in rc.index_verdict(503, {}, "tool", ours)


def test_this_repository_has_no_private_text_in_tracked_files():
    hits, scope = rc.check_tree(rc.read_names(None))
    assert hits == [], "\n".join(hits)
    assert "tracked and new files" in scope


def test_this_repository_is_consistent_for_a_release():
    """Version in three places, changelog entry, MIT in both places, pinned actions, no relative link in the README."""
    hits, _ = rc.check_meta()
    assert hits == [], "\n".join(hits)


def git(repo, *args, email="1+dev" + AT + "users.noreply.github.com"):
    import subprocess
    env = {"GIT_AUTHOR_NAME": "dev", "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": "dev", "GIT_COMMITTER_EMAIL": email,
           "GIT_CONFIG_GLOBAL": str(repo / "no-such-config"), "GIT_CONFIG_NOSYSTEM": "1", "PATH": __import__("os").environ["PATH"]}
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args], cwd=repo, check=True, capture_output=True, env=env)


def test_history_scan_reads_removed_lines_messages_and_author_addresses(tmp_path, monkeypatch):
    """What was committed once stays in the history: a later commit that deletes it does not clean it."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "notes.md").write_text(f"data lives in {HOME_MAC}\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "first", email=PERSONAL)
    (tmp_path / "notes.md").write_text("data lives in the data directory\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "tidy notes\n\nmoved off the kiku-pc machine")
    monkeypatch.setattr(rc, "ROOT", tmp_path)
    assert rc.check_tree(("kiku-pc",))[0] == []                                   # the files as they are now are clean
    hits, scope = rc.check_history(("kiku-pc",))
    kinds = sorted(hit.split(": ")[1].split(":")[0] for hit in hits)
    assert scope == "2 commits on all refs"
    assert kinds == ["added home-path", "author-email", "private-name", "removed home-path"], hits   # author and committer are one line
    assert any(" message: private-name: kiku-pc" in hit for hit in hits)
    assert any(hit.endswith(f"notes.md: added home-path: {HOME_MAC.rsplit('/', 2)[0]}") for hit in hits), hits

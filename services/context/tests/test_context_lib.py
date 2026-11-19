import argparse
import subprocess
from pathlib import Path

import context_lib


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def check(root: Path, config: dict | None = None, **options) -> list[dict]:
    return context_lib.run(argparse.Namespace(root=root, config=config or {}, **options))["findings"]


def found(findings: list[dict]) -> list[tuple]:
    return [(finding["rule"], finding.get("line"), finding["detail"].get("path") or finding["detail"].get("script")
             or finding["detail"].get("target")) for finding in findings]


BASE = {"scripts/test.sh": "", "services/api/app.py": "", "docs/guide.md": "", "README.md": ""}


def test_interface_matches_the_contract():
    assert context_lib.NAME == "context" and context_lib.SUMMARY
    assert set(context_lib.RULES) == {"missing-path", "missing-script", "memory-dangling", "memory-unindexed", "oversized"}
    context_lib.add_arguments(argparse.ArgumentParser())


def test_missing_paths_in_code_spans_links_fences_and_imports(tmp_path):
    root = project(tmp_path, {**BASE, "CLAUDE.md": (
        "Run `scripts/test.sh` and `scripts/lint.sh` before committing.\n"
        "The API lives in `services/api/app.py:47` and `services/api/routes.py:12`.\n"
        "See [the guide](docs/guide.md#setup) and [old notes](docs/notes.md).\n"
        "```bash\n./scripts/test.sh --fast\nuv run python services/api/migrate.py --apply\n```\n"
        "@docs/extra.md\n")})
    assert found(check(root)) == [
        ("missing-path", 1, "scripts/lint.sh"), ("missing-path", 2, "services/api/routes.py"),
        ("missing-path", 3, "docs/notes.md"), ("missing-path", 6, "services/api/migrate.py"),
        ("missing-path", 8, "docs/extra.md")]
    assert all(finding["severity"] == "warn" and finding["file"] == "CLAUDE.md" for finding in check(root))


def test_things_that_are_not_stale_paths_are_left_alone(tmp_path):
    root = project(tmp_path, {**BASE, ".gitignore": "runs/\n", "runs/.keep": "", "CLAUDE.md": (
        "Not \"there's an issue in the classifier.\" It's `food_vision/classifier.py:47`.\n"
        "Each service lives under `services/<service-name>/` or `services/{name}/x.py`, globs like `services/*/tests`.\n"
        "Write to `/tmp/<job-name>/progress.log`, `/tmp/job/progress.log`, `~/.claude/settings.json`.\n"
        "Use `fail/warn`, `and/or`, `application/json`, `origin/main`, `owner/repo`, `mac/ios`, `1/2`, `v1.2.3`.\n"
        "Fetch `https://example.com/a/b.json`, set `MODE=fast/slow`, run `rm -rf`, `git push --force`.\n"
        "Outputs go to `runs/latest/results.jsonl`. Files such as `MEMORY.md`, `package.json`, `.env` and `app.py`.\n"
        "Release notes go to `docs/release-notes/vX.Y.Z.md`, placeholders `path/to/file.py` and `docs/foo.md`.\n"
        "```\ntail -f /tmp/<job-name>/progress.log\nservices/<name>/README.md\n```\n"
        "Image ![alt](docs/missing.png) and anchor [top](#top), mail [me](mailto:a@b.c).\n")})
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert found(check(root)) == []


def test_strict_also_reports_paths_whose_directory_does_not_exist(tmp_path):
    root = project(tmp_path, {**BASE, "AGENTS.md": "See `food_vision/classifier.py` and `settings.yaml`.\n"})
    assert found(check(root)) == []
    assert found(check(root, strict=True)) == [("missing-path", 1, "food_vision/classifier.py"),
                                               ("missing-path", 1, "settings.yaml")]


def test_paths_resolve_from_the_root_or_from_the_instruction_file(tmp_path):
    root = project(tmp_path, {**BASE, "services/api/CLAUDE.md": "Edit `app.py`, `./app.py`, `scripts/test.sh`, "
                                                                "`../../docs/guide.md` and `tests/test_app.py`.\n",
                              "services/api/tests/.keep": ""})
    assert found(check(root)) == [("missing-path", 1, "tests/test_app.py")]


def test_absolute_paths_under_the_project_are_checked(tmp_path):
    root = project(tmp_path, {**BASE})
    (root / "CLAUDE.md").write_text(f"Use `{root}/scripts/test.sh` and `{root}/scripts/gone.sh`, not `/usr/bin/env`.\n")
    assert found(check(root)) == [("missing-path", 1, (root / "scripts/gone.sh").as_posix())]


def test_windows_absolute_paths_are_read_with_either_slash():
    assert context_lib.path_candidate(r"C:\proj\scripts\gone.sh", "code") == "C:/proj/scripts/gone.sh"
    assert context_lib.path_candidate("C:/proj/scripts/gone.sh", "code") == "C:/proj/scripts/gone.sh"
    assert context_lib.path_candidate(r"scripts\gone.sh", "code") is None   # a backslash elsewhere is an escape, not a path


def test_missing_npm_scripts_and_make_targets(tmp_path):
    root = project(tmp_path, {**BASE, "package.json": '{"scripts": {"dev": "vite", "test:unit": "vitest"}}',
                              "Makefile": "build: deps\n\tgo build\ndeps lint:\n\ttrue\nVAR := 1\n",
                              "CLAUDE.md": (
        "Run `npm run dev`, `npm run typecheck`, `pnpm test:unit`, `pnpm lint`, `yarn build`, `bun run e2e`.\n"
        "Builtins: `npm install`, `npm test`, `pnpm add zod`, `yarn install`, `npm run <script>`, `pnpm dlx x`.\n"
        "```\nmake build\nmake -j4 lint\nmake release\n```\nPlease make sure it works.\n")})
    assert found(check(root)) == [
        ("missing-script", 1, "typecheck"), ("missing-script", 1, "lint"), ("missing-script", 1, "build"),
        ("missing-script", 1, "e2e"), ("missing-script", 6, "release")]


def test_commands_are_not_checked_without_a_package_json_or_makefile(tmp_path):
    root = project(tmp_path, {**BASE, "CLAUDE.md": "Run `npm run build` and `make all`.\n"})
    assert found(check(root)) == []


def test_which_files_count_as_instructions(tmp_path):
    stale = "See `scripts/gone.sh`.\n"
    root = project(tmp_path, {**BASE, "CLAUDE.md": stale, "sub/AGENTS.md": stale, "GEMINI.md": stale,
                              ".cursorrules": stale, ".claude/commands/ship.md": stale, ".cursor/rules/a.mdc": stale,
                              ".claude/worktrees/w1/CLAUDE.md": stale, ".claude/worktrees/w1/docs/x.md": stale,
                              "docs/other.md": stale, "node_modules/pkg/CLAUDE.md": stale})
    files = sorted({finding["file"] for finding in check(root)})
    assert files == [".claude/commands/ship.md", ".cursor/rules/a.mdc", ".cursorrules", "CLAUDE.md", "GEMINI.md", "sub/AGENTS.md"]
    assert sorted({finding["file"] for finding in check(root, files=["docs/*.md"])}) == ["docs/other.md"]
    assert sorted({finding["file"] for finding in check(root, files=["./CLAUDE.md"])}) == ["CLAUDE.md"]


def test_oversized_file_is_information_only(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": "word " * 2000})
    assert found(check(root)) == []
    findings = check(root, max_bytes=5000)
    assert [(finding["rule"], finding["severity"]) for finding in findings] == [("oversized", "info")]
    assert findings[0]["detail"]["bytes"] == 10000


def test_memory_directory_index_and_links(tmp_path):
    root = project(tmp_path / "proj", {**BASE})
    memory = project(tmp_path / "memory", {
        "MEMORY.md": "- [GPU](gpu.md) - host\n- [Gone](gone.md) - removed\n- [Docs](https://example.com/x.md)\n",
        "gpu.md": "---\nname: build-box\ndescription: d\n---\nSee [[budget-note]] and [[missing-note]], script `scripts/gone.sh`.\n",
        "budget.md": "---\nname: budget-note\n---\nLinks back to [[build-box]] and `[[name]]` syntax.\n"})
    findings = check(root, memory=[str(memory)])
    assert found(findings) == [
        ("memory-dangling", 2, "gone.md"), ("memory-unindexed", None, "budget.md"),
        ("memory-dangling", 5, "missing-note"), ("missing-path", 5, "scripts/gone.sh")]
    assert [finding["severity"] for finding in findings] == ["warn", "warn", "info", "warn"]
    assert findings[0]["file"] == "memory/MEMORY.md"


def test_memory_directory_without_an_index_or_missing(tmp_path):
    import pytest

    root = project(tmp_path / "proj", {**BASE})
    memory = project(tmp_path / "memory", {"a.md": "x\n"})
    assert found(check(root, memory=[str(memory)])) == [("memory-unindexed", None, None)]
    with pytest.raises(RuntimeError):
        check(root, memory=[str(tmp_path / "absent")])


def test_ignore_patterns_and_severity_overrides(tmp_path):
    root = project(tmp_path, {**BASE, "CLAUDE.md": "`scripts/a.sh` `scripts/b.sh`\n"})
    assert found(check(root, config={"ignore": ["scripts/a.*"]})) == [("missing-path", 1, "scripts/b.sh")]
    assert check(root, config={"severity": {"missing-path": "fail"}})[0]["severity"] == "fail"
    assert check(root, config={"severity": {"missing-path": "off"}}) == []


def test_findings_satisfy_the_contract(tmp_path):
    from contracts import schema

    root = project(tmp_path, {**BASE, "package.json": "{}", "CLAUDE.md": "`scripts/a.sh` `npm run nope`\n" + "y" * 50})
    result = context_lib.run(argparse.Namespace(root=root, config={"max-bytes": 10}))
    assert len(result["findings"]) == 3 and result["stats"]["instruction_files"] == 1
    schema.validate_result(result, context_lib.RULES)


def test_path_written_with_the_project_folder_name_in_front(tmp_path):
    root = project(tmp_path / "myproj", {**BASE, "CLAUDE.md": "See `myproj/scripts/test.sh` and `myproj/scripts/gone.sh`.\n"})
    assert found(check(root)) == [("missing-path", 1, "myproj/scripts/gone.sh")]


def test_a_path_whose_directory_is_gone_too_is_reported_as_info_when_its_first_directory_exists(tmp_path):
    root = project(tmp_path, {**BASE, "app/api/routes.py": ""})
    (root / "CLAUDE.md").write_text("Handlers live in `app/cli/utils.py`. Example: `food_vision/models/classifier.py`. See `app/gone.py`.\n")
    result = context_lib.run(argparse.Namespace(root=root, config={}))
    assert [(f["severity"], f["detail"]["path"]) for f in result["findings"]] == [
        ("info", "app/cli/utils.py"),   # app/ exists, app/cli/ does not: a removed package
        ("warn", "app/gone.py")]        # the illustration under a directory that never existed stays silent
    assert result["findings"][0]["detail"]["rooted"] is True


def test_a_directory_must_exist_with_the_same_spelling(tmp_path):
    root = project(tmp_path, {**BASE})
    (root / "Vendor").mkdir()            # untracked, so only the disk can answer
    (root / "CLAUDE.md").write_text("The mirror is `vendor/app`, the fork `Vendor/app`.\n")
    assert found(check(root)) == [("missing-path", 1, "Vendor/app")]


def test_rooted_counts_the_directories_of_the_path_as_written_not_of_the_instruction_file(tmp_path):
    root = project(tmp_path, {**BASE, "packages/app/src/index.ts": ""})
    (root / "packages/app/AGENTS.md").write_text("Branch `origin/dev`, model `openai/gpt-5`, files `.spec.ts`, but `src/old/thing.ts`.\n")
    result = context_lib.run(argparse.Namespace(root=root, config={}))
    assert [(f["severity"], f["detail"]["path"]) for f in result["findings"]] == [("info", "src/old/thing.ts")]

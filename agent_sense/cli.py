"""agent-sense: local checks for work done with coding agents. No model, no network service.

    agent-sense done --run        run the tests and record that they passed on these files
    agent-sense done              is "done" true? tests current, tests not weakened, work committed
    agent-sense deps              imports that no manifest declares
    agent-sense context           CLAUDE.md / AGENTS.md / memory that point at things that are gone
    agent-sense boundary          one service reaching into another
    agent-sense features          the feature list: is every feature marked passing backed by a run?
    agent-sense page URL          what the agent cannot see: errors, overflow, dead buttons, IME Enter
    agent-sense launch URL        Japan launch check: third-party tags vs. the notice, required pages
    agent-sense session           what went wrong repeatedly in past sessions, from the local logs
    agent-sense all               the checks that need no browser, in one run (for pre-commit)
    agent-sense init              write .agent-sense.toml and show the Claude Code hook
    agent-sense hook stop         Claude Code Stop hook: refuse to stop while a check fails

Exit code 0 when nothing fails, 1 when a check fails, 2 when the tool itself cannot run.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
# installed: services are shipped inside the package. In the repository: the services/ directory.
INSTALLED = (PACKAGE / "_services").is_dir()
SERVICES_DIR = PACKAGE / "_services" if INSTALLED else PACKAGE.parent / "services"
SERVICES = ("done", "deps", "context", "boundary", "features", "page", "launch", "session")
for _service in SERVICES:
    sys.path.insert(0, str(SERVICES_DIR / _service))
sys.path.insert(0, str(SERVICES_DIR if INSTALLED else PACKAGE.parent))

from contracts import schema  # noqa: E402

CONFIG_FILE = ".agent-sense.toml"
ALL_DEFAULT = ("done", "deps", "context", "boundary")
HOOK_DEFAULT = ("done",)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
HOOK_SETTINGS = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "agent-sense hook stop", "timeout": 60}]}]}}


def clean(text: object, limit: int = 400) -> str:
    """Text from a checked project or page made safe for a terminal."""
    return _CONTROL.sub("", str(text).replace("\n", " ").replace("\t", " "))[:limit]


def load(name: str):
    """The service module, or None when it is not part of this build."""
    try:
        return importlib.import_module(f"{name}_lib")
    except ModuleNotFoundError as error:
        if error.name == f"{name}_lib":
            return None
        raise


def find_root(start: Path) -> Path:
    done = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True)
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else start


def read_config(root: Path) -> dict:
    path = root / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(f"{CONFIG_FILE} is not valid TOML: {error}") from error


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="project directory (default: the git repository around the current directory)")
    parser.add_argument("--fail-on", choices=("fail", "warn", "never"), default="fail")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--out", help="also write the JSON report to this file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-sense", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in SERVICES:
        module = load(name)
        if module is None:
            continue
        sub = commands.add_parser(name, help=module.SUMMARY, description=module.__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
        add_common(sub)
        module.add_arguments(sub)
    everything = commands.add_parser("all", help="run the checks that need no browser")
    add_common(everything)
    everything.add_argument("--checks", help=f"comma separated (default: [all] checks in {CONFIG_FILE}, "
                                             f"else {','.join(ALL_DEFAULT)})")
    init = commands.add_parser("init", help=f"write {CONFIG_FILE} and show the Claude Code hook")
    init.add_argument("--root")
    init.add_argument("--hook", action="store_true", help="also add the Stop hook to .claude/settings.json of the project")
    hook = commands.add_parser("hook", help="entry points for Claude Code hooks")
    hook.add_argument("event", choices=("stop",))
    return parser


def run_service(name: str, args: argparse.Namespace, root: Path, config: dict) -> dict:
    module = load(name)
    if module is None:
        raise RuntimeError(f"the {name} check is not available in this build")
    args.root, args.config = root, dict(config.get(name, {}))
    result = schema.validate_result(module.run(args), module.RULES)
    return schema.build_report(name, result)


def default_args(name: str) -> argparse.Namespace:
    """The service's options at their defaults, for `all` and the hook."""
    parser = argparse.ArgumentParser()
    load(name).add_arguments(parser)
    return parser.parse_args([])


def format_text(reports: list[dict]) -> str:
    lines = []
    for report in reports:
        summary = report["summary"]
        lines.append(f"{report['verdict'].upper():4} {report['check']}  "
                     f"({summary['fail']} fail, {summary['warn']} warn, {summary['info']} info)")
        for finding in report["findings"]:
            lines.append(f"  [{finding['severity']}] {finding['rule']}: {clean(finding['message'])}")
            if finding.get("file"):
                at = clean(finding["file"], 200) + (f":{finding['line']}" if finding.get("line") else "")
                lines.append(f"      at {at}")
            for extra in finding.get("detail", {}).get("lines", [])[:30]:
                lines.append(f"      | {clean(extra, 200)}")
            if finding.get("fix"):
                lines.append(f"      fix: {clean(finding['fix'])}")
    return "\n".join(lines)


def emit(reports: list[dict], args: argparse.Namespace) -> int:
    payload = reports[0] if len(reports) == 1 else {"version": schema.REPORT_VERSION, "reports": reports}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.format == "json" else format_text(reports))
    return max(schema.exit_code(report, args.fail_on) for report in reports)


def init(root: Path, write_hook: bool) -> int:
    path = root / CONFIG_FILE
    if path.exists():
        print(f"{path} already exists, left as it is")
    else:
        done = load("done")
        command = done.detect_test_command(root) if done else None
        test_line = f'test = "{command}"' if command else '# test = "your test command"'
        path.write_text(
            "# agent-sense: local checks for work done with coding agents\n"
            f"[done]\n{test_line}\n# ignore = [\"*.md\", \"docs/*\"]   # edits here do not make the test run stale\n\n"
            "[hook]\nchecks = [\"done\"]   # checks the Stop hook runs; it blocks only on a failing finding\n\n"
            "[all]\nchecks = [\"done\", \"deps\", \"context\", \"boundary\"]\n", encoding="utf-8")
        print(f"wrote {path}")
    settings = root / ".claude" / "settings.json"
    if write_hook:
        current = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
        stops = current.setdefault("hooks", {}).setdefault("Stop", [])
        if "agent-sense hook stop" in json.dumps(stops):
            print(f"{settings} already has the hook")
        else:
            stops.extend(HOOK_SETTINGS["hooks"]["Stop"])
            settings.parent.mkdir(exist_ok=True)
            settings.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"added the Stop hook to {settings}")
    else:
        print(f"To make Claude Code refuse to stop while a check fails, run `agent-sense init --hook`,\n"
              f"or add this to {settings}:\n{json.dumps(HOOK_SETTINGS, indent=2)}")
    return 0


def hook_stop(stdin_text: str) -> int:
    """Claude Code Stop hook. Blocks (JSON decision on stdout) while a configured check fails.

    Silent and permissive whenever it cannot judge: no config in the project, a stop that is
    already the result of this hook, or an internal error. A hook must never trap a session.
    """
    try:
        event = json.loads(stdin_text or "{}")
        if event.get("stop_hook_active"):
            return 0
        root = find_root(Path(event.get("cwd") or "."))
        if not (root / CONFIG_FILE).is_file():
            return 0
        config = read_config(root)
        names = [name for name in config.get("hook", {}).get("checks", HOOK_DEFAULT) if load(name)]
    except Exception as error:  # noqa: BLE001
        print(f"agent-sense hook: skipped ({type(error).__name__}: {clean(error, 200)})", file=sys.stderr)
        return 0
    reports = []
    for name in names:   # one check that cannot run must not switch the others off
        try:
            reports.append(run_service(name, default_args(name), root, config))
        except Exception as error:  # noqa: BLE001
            print(f"agent-sense hook: {name} skipped ({type(error).__name__}: {clean(error, 200)})", file=sys.stderr)
    failing = [dict(report, findings=[f for f in report["findings"] if f["severity"] == "fail"]) for report in reports]
    failing = [report for report in failing if report["findings"]]
    if not failing:
        return 0
    reason = ("agent-sense: this cannot be reported as done yet.\n" + format_text(failing) +
              "\nResolve each finding, or if one is wrong tell the user which one and why. "
              "Do not edit tests, the config or the hook to make it pass.")
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hook":
        return hook_stop(sys.stdin.read())
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    if args.command == "init":
        return init(root, args.hook)
    try:
        config = read_config(root)
        if args.command == "all":
            names = ([part.strip() for part in args.checks.split(",") if part.strip()] if args.checks
                     else list(config.get("all", {}).get("checks", ALL_DEFAULT)))
            unknown = [name for name in names if name not in SERVICES]
            if unknown:
                raise RuntimeError(f"unknown check {', '.join(unknown)}; choose from {', '.join(SERVICES)}")
            reports = [run_service(name, default_args(name), root, config) for name in names]
        else:
            reports = [run_service(args.command, args, root, config)]
    except RuntimeError as error:
        print(f"agent-sense: {error}", file=sys.stderr)
        return 2
    return emit(reports, args)


if __name__ == "__main__":
    raise SystemExit(main())

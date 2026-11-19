"""Browser eval for the page service: known-answer pages through a real Chromium.

    uv run --no-project --python 3.12 --with playwright==1.65.2 python evals/page_eval.py [--channel chrome]
    uv run ... python evals/page_eval.py --real https://example.org https://developer.mozilla.org

Every fixture under evals/page_fixtures/ lists the findings it must produce (rule, severity,
count, and a piece of the selector). A fixture passes when the fail and warn findings match
exactly: nothing missing, nothing extra. The run passes when the share of passing fixtures
reaches THRESHOLD. --real runs read-only (no clicking, no typing) against public pages and
prints the counts per rule, as a check for noise rather than a known answer.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "evals" / "page_fixtures"
sys.path.insert(0, str(ROOT / "services" / "page"))
sys.path.insert(0, str(ROOT))

import page_lib  # noqa: E402
from contracts import schema  # noqa: E402

THRESHOLD = 1.0

# file -> (options, [(rule, severity, count, selector part or None)]). Info findings are listed
# only where the fixture is there to prove that something is downgraded rather than dropped.
EXPECTED = {
    # Traps: carousel that scrolls inside its box, intended ellipsis, toggle that only flips
    # aria-pressed, button wired by delegation, copy-to-clipboard, scroll-to-top at the top,
    # skip link, IME-guarded chat input.
    "clean.html": ({}, [("text-clipped", "info", 1, "p.name")]),
    "console.html": ({}, [("console-error", "fail", 1, None), ("console-error", "warn", 1, None)]),
    "request.html": ({}, [("request-failed", "fail", 1, None), ("request-failed", "warn", 1, None)]),
    "overflow.html": ({}, [("horizontal-overflow", "fail", 1, "table.prices")]),
    "clipped.html": ({}, [("text-clipped", "warn", 2, "div.badge"), ("text-clipped", "info", 1, "p.trunc")]),
    "overlap.html": ({}, [("overlap", "warn", 1, "button#save")]),
    "dead.html": ({}, [("dead-control", "fail", 4, "button#dead")]),
    "click-error.html": ({}, [("console-error", "fail", 1, "button#broken")]),
    # href="#" and javascript: links are judged by clicking: dead here, so they are dead-control.
    "placeholder.html": ({}, [("placeholder-link", "warn", 2, None), ("placeholder-text", "warn", 3, None),
                              ("dead-control", "fail", 2, "a:nth-of-type(1)")]),
    # Without clicking they cannot be judged and drop to info.
    "placeholder.html (click off)": ({"click": "off"}, [("placeholder-link", "warn", 2, None), ("placeholder-link", "info", 2, None),
                                                        ("placeholder-text", "warn", 3, None)]),
    "title-default.html": ({}, [("default-title", "fail", 1, None)]),
    "title-empty.html": ({}, [("default-title", "fail", 1, None)]),
    "ime-naive.html": ({}, [("ime-enter-submit", "fail", 1, "input#chat")]),
    "ime-fetch.html": ({}, [("ime-enter-submit", "fail", 1, "input#q")]),
    "ime-keyup.html": ({}, [("ime-enter-submit", "fail", 1, "input#chat")]),
    "ime-safari.html": ({}, [("ime-enter-submit", "warn", 1, "input#chat")]),
    "ime-guarded.html": ({}, []),
    "ime-form.html": ({}, []),
}
# Fixtures that must stay silent for a rule: the negative for every rule.
NEGATIVE_FOR = {
    "console-error": "clean.html", "request-failed": "clean.html", "horizontal-overflow": "clean.html",
    "text-clipped": "clean.html", "overlap": "clean.html", "dead-control": "clean.html",
    "placeholder-link": "clean.html", "placeholder-text": "clean.html", "default-title": "clean.html",
    "ime-enter-submit": "ime-guarded.html",
}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory: Path):
    handler = functools.partial(_Quiet, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def namespace(urls: list[str], channel: str | None, **options) -> argparse.Namespace:
    values = {"root": ROOT, "config": {}, "urls": urls, "widths": None, "click": None, "ime": None, "channel": channel,
              "timeout": None, "wait": None, "max_controls": None}
    values.update(options)
    return argparse.Namespace(**values)


def counts(findings: list[dict], include_info: bool) -> Counter:
    return Counter((finding["rule"], finding["severity"]) for finding in findings
                   if include_info or finding["severity"] != "info")


def judge(name: str, findings: list[dict]) -> list[str]:
    """Problems with one fixture's findings; empty when it matches."""
    _, expected = EXPECTED[name]
    problems = []
    want = Counter({(rule, severity): count for rule, severity, count, _ in expected if severity != "info"})
    got = counts(findings, include_info=False)
    for key in sorted(set(want) | set(got)):
        if want[key] != got[key]:
            problems.append(f"{key[0]} [{key[1]}]: expected {want[key]}, got {got[key]}")
    for rule, severity, count, part in expected:
        matching = [finding for finding in findings if finding["rule"] == rule and finding["severity"] == severity]
        if severity == "info" and len(matching) != count:
            problems.append(f"{rule} [info]: expected {count}, got {len(matching)}")
        if part and not any(part in json.dumps(finding["detail"]) or part in finding["message"] for finding in matching):
            problems.append(f"{rule}: no finding names {part}")
    return problems


def run_fixtures(channel: str | None) -> int:
    server = serve(FIXTURES)
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    started = time.time()
    per_rule = {rule: {"expected": 0, "got": 0, "fixtures_ok": 0, "fixtures": 0} for rule in page_lib.RULES}
    passed, lines = 0, []
    try:
        for name, (options, expected) in EXPECTED.items():
            result = page_lib.run(namespace([base + name.split(' ')[0]], channel, **options))
            schema.validate_result(result, page_lib.RULES)
            findings = result["findings"]
            problems = judge(name, findings)
            passed += not problems
            lines.append(f"{'ok  ' if not problems else 'FAIL'} {name}" + "".join(f"\n       {problem}" for problem in problems))
            if problems:
                lines += [f"       got: [{f['severity']}] {f['rule']}: {f['message'][:150]}" for f in findings]
            got = counts(findings, include_info=False)
            want = Counter()
            for rule, severity, count, _ in expected:
                if severity != "info":
                    want[rule] += count
            for rule in page_lib.RULES:
                found = sum(count for (found_rule, _), count in got.items() if found_rule == rule)
                involved = want[rule] or found or NEGATIVE_FOR.get(rule) == name
                if involved:
                    per_rule[rule]["fixtures"] += 1
                    per_rule[rule]["fixtures_ok"] += want[rule] == found
                per_rule[rule]["expected"] += want[rule]
                per_rule[rule]["got"] += found
    finally:
        server.shutdown()
    print("\n".join(lines))
    print("\nrule                  expected  got  fixtures matching")
    for rule, row in per_rule.items():
        print(f"{rule:22}{row['expected']:8}{row['got']:5}  {row['fixtures_ok']}/{row['fixtures']}")
    share = passed / len(EXPECTED)
    verdict = "PASS" if share >= THRESHOLD else "FAIL"
    print(f"\n{verdict}: {passed}/{len(EXPECTED)} fixtures match (threshold {THRESHOLD:.0%}), {time.time() - started:.0f}s")
    return 0 if share >= THRESHOLD else 1


def run_real(urls: list[str], channel: str | None) -> int:
    for url in urls:
        result = page_lib.run(namespace([url], channel, click="off", ime="off"))
        schema.validate_result(result, page_lib.RULES)
        tally = counts(result["findings"], include_info=True)
        print(f"{url}\n  " + (", ".join(f"{rule} [{severity}] x{count}" for (rule, severity), count in sorted(tally.items())) or "nothing"))
        for finding in result["findings"]:
            if finding["severity"] != "info":
                print(f"    [{finding['severity']}] {finding['rule']}: {finding['message'][:170]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", help="installed browser to drive (chrome, msedge)")
    parser.add_argument("--real", nargs="+", metavar="URL", help="read-only run against public pages instead of the fixtures")
    args = parser.parse_args()
    return run_real(args.real, args.channel) if args.real else run_fixtures(args.channel)


if __name__ == "__main__":
    raise SystemExit(main())

"""Known-answer eval for the launch check: fixture sites in a real browser, no real vendor contacted.

    uv run --no-project --python 3.12 --with playwright python evals/launch_eval.py [--channel chrome]

Each fixture site under evals/launch_fixtures/<name>/ is served by a local http.server and reached
as http://<name>.example.test/ through a Playwright route; requests to any other host (the vendor
tags the fixtures include) are answered locally with an empty body. A fixture passes when the set
of fail and warn rules equals the expected set and the extra check on details holds.
Pass threshold: every fixture (10 of 10). Exit code 1 below that.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "evals" / "launch_fixtures"
sys.path.insert(0, str(ROOT / "services" / "launch"))
sys.path.insert(0, str(ROOT))

import launch_lib  # noqa: E402
from contracts import schema  # noqa: E402


def detail(rule: str, key: str):
    return lambda findings: next(finding["detail"][key] for finding in findings if finding["rule"] == rule)


# name -> (expected fail/warn rules, optional check over the findings, description of that check)
EXPECTED = {
    "good": (set(), None, ""),
    "undisclosed": ({"external-undisclosed"},
                    lambda found: [f["detail"]["vendor"] for f in found if f["rule"] == "external-undisclosed"] == ["Microsoft Clarity"],
                    "only Microsoft Clarity is reported"),
    "paid-missing": ({"tokushoho-missing"}, None, ""),
    "paid-incomplete": ({"tokushoho-incomplete"},
                        lambda found: detail("tokushoho-incomplete", "missing")(found) == [
                            "address (所在地)", "phone number (電話番号)", "payment time (支払時期)"],
                        "address, phone and payment time are listed as missing"),
    "paid-complete": (set(), None, ""),
    "free": (set(), None, ""),
    "notice-page": (set(), None, ""),
    "test-key": ({"test-key"}, lambda found: detail("test-key", "source")(found).endswith("/app.js"), "the key is located in app.js"),
    "localhost": ({"localhost-reference"},
                  lambda found: sum(1 for f in found if f["rule"] == "localhost-reference") == 2, "the link and the image are both reported"),
    "bare": ({"policy-missing", "terms-missing", "contact-missing"}, None, ""),
}


def serve() -> tuple[http.server.ThreadingHTTPServer, int]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def make_route(port: int):
    async def route(found) -> None:
        parts = urlsplit(found.request.url)
        host = parts.hostname or ""
        if not host.endswith(".example.test"):
            await found.fulfill(status=200, content_type="application/javascript", body="")
            return
        site, path = host.split(".")[0], parts.path if parts.path != "/" else "/index.html"
        folder = site if (FIXTURES / site / path.lstrip("/")).is_file() else "_common"
        response = await found.fetch(url=f"http://127.0.0.1:{port}/{folder}{path}")
        await found.fulfill(response=response)
    return route


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel")
    options = parser.parse_args()
    server, port = serve()
    passed = 0
    try:
        for name, (expected, extra, what) in EXPECTED.items():
            args = argparse.Namespace(root=ROOT, config={}, url=f"http://{name}.example.test/", pages=6, policy=None, paid=None,
                                      channel=options.channel, timeout=15000, wait=0, route=make_route(port))
            result = schema.validate_result(launch_lib.run(args), launch_lib.RULES)
            got = {finding["rule"] for finding in result["findings"] if finding["severity"] in ("fail", "warn")}
            good = got == expected and (extra is None or bool(extra(result["findings"])))
            passed += good
            print(f"{'PASS' if good else 'FAIL'} {name}: expected {sorted(expected) or 'nothing'}, got {sorted(got) or 'nothing'}"
                  + (f"; check: {what}" if what else ""))
            if not good:
                for finding in result["findings"]:
                    print(f"      [{finding['severity']}] {finding['rule']}: {finding['message']}")
    finally:
        server.shutdown()
    print(f"launch eval: {passed}/{len(EXPECTED)} fixtures (threshold {len(EXPECTED)}/{len(EXPECTED)})")
    return 0 if passed == len(EXPECTED) else 1


if __name__ == "__main__":
    raise SystemExit(main())

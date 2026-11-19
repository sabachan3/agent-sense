"""Field run of the launch check on real sites (read-only): rates per rule, and the records for a hand check.

    uv run --no-project --python 3.12 --with playwright python evals/launch_field.py URLS.txt [--count 30] [--workers 2]

Takes every k-th URL of the file so the sample is spread over it. Progress goes to
/tmp/launch-field/progress.log (follow it with: tail -f /tmp/launch-field/progress.log), results to
runs/launch_field.json and the recorded policy texts to runs/launch_field_records.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "launch"))
sys.path.insert(0, str(ROOT))

import launch_lib  # noqa: E402
from contracts import schema  # noqa: E402

PROGRESS = Path("/tmp/launch-field/progress.log")


def progress(done: int, total: int, started: float, errors: int, last: str) -> None:
    elapsed = time.time() - started
    rate = done / elapsed if elapsed else 0.0
    eta = (total - done) / rate if rate else 0.0
    line = (f"{datetime.now().isoformat(timespec='seconds')} launch-field {done}/{total} ({done / total:.0%}) "
            f"rate {rate * 60:.1f} sites/min eta {eta:.0f}s errors {errors} last {last}")
    print(line, flush=True)
    with PROGRESS.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def field(urls: list[str], workers: int, channel: str | None, pages: int) -> tuple[list[dict], list[dict]]:
    from playwright.async_api import async_playwright

    vendors = launch_lib.load_vendors()
    results, records = [None] * len(urls), [None] * len(urls)
    state = {"done": 0, "errors": 0}
    started = time.time()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=channel)
        gate = asyncio.Semaphore(workers)

        async def one(index: int, url: str) -> None:
            async with gate:
                try:
                    record = await asyncio.wait_for(launch_lib.crawl(url, pages=pages, timeout=20000, browser=browser), 240)
                except Exception as error:
                    record = {"url": url, "pages": [{"url": url, "error": f"{type(error).__name__}: {str(error)[:120]}"}]}
                result = schema.validate_result(launch_lib.analyse(record, vendors), launch_lib.RULES)
                results[index] = {"url": url, **result}
                records[index] = {"url": url, "documents": record.get("documents", {}),
                                  "pages": [page.get("final_url") or page["url"] for page in record["pages"]]}
                state["done"] += 1
                state["errors"] += any(finding["rule"] == "page-error" for finding in result["findings"])
                progress(state["done"], len(urls), started, state["errors"], url)

        try:
            await asyncio.gather(*(one(index, url) for index, url in enumerate(urls)))
        finally:
            await browser.close()
    return results, records


def summarise(results: list[dict]) -> dict:
    loaded = [result for result in results if not any(f["rule"] == "page-error" for f in result["findings"])]
    sites = Counter()
    for result in loaded:
        for key in {(finding["rule"], finding["severity"]) for finding in result["findings"]}:
            sites[key] += 1
    return {"sites": len(results), "loaded": len(loaded),
            "paid": sum(1 for result in loaded if result["stats"].get("paid")),
            "with_policy_text": sum(1 for result in loaded if result["stats"].get("notice_chars", 0) >= launch_lib.MIN_NOTICE_CHARS),
            "sites_per_rule": {f"{rule} ({severity})": count for (rule, severity), count in sorted(sites.items())}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls_file")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--channel")
    parser.add_argument("--out", default=str(ROOT / "runs" / "launch_field.json"))
    args = parser.parse_args()
    lines = [line.strip() for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    step = max(1, len(lines) // args.count)
    urls = lines[::step][:args.count]
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    print(f"follow progress with: tail -f {PROGRESS}", flush=True)
    results, records = asyncio.run(field(urls, args.workers, args.channel, args.pages))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = summarise(results)
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out.with_name(out.stem + "_records.json").write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

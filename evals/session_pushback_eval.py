"""Eval for the pushback heuristic of the session check (session_lib.pushback).

A. Development cases, evals/session_pushback_cases.json: sentences written for this eval and used
   while the patterns were tuned. Threshold: precision and recall both 0.95.
B. Held-out cases, evals/session_pushback_heldout.json: written by a separate agent that never saw
   the patterns, and never used for tuning. This is the number to quote. Threshold: the floors
   below, set under the first measurement so that a regression fails the eval.
C. Field, optional: the user messages of the local Claude Code logs, labelled by hand. The labels
   live in runs/session_pushback_labels.json (git-ignored, keyed by a hash of the message start, so
   no message text is stored). Prints counts only.

    uv run --no-project --python 3.12 evals/session_pushback_eval.py
    uv run --no-project --python 3.12 evals/session_pushback_eval.py --dump    # list unlabelled field messages

To label: run with --dump, read runs/session_pushback_unlabelled.json, and add "hash": 1, 0 or
null (unclear, left out of the count) to the labels file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "session"))
import session_lib  # noqa: E402

DEV_FLOOR = {"precision": 0.95, "recall": 0.95}
HELDOUT_FLOOR = {"precision": 0.70, "recall": 0.55}   # first measurement: 0.75 and 0.60


def score(pairs: list[tuple[int, str]], show: bool) -> dict:
    """Precision and recall of session_lib.pushback over (label, text) pairs."""
    tp = fp = fn = tn = 0
    for label, text in pairs:
        phrase = session_lib.pushback(text)
        if phrase and label:
            tp += 1
        elif phrase:
            fp += 1
            if show:
                print(f"    false alarm on {phrase!r}: {text}")
        elif label:
            fn += 1
            if show:
                print(f"    missed: {text}")
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn) if tp + fn else 1.0}


def line(result: dict) -> str:
    return (f"precision {result['precision']:.2f} ({result['tp']}/{result['tp'] + result['fp']}), "
            f"recall {result['recall']:.2f} ({result['tp']}/{result['tp'] + result['fn']})")


def cases(path: Path) -> list[tuple[int, str]]:
    return [(int(label), text) for label, text in json.loads(path.read_text(encoding="utf-8"))["cases"]]


def message_key(text: str) -> str:
    return hashlib.sha1(text[:session_lib.CORRECTION_WINDOW].encode("utf-8")).hexdigest()[:16]


def field_messages(logs: Path) -> list[str]:
    """Every user message of a main session that follows an assistant text, once each."""
    seen, messages = set(), []
    for session in session_lib.load_sessions(logs, None, None):
        events = session["events"]
        for index, event in enumerate(events):
            if event["kind"] != "user" or not any(earlier["kind"] == "assistant" for earlier in events[:index]):
                continue
            key = message_key(event["text"])
            if key not in seen:
                seen.add(key)
                messages.append(event["text"])
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", default=session_lib.DEFAULT_LOGS)
    parser.add_argument("--labels", default=str(ROOT / "runs" / "session_pushback_labels.json"))
    parser.add_argument("--dump", action="store_true", help="write the unlabelled field messages to runs/ for labelling")
    parser.add_argument("--quiet", action="store_true", help="do not print the sentences that were misjudged")
    args = parser.parse_args(argv)
    ok = True
    for title, name, floor in (("A. development cases", "session_pushback_cases.json", DEV_FLOOR),
                               ("B. held-out cases", "session_pushback_heldout.json", HELDOUT_FLOOR)):
        result = score(cases(ROOT / "evals" / name), show=not args.quiet)
        passed = all(result[key] >= floor[key] for key in floor)
        ok = ok and passed
        print(f"{title}: {line(result)}; floor precision {floor['precision']:.2f}, recall {floor['recall']:.2f}: "
              f"{'pass' if passed else 'FAIL'}")
    logs, labels_path = Path(args.logs).expanduser(), Path(args.labels)
    if logs.is_dir() and (labels_path.is_file() or args.dump):
        labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.is_file() else {}
        messages = field_messages(logs)
        pairs = [(labels[message_key(text)], text) for text in messages if labels.get(message_key(text)) is not None]
        unlabelled = [text for text in messages if message_key(text) not in labels]
        print(f"C. field, {len(messages)} messages, {len(pairs)} labelled, {len(unlabelled)} unlabelled: "
              f"{line(score(pairs, show=False))} (no floor: private data, small sample)")
        if args.dump:
            out = ROOT / "runs" / "session_pushback_unlabelled.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({message_key(text): text[:400] for text in unlabelled}, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
            print(f"   unlabelled messages: {out}")
    else:
        print("C. field: skipped (no logs or no labels file)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

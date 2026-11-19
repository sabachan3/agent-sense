"""Count what went wrong, and what was repeated by hand, in local Claude Code session logs.

Claude Code keeps one JSONL transcript per session under ~/.claude/projects/<project>/ and one
per subagent under <project>/<session>/subagents/. This service reads them (nothing leaves the
machine) and counts:

  repeated-failure    the same Bash command, after normalisation, failed several times
  blind-retry         a failing tool call repeated back to back with identical input
  repeated-flow       a sequence of 2 to 5 commands that recurs in 3 or more sessions: a candidate
                      for a script or a skill
  claim-then-failure  the assistant claimed completion, then a test command failed (hard
                      evidence), or the user's next message reads like a correction (a heuristic:
                      a word list matched against the start of the message, so it both misses
                      polite corrections and catches unrelated bug reports)
  ended-unverified    the session ended with file edits after its last passing test command
  interruptions       the user interrupted, or a tool call was denied (counted, top kinds listed)

Everything is a count over log entries: same logs in, same numbers out. Findings are `warn` or
`info` only; this check never fails a build. Findings carry normalised commands, session ids and
matched phrases, never message text.

The transcript format is not a published contract. Unknown entry types, unknown fields and
truncated lines are skipped.
"""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

NAME = "session"
SUMMARY = "count repeated failures, repeated manual flows and unverified completion claims in local session logs"
RULES = {"repeated-failure": "warn", "blind-retry": "warn", "repeated-flow": "info", "claim-then-failure": "warn",
         "ended-unverified": "warn", "interruptions": "info"}

DEFAULT_LOGS = "~/.claude/projects"
DEFAULT_SINCE = 30
DEFAULT_TOP = 5
MIN_FAILURES = 3        # failures of one normalised command before it is reported
MIN_FLOW_SESSIONS = 3   # sessions a command sequence must appear in
FLOW_SIZES = (2, 3, 4, 5)
ACTIVE_MINUTES = 5      # a session written to this recently is still running: not "ended"
CORRECTION_WINDOW = 120  # characters at the start of a user message searched for a correction

# Commands that run a test suite. Matched per command segment; see is_test_command.
TEST_PATTERNS = (
    r"\bpytest\b", r"scripts/test\.sh", r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b", r"\bvitest\b", r"\bjest\b",
    r"\bgo\s+test\b", r"\bcargo\s+test\b", r"\bpython3?\s+-m\s+unittest\b", r"\bplaywright\s+test\b",
    r"\bmake\s+(?:test|check)\b", r"\bphpunit\b", r"\brspec\b",
)
# Output of a test run that failed even though the shell reported success (for example `pytest | tail`).
TEST_FAILURE_OUTPUT = re.compile(r"\b\d+ (?:failed|errors?)\b(?! *=)|^FAILED |Tests:\s+\d+ failed|^--- FAIL|\bFAIL\s+\S+\.(?:test|spec)\.",
                                 re.MULTILINE)
# Programs that mention a test tool without running it.
NOT_A_RUN = {"grep", "rg", "cat", "echo", "ls", "find", "sed", "head", "tail", "which", "git", "pip", "pip3", "brew",
             "printf", "wc", "awk", "gh"}
_INSTALL = re.compile(r"\b(?:pip3?|uv\s+pip)\s+install\b|\buv\s+add\b|\bplaywright\s+install\b|\b(?:npm|pnpm|yarn)\s+(?:install|add|i)\b")

# Phrases by which the assistant says the work is finished.
CLAIM_PATTERNS = (
    r"(?-i:\bDONE(?:_WITH_CONCERNS)?\b)", r"^\s*Done[.!]", r"完了(?:しました|です|済み)", r"\ball (?:the )?tests? (?:are )?pass", r"\btests? (?:now )?pass(?:es|ed|ing)?\b",
    r"テスト(?:は|が|も)?(?:すべて|全て|全部|全件)?(?:通り|通過|パス|成功)", r"修正しました", r"実装しました", r"直しました",
    r"\bready to merge\b", r"\b(?:is|are) now (?:fixed|working)\b", r"\bsuccessfully (?:implemented|fixed|completed)\b",
)
# Starts of a user message that push back on the previous answer. A heuristic, see the module docstring.
# Grouped by construction, not by topic, so that a complaint about anything has a chance to match.
CORRECTION_PATTERNS = (
    # flat contradiction, or "that is not what I meant"
    r"いや", r"違(?:う|い)", r"嘘", r"^(?:これ|それ|そこ)は.{0,20}では(?:なく|ない|ありません)", r"について(?:聞|訊|言)いて",
    r"(?:方|ほう)が(?:いい|良い|よい)のでは",
    # still happening
    r"まだ.{0,12}(?:ない|ません|ます(?!か)|ている|てる|エラー|同じ)", r"引き続き", r"同じ(?:エラー|問題|症状|現象)", r"相変わらず",
    # the expected thing does not happen: a verb of working, showing or arriving, negated
    r"(?:動(?:か|き)|でき|出来|され|入(?:ら|り)|出|見え|見れ|開(?:か|き|け)|届(?:か|き)|映(?:ら|り)|変わ(?:ら|り)|直(?:ら|り)|通(?:ら|り)|消え|反映(?:し|され)|解決(?:し|され)"
    r"|起き|合っ|合わ|足り|読め|使え|押せ|進(?:ま|み)|終わ(?:ら|り)|止ま(?:ら|り)|戻(?:ら|り)|始ま(?:ら|り)|表示し)"
    r"(?:てい?)?(?:ない|ません|なく|ず(?:に|、|,))",
    # an unwanted result
    r"エラー(?!時|ハンドリング|処理|表示|メッセージ|画面|ページ|の(?:場合|とき|時))", r"失敗(?!時|した(?:とき|時|場合|ら)|する(?:と|場合))", r"バグ(?!報告|トラッカー)",
    r"(?:た|だ)のに", r"はず(?:です|だ|なのに)", r"のまま(?:です|だ|に|で)", r"崩れ", r"(?:と言った|頼んだのは|お願いしたのは|依頼したのは)",
    r"(?:前|元)の(?:ほう|方)が", r"元に戻", r"(?<!\d)(?:40[0-4]|50[0-4])(?!\d)", r"不具合", r"おかしい", r"(?:現象|症状)", r"(?:て|で)しまっ(?:て|た)", r"すぎ(?:る|て|です|ます|だ)",
    r"(?:にく|づら)い", r"(?:のが|のは)嫌", r"気にな(?:る|り|って)", r"(?:現状|今|いま)の(?:実装|仕様|設定|状態)(?:だと|では)", r"このままだと",
    # asked to redo, to stop, or to do something about it
    r"直し(?:て|た|、|,)", r"修正して", r"ないでください", r"(?:なんとか|どうにか)(?:でき|なり|なら)(?:ませんか|ないですか|ない(?:の|か))",
    r"\bnot working\b", r"\bstill\b", r"\bdoes(?:n't| not) work\b", r"\bwrong\b", r"\bbroken\b", r"\bfail(?:s|ed|ing|ure)?\b",
    r"\berrors?\b(?! (?:handling|messages?|pages?|states?|boundar|logging|codes?\b))", r"^no[,. ]", r"^nope\b", r"\bthat'?s not\b", r"\b(?:did|is|was|does|should)(?:n't| not)\b", r"\bcan(?:'t|not)\b",
    r"\bunable to\b", r"\bincorrect\b", r"\bbugs?\b", r"\bforbidden\b", r"\bdenied\b", r"\btraceback\b", r"\bexception\b",
    r"\bnot found\b", r"\binstead of\b", r"\bagain\b", r"\bput (?:it|that|them) back\b", r"\bI (?:said|asked|told|wanted|meant)\b",
    r"\bshould (?:be|have)\b", r"\b(?:revert|undo)\b", r"\bcrash(?:es|ed|ing)?\b", r"\bmissing\b",
)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Edits that no test run verifies: notes, memory, scratch files.
EDIT_IGNORE = re.compile(r"/\.claude/|/scratchpad/|^/(?:private/)?tmp/|/tmp/|\.(?:md|mdx|txt|rst)$", re.IGNORECASE)
# Segments left out of flows and of normalised commands: they carry no action of their own.
NOISE_PROGRAMS = {"cd", "echo", "export", "set", "true", "sleep", "printf", "pwd", "source", "unset", "wait", "exit", ":"}
# Shell words that wrap a command, and words that open or close a block without running anything.
WRAPPER_WORDS = {"do", "then", "else", "if", "elif", "while", "until", "!", "time", "nohup", "exec", "command"}
BLOCK_WORDS = {"for", "done", "fi", "esac", "case", "select", "function", "}", "{", ")", "("}
READ_ONLY_PROGRAMS = {"ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "which", "file", "stat", "du", "df", "tree",
                      "date", "whoami", "env", "ps", "sort", "uniq", "diff", "test", "["}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "config", "describe"}

_CLAIM = re.compile("|".join(CLAIM_PATTERNS), re.IGNORECASE | re.MULTILINE)
_CORRECTION = re.compile("|".join(CORRECTION_PATTERNS), re.IGNORECASE | re.MULTILINE)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r"^\d*(?:>>?|<)&?\d*")
_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_:-]*$")
_PATHLIKE = re.compile(r"/|^~|^\.{1,2}$|\.[A-Za-z0-9]{1,5}$|\*")
_QUOTED = re.compile(r"'[^'\n]*'|\"(?:[^\"\\\n]|\\.)*\"")
_REDIRECT_TARGET = re.compile(r"(?<![0-9&<>])[12]?>>?\s*([^\s;&|<>()]+)|\btee\s+(?:-a\s+)?([^\s;&|<>()]+)")
_IN_PLACE = re.compile(r"\bsed\s+(?:-[A-Za-z]*\s+)*-i\b|\bperl\s+-[A-Za-z]*pi|\bgit\s+apply\b|\bpatch\s+-p")
_INTERRUPT = "[Request interrupted"
_USER_DENIED = ("doesn't want to proceed with this tool use", "The tool use was rejected")


# ---------------------------------------------------------------- commands

def split_segments(command: str) -> list[str]:
    """Simple commands of a shell line: split on &&, ||, ; and newlines outside quotes.

    Only the first command of a pipeline is kept, and everything from a here-document marker on is
    dropped (the body is data, not commands).
    """
    segments, current, quote, index, piped = [], [], "", 0, False

    def keep(text: str) -> None:
        if not piped:
            current.append(text)

    while index < len(command):
        char = command[index]
        if quote:
            if char == "\\" and quote == '"' and index + 1 < len(command):
                keep(command[index:index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
            keep(char)
        elif char in "'\"":
            quote = char
            keep(char)
        elif char == "\\" and index + 1 < len(command):
            if command[index + 1] != "\n":
                keep(command[index:index + 2])
            index += 2
            continue
        elif command.startswith("<<", index):
            break
        elif command.startswith(("&&", "||"), index) or char in ";\n":
            segments.append("".join(current))
            current, piped = [], False
            index += 2 if command.startswith(("&&", "||"), index) else 1
            continue
        elif char == "|":
            piped = True  # the rest of the pipeline only filters output
        else:
            keep(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _tokens(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    cleaned, skip = [], False
    for token in tokens:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(token):
            skip = _REDIRECT.fullmatch(token) is not None and not token.endswith(("&1", "&2"))
            continue
        cleaned.append(token)
    while cleaned and _ASSIGNMENT.match(cleaned[0]):
        cleaned.pop(0)
    return cleaned


def _program(token: str) -> str:
    if "/" not in token:
        return token
    parts = [part for part in token.split("/") if part and part != "."]
    if not parts:
        return token
    # a project script keeps its directory (scripts/test.sh); an absolute tool path keeps its name
    return "/".join(parts[-2:]) if not token.startswith("/") and len(parts) >= 2 else parts[-1]


def _placeholder(token: str) -> str:
    if re.match(r"^[a-z][a-z0-9+.-]*://", token):
        return "<url>"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return "<n>"
    if re.fullmatch(r"[0-9a-f]{7,40}", token):
        return "<hash>"
    if _PATHLIKE.search(token):
        return "<path>"
    return "<arg>"


def normalise_segment(segment: str) -> str | None:
    """One simple command with its arguments replaced by placeholders; None for noise (cd, echo...).

    The program, up to two subcommand words before the first flag, and the flags are kept.
    """
    tokens = _tokens(segment.strip().lstrip("({ ").rstrip(") "))
    while tokens and tokens[0] in WRAPPER_WORDS:
        tokens.pop(0)
    if tokens:  # a subshell: (which jq)
        tokens[0], tokens[-1] = tokens[0].lstrip("({"), tokens[-1] if len(tokens) == 1 else tokens[-1].rstrip(")")
        tokens[0] = tokens[0].rstrip(")") if len(tokens) == 1 else tokens[0]
        tokens = [token for token in tokens if token]
    if not tokens or tokens[0] in BLOCK_WORDS or tokens[0].endswith("()") or "(){" in tokens[0]:
        return None
    program = _program(tokens[0])
    if program in NOISE_PROGRAMS:
        return None
    out, words, flagged = [program], 0, False
    for token in tokens[1:]:
        if token.startswith("-") and len(token) > 1 and not re.fullmatch(r"-\d+(?:\.\d+)?", token):
            item, flagged = (token.split("=", 1)[0] + "=<v>" if "=" in token else token), True
        elif _WORD.match(token) and words < 2 and not flagged and not out[-1].startswith("<"):
            item, words = token, words + 1
        else:
            # the value of a flag is an argument whatever it looks like (a commit message, a file)
            item = "<arg>" if out[-1].startswith("-") and not token.startswith(("http://", "https://")) else _placeholder(token)
        if item.startswith("<") and out[-1] == item:
            continue
        out.append(item)
        if len(out) >= 10:
            break
    return " ".join(out)


def normalise_command(command: str) -> str:
    """A Bash tool command reduced to its shape, for grouping: 'git add <path> && git commit -m <arg>'."""
    parts = [part for part in (normalise_segment(segment) for segment in split_segments(command)) if part]
    return " && ".join(parts) if parts else "(no command)"


def is_test_command(command: str, patterns=TEST_PATTERNS) -> bool:
    """True when some segment runs a test suite (not when it merely mentions or installs one)."""
    for segment in split_segments(command):
        tokens = _tokens(segment)
        if not tokens or _program(tokens[0]) in NOT_A_RUN or _INSTALL.search(segment):
            continue
        if any(re.search(pattern, segment) for pattern in patterns):
            return True
    return False


def is_read_only(unit: str) -> bool:
    words = unit.split()
    if words[0] == "git":
        return len(words) > 1 and words[1] in READ_ONLY_GIT
    return words[0] in READ_ONLY_PROGRAMS


# ---------------------------------------------------------------- transcripts

def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and isinstance(block.get("text"), str))
    return ""


def _human_text(entry: dict) -> str | None:
    """The text a person typed in this entry, or None for tool results and injected messages."""
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isVisibleInTranscriptOnly"):
        return None
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
    else:
        return None
    typed = [text.strip() for text in texts if isinstance(text, str) and text.strip() and not text.lstrip().startswith("<")]
    return "\n".join(typed) if typed else None


def parse_entries(entries, main: bool = True) -> list[dict]:
    """Ordered events of one transcript stream.

    Events: {"kind": "tool", "tool", "input", "error", "denied", "interrupted", "text", "ts"} placed
    where the result arrived; {"kind": "assistant", "text", "ts"}; {"kind": "user", "text", "ts"}
    (typed by a person, main stream only); {"kind": "interrupt", "ts"}.
    """
    events, pending = [], {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind, message, stamp = entry.get("type"), entry.get("message"), entry.get("timestamp")
        if kind not in ("user", "assistant") or not isinstance(message, dict):
            continue
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if kind == "assistant":
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                    events.append({"kind": "assistant", "text": block["text"], "ts": stamp})
                elif block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    tool_input = block.get("input")
                    pending[block["id"]] = (str(block.get("name") or "unknown"), tool_input if isinstance(tool_input, dict) else {})
            continue
        results = [block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_result"]
        for block in results:
            tool, tool_input = pending.pop(block.get("tool_use_id"), ("unknown", {}))
            text = _result_text(block.get("content"))
            decision = entry.get("permissionDecision")
            rejected = isinstance(decision, dict) and decision.get("decision") == "reject"
            denied = bool(entry.get("toolDenialKind")) or rejected or any(mark in text for mark in _USER_DENIED)
            raw = entry.get("toolUseResult")
            interrupted = (isinstance(raw, dict) and raw.get("interrupted") is True) or _INTERRUPT in text
            denial = entry.get("toolDenialKind") or (decision.get("reasonType") if rejected else None) or ("user" if denied else None)
            events.append({"kind": "tool", "tool": tool, "input": tool_input, "error": block.get("is_error") is True,
                           "denied": denied, "denial": denial, "interrupted": interrupted, "text": text[-4000:], "ts": stamp})
        if results:
            continue
        raw_text = content if isinstance(content, str) else _result_text(blocks)
        if _INTERRUPT in raw_text[:200]:
            events.append({"kind": "interrupt", "ts": stamp})
            continue
        typed = _human_text(entry) if main and not entry.get("isSidechain") else None
        if typed:
            events.append({"kind": "user", "text": typed, "ts": stamp})
    return events


def read_streams(path: Path) -> dict[str | None, list[dict]]:
    """Entries of one .jsonl file grouped by stream: None for the main thread, agent id for sidechains."""
    streams: dict[str | None, list[dict]] = defaultdict(list)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return streams
    with handle:
        for line in handle:
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a truncated or partly written line
            if isinstance(entry, dict) and entry.get("type") in ("user", "assistant"):
                streams[str(entry.get("agentId") or "sidechain") if entry.get("isSidechain") else None].append(entry)
    return streams


def project_dir_name(root: Path | str) -> str:
    """The directory name Claude Code derives from a project path."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def _parse_time(stamp) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_sessions(logs: Path, project: str | None, since: datetime | None) -> list[dict]:
    """Streams found under `logs`: {"project", "session", "main", "events", "last"}.

    `project` is a substring of the project directory name (None for all projects).
    """
    sessions = []
    if not logs.is_dir():
        return sessions
    for folder in sorted(path for path in logs.iterdir() if path.is_dir()):
        if project is not None and project not in folder.name:
            continue
        for path in sorted(folder.rglob("*.jsonl")):
            relative = path.relative_to(folder).parts
            subagent = len(relative) > 1
            session = relative[0] if subagent else path.stem
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if since and modified < since:
                continue
            for stream, entries in read_streams(path).items():
                main = stream is None and not subagent
                events = parse_entries(entries, main=main)
                if not events:
                    continue
                stamps = [parsed for parsed in (_parse_time(event.get("ts")) for event in events) if parsed]
                last = max(stamps) if stamps else modified
                if since and last < since:
                    continue
                sessions.append({"project": folder.name, "session": session, "main": main, "events": events, "last": last})
    return sessions


# ---------------------------------------------------------------- measures

def _command(event: dict) -> str | None:
    command = event["input"].get("command") if event["tool"] == "Bash" else None
    return command if isinstance(command, str) and command.strip() else None


def _failed(event: dict) -> bool:
    return event["error"] and not event["denied"] and not event["interrupted"]


def bash_write_targets(command: str) -> list[str]:
    """Files a shell command writes, as far as redirections, tee and in-place editors show.

    A heuristic: quoted text and here-document bodies are ignored, and a script that writes files
    itself (python3 - <<EOF ...) is not seen.
    """
    marker = command.find("<<")
    head = command if marker < 0 else command[:command.find("\n", marker) if "\n" in command[marker:] else len(command)]
    head = _QUOTED.sub("Q", head)
    targets = [first or second for first, second in _REDIRECT_TARGET.findall(head)]
    targets = [target for target in targets if not target.startswith(("/dev/", "&", "$")) and target != "Q"
               and re.fullmatch(r"[\w./~+@-]+", target)]
    if _IN_PLACE.search(head):
        targets.append("(edited in place)")
    return targets


def edited_files(event: dict) -> list[str]:
    """Names of project files a successful tool call changed (notes, memory and scratch files excluded)."""
    if event["kind"] != "tool" or event["error"] or event["denied"]:
        return []
    if event["tool"] in EDIT_TOOLS:
        paths = [event["input"].get("file_path") or event["input"].get("notebook_path") or ""]
    elif _command(event):
        paths = bash_write_targets(_command(event))
    else:
        return []
    return [Path(path).name for path in paths if isinstance(path, str) and path and not EDIT_IGNORE.search(path)]


def test_outcome(event: dict, patterns=TEST_PATTERNS) -> str | None:
    """'pass' or 'fail' for a Bash event that ran a test suite, else None."""
    command = _command(event)
    if not command or event["denied"] or event["interrupted"] or not is_test_command(command, patterns):
        return None
    return "fail" if event["error"] or TEST_FAILURE_OUTPUT.search(event["text"]) else "pass"


def tool_counts(sessions: list[dict]) -> tuple[Counter, Counter]:
    calls, errors = Counter(), Counter()
    for session in sessions:
        for event in session["events"]:
            if event["kind"] == "tool":
                calls[event["tool"]] += 1
                errors[event["tool"]] += 1 if _failed(event) else 0
    return calls, +errors


def failure_groups(sessions: list[dict]) -> list[dict]:
    """Failed Bash commands grouped by normalised command, most frequent first."""
    count, where = Counter(), defaultdict(set)
    for session in sessions:
        for event in session["events"]:
            if event["kind"] == "tool" and _failed(event) and _command(event):
                shape = normalise_command(_command(event))
                count[shape] += 1
                where[shape].add(session["session"])
    return [{"command": shape, "failures": total, "sessions": len(where[shape])}
            for shape, total in sorted(count.items(), key=lambda item: (-item[1], item[0]))]


def blind_retries(sessions: list[dict]) -> list[dict]:
    """Failed tool calls repeated immediately with identical input, grouped by tool and command shape."""
    count, where = Counter(), defaultdict(set)
    for session in sessions:
        previous = None
        for event in session["events"]:
            if event["kind"] != "tool":
                continue
            if previous and _failed(previous) and _failed(event) and (previous["tool"], previous["input"]) == (event["tool"], event["input"]):
                shape = normalise_command(_command(event)) if _command(event) else event["tool"]
                count[(event["tool"], shape)] += 1
                where[(event["tool"], shape)].add(session["session"])
            previous = event
    return [{"tool": tool, "command": shape, "retries": total, "sessions": len(where[(tool, shape)])}
            for (tool, shape), total in sorted(count.items(), key=lambda item: (-item[1], item[0]))]


def flow_units(events: list[dict]) -> list[str]:
    """Normalised simple commands of the successful Bash calls of a stream, immediate repeats collapsed."""
    units: list[str] = []
    for event in events:
        if event["kind"] != "tool" or event["error"] or event["denied"] or not _command(event):
            continue
        for segment in split_segments(_command(event)):
            unit = normalise_segment(segment)
            if unit and (not units or units[-1] != unit):
                units.append(unit)
    return units


def _contains(longer: tuple, shorter: tuple) -> bool:
    return any(longer[start:start + len(shorter)] == shorter for start in range(len(longer) - len(shorter) + 1))


def repeated_flows(sessions: list[dict], min_sessions: int = MIN_FLOW_SESSIONS) -> list[dict]:
    """Command sequences seen in `min_sessions` or more sessions, longest and most widespread first.

    A sequence must start and end with a command that changes something (not ls, grep, git
    status...), and a sequence contained in a longer one seen in as many sessions is left out.
    """
    seen, occurrences = defaultdict(set), Counter()
    for session in sessions:
        units = flow_units(session["events"])
        for size in FLOW_SIZES:
            for start in range(len(units) - size + 1):
                gram = tuple(units[start:start + size])
                if len(set(gram)) < 2 or is_read_only(gram[0]) or is_read_only(gram[-1]):
                    continue
                seen[gram].add(session["session"])
                occurrences[gram] += 1
    candidates = sorted((gram for gram, where in seen.items() if len(where) >= min_sessions),
                        key=lambda gram: (-len(gram), -len(seen[gram]), gram))
    kept: list[tuple] = []
    for gram in candidates:
        if not any(len(other) > len(gram) and len(seen[other]) >= len(seen[gram]) and _contains(other, gram) for other in kept):
            kept.append(gram)
    kept.sort(key=lambda gram: (-len(seen[gram]), -len(gram), gram))
    return [{"commands": list(gram), "sessions": len(seen[gram]), "occurrences": occurrences[gram]} for gram in kept]


def interruption_counts(sessions: list[dict]) -> dict:
    interrupts, denials = 0, Counter()
    for session in sessions:
        for event in session["events"]:
            if event["kind"] == "interrupt" or (event["kind"] == "tool" and event["interrupted"] and not event["denied"]):
                interrupts += 1
            if event["kind"] == "tool" and event["denied"]:
                denials[f"{event['denial'] or 'unknown'}:{event['tool']}"] += 1
    return {"interruptions": interrupts, "denials": sum(denials.values()), "denial_kinds": dict(denials.most_common())}


def pushback(reply: str) -> str | None:
    """The phrase that makes a user message read as pushback on the answer before it, or None.

    Only the first CORRECTION_WINDOW characters are searched: a complaint comes first, and a long
    pasted log further down would match almost any word list.
    """
    match = _CORRECTION.search(reply[:CORRECTION_WINDOW])
    return match.group(0) if match else None


def claim_outcomes(session: dict, patterns=TEST_PATTERNS) -> list[dict]:
    """Completion claims of one main session and what followed each.

    A claim is the last assistant text before the user's next message (or the end) when it matches
    CLAIM_PATTERNS. Its window runs to the end of the user's next turn. `outcome` is 'failing-test'
    when a test command fails inside the window, else 'pushback' when the next user message starts
    like a correction (heuristic), else 'none'.
    """
    events, claims = session["events"], []
    user_at = [index for index, event in enumerate(events) if event["kind"] == "user"]
    bounds = user_at + [len(events)]
    start = 0
    for position, end in enumerate(bounds):
        texts = [index for index in range(start, end) if events[index]["kind"] == "assistant"]
        start = end + 1
        if not texts:
            continue
        last = texts[-1]
        match = _CLAIM.search(events[last]["text"])
        if not match:
            continue
        window_end = bounds[position + 1] if position + 1 < len(bounds) else len(events)
        failing = next((event for event in events[last + 1:window_end]
                        if event["kind"] == "tool" and test_outcome(event, patterns) == "fail"), None)
        reply = events[end]["text"] if end < len(events) else ""
        correction = pushback(reply)
        outcome = "failing-test" if failing else "pushback" if correction else "none"
        claims.append({"session": session["session"], "project": session["project"], "ts": events[last].get("ts"),
                       "claim": match.group(0), "outcome": outcome,
                       "command": normalise_command(_command(failing)) if failing else None,
                       "correction": correction if not failing else None})
    return claims


def unverified_end(session: dict, patterns=TEST_PATTERNS) -> dict | None:
    """Edits left unverified at the end of a main session, or None when there are none.

    `tested` says whether the session ran a passing test command at any point: when it did, the
    edits came after it; when it did not, nothing was ever run.
    """
    edits, tested, files = 0, False, []
    for event in session["events"]:
        if event["kind"] != "tool" or event["error"] or event["denied"]:
            continue
        if test_outcome(event, patterns) == "pass":
            edits, tested, files = 0, True, []
        else:
            changed = edited_files(event)
            edits += len(changed)
            files += changed
    if not edits:
        return None
    return {"session": session["session"], "project": session["project"], "edits": edits, "tested": tested,
            "files": sorted(set(files))[:5], "last": session["last"].isoformat()}


# ---------------------------------------------------------------- service interface

def add_arguments(parser) -> None:
    parser.add_argument("--logs", default=DEFAULT_LOGS, help="directory holding the session logs (default: %(default)s)")
    parser.add_argument("--project", help="only projects whose log directory name contains this text "
                                          "(default: the project being checked)")
    parser.add_argument("--all-projects", action="store_true", help="read every project's logs")
    parser.add_argument("--since", type=int, default=DEFAULT_SINCE, metavar="DAYS",
                        help="only sessions active in the last DAYS days (default: %(default)s)")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, metavar="N", help="findings kept per rule (default: %(default)s)")
    parser.add_argument("--test-pattern", action="append", default=[], metavar="REGEX",
                        help="an extra regular expression that marks a command as a test run (repeatable)")


def analyse(sessions: list[dict], patterns=TEST_PATTERNS, now: datetime | None = None, min_failures: int = MIN_FAILURES,
            min_flow_sessions: int = MIN_FLOW_SESSIONS) -> dict:
    """All tables for a list of loaded sessions. Pure: no file access."""
    now = now or datetime.now(timezone.utc)
    mains = [session for session in sessions if session["main"]]
    calls, errors = tool_counts(sessions)
    failures = failure_groups(sessions)
    claims = [claim for session in mains for claim in claim_outcomes(session, patterns)]
    ended = [session for session in mains if now - session["last"] >= timedelta(minutes=ACTIVE_MINUTES)]
    unverified = [found for found in (unverified_end(session, patterns) for session in ended) if found]
    edited = sum(1 for session in ended if any(edited_files(event) for event in session["events"]))
    return {
        "sessions": len(mains), "subagent_streams": len(sessions) - len(mains),
        "projects": len({session["project"] for session in sessions}),
        "user_messages": sum(1 for session in mains for event in session["events"] if event["kind"] == "user"),
        "tool_calls": dict(calls.most_common()), "tool_errors": dict(errors.most_common()),
        "bash_failures": sum(group["failures"] for group in failures),
        "failure_groups": failures, "repeated_failures": [group for group in failures if group["failures"] >= min_failures],
        "blind_retries": blind_retries(sessions),
        "flows": repeated_flows(sessions, min_flow_sessions),
        **interruption_counts(sessions),
        "claims": len(claims), "claims_then_failing_test": sum(1 for claim in claims if claim["outcome"] == "failing-test"),
        "claims_then_pushback": sum(1 for claim in claims if claim["outcome"] == "pushback"),
        "contradicted_claims": [claim for claim in claims if claim["outcome"] != "none"],
        "sessions_ended": len(ended), "sessions_with_edits": edited,
        "ended_unverified": len(unverified), "ended_after_passing_test": sum(1 for item in unverified if item["tested"]),
        "ended_never_tested": sum(1 for item in unverified if not item["tested"]), "unverified": unverified,
    }


def findings_from(stats: dict, top: int = DEFAULT_TOP) -> list[dict]:
    findings = []
    for group in stats["repeated_failures"][:top]:
        findings.append({"rule": "repeated-failure", "severity": "warn",
                         "message": f"`{group['command']}` failed {group['failures']} times in {group['sessions']} session(s)",
                         "fix": "find out why it fails and fix the cause, or record the working form in CLAUDE.md or a script",
                         "detail": group})
    for retry in stats["blind_retries"][:top]:
        findings.append({"rule": "blind-retry", "severity": "warn",
                         "message": f"{retry['tool']} `{retry['command']}` was repeated unchanged right after failing, "
                                    f"{retry['retries']} time(s) in {retry['sessions']} session(s)",
                         "fix": "a failing call repeated without a change fails again; read the error first", "detail": retry})
    for flow in stats["flows"][:top]:
        findings.append({"rule": "repeated-flow", "severity": "info",
                         "message": f"{len(flow['commands'])} commands run in the same order in {flow['sessions']} sessions "
                                    f"({flow['occurrences']} times): " + " -> ".join(flow["commands"]),
                         "fix": "turn this sequence into one script or a skill", "detail": flow})
    hard = [claim for claim in stats["contradicted_claims"] if claim["outcome"] == "failing-test"]
    soft = [claim for claim in stats["contradicted_claims"] if claim["outcome"] == "pushback"]
    for claim in hard[:top]:
        findings.append({"rule": "claim-then-failure", "severity": "warn",
                         "message": f"session {claim['session'][:8]}: the assistant said \"{claim['claim']}\" and then "
                                    f"`{claim['command']}` failed",
                         "fix": "run the tests before the completion report, and gate the report on them", "detail": claim})
    for claim in soft[:top]:
        findings.append({"rule": "claim-then-failure", "severity": "info",
                         "message": f"session {claim['session'][:8]}: the assistant said \"{claim['claim']}\" and the next "
                                    f"user message started like a correction (\"{claim['correction']}\"); heuristic",
                         "fix": "check what was missed in that session; if it recurs, add a check for it", "detail": claim})
    ordered = sorted(stats["unverified"], key=lambda item: (not item["tested"], -item["edits"], item["session"]))
    for item in ordered[:top]:
        how = "after its last passing test run" if item["tested"] else "and never ran a test command"
        findings.append({"rule": "ended-unverified", "severity": "warn" if item["tested"] else "info",
                         "message": f"session {item['session'][:8]} ended with {item['edits']} edit(s) {how} "
                                    f"({', '.join(item['files'])})",
                         "fix": "run the tests after the last edit before ending a session", "detail": item})
    if stats["interruptions"] or stats["denials"]:
        kinds = ", ".join(f"{kind} x{count}" for kind, count in list(stats["denial_kinds"].items())[:top])
        findings.append({"rule": "interruptions", "severity": "info",
                         "message": f"{stats['interruptions']} user interruption(s) and {stats['denials']} denied tool call(s)"
                                    + (f": {kinds}" if kinds else ""),
                         "fix": "", "detail": {"interruptions": stats["interruptions"], "denials": stats["denial_kinds"]}})
    return findings


def run(args) -> dict:
    config = getattr(args, "config", None) or {}
    logs = Path(str(getattr(args, "logs", None) or config.get("logs") or DEFAULT_LOGS)).expanduser()
    if not logs.is_dir():
        raise RuntimeError(f"no session logs at {logs}")
    if getattr(args, "all_projects", False) or config.get("all_projects"):
        project = None
    else:
        project = getattr(args, "project", None) or config.get("project") or project_dir_name(Path(args.root))
    days = getattr(args, "since", None)
    days = config.get("since", DEFAULT_SINCE) if days is None else days
    now = getattr(args, "now", None) or datetime.now(timezone.utc)
    extra = list(getattr(args, "test_pattern", None) or []) + list(config.get("test_patterns", []))
    for pattern in extra:
        try:
            re.compile(pattern)
        except re.error as error:
            raise RuntimeError(f"bad --test-pattern {pattern!r}: {error}") from error
    patterns = TEST_PATTERNS + tuple(extra)
    top = getattr(args, "top", None) or config.get("top", DEFAULT_TOP)
    sessions = load_sessions(logs, project, now - timedelta(days=days) if days else None)
    stats = analyse(sessions, patterns, now, config.get("min_failures", MIN_FAILURES),
                    config.get("min_flow_sessions", MIN_FLOW_SESSIONS))
    stats["scope"] = {"logs": str(logs), "project": project or "(all projects)", "since_days": days}
    return {"findings": findings_from(stats, top), "stats": stats}

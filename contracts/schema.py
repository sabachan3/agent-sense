"""Shapes passed between the services and the command line. See README.md in this directory."""

from __future__ import annotations

SEVERITIES = ("fail", "warn", "info")
ORDER = {"fail": 2, "warn": 1, "info": 0, "ok": 0}
REPORT_VERSION = 1


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_finding(finding: dict, rules=None) -> dict:
    _require(isinstance(finding, dict), "finding must be an object")
    _require(isinstance(finding.get("rule"), str) and finding["rule"], "finding needs a rule")
    if rules is not None:
        _require(finding["rule"] in rules, f"unknown rule {finding['rule']!r}")
    _require(finding.get("severity") in SEVERITIES, f"unknown severity {finding.get('severity')!r}")
    _require(isinstance(finding.get("message"), str) and finding["message"], "finding needs a message")
    _require(isinstance(finding.get("fix", ""), str), "fix must be text")
    _require(finding.get("file") is None or isinstance(finding["file"], str), "file must be text")
    _require(finding.get("line") is None or isinstance(finding["line"], int), "line must be a number")
    _require(isinstance(finding.get("detail", {}), dict), "detail must be an object")
    return finding


def validate_result(result: dict, rules=None) -> dict:
    """What a service's run() returns: findings plus free-form stats."""
    _require(isinstance(result, dict), "result must be an object")
    _require(isinstance(result.get("findings"), list), "result needs a findings list")
    _require(isinstance(result.get("stats", {}), dict), "stats must be an object")
    for finding in result["findings"]:
        validate_finding(finding, rules)
    return result


def build_report(check: str, result: dict) -> dict:
    findings = result["findings"]
    counts = {severity: sum(1 for finding in findings if finding["severity"] == severity) for severity in SEVERITIES}
    verdict = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return {"version": REPORT_VERSION, "check": check, "verdict": verdict, "summary": counts,
            "stats": result.get("stats", {}), "findings": findings}


def exit_code(report: dict, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    return 1 if ORDER[report["verdict"]] >= ORDER[fail_on] else 0

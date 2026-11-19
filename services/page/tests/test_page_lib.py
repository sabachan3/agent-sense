"""Gate tests for the page service: pure analysis only, no browser."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import page_lib
from contracts import schema

URL = "http://localhost:3000/"


def view(width=360, **parts):
    base = {"width": width, "client_width": width, "scroll_width": width, "overflow": [], "clipped": [], "covered": [],
            "links": [], "images": [], "texts": [], "console": [], "requests": []}
    return {**base, **parts}


def recording(*views, **parts):
    base = {"url": URL, "final_url": URL, "local": True, "title": "Orders", "views": list(views) or [view()],
            "controls": [], "ime": [], "clicked": False, "ime_checked": False}
    return {**base, **parts}


def rules(found):
    return [(finding["rule"], finding["severity"]) for finding in found]


def findings(rec):
    found, _ = page_lib.analyse(rec)
    for finding in found:
        schema.validate_finding(finding, page_lib.RULES)
    return found


def test_embedded_scripts_are_ascii():
    for name in ("INIT", "COLLECT", "TAG_CONTROLS", "ARM", "PROBE", "TAG_INPUTS", "IME_ARM", "IME_PROBE"):
        script = getattr(page_lib, name)
        assert script.isascii(), name
        assert "\\u" not in script, name


def test_interface_names():
    assert page_lib.NAME == "page" and page_lib.SUMMARY
    assert set(page_lib.RULES.values()) <= set(schema.SEVERITIES)


@pytest.mark.parametrize("url,local", [
    ("http://localhost:3000/", True), ("http://127.0.0.1:5173/a", True), ("file:///tmp/a.html", True),
    ("http://app.localhost/", True), ("https://example.org/", False), ("https://localhost.evil.com/", False),
])
def test_is_local(url, local):
    assert page_lib.is_local(url) is local


def test_auto_touches_only_local_pages():
    assert page_lib.enabled("auto", "http://localhost:3000/")
    assert not page_lib.enabled("auto", "https://example.org/")
    assert page_lib.enabled("on", "https://example.org/")
    assert not page_lib.enabled("off", "http://localhost:3000/")


def test_parse_widths():
    assert page_lib.parse_widths(None) == (360, 768, 1280)
    assert page_lib.parse_widths("390, 1440,390") == (390, 1440)
    assert page_lib.parse_widths([320, 1024]) == (320, 1024)
    with pytest.raises(RuntimeError):
        page_lib.parse_widths("wide")
    with pytest.raises(RuntimeError):
        page_lib.parse_widths("10")


def test_to_url_turns_paths_into_file_urls(tmp_path):
    assert page_lib.to_url("https://a.test/x", tmp_path) == "https://a.test/x"
    assert page_lib.to_url("out/index.html", tmp_path) == (tmp_path / "out" / "index.html").resolve().as_uri()
    assert page_lib.to_url(str(tmp_path / "a.html"), Path("/elsewhere")) == (tmp_path / "a.html").resolve().as_uri()


@pytest.mark.parametrize("title,bad", [
    ("Create Next App", True), ("Vite + React", True), ("  vite + react + ts ", True), ("", True), (None, True),
    ("Document", True), ("Orders dashboard", False), ("React App for teams", False),
])
def test_default_title(title, bad):
    assert bool(page_lib.default_title(title)) is bad


@pytest.mark.parametrize("link,local,kind", [
    ({"href": "#"}, True, "hash"), ({"href": " #! "}, True, "hash"), ({"href": ""}, True, "empty"),
    ({"href": "javascript:void(0)"}, True, "javascript"), ({"href": "#main"}, True, None),
    ({"href": None}, True, None), ({"href": "/pricing", "host": "localhost", "protocol": "http:"}, True, None),
    ({"href": "https://example.com/a", "host": "example.com", "protocol": "https:"}, True, "example"),
    ({"href": "https://www.example.org", "host": "www.example.org", "protocol": "https:"}, False, "example"),
    ({"href": "http://localhost:3000/api", "host": "localhost", "protocol": "http:"}, False, "localhost"),
    ({"href": "http://localhost:3000/api", "host": "localhost", "protocol": "http:"}, True, None),
    ({"href": "https://counterexample.com", "host": "counterexample.com", "protocol": "https:"}, False, None),
])
def test_link_kind(link, local, kind):
    assert page_lib.link_kind(link, local) == kind


@pytest.mark.parametrize("text,kind", [
    ("Lorem ipsum dolor sit amet", "lorem ipsum"), ("Signed, John Doe", "sample person"),
    ("Welcome to Your Company", "sample company"), ("Acme Inc builds rockets", "sample company"),
    ("TODO: write this", "todo marker"), ("FIXME later", "todo marker"), ("ここにテキストが入ります", "sample text"),
    ("ダミーテキストです", "sample text"),
    ("My todo list", None), ("Todo: buy milk", None), ("Johnny Doering", None), ("Acme climbing gear", None),
    ("山田太郎", None), ("Your company's data stays private", None),
])
def test_text_kind(text, kind):
    found = page_lib.text_kind(text)
    assert (found[0] if found else None) == kind


@pytest.mark.parametrize("label,danger", [
    ("Delete account", True), ("Log out", True), ("Pay now", True), ("アカウントを削除", True), ("送信", True),
    ("Show details", False), ("Payment history", False), ("Display", False), ("Reorder list", False), ("詳細を見る", False),
])
def test_destructive(label, danger):
    assert page_lib.destructive(label) is danger


def test_control_skip_reasons():
    base = {"label": "Open", "type": "", "in_form": False}
    assert page_lib.control_skip_reason(base) is None
    assert page_lib.control_skip_reason({**base, "aria_disabled": True}) == "aria-disabled"
    assert page_lib.control_skip_reason({**base, "native": True})
    assert page_lib.control_skip_reason({**base, "label": "Delete"}) == "label looks destructive"
    assert "required" in page_lib.control_skip_reason({**base, "in_form": True, "type": "submit", "form_invalid": True})
    assert page_lib.control_skip_reason({**base, "in_form": True, "type": "submit"})
    assert page_lib.control_skip_reason({**base, "in_form": True, "type": "button"}) is None


def test_click_effects():
    quiet = {"mutations": 0, "submits": 0, "focus_moved": False, "values_changed": False, "title_changed": False,
             "scrolled": False, "url_changed": False, "calls": []}
    none = dict.fromkeys(page_lib._Events.KEYS, 0)
    assert page_lib.click_effects(quiet, none) == []
    assert page_lib.click_effects(None, none) == ["navigation"]
    assert page_lib.click_effects({**quiet, "mutations": 3}, none) == ["dom change"]
    assert page_lib.click_effects({**quiet, "calls": ["clipboard"]}, none) == ["called clipboard"]
    assert page_lib.click_effects(quiet, {**none, "requests": 1, "exceptions": 1}) == ["network request", "exception"]
    assert page_lib.click_effects({**quiet, "scrolled": True}, none) == ["scrolled"]


def test_hash_link_jump_is_not_an_effect():
    quiet = {"mutations": 0, "scrolled": True, "url_changed": False, "calls": []}
    events = {**dict.fromkeys(page_lib._Events.KEYS, 0), "navigations": 1}
    assert page_lib.click_effects(quiet, events, hash_link=True) == []
    assert page_lib.click_effects(quiet, events, hash_link=False) == ["navigation", "scrolled"]
    assert page_lib.click_effects({**quiet, "url_changed": True}, events, hash_link=True) == ["navigation"]
    assert page_lib.click_effects({**quiet, "mutations": 1}, events, hash_link=True) == ["dom change"]


def test_ime_signals():
    control = {"added": 0, "length": 4}
    calm = {"added": 0, "submits": 0, "length": 4, "url_changed": False}
    assert page_lib.ime_signals(control, calm, 0, 0, 4) == []
    assert page_lib.ime_signals(control, None, 0, 0, 4) == ["navigation"]
    assert page_lib.ime_signals(control, {**calm, "submits": 1}, 0, 0, 4) == ["form submit"]
    assert page_lib.ime_signals(control, {**calm, "length": 0}, 0, 0, 4) == ["field cleared"]
    assert page_lib.ime_signals(control, {**calm, "added": 2}, 0, 0, 4) == ["dom change"]
    assert page_lib.ime_signals(control, calm, 2, 0, 4) == ["network request"]


def test_ime_signals_ignore_what_was_already_happening():
    noisy = {"added": 3, "length": 4}
    calm = {"added": 5, "submits": 0, "length": 4, "url_changed": False}
    assert page_lib.ime_signals(noisy, calm, 2, 1, 4) == []  # a suggestion list was already updating and fetching


def test_clean_page_has_no_findings():
    assert findings(recording(view(360), view(1280))) == []


def test_page_error():
    found = findings({"url": URL, "error": "TimeoutError: 30000ms exceeded"})
    assert rules(found) == [("page-error", "fail")]


def test_default_title_finding():
    found = findings(recording(title="Vite + React"))
    assert rules(found) == [("default-title", "fail")] and "Vite + React" in found[0]["message"]


def test_console_exception_fails_and_console_error_warns_once_across_widths():
    console = [{"kind": "exception", "text": "ReferenceError: chart is not defined"}, {"kind": "error", "text": "no settings"}]
    found = findings(recording(view(360, console=console), view(1280, console=console)))
    assert rules(found) == [("console-error", "fail"), ("console-error", "warn")]
    assert found[0]["detail"]["widths"] == [360, 1280]


def test_request_failed_same_origin_fails_third_party_warns():
    requests = [{"url": "http://localhost:3000/api/items", "status": 500, "method": "GET"},
                {"url": "https://cdn.other.test/x.js", "status": None, "failure": "net::ERR_NAME_NOT_RESOLVED", "method": "GET"}]
    found = findings(recording(view(360, requests=requests)))
    assert rules(found) == [("request-failed", "fail"), ("request-failed", "warn")]
    assert "HTTP 500" in found[0]["message"] and "ERR_NAME_NOT_RESOLVED" in found[1]["message"]


def test_file_pages_treat_file_requests_as_same_origin():
    rec = recording(view(360, requests=[{"url": "file:///tmp/site/missing.png", "status": None, "failure": "net::ERR_FILE_NOT_FOUND"}]),
                    url="file:///tmp/site/index.html", final_url="file:///tmp/site/index.html")
    assert rules(findings(rec)) == [("request-failed", "fail")]


def test_horizontal_overflow_names_the_element_and_the_widths():
    item = {"locator": "table.prices", "right": 708, "width": 700, "text": "Plan"}
    found = findings(recording(view(360, scroll_width=708, overflow=[item]), view(768), view(1280)))
    assert rules(found) == [("horizontal-overflow", "fail")]
    assert "at 360px" in found[0]["message"] and "table.prices" in found[0]["message"] and "708px" in found[0]["message"]
    assert found[0]["detail"]["widths"] == [360]


def test_horizontal_overflow_without_a_named_element_is_still_reported():
    found = findings(recording(view(360, scroll_width=420)))
    assert rules(found) == [("horizontal-overflow", "fail")] and "no single element" in found[0]["message"]


def test_text_clipped_warns_and_intended_truncation_is_info():
    cut = {"locator": "div.badge", "text": "Enterprise plan", "axis": "x", "hidden_px": 80, "intent": None}
    dots = {"locator": "p.name", "text": "A long name", "axis": "x", "hidden_px": 50, "intent": "ellipsis"}
    found = findings(recording(view(360, clipped=[cut, dots]), view(1280, clipped=[cut])))
    assert rules(found) == [("text-clipped", "warn"), ("text-clipped", "info")]
    assert "every width" in found[0]["message"] and "80px of its width" in found[0]["message"]


def test_overlap_groups_controls_by_what_covers_them():
    covered = [{"locator": "button#a", "label": "A", "by": "div.banner", "by_position": "fixed"},
               {"locator": "button#b", "label": "B", "by": "div.banner", "by_position": "fixed"},
               {"locator": "button#c", "label": "C", "by": "div.tip", "by_position": "absolute"}]
    found = findings(recording(view(360, covered=covered)))
    assert rules(found) == [("overlap", "warn"), ("overlap", "warn")]
    assert "2 controls" in found[0]["message"] and found[0]["detail"]["controls"] == ["button#a", "button#b"]


def test_dead_control_and_alive_control():
    controls = [{"locator": "button#dead", "label": "Learn more", "tag": "button", "effects": []},
                {"locator": "button#alive", "label": "Show", "tag": "button", "effects": ["dom change"]},
                {"locator": "button#danger", "label": "Delete", "tag": "button", "effects": [], "skipped": "label looks destructive"}]
    found, stats = page_lib.analyse(recording(controls=controls, clicked=True, click_width=1280))
    assert rules(found) == [("dead-control", "fail")] and "button#dead" in found[0]["message"]
    assert stats["clicked"] == 2 and stats["skipped_controls"] == 1


def test_exception_after_click_is_a_console_error_not_a_dead_control():
    controls = [{"locator": "button#broken", "label": "Open", "tag": "button", "effects": ["exception"],
                 "error": "ReferenceError: settings is not defined"}]
    found = findings(recording(controls=controls, clicked=True))
    assert rules(found) == [("console-error", "fail")] and "settings is not defined" in found[0]["message"]


def test_hash_links_are_judged_by_the_click_when_there_was_one():
    links = [{"locator": "a#x", "href": "#", "host": "localhost", "protocol": "http:", "text": "More", "role": ""}]
    unclicked = findings(recording(view(360, links=links)))
    assert rules(unclicked) == [("placeholder-link", "info")]
    alive = findings(recording(view(360, links=links), clicked=True,
                               controls=[{"locator": "a#x", "label": "More", "effects": ["dom change"]}]))
    assert alive == []
    dead = findings(recording(view(360, links=links), clicked=True, controls=[{"locator": "a#x", "label": "More", "effects": []}]))
    assert rules(dead) == [("dead-control", "fail")]
    skipped = findings(recording(view(360, links=links), clicked=True,
                                 controls=[{"locator": "a#x", "label": "Delete", "effects": [], "skipped": "label looks destructive"}]))
    assert rules(skipped) == [("placeholder-link", "info")]


def test_placeholder_links_images_and_text():
    links = [{"locator": "a.blog", "href": "https://example.com/blog", "host": "example.com", "protocol": "https:", "text": "Blog"},
             {"locator": "a.ok", "href": "/pricing", "host": "localhost", "protocol": "http:", "text": "Pricing"}]
    images = [{"locator": "img.hero", "host": "via.placeholder.com"}, {"locator": "img.logo", "host": "localhost"}]
    texts = [{"locator": "p.lead", "text": "Lorem ipsum dolor sit amet"}, {"locator": "p.real", "text": "Track every order"}]
    found = findings(recording(view(360, links=links, images=images, texts=texts), view(1280, links=links, images=images, texts=texts)))
    assert rules(found) == [("placeholder-link", "warn"), ("placeholder-link", "warn"), ("placeholder-text", "warn")]
    assert found[0]["detail"]["widths"] == [360, 1280]


def test_localhost_link_is_flagged_only_on_a_remote_page():
    links = [{"locator": "a.api", "href": "http://localhost:3000/api", "host": "localhost", "protocol": "http:", "text": "API"}]
    assert findings(recording(view(360, links=links))) == []
    remote = recording(view(360, links=links), url="https://shop.test/", final_url="https://shop.test/", local=False)
    assert rules(findings(remote)) == [("placeholder-link", "warn")]


def test_ime_findings_by_phase():
    def field(**phases):
        return {"locator": "input#chat", "keydown": [], "keyup": [], "safari": [], **phases}

    assert rules(findings(recording(ime=[field(keydown=["field cleared", "dom change"])], ime_checked=True))) == [("ime-enter-submit", "fail")]
    assert rules(findings(recording(ime=[field(keyup=["form submit"])], ime_checked=True))) == [("ime-enter-submit", "fail")]
    assert rules(findings(recording(ime=[field(safari=["field cleared"])], ime_checked=True))) == [("ime-enter-submit", "warn")]
    assert rules(findings(recording(ime=[field(keydown=["dom change"])], ime_checked=True))) == [("ime-enter-submit", "warn")]
    assert findings(recording(ime=[field()], ime_checked=True)) == []
    assert findings(recording(ime=[{"locator": "input#x", "skipped": "the field did not take the composition"}])) == []
    both = findings(recording(ime=[field(keydown=["field cleared"], keyup=["field cleared"])], ime_checked=True))
    assert len(both) == 1 and both[0]["detail"]["phase"] == "keydown" and "isComposing" in both[0]["fix"]


def test_findings_per_rule_are_capped_and_the_rest_counted():
    texts = [{"locator": f"p:nth-of-type({index})", "text": "Lorem ipsum"} for index in range(1, 13)]
    found, stats = page_lib.analyse(recording(view(360, texts=texts)))
    assert len(found) == page_lib.MAX_PER_RULE and stats["dropped"] == {"placeholder-text": 12 - page_lib.MAX_PER_RULE}


def namespace(**values):
    base = {"root": Path("/project"), "config": {}, "urls": [], "widths": None, "click": None, "ime": None, "channel": None,
            "timeout": None, "wait": None, "max_controls": None}
    return argparse.Namespace(**{**base, **values})


def test_options_command_line_wins_over_config_over_defaults():
    urls, options = page_lib.options_from(namespace(config={"urls": ["http://localhost:3000"], "widths": [390], "click": "off",
                                                            "max-controls": 5}, widths="414,1024"))
    assert urls == ["http://localhost:3000"]
    assert options["widths"] == (414, 1024) and options["click"] == "off" and options["ime"] == "auto"
    assert options["max_controls"] == 5 and options["timeout"] == 30000


def test_run_without_urls_cannot_run():
    with pytest.raises(RuntimeError, match="no page to check"):
        page_lib.run(namespace(), recorder=lambda urls, options: [])


def test_run_returns_a_valid_result_with_stats():
    seen = {}

    def recorder(urls, options):
        seen.update(urls=urls, options=options)
        return [recording(title="Create Next App", clicked=True,
                          controls=[{"locator": "button#dead", "label": "Go", "tag": "button", "effects": []}])]

    result = page_lib.run(namespace(urls=["http://localhost:3000/", "http://localhost:3000/"]), recorder=recorder)
    schema.validate_result(result, page_lib.RULES)
    assert seen["urls"] == ["http://localhost:3000/"]
    assert sorted(rules(result["findings"])) == [("dead-control", "fail"), ("default-title", "fail")]
    assert result["stats"]["pages"][0]["clicked"] == 1 and result["stats"]["widths"] == [360, 768, 1280]
    assert schema.build_report("page", result)["verdict"] == "fail"

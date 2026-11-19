"""Gate tests for the launch check: pure functions over recorded sites, no browser, no network."""
import argparse
import json

import pytest

import launch_lib as lib
from contracts import schema

VENDORS = lib.load_vendors()
POLICY = ("プライバシーポリシー 当社は、利用者の個人情報を以下のとおり取り扱います。" + "取得する情報、利用目的、第三者提供について定めます。" * 12)


def link(href, text="", area="footer"):
    return {"href": href, "text": text, "area": area}


def page(url="https://shop.example.jp/", text="", links=(), requests=(), sources=(), forms=0):
    return {"url": url, "final_url": url, "text": text, "links": list(links), "forms": forms,
            "requests": [{"url": address, "type": "script"} for address in requests], "sources": list(sources)}


FOOTER = [link("https://shop.example.jp/privacy", "プライバシーポリシー"), link("https://shop.example.jp/terms", "利用規約"),
          link("https://shop.example.jp/contact", "お問い合わせ"), link("https://shop.example.jp/company", "運営会社")]


def site(requests=(), policy=POLICY, links=FOOTER, text="", documents=None, **more):
    docs = {"policy": {"url": "https://shop.example.jp/privacy", "text": policy}} if policy is not None else {}
    docs.update(documents or {})
    return {"url": "https://shop.example.jp/", "pages": [page(text=text, links=links, requests=requests, **more)],
            "documents": docs}


def rules(result):
    return sorted(finding["rule"] for finding in result["findings"])


def check(record, **options):
    return schema.validate_result(lib.analyse(record, VENDORS, **options), lib.RULES)


# ---- vendor table

def test_vendor_table_is_well_formed():
    raw = json.loads(lib.VENDORS_FILE.read_text(encoding="utf-8"))
    assert len(raw["vendors"]) >= 60
    names, patterns = set(), {}
    for vendor in raw["vendors"]:
        assert set(vendor) >= {"name", "operator", "operator_aliases", "category", "hosts", "aliases", "verified"}, vendor
        assert vendor["name"] not in names
        names.add(vendor["name"])
        assert vendor["hosts"] and vendor["aliases"] and isinstance(vendor["verified"], bool)
        assert vendor["category"] in ("analytics", "ads", "support", "monitoring", "payment", "auth", "embed", "consent",
                                      "fonts", "cdn")
        for pattern in vendor["hosts"]:
            assert pattern == pattern.lower().strip() and "." in pattern and not pattern.startswith(("http", ".", "/"))
            assert pattern not in patterns, f"{pattern} is in both {patterns.get(pattern)} and {vendor['name']}"
            patterns[pattern] = vendor["name"]


def test_every_category_named_in_the_brief_is_covered():
    names = " ".join(vendor["name"] for vendor in VENDORS)
    for wanted in ("Google Analytics", "Clarity", "KARTE", "Yahoo", "AD EBiS", "LINE Tag", "Criteo", "Intercom", "Channel Talk",
                   "Sentry", "Stripe", "PAY.JP", "Supabase", "YouTube", "reCAPTCHA", "Turnstile", "Google Fonts", "FONTPLUS"):
        assert wanted in names, wanted


@pytest.mark.parametrize("url, name", [
    ("https://www.google-analytics.com/g/collect?v=2", "Google Analytics"),
    ("https://www.googletagmanager.com/gtag/js?id=G-1", "Google tag (gtag.js, Tag Manager)"),
    ("https://www.google.com/recaptcha/api.js", "reCAPTCHA"),
    ("https://www.gstatic.com/recaptcha/releases/x.js", "reCAPTCHA"),
    ("https://www.gstatic.com/firebasejs/10/firebase-app.js", "Google Hosted Libraries / static content"),
    ("https://www.google.com/pagead/1p-user-list/1", "Google Ads / DoubleClick"),
    ("https://cdnjs.cloudflare.com/ajax/libs/x.js", "cdnjs"),
    ("https://challenges.cloudflare.com/turnstile/v0/api.js", "Cloudflare Turnstile"),
    ("https://js.stripe.com/v3/", "Stripe"),
    ("https://d.line-scdn.net/n/line_tag/public/release/v1/lt.js", "LINE Tag"),
    ("https://platform.twitter.com/widgets.js", "X (Twitter) widgets and media"),
    ("https://bat.bing.com/bat.js", "Microsoft Advertising (UET)"),
    ("https://static.ads-twitter.com/uwt.js", "X (Twitter) Ads"),
])
def test_match_vendor_takes_the_longest_pattern(url, name):
    assert lib.match_vendor(url, VENDORS)["name"] == name


def test_unknown_and_lookalike_hosts_match_nothing():
    assert lib.match_vendor("https://www.google.com/search?q=1", VENDORS) is None
    assert lib.match_vendor("https://notstripe.com/v3/", VENDORS) is None
    assert lib.match_vendor("https://stripe.com.evil.example/v3/", VENDORS) is None


# ---- hosts

@pytest.mark.parametrize("host, expected", [
    ("www.example.co.jp", "example.co.jp"), ("a.b.example.com", "example.com"), ("me.github.io", "me.github.io"),
    ("x.me.github.io", "me.github.io"), ("app.vercel.app", "app.vercel.app"), ("localhost", "localhost"),
    ("127.0.0.1", "127.0.0.1"), ("example.com", "example.com"), ("shop.example.com.au", "example.com.au"),
])
def test_registrable(host, expected):
    assert lib.registrable(host) == expected


def test_is_local():
    assert lib.is_local("localhost") and lib.is_local("127.0.0.1") and lib.is_local("192.168.1.4")
    assert not lib.is_local("localhost.example.com") and not lib.is_local("example.jp")


# ---- names on a notice

def test_mentions_needs_word_boundaries_for_latin_names():
    spaced, squashed = lib.normalise("We use Stripe for payments, and HotjarX."), lib.squash("We use Stripe, HotjarX")
    assert lib.mentions("Stripe", spaced, squashed)
    assert not lib.mentions("Hotjar", spaced, squashed)
    assert not lib.mentions("LINE Tag", lib.normalise("see the online tag list"), "")


def test_mentions_ignores_spacing_and_width():
    text = "当社は Google  アナリティクス および Ｇｏｏｇｌｅ Ａｎａｌｙｔｉｃｓ を利用します"
    spaced, squashed = lib.normalise(text), lib.squash(text)
    assert lib.mentions("Googleアナリティクス", spaced, squashed)
    assert lib.mentions("Google Analytics", spaced, squashed)


def test_disclosure_levels():
    vendor = next(vendor for vendor in VENDORS if vendor["name"] == "Google Ads / DoubleClick")
    def level(text):
        return lib.disclosure(vendor, lib.normalise(text), lib.squash(text))[0]
    assert level("Google広告を利用しています") == "service"
    assert level("Google Analytics を利用しています") == "operator"
    assert level("外部サービスは利用していません") == "none"


# ---- links

def test_find_role_by_text_and_by_address():
    links = [link("https://x.jp/a", "プライバシーポリシー"), link("https://x.jp/privacy-policy", ""), link("https://x.jp/blog", "ブログ"),
             link("https://x.jp/photos", "photos"), link("mailto:hi@x.jp", "メール"), link("javascript:void(0)", "privacy")]
    assert [item["href"] for item in lib.find_role(links, "policy")] == ["https://x.jp/a", "https://x.jp/privacy-policy"]
    assert lib.find_role(links, "terms") == []
    assert [item["href"] for item in lib.find_role(links, "contact")] == ["mailto:hi@x.jp"]


def test_content_hosts_are_not_taken_for_tags():
    for url in ("https://th.bing.com/th?id=1", "https://i.pinimg.com/236x/a.jpg", "https://www.linkedin.com/company/x"):
        assert lib.match_vendor(url, VENDORS) is None, url


def test_role_needs_a_label_not_a_sentence_and_ignores_fragments_and_other_sites():
    sentence = link("https://x.jp/#faq", "プライバシーはどのように保護されますか。アップロードしたファイルは保存されません")
    assert lib.find_role([sentence, link("https://x.jp/guide.html#supported-platforms", "ガイド")], "policy") == []
    assert lib.find_role([link("https://x.jp/guide.html#supported-platforms", "ガイド")], "contact") == []
    assert lib.find_role([link("https://x.jp/guide.html#supported-platforms", "Supported Platforms")], "contact") == []
    assert lib.find_role([link("https://x.jp/a", "Contact us")], "contact") and lib.find_role([link("https://x.jp/b", "Privacy Policy")], "policy")
    other = [link("https://www.linkedin.com/company/x", "LinkedIn"), link("https://x.jp/company", "")]
    assert [item["href"] for item in lib.find_role(other, "operator", "x.jp")] == ["https://x.jp/company"]


def test_choose_pages_prefers_navigation_and_stays_on_the_site():
    top = page(links=[link("https://shop.example.jp/a", area="body"), link("https://shop.example.jp/b", area="nav"),
                      link("https://other.example.com/c", area="nav"), link("https://shop.example.jp/file.pdf", area="nav"),
                      link("https://shop.example.jp/#top", area="nav"), link("https://shop.example.jp/b#x", area="footer")])
    assert lib.choose_pages(top, "example.jp", 5) == ["https://shop.example.jp/b", "https://shop.example.jp/a"]


# ---- required pages

def test_complete_site_has_no_findings():
    result = check(site())
    assert result["findings"] == [] and result["stats"]["paid"] is False


def test_missing_pages_are_reported_by_severity():
    result = check(site(policy=None, links=[link("https://shop.example.jp/blog", "ブログ")]))
    found = {finding["rule"]: finding["severity"] for finding in result["findings"]}
    assert found == {"policy-missing": "fail", "terms-missing": "warn", "contact-missing": "warn", "operator-missing": "info"}


def test_page_error():
    result = check({"url": "https://x.jp/", "pages": [{"url": "https://x.jp/", "error": "TimeoutError: 30000ms"}]})
    assert rules(result) == ["page-error"]


# ---- external services

GA = "https://www.google-analytics.com/g/collect"


def test_undisclosed_service_names_hosts_page_and_pages_searched():
    result = check(site(requests=[GA, "https://shop.example.jp/app.js", "https://cdn.example.jp/a.css"]))
    assert rules(result) == ["external-undisclosed"]
    finding = result["findings"][0]
    assert finding["severity"] == "warn" and "Google Analytics" in finding["message"]
    assert finding["detail"]["hosts"] == ["www.google-analytics.com"]
    assert finding["detail"]["searched"] == ["https://shop.example.jp/privacy"]
    assert "law" not in finding["message"].lower() and "illegal" not in finding["message"].lower()


def test_service_named_on_the_policy_is_not_reported():
    assert check(site(requests=[GA], policy=POLICY + "アクセス解析に Google アナリティクス を利用しています。"))["findings"] == []


def test_service_named_only_on_a_separate_notice_page_is_not_reported():
    notice = {"notice1": {"url": "https://shop.example.jp/external", "text": "外部送信: Google Analytics (Google LLC) 閲覧履歴"}}
    assert check(site(requests=[GA], documents=notice))["findings"] == []


def test_operator_named_without_the_service_is_info():
    result = check(site(requests=["https://googleads.g.doubleclick.net/pagead/x"], policy=POLICY + "Google Analytics を利用"))
    assert [(finding["rule"], finding["severity"]) for finding in result["findings"]] == [("external-service-unnamed", "info")]


def test_fonts_and_cdns_are_info_and_unknown_hosts_are_grouped():
    result = check(site(requests=["https://fonts.googleapis.com/css2", "https://a.tracker.example.net/t.js",
                                  "https://b.tracker.example.net/u.js"]))
    found = {finding["rule"]: finding for finding in result["findings"]}
    assert found["external-undisclosed"]["severity"] == "info"
    assert found["external-unknown"]["detail"]["hosts"] == ["a.tracker.example.net", "b.tracker.example.net"]
    assert len(result["findings"]) == 2


def test_without_any_policy_services_are_still_listed():
    result = check(site(requests=[GA], policy=None, links=[link("https://shop.example.jp/terms", "利用規約")]))
    assert {"external-undisclosed", "policy-missing"} <= set(rules(result))
    assert "no privacy policy" in next(f for f in result["findings"] if f["rule"] == "external-undisclosed")["message"]


def test_policy_linked_but_not_loaded_is_unreadable():
    assert rules(check(site(requests=[GA], policy=None))) == ["notice-unreadable"]


def test_unreadable_policy_is_one_finding_not_one_per_service():
    result = check(site(requests=[GA, "https://www.clarity.ms/tag/x"], policy="PDF"))
    assert rules(result) == ["notice-unreadable"]


def test_ignore_list_and_first_party_requests():
    assert check(site(requests=[GA, "https://api.example.jp/v1"]), ignore=("Google Analytics",))["findings"] == []


# ---- paid sites

STRIPE = "https://js.stripe.com/v3/"
FULL = ("特定商取引法に基づく表記 販売業者 株式会社例 所在地 東京都 電話番号 03-0000-0000 運営責任者 山田 販売価格 各ページに記載 "
        "支払方法 クレジットカード 支払時期 購入時 引渡時期 即時 返品 不可")
STRIPE_POLICY = POLICY + "決済に Stripe を利用します。"


def test_free_site_is_not_asked_for_a_statements_page():
    result = check(site(text="料金は無料です。ブログの購入履歴はありません。"))
    assert result["findings"] == [] and result["stats"]["paid"] is False


def test_paid_by_payment_service_without_statements_page_fails():
    result = check(site(requests=[STRIPE], policy=STRIPE_POLICY))
    assert rules(result) == ["tokushoho-missing"] and result["findings"][0]["severity"] == "fail"
    assert "Stripe is loaded" in result["findings"][0]["message"]


def test_paid_by_price_next_to_purchase_wording():
    assert lib.paid_evidence([page(text="プロプラン 月額 ¥1,980 で今すぐ申し込む")], VENDORS)
    assert lib.paid_evidence([page(text="スタンダード 2,980円（税込）")], VENDORS)
    assert not lib.paid_evidence([page(text="資本金 1,000,000円")], VENDORS)
    assert not lib.paid_evidence([page(text="購入" + "あ" * 200 + "1,000円")], VENDORS)


def test_paid_flag_overrides_detection():
    assert rules(check(site(), paid="yes")) == ["tokushoho-missing"]
    assert check(site(requests=[STRIPE], policy=STRIPE_POLICY), paid="no")["findings"] == []


def test_statements_page_complete_and_incomplete():
    links = FOOTER + [link("https://shop.example.jp/tokushoho", "特定商取引法に基づく表記")]
    def with_page(text):
        return site(requests=[STRIPE], policy=STRIPE_POLICY, links=links,
                    documents={"tokushoho": {"url": "https://shop.example.jp/tokushoho", "text": text}})
    assert check(with_page(FULL))["findings"] == []
    result = check(with_page("特定商取引法に基づく表記 販売業者 株式会社例 販売価格 各ページ 支払方法 カード 返品 不可 運営責任者 山田"))
    assert rules(result) == ["tokushoho-incomplete"]
    assert result["findings"][0]["detail"]["missing"] == ["address (所在地)", "phone number (電話番号)", "payment time (支払時期)",
                                                         "delivery time (引渡時期 / 提供時期)"]


def test_disclosure_on_request_satisfies_address_and_phone():
    text = FULL.replace("所在地 東京都 電話番号 03-0000-0000", "請求があった場合には遅滞なく開示いたします")
    assert lib.tokushoho_gaps(text) == []


# ---- leftovers

def test_test_keys_are_found_and_shortened():
    source = {"url": "https://shop.example.jp/app.js", "text": 'Stripe("pk_test_51AbCdEfGhIjKlMnOp"); const live = "pk_live_51AbCdEfGhIjKl";'}
    result = check(site(sources=[source]))
    assert rules(result) == ["test-key"]
    assert result["findings"][0]["detail"] == {"key": "pk_test_51Ab...", "source": "https://shop.example.jp/app.js"}


def test_localhost_references_only_on_published_sites():
    links = FOOTER + [link("http://localhost:3000/dashboard", "ダッシュボード")]
    result = check(site(links=links, requests=["http://127.0.0.1:8000/api/me"]))
    assert rules(result) == ["localhost-reference", "localhost-reference"]
    local = {"url": "http://localhost:3000/", "pages": [page(url="http://localhost:3000/", links=links)],
             "documents": {"policy": {"url": "http://localhost:3000/privacy", "text": POLICY}}}
    assert "localhost-reference" not in rules(check(local))


# ---- interface

def test_embedded_script_is_ascii_only():
    assert lib.EXTRACT.isascii() and lib.SCROLL.isascii()


def test_run_without_url_is_a_runtime_error():
    with pytest.raises(RuntimeError, match="no URL"):
        lib.run(argparse.Namespace(root=None, config={}, url=None))


def test_every_emitted_rule_is_declared_and_summary_states_the_limit():
    assert "not whether the site is lawful" in lib.SUMMARY
    parser = argparse.ArgumentParser()
    lib.add_arguments(parser)
    args = parser.parse_args(["https://x.jp/", "--pages", "3", "--paid", "no"])
    assert (args.url, args.pages, args.paid) == ("https://x.jp/", 3, "no")

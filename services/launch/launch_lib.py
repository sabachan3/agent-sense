"""Launch check for a web service aimed at Japan: what the site sends out, and which pages it links.

The check loads the site in Chromium, records every request the pages make, and compares the
third-party services it saw with the names written on the privacy policy and on any page about
external transmission. It also looks for links to a privacy policy, terms, a contact, the
operator, and (for sites that sell something) the page of statements under the Act on Specified
Commercial Transactions, and for test-mode keys and localhost references left in the page.

It reports what was found and what was not found, and where it looked. It does not decide
whether a site meets any law, and nothing it prints is legal advice. See docs/launch.md.

Playwright is imported only inside the functions that drive a browser; the analysis is pure
functions over the recorded site (`analyse`), so it can be tested without one.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

NAME = "launch"
SUMMARY = ("external services a site sends data to versus the names on its privacy policy, plus required-page links, "
           "for services aimed at Japan (reports what was found, not whether the site is lawful)")
RULES = {
    "page-error": "fail",
    "external-undisclosed": "warn",
    "external-service-unnamed": "info",
    "external-unknown": "info",
    "notice-unreadable": "warn",
    "policy-missing": "fail",
    "terms-missing": "warn",
    "contact-missing": "warn",
    "operator-missing": "info",
    "tokushoho-missing": "fail",
    "tokushoho-incomplete": "warn",
    "test-key": "fail",
    "localhost-reference": "fail",
}
VENDORS_FILE = Path(__file__).with_name("vendors.json")
QUIET_CATEGORIES = ("cdn", "fonts")   # still third parties, reported as info
MAX_TEXT = 200_000
MAX_LINKS = 1500
MAX_SCRIPTS = 40
MAX_SCRIPT_BYTES = 3_000_000
MAX_UNKNOWN = 30
MAX_LINK_TEXT = 30        # a longer link text is a sentence that happens to contain the word, not a label
MIN_NOTICE_CHARS = 200    # less readable text than this on the notice pages and names cannot be compared

# Second-level suffixes under which each name is its own site. This is a short built-in list, not
# the Public Suffix List: a host under a suffix that is missing here is grouped one level too high.
_SUFFIXES = frozenset("""
co.jp ne.jp or.jp go.jp ac.jp ad.jp ed.jp gr.jp lg.jp com.au net.au org.au co.uk org.uk ac.uk gov.uk co.kr or.kr
com.cn net.cn com.tw com.hk com.sg co.nz com.br co.in co.id com.mx co.th com.vn com.my
github.io gitlab.io vercel.app netlify.app pages.dev workers.dev web.app firebaseapp.com herokuapp.com onrender.com
fly.dev cloudfront.net azurewebsites.net appspot.com run.app amplifyapp.com surge.sh readthedocs.io blogspot.com
hatenablog.com hatenablog.jp myshopify.com thebase.in stores.jp
""".split())
_LOCAL = re.compile(r"^(localhost|.*\.localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|10\.\d+\.\d+\.\d+|"
                    r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$")

# Link roles: (words looked for in the link text, pattern looked for in the link address).
ROLES = {
    "policy": (("プライバシー", "個人情報", "privacy"), r"privacy|kojin|personal-?info|/pp(?:/|\.html?)?$"),
    "terms": (("利用規約", "ご利用規約", "サービス規約", "会員規約", "規約", "ご利用条件", "terms of", "terms"),
              r"terms|/tos\b|kiyaku|agreement|/rule"),
    "contact": (("お問い合わせ", "お問合せ", "お問い合せ", "問い合わせ", "問合せ", "contact", "inquiry", "サポート", "support",
                 "ヘルプ"),
                r"/contact|/inquiry|toiawase|/support(?:/|$)|^mailto:|forms\.gle/|docs\.google\.com/forms|/help(?:/|$)"),
    "operator": (("運営会社", "会社概要", "運営者", "会社情報", "企業情報", "運営元", "about us", "company", "会社案内"),
                 r"/company|/about|/corporate|/corp\b|unei"),
    "tokushoho": (("特定商取引", "特商法", "通信販売に関する表示"), r"tokusho|tokutei|scta|commerce-?law|commercial-?transaction"),
    "notice": (("外部送信", "利用者情報の送信", "情報収集モジュール", "インフォマティブデータ", "cookieポリシー",
                "クッキーポリシー", "cookie policy", "cookieについて", "クッキーについて", "cookieの利用", "外部ツール"),
               r"gaibu|external-?(?:transmission|service|tool)|cookie|datatransmission|transmission"),
}
# Addresses that only suggest a statements page; accepted when that page mentions the act itself.
TOKUSHOHO_WEAK = r"/legal|/law\b|/commerce|/notation|/sct\b"
TOKUSHOHO_PAGE_WORDS = ("特定商取引", "特商法")

PRICE = re.compile(r"(?:[¥￥]\s?\d[\d,]{2,}|\d[\d,]{2,}\s?円)")
PURCHASE_WORDS = ("購入", "カートに入れる", "カートに追加", "申し込", "申込", "お支払", "決済", "月額", "年額", "税込",
                  "有料プラン", "料金プラン", "サブスクリプション", "定期購入")
PURCHASE_WINDOW = 80   # characters between a price and a purchase word for the page to count as selling

ON_REQUEST = re.compile(r"請求.{0,30}(?:遅滞なく|開示|提供)|開示.{0,12}請求|ご請求いただ")
TOKUSHOHO_ITEMS = (
    ("seller name (販売業者)", ("販売業者", "販売事業者", "事業者名", "事業者の名称", "会社名", "運営会社", "屋号", "販売者",
                             "事業者の氏名", "名称"), False),
    ("address (所在地)", ("所在地", "住所"), True),
    ("phone number (電話番号)", ("電話番号", "tel", "電話"), True),
    ("person in charge (代表者 / 運営責任者)", ("代表者", "責任者", "代表取締役"), False),
    ("price (販売価格)", ("販売価格", "価格", "料金", "対価", "代金"), False),
    ("payment method (支払方法)", ("支払方法", "支払い方法", "お支払方法", "決済方法", "決済手段", "支払手段"), False),
    ("payment time (支払時期)", ("支払時期", "支払い時期", "支払期限", "支払い期限", "決済時期", "支払の時期", "支払いの時期",
                              "課金時期", "請求時期"), False),
    ("delivery time (引渡時期 / 提供時期)", ("引渡時期", "引渡し時期", "引き渡し時期", "提供時期", "引渡", "引き渡し", "納期",
                                       "提供開始", "利用開始", "発送時期", "お届け"), False),
    ("returns and cancellation (返品 / キャンセル)", ("返品", "キャンセル", "解約", "返金"), False),
)

TEST_KEYS = (
    ("test-mode publishable key (pk_test_, as used by Stripe, PAY.JP and Clerk)", re.compile(r"\bpk_test_[A-Za-z0-9]{10,}")),
    ("test-mode secret key (sk_test_ or rk_test_)", re.compile(r"\b[sr]k_test_[A-Za-z0-9]{10,}")),
    ("Square sandbox application id", re.compile(r"\bsandbox-sq0id[bp]-[A-Za-z0-9_-]{10,}")),
)
SANDBOX_HOSTS = ("sandbox.paypal.com", "squareupsandbox.com", "sandbox.komoju.com")

EXTRACT = r"""
([maxText, maxLinks]) => {
  const area = el => el.closest('footer, [role="contentinfo"], [class*="footer" i], [id*="footer" i]') ? 'footer'
    : el.closest('nav, header, [role="navigation"]') ? 'nav' : 'body';
  const links = [];
  for (const a of document.querySelectorAll('a[href]')) {
    if (links.length >= maxLinks) break;
    const img = a.querySelector('img[alt]');
    const text = (a.innerText || a.textContent || a.getAttribute('aria-label') || a.getAttribute('title')
      || (img ? img.getAttribute('alt') : '') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    links.push({href: a.href, text, area: area(a)});
  }
  const forms = document.querySelectorAll('form').length;
  const text = (document.body ? document.body.innerText : '').slice(0, maxText);
  return {links, forms, text, title: document.title.slice(0, 200)};
}
"""
# To the bottom, and again while the page keeps growing: footers are often rendered only when reached.
SCROLL = """async () => {
  const height = () => document.body ? document.body.scrollHeight : 0;
  for (let step = 0, last = -1; step < 5 && height() !== last; step++) {
    last = height();
    window.scrollTo(0, last);
    await new Promise(resolve => setTimeout(resolve, 350));
  }
}"""


# ---------------------------------------------------------------- hosts and vendors

def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_local(host: str) -> bool:
    return bool(_LOCAL.match(host))


def registrable(host: str) -> str:
    """The site a host belongs to: the name directly under its suffix (see _SUFFIXES for the limits)."""
    host = host.lower().rstrip(".")
    if not host or is_local(host) or re.fullmatch(r"[\d.]+|\[.*\]", host):
        return host
    labels = host.split(".")
    for size in (3, 2):
        if len(labels) > size and ".".join(labels[-size:]) in _SUFFIXES:
            return ".".join(labels[-size - 1:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def load_vendors(path: Path | None = None) -> list[dict]:
    return json.loads((path or VENDORS_FILE).read_text(encoding="utf-8"))["vendors"]


def _pattern_match(pattern: str, host: str, path: str) -> bool:
    want_host, _, want_path = pattern.partition("/")
    if host != want_host and not host.endswith("." + want_host):
        return False
    return not want_path or path.startswith("/" + want_path)


def match_vendor(url: str, vendors: list[dict]) -> dict | None:
    """The vendor whose longest host pattern matches the request address, or None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host, path = (parts.hostname or "").lower(), parts.path or "/"
    best, best_size = None, -1
    for vendor in vendors:
        for pattern in vendor["hosts"]:
            if len(pattern) > best_size and _pattern_match(pattern, host, path):
                best, best_size = vendor, len(pattern)
    return best


# ---------------------------------------------------------------- text matching

def normalise(text: str) -> str:
    """Width-folded, lower-case text with runs of whitespace as one space."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "").lower()).strip()


def squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "").lower())


def mentions(alias: str, spaced: str, squashed: str) -> bool:
    """Whether a notice names `alias`. Latin names need word boundaries; Japanese ones ignore spacing."""
    wanted = normalise(alias)
    if not wanted:
        return False
    if wanted.isascii():
        body = r"\s*".join(re.escape(part) for part in wanted.split(" "))
        return re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", spaced) is not None
    return squash(alias) in squashed


def disclosure(vendor: dict, spaced: str, squashed: str) -> tuple[str, str | None]:
    """('service', alias) when the service is named, ('operator', alias) when only its company is, else ('none', None)."""
    for alias in [vendor["name"], *vendor.get("aliases", [])]:
        if mentions(alias, spaced, squashed):
            return "service", alias
    for alias in [vendor.get("operator", ""), *vendor.get("operator_aliases", [])]:
        if alias and mentions(alias, spaced, squashed):
            return "operator", alias
    return "none", None


# ---------------------------------------------------------------- links

def _labelled(link: dict, role: str) -> bool:
    text, spaced = squash(link.get("text", "")), normalise(link.get("text", ""))
    if len(text) > MAX_LINK_TEXT:
        return False
    for word in ROLES[role][0]:
        if word.isascii():  # "support" must not match "Supported Platforms", "terms" not "determs"
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", spaced):
                return True
        elif squash(word) in text:
            return True
    return False


def link_role_hits(link: dict, role: str, site: str | None = None) -> bool:
    """Whether a link plays `role`: by its (short) text, or by the path of its address.

    The operator's page is only recognised by address on the site itself, since /company and
    /about on another site are somebody else's.
    """
    href = (link.get("href") or "").lower()
    if _labelled(link, role):
        return True
    if role == "operator" and site and registrable(host_of(href)) != site:
        return False
    target = href if role == "contact" else urlsplit(href).path
    return re.search(ROLES[role][1], target) is not None


def find_role(links: list[dict], role: str, site: str | None = None) -> list[dict]:
    """Links that play `role`, links matched by their text first, without duplicate addresses."""
    hits = [link for link in links if _safe(link) and link_role_hits(link, role, site)]
    hits.sort(key=lambda link: 0 if _labelled(link, role) else 1)
    seen, unique = set(), []
    for link in hits:
        key = urldefrag(link["href"])[0] if not link["href"].startswith("mailto:") else link["href"]
        if key not in seen:
            seen.add(key)
            unique.append(link)
    return unique


# A page that only lists the legal documents ("規約・ポリシー一覧"): the footer links to it instead of to each document.
HUB_WORDS = ("ポリシー", "規約", "法的", "legal", "policies", "policy")
HUB_PATH = r"/(?:polic(?:y|ies)|legal|terms|rules?|kiyaku)(?:/|/index\.html?)?$"


def find_hubs(links: list[dict], site: str) -> list[dict]:
    """Same-site links to a page that may list the policy, the terms and the statements page."""
    hits = []
    for link in links:
        href = link.get("href") or ""
        if not href.startswith(("http://", "https://")) or registrable(host_of(href)) != site:
            continue
        text, spaced = squash(link.get("text", "")), normalise(link.get("text", ""))
        labelled = len(text) <= MAX_LINK_TEXT and any(
            (re.search(rf"(?<![a-z]){word}(?![a-z])", spaced) if word.isascii() else word in text) for word in HUB_WORDS)
        if labelled or re.search(HUB_PATH, urlsplit(href.lower()).path):
            hits.append(link)
    # a link that says it lists policies or terms before one that only has a path such as /legal/
    def named(link: dict) -> bool:
        return any(word in squash(link.get("text", "")) for word in ("ポリシー", "規約", "policies"))
    hits.sort(key=lambda link: (0 if named(link) else 1, 0 if link.get("area") == "footer" else 1))
    seen, unique = set(), []
    for link in hits:
        key = urldefrag(link["href"])[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(link)
    return unique


def _safe(link: dict) -> bool:
    href = link.get("href") or ""
    return href.startswith(("http://", "https://", "mailto:"))


def weak_tokushoho(links: list[dict]) -> list[dict]:
    return [link for link in links if _safe(link) and re.search(TOKUSHOHO_WEAK, urlsplit(link["href"]).path.lower())]


def choose_pages(top: dict, site: str, limit: int) -> list[str]:
    """Same-site pages worth loading to see more tags: footer and navigation links first."""
    order = {"nav": 0, "footer": 1, "body": 2}
    start = urldefrag(top.get("final_url") or top["url"])[0]
    seen, chosen = {start, start.rstrip("/")}, []
    for link in sorted(top.get("links", []), key=lambda item: order.get(item.get("area"), 2)):
        href = urldefrag(link.get("href") or "")[0]
        if not href.startswith(("http://", "https://")) or registrable(host_of(href)) != site:
            continue
        if href in seen or href.rstrip("/") in seen:
            continue
        if re.search(r"\.(pdf|zip|png|jpe?g|gif|svg|webp|mp4|xml|json|csv|dmg|exe)$", urlsplit(href).path.lower()):
            continue
        seen.add(href)
        chosen.append(href)
        if len(chosen) >= limit:
            break
    return chosen


# ---------------------------------------------------------------- paid, statements page, keys

def paid_evidence(pages: list[dict], vendors: list[dict]) -> list[str]:
    """Reasons to think the site sells something: payment services loaded, or prices next to purchase wording."""
    reasons = []
    for page in pages:
        for request in page.get("requests", []):
            vendor = match_vendor(request["url"], vendors)
            # the payment company's own site loads its own hosts; that is not a shop using the service
            if vendor and vendor.get("paid") and match_vendor(page["url"], vendors) is not vendor:
                reason = f"{vendor['name']} is loaded on {page['url']}"
                if reason not in reasons:
                    reasons.append(reason)
        text = unicodedata.normalize("NFKC", page.get("text", ""))
        for found in PRICE.finditer(text):
            window = text[max(0, found.start() - PURCHASE_WINDOW):found.end() + PURCHASE_WINDOW]
            word = next((word for word in PURCHASE_WORDS if word in window), None)
            if word:
                reasons.append(f'price "{found.group(0)}" next to "{word}" on {page["url"]}')
                break
    return reasons[:6]


def tokushoho_gaps(text: str) -> list[str]:
    """Commonly listed items with no matching keyword on the statements page."""
    squashed = squash(text)
    on_request = ON_REQUEST.search(squashed) is not None
    gaps = []
    for label, words, may_be_on_request in TOKUSHOHO_ITEMS:
        if any(squash(word) in squashed for word in words) or (may_be_on_request and on_request):
            continue
        gaps.append(label)
    return gaps


def find_test_keys(text: str) -> list[tuple[str, str]]:
    found = []
    for label, pattern in TEST_KEYS:
        for match in pattern.finditer(text or ""):
            value = match.group(0)
            item = (label, value[:12] + "..." if len(value) > 15 else value)
            if item not in found:
                found.append(item)
    return found


# ---------------------------------------------------------------- analysis

def _finding(rule: str, message: str, fix: str = "", severity: str | None = None, **detail) -> dict:
    finding = {"rule": rule, "severity": severity or RULES[rule], "message": message, "fix": fix}
    if detail:
        finding["detail"] = detail
    return finding


def analyse(record: dict, vendors: list[dict], paid: str = "auto", ignore: tuple[str, ...] = ()) -> dict:
    """Findings and stats for a recorded site (the shape `crawl` returns)."""
    top = record["pages"][0] if record.get("pages") else {"url": record.get("url", ""), "error": "no page loaded"}
    if top.get("error"):
        return {"findings": [_finding("page-error", f"could not load {top['url']}: {top['error']}",
                                      "check the URL and that the server is running")],
                "stats": {"pages": 0}}
    status = top.get("status")
    if isinstance(status, int) and status >= 400:
        # a maintenance, error or bot-check page has no footer: its missing links say nothing about the site
        return {"findings": [_finding("page-error", f"{top['url']} answered HTTP {status}; nothing was judged",
                                      "run again when the site answers normally, or pass a page that loads")],
                "stats": {"pages": 0, "status": status}}
    site_host = host_of(top.get("final_url") or top["url"])
    site = registrable(site_host)
    pages = [page for page in record["pages"] if not page.get("error")]
    site_pages = [page for page in pages if registrable(host_of(page.get("final_url") or page["url"])) == site]
    links = [link for page in site_pages for link in page.get("links", [])]
    documents = record.get("documents", {})
    findings: list[dict] = []
    looked = f"links on {len(site_pages)} page(s) of {site}"

    # --- required pages
    roles = {role: find_role(links, role, site) for role in ROLES}
    if not roles["policy"] and not documents.get("policy"):
        findings.append(_finding(
            "policy-missing", f"no link to a privacy policy was found ({looked})",
            "link the privacy policy from every page, usually the footer, with a text such as プライバシーポリシー"))
    if not roles["terms"]:
        findings.append(_finding("terms-missing", f"no link to terms of use was found ({looked})",
                                 "link the terms (利用規約) from the footer, or ignore this if the site has no accounts or sales"))
    has_form = any(page.get("forms") for page in site_pages)
    if not roles["contact"]:
        note = "; a form exists on the site but no link is labelled as a contact" if has_form else ""
        findings.append(_finding("contact-missing", f"no contact link, form link or mail address was found ({looked}){note}",
                                 "link a contact page (お問い合わせ) or a mail address from the footer"))
    if not roles["operator"]:
        findings.append(_finding("operator-missing", f"no link to a page about the operator was found ({looked})",
                                 "say who runs the service (運営者情報 / 会社概要) and link it"))

    # --- what the site sends out
    seen: dict[str, dict] = {}
    unknown: dict[str, dict] = {}
    for page in site_pages:
        for request in page.get("requests", []):
            host = host_of(request["url"])
            if not host or is_local(host) or registrable(host) == site:
                continue
            vendor = match_vendor(request["url"], vendors)
            if vendor:
                entry = seen.setdefault(vendor["name"], {"vendor": vendor, "hosts": [], "pages": []})
            else:
                entry = unknown.setdefault(registrable(host), {"hosts": [], "pages": []})
            if host not in entry["hosts"]:
                entry["hosts"].append(host)
            if page["url"] not in entry["pages"]:
                entry["pages"].append(page["url"])

    notice_docs = [doc for key, doc in documents.items() if key == "policy" or key.startswith("notice")]
    notice_text = "\n".join(doc.get("text", "") for doc in notice_docs)
    searched = [doc["url"] for doc in notice_docs]
    spaced, squashed = normalise(notice_text), squash(notice_text)
    readable = len(squashed) >= MIN_NOTICE_CHARS
    reportable = {name: entry for name, entry in seen.items() if name not in ignore}
    linked = [link["href"] for link in roles["policy"][:2]]
    if not notice_docs and linked:
        searched, notice_docs = linked, [{"url": address, "text": ""} for address in linked]  # linked, but nothing was read
    if notice_docs and not readable and reportable:
        findings.append(_finding(
            "notice-unreadable",
            f"the policy page(s) had {len(squashed)} characters of readable text, so service names could not be compared: "
            + ", ".join(searched),
            "publish the policy as page text (not an image or a PDF), or pass the right page with --policy",
            searched=searched, services=sorted(reportable)))
    for name, entry in sorted(reportable.items()):
        vendor = entry["vendor"]
        quiet = vendor["category"] in QUIET_CATEGORIES
        detail = {"vendor": name, "operator": vendor.get("operator", ""), "category": vendor["category"],
                  "hosts": entry["hosts"][:8], "pages": entry["pages"][:5], "searched": searched}
        where = f"{', '.join(entry['hosts'][:3])} loaded on {entry['pages'][0]}"
        if not notice_docs:
            findings.append(_finding(
                "external-undisclosed",
                f"{name} ({vendor.get('operator', '')}) receives requests from the site ({where}) and no privacy policy "
                "or external-transmission page was found to compare with",
                "publish a page that names each external service, what is sent and why, and link it from the footer",
                severity="info" if quiet else None, **detail))
            continue
        if not readable:
            continue
        how, alias = disclosure(vendor, spaced, squashed)
        if how == "service":
            continue
        if how == "operator":
            findings.append(_finding(
                "external-service-unnamed",
                f'{name} receives requests from the site ({where}); the pages searched mention "{alias}" but not this service',
                f"name the service itself ({name}) with what is sent and why, if it is not covered by the existing text",
                matched=alias, **detail))
            continue
        findings.append(_finding(
            "external-undisclosed",
            f"{name} ({vendor.get('operator', '')}) receives requests from the site ({where}) and its name was not found on "
            + ", ".join(searched),
            f"add {name} to the privacy policy or the external-transmission page (the service, what is sent, the purpose), "
            "or remove the tag",
            severity="info" if quiet else None, **detail))
    for domain, entry in sorted(unknown.items(), key=lambda item: -len(item[1]["pages"]))[:MAX_UNKNOWN]:
        if domain in ignore:
            continue
        findings.append(_finding(
            "external-unknown",
            f"requests go to {domain} ({', '.join(entry['hosts'][:3])}), which is not in the vendor table; "
            f"first seen on {entry['pages'][0]}",
            "check whether this is your own infrastructure or an external service that the policy should name",
            domain=domain, hosts=entry["hosts"][:8], pages=entry["pages"][:5]))

    # --- statements under the Act on Specified Commercial Transactions (paid sites only)
    reasons = ["--paid yes"] if paid == "yes" else [] if paid == "no" else paid_evidence(site_pages, vendors)
    tokushoho = documents.get("tokushoho")
    if reasons:
        if tokushoho and any(word in squash(tokushoho.get("text", "")) for word in TOKUSHOHO_PAGE_WORDS) \
                or (tokushoho and roles["tokushoho"]):
            gaps = tokushoho_gaps(tokushoho.get("text", ""))
            if gaps:
                findings.append(_finding(
                    "tokushoho-incomplete",
                    f"the statements page {tokushoho['url']} has no wording for: {'; '.join(gaps)}",
                    "add the missing items, or ignore the ones that do not apply to what is sold",
                    page=tokushoho["url"], missing=gaps, paid_evidence=reasons))
        elif not roles["tokushoho"]:
            # A payment service that is loaded, or --paid yes, is evidence of selling on this site. A price in
            # the text is not: corporate sites, sales to businesses and marketplaces show prices too.
            firm = [reason for reason in reasons if not reason.startswith("price ")]
            findings.append(_finding(
                "tokushoho-missing",
                f"the site looks paid ({(firm or reasons)[0]}) and no link to 特定商取引法に基づく表記 was found ({looked})"
                + ("" if firm else "; only prices in the text suggest it, so check whether consumers can buy here"),
                "publish the statements page and link it from the footer; pass --paid no if nothing is sold here",
                severity=None if firm else "warn", paid_evidence=reasons))

    # --- leftovers from development
    keys: dict[tuple[str, str], str] = {}
    for page in pages:
        for source in page.get("sources", []):
            for item in find_test_keys(source.get("text", "")):
                keys.setdefault(item, source["url"])
    for (label, value), where in sorted(keys.items()):
        findings.append(_finding("test-key", f"{label} {value} is in {where}",
                                 "build the published site with the live-mode key", key=value, source=where))
    for page in site_pages:
        for request in page.get("requests", []):
            host = host_of(request["url"])
            if any(host == sandbox or host.endswith("." + sandbox) for sandbox in SANDBOX_HOSTS):
                keys_seen = (host, page["url"])
                if keys_seen not in keys:
                    keys[keys_seen] = ""
                    findings.append(_finding("test-key", f"the page {page['url']} calls the sandbox host {host}",
                                             "point the published site at the live payment environment", source=page["url"]))
    if not is_local(site_host):
        local: dict[str, str] = {}
        for page in site_pages:
            for request in page.get("requests", []):
                if is_local(host_of(request["url"])):
                    local.setdefault(request["url"][:200], f"requested by {page['url']}")
            for link in page.get("links", []):
                if is_local(host_of(link.get("href") or "")):
                    local.setdefault(link["href"][:200], f"linked from {page['url']}")
        for address, where in sorted(local.items())[:10]:
            findings.append(_finding("localhost-reference", f"{address} is {where}",
                                     "replace the development address with the published one", address=address))

    stats = {"site": site, "pages": len(site_pages), "pages_failed": len(record["pages"]) - len(pages),
             "services": sorted(seen), "unknown_domains": len(unknown), "notice_pages": searched,
             "notice_chars": len(squashed), "paid": bool(reasons), "paid_evidence": reasons,
             "roles": {role: [{"href": link["href"], "text": link.get("text", "")} for link in hits[:3]]
                       for role, hits in roles.items()}}
    return {"findings": findings, "stats": stats}


# ---------------------------------------------------------------- browser

async def _load(context, url: str, timeout: int, wait: int, scan_sources: bool, site: str) -> dict:
    """Load one page and return its record: text, links, requests, and same-site sources to scan for keys."""
    page = await context.new_page()
    requests, responses = [], []
    page.on("request", lambda request: requests.append({"url": request.url[:300], "type": request.resource_type}))
    if scan_sources:
        page.on("response", lambda response: responses.append(response))
    try:
        answer = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # pages that never go idle are read as they stand
        try:
            await page.evaluate(SCROLL)
            await page.wait_for_timeout(600 + wait)
        except Exception:
            pass
        data = await page.evaluate(EXTRACT, [MAX_TEXT, MAX_LINKS])
        record = {"url": url, "final_url": page.url, "status": answer.status if answer else None, **data,
                  "requests": requests, "sources": []}
        if scan_sources:
            record["sources"].append({"url": page.url, "text": (await page.content())[:MAX_SCRIPT_BYTES]})
            scripts = [response for response in responses if response.request.resource_type == "script"
                       and registrable(host_of(response.url)) == site][:MAX_SCRIPTS]
            for response in scripts:
                try:
                    record["sources"].append({"url": response.url[:300], "text": (await response.text())[:MAX_SCRIPT_BYTES]})
                except Exception:
                    continue  # body no longer available
        return record
    except Exception as error:
        return {"url": url, "error": f"{type(error).__name__}: {str(error).splitlines()[0][:200]}", "requests": requests}
    finally:
        await page.close()


async def crawl(url: str, pages: int = 8, policy: str | None = None, channel: str | None = None, timeout: int = 30000,
                wait: int = 0, route=None, browser=None) -> dict:
    """Load the top page, the policy, notice and statements pages it links, and more same-site pages up to `pages`.

    `route` is an optional Playwright route handler for every request (the eval uses it to serve
    fixture sites under made-up host names and to answer vendor hosts locally).
    """
    from playwright.async_api import async_playwright

    async def work(active) -> dict:
        context = await active.new_context(viewport={"width": 1280, "height": 900}, locale="ja-JP")
        try:
            if route:
                await context.route("**/*", route)
            top = await _load(context, url, timeout, wait, True, registrable(host_of(url)))
            record = {"url": url, "pages": [top], "documents": {}}
            if top.get("error"):
                return record
            site = registrable(host_of(top["final_url"]))
            visited = {urldefrag(top["final_url"])[0].rstrip("/"): top, urldefrag(url)[0].rstrip("/"): top}

            async def visit(address: str) -> dict:
                key = urldefrag(address)[0].rstrip("/")
                if key not in visited:
                    visited[key] = await _load(context, urldefrag(address)[0], timeout, wait,
                                               registrable(host_of(address)) == site, site)
                    record["pages"].append(visited[key])
                return visited[key]

            def remember(role: str, page: dict) -> None:
                if not page.get("error"):
                    record["documents"][role] = {"url": page.get("final_url") or page["url"], "text": page.get("text", "")}

            def http(links: list[dict]) -> list[str]:
                return [link["href"] for link in links if link["href"].startswith(("http://", "https://"))]

            hub_links: list[dict] = []
            if not policy and not find_role(top["links"], "policy"):
                for address in http(find_hubs(top["links"], site))[:2]:
                    hub_links += (await visit(address)).get("links", [])
                    if find_role(hub_links, "policy"):
                        break
            policies = [policy] if policy else http(find_role(top["links"] + hub_links, "policy"))[:1]
            for address in policies:
                remember("policy", await visit(address))
            pool = list(top["links"]) + hub_links
            for address in policies:
                pool += visited[urldefrag(address)[0].rstrip("/")].get("links", [])
            policy_keys = {urldefrag(address)[0].rstrip("/") for address in policies}
            notices = [address for address in http(find_role(pool, "notice"))
                       if urldefrag(address)[0].rstrip("/") not in policy_keys][:2]
            for index, address in enumerate(notices):
                remember(f"notice{index + 1}", await visit(address))
            statements = http(find_role(pool, "tokushoho"))[:1]
            if statements:
                remember("tokushoho", await visit(statements[0]))
            else:
                for address in http(weak_tokushoho(pool))[:2]:
                    page = await visit(address)
                    if any(word in squash(page.get("text", "")) for word in TOKUSHOHO_PAGE_WORDS):
                        remember("tokushoho", page)
                        break
            for address in choose_pages(top, site, pages):
                if len(record["pages"]) >= pages:
                    break
                await visit(address)
            # a policy or statements page linked only from a page loaded later (a shared footer on the main site)
            later = [link for page in record["pages"] if not page.get("error")
                     and registrable(host_of(page.get("final_url") or page["url"])) == site for link in page.get("links", [])]
            if "policy" not in record["documents"]:
                for address in http(find_role(later, "policy"))[:1]:
                    found = await visit(address)
                    remember("policy", found)
                    later += found.get("links", [])
                    if "notice1" not in record["documents"]:
                        key = urldefrag(address)[0].rstrip("/")
                        for extra in [item for item in http(find_role(later, "notice"))
                                      if urldefrag(item)[0].rstrip("/") != key][:2]:
                            remember(f"notice{len([name for name in record['documents'] if name.startswith('notice')]) + 1}",
                                     await visit(extra))
            if "tokushoho" not in record["documents"]:
                for address in http(find_role(later, "tokushoho"))[:1]:
                    remember("tokushoho", await visit(address))
            return record
        finally:
            await context.close()

    if browser is not None:
        return await work(browser)
    async with async_playwright() as playwright:
        own = await playwright.chromium.launch(channel=channel)
        try:
            return await work(own)
        finally:
            await own.close()


# ---------------------------------------------------------------- service interface

def add_arguments(parser) -> None:
    parser.add_argument("url", nargs="?", help="the site's top page (default: url in the [launch] table of .agent-sense.toml)")
    parser.add_argument("--pages", type=int, help="how many pages of the site to load (default 8)")
    parser.add_argument("--policy", help="address of the privacy policy, when the link cannot be found")
    parser.add_argument("--paid", choices=("auto", "yes", "no"),
                        help="whether the site sells something (default auto: payment services or prices on the pages)")
    parser.add_argument("--channel", help="drive an installed browser (chrome, msedge) instead of the bundled one")
    parser.add_argument("--timeout", type=int, default=30000, help="page load timeout in ms")
    parser.add_argument("--wait", type=int, default=0, help="extra ms to wait on each page, for tags that load late")


def run(args) -> dict:
    import asyncio

    config = getattr(args, "config", None) or {}
    url = getattr(args, "url", None) or config.get("url")
    if not url:
        raise RuntimeError("no URL: pass the site's top page, or set url in the [launch] table of .agent-sense.toml")
    pages = getattr(args, "pages", None) or int(config.get("pages", 8))
    paid = getattr(args, "paid", None) or config.get("paid", "auto")
    if paid not in ("auto", "yes", "no"):
        raise RuntimeError(f"paid must be auto, yes or no, not {paid!r}")
    try:
        record = asyncio.run(crawl(url, pages=max(1, pages), policy=getattr(args, "policy", None) or config.get("policy"),
                                   channel=getattr(args, "channel", None), timeout=getattr(args, "timeout", 30000),
                                   wait=getattr(args, "wait", 0), route=getattr(args, "route", None)))
    except ImportError as error:
        raise RuntimeError("playwright is not installed: pip install playwright") from error
    except Exception as error:
        raise RuntimeError(f"could not start the browser: {str(error).splitlines()[0][:200]}") from error
    return analyse(record, load_vendors(), paid=paid, ignore=tuple(config.get("ignore", ())))

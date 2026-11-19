# launch: what a site sends out, and which pages it links

```
agent-sense launch https://example.jp/ [--pages 8] [--policy URL] [--paid auto|yes|no]
                   [--channel chrome|msedge] [--timeout MS] [--wait MS]
```

For a web service aimed at Japan. The check opens the site in Chromium, records every request the
pages make, and reports what it found and what it did not find, with the pages it looked at.

**It does not decide whether a site meets any law, and nothing it prints is legal advice.** A
finding means "this name was not found on these pages" or "no link with this wording was found".
Whether that matters for your service is a question for you or your lawyer. A clean run does not
mean the site is compliant.

## What it loads

1. The top page.
2. The privacy policy linked from it (or `--policy`), up to two pages about external transmission
   or cookies linked from the top page or the policy, and the page of statements under the Act on
   Specified Commercial Transactions if one is linked.
3. More pages of the same site, navigation links first, then footer links, until `--pages` pages
   are loaded (default 8).

When the top page has no policy link but links to a page that lists the legal documents
("規約・ポリシー一覧", or a path ending in `/policy`, `/legal`, `/terms`), up to two such pages are
loaded and their links count.

Each page is loaded once, scrolled to the bottom up to five times while it keeps growing, and read
after the network goes quiet (8 s at most). Nothing is clicked, no form is submitted, no consent banner is answered.

## Rules

| rule | severity | reported when |
|---|---|---|
| `external-undisclosed` | warn (info for fonts and CDNs) | a page of the site sends a request to a service in the vendor table, and neither the service's name, its aliases, nor its operator appears on the policy or notice pages |
| `external-service-unnamed` | info | the same, but the operator is named (for example the policy names Google Analytics and the page also loads Google Ads) |
| `external-unknown` | info | requests go to another site that is not in the vendor table; one finding per site, 30 at most |
| `notice-unreadable` | warn | services were seen, a policy is linked, and it has fewer than 200 characters of text (an image, a PDF, a page that failed to load); names are then not compared |
| `policy-missing` | fail | no link to a privacy policy on any loaded page of the site |
| `terms-missing` | warn | no link to terms of use |
| `contact-missing` | warn | no contact link, form link or mail address |
| `operator-missing` | info | no link to a page about the operator |
| `tokushoho-missing` | fail, or warn when only prices in the text suggest selling | the site looks paid and no link to a statements page (特定商取引法に基づく表記) was found |
| `tokushoho-incomplete` | warn | the site looks paid and the statements page has no wording for some commonly listed items |
| `test-key` | fail | a test-mode key (`pk_test_`, `sk_test_`, `rk_test_`, Square sandbox id) is in the HTML or a same-site script, or a page calls a payment sandbox host |
| `localhost-reference` | fail | a published page links to or requests `localhost`, `127.0.0.1` or a private address |
| `page-error` | fail | the top page could not be loaded, or answered with HTTP 400 or above (maintenance, bot check); nothing else is judged then |

### How each is decided

**External services.** A request counts as third-party when its host belongs to another site than
the page (the name directly under the public suffix; a short built-in suffix list covers `co.jp`,
`ne.jp`, `com.au`, `github.io`, `vercel.app` and similar, not the full Public Suffix List). The
request address is matched against `services/launch/vendors.json` (110 services; host suffixes,
some with a path such as `google.com/recaptcha`; the longest match wins). The text of the policy
and notice pages is folded to half-width lower case. Latin names must appear as whole words
(spaces optional), Japanese names are matched ignoring spaces. `googletagmanager.com` also serves
`gtag.js` for Analytics and Ads, so that entry is satisfied by any of those names.

**Links.** A link plays a role when its text contains one of the words for that role
(プライバシー, 個人情報, privacy; 利用規約, 規約, terms; お問い合わせ, contact, サポート; 運営会社,
会社概要; 特定商取引, 特商法; 外部送信, Cookieポリシー) or its address matches the role's pattern
(`/privacy`, `/terms`, `/contact`, `mailto:`, `/company`, `/tokushoho`, `/cookie`). The lists are
constants at the top of `launch_lib.py`.

**Paid.** With `--paid auto`, a site looks paid when a page loads a payment or shop service
(Stripe, PayPal, PAY.JP, Square, KOMOJU, Shopify, BASE, STORES, GMO Payment Gateway), or shows a
price (`¥1,980`, `1,980円`) within 80 characters of purchase wording (購入, 申し込, 月額, 税込 and
similar). The evidence is in the finding. Free sites are never asked for a statements page.
A loaded payment service (or `--paid yes`) makes a missing statements page a `fail`. A price in
the text alone makes it a `warn`: corporate sites, services sold to businesses and marketplaces
show prices without selling to consumers on that site. A payment company's own site loading its
own hosts is not evidence.

**Statements page items.** Seller name, address, phone number, person in charge, price, payment
method, payment time, delivery time, returns and cancellation. Each item is a keyword list. The
wording that the address and phone number are disclosed without delay on request (請求があれば遅滞なく
開示) satisfies address and phone. A page reached only through an address like `/legal` counts
only when its text mentions 特定商取引 or 特商法.

## What it cannot see

- **Tags that load after consent, login, a click, or on pages it did not visit.** It answers no
  consent banner and has no account. A tag that fires only at checkout is not seen.
- **Tags served from your own domain** (a first-party proxy, server-side tagging, Vercel
  Analytics under `/_vercel/`), and anything your server sends to another company.
- **Notices in images, PDFs, or behind a click** (accordions that add text only when opened).
  These give `notice-unreadable` or a false `external-undisclosed`.
- **Meaning.** It finds names, not whether the notice also states what is sent and why. A name
  mentioned in an unrelated sentence counts as named. A service described without its name
  ("アクセス解析ツール") counts as not named.
- **Links without the listed wording**, for example a policy linked only as "Legal" or reached
  through a menu that renders on click. In the run on 40 Japanese services one site's policy link
  was not found for this reason.
- **Sites that refuse a headless browser.** Two of the 40 answered 403 and are reported as
  `page-error`, which says nothing about the site.
- **Whether a statements page is required.** Prices in a news article make a site look paid; a
  service that takes payment only inside an app does not. Use `--paid yes` or `--paid no`.
- **Vendors not in the table.** They appear as `external-unknown` with their hosts, without a name.
  `verified: true` in the table means the host resolved in DNS on 2026-11-19, not that it was
  checked against the vendor's documentation.

## Settings in `.agent-sense.toml`

```toml
[launch]
url = "https://example.jp/"
pages = 8
paid = "auto"
policy = "https://example.jp/privacy"
ignore = ["Google Fonts", "example-cdn.net"]   # vendor names or unknown sites to leave out
```

## Evidence

- Gate tests: `services/launch/tests` (pure functions over recorded sites, no browser).
- Known-answer eval: `evals/launch_eval.py`, 10 fixture sites in a real browser with vendor hosts
  answered locally. Threshold 10 of 10.
- Field run: `evals/launch_field.py` on real sites; see the numbers in the repository results.
  `evals/launch_jp_sites.txt` lists the 40 Japanese commercial services of the 2026-11-20 run.

# page: what the rendered page does, as text

A coding agent edits files and cannot see the result in a browser. `page` opens each URL in
Chromium and writes down what a person would notice: script errors, broken requests, layouts
that break at a phone width, buttons that do nothing, leftover placeholders, and text inputs
that send a message while a Japanese, Chinese or Korean conversion is still open. Every finding
names the element with a selector and says what to change.

No model and no screenshots are involved. The same page gives the same findings.

## Options

| option | default | meaning |
|---|---|---|
| `urls` | `urls` in the `[page]` config table | page URLs, or paths to HTML files (relative to the project root) |
| `--widths` | `360,768,1280` | viewport widths; the page is loaded once per width |
| `--click` | `auto` | `auto` clicks controls only on `localhost`, `127.0.0.1` and `file:` pages; `on` and `off` force it |
| `--ime` | `auto` | same switch for the IME check |
| `--max-controls` | `25` | most controls clicked per page |
| `--channel` | bundled Chromium | `chrome` or `msedge` to drive an installed browser |
| `--timeout` | `30000` | page load timeout in ms |
| `--wait` | `0` | extra ms after load, for client-side rendering |

Clicking and typing change the application's state, so by default they only run on pages served
from your own machine. Config keys in `.agent-sense.toml` use the same names (`max-controls` or
`max_controls`); the command line wins.

## Rules

| rule | severity | decided by |
|---|---|---|
| `page-error` | fail | the page did not load |
| `console-error` | fail for an uncaught exception, warn for `console.error` | browser console during load; exceptions thrown by a click handler are reported too |
| `request-failed` | fail for the page's own origin, warn for other origins | HTTP status 400 or above, or a network failure |
| `horizontal-overflow` | fail | the document is wider than the viewport |
| `text-clipped` | warn; info when truncation is declared | text box with hidden overflow whose content is larger than the box |
| `overlap` | warn | another element sits on the centre of a control |
| `dead-control` | fail | a click changes nothing observable |
| `placeholder-link` | warn; info for unverified `#` links | `example.com`, `localhost` on a deployed page, placeholder image services, `href="#"` |
| `placeholder-text` | warn | Lorem ipsum, John Doe, Your Company, `TODO:` and similar in visible text |
| `default-title` | fail | empty title or a framework template title |
| `ime-enter-submit` | fail; warn for the Safari-only case and for a weak signal | Enter during or right after an IME composition triggers a submit |

At most 8 findings per rule and page are kept; the number dropped is in `stats.pages[].dropped`.
The same finding at several widths is reported once, with the widths in `detail.widths`.

### console-error and request-failed

Listeners record uncaught exceptions, `console.error` calls, responses with status 400 or above,
and requests that fail at the network level. Chromium's own "Failed to load resource" console
line is dropped because `request-failed` already covers it. Requests the page cancels itself
(`ERR_ABORTED`) are ignored.

### horizontal-overflow

Checked when `scrollWidth` of the document exceeds the viewport width. An element is named when
its right edge is past the viewport, it is not `position: fixed`, no ancestor clips it
(`overflow-x` other than `visible`), and it sticks out further than its parent. That last test
is what names a 700px table instead of every cell inside it. A carousel that scrolls inside its
own `overflow-x: auto` box is not reported. If nothing can be named, the finding still reports
the two widths.

### text-clipped

An element is examined when it holds text directly (or only inline children), has
`overflow: hidden` or `clip` on an axis, and its content is larger than its box by more than 4px
in width or more than half the font size in height. `text-overflow: ellipsis` and
`-webkit-line-clamp` mark truncation as intended, which is reported as info. Boxes of 2px or
less, classes such as `sr-only` and `visually-hidden`, and absolutely positioned elements with
`clip` or `clip-path` are skipped.

### overlap

Each link, button and form field (up to 150) is scrolled to the middle of the viewport and
`elementFromPoint` is asked what is at its centre. The control passes when the answer is the
control, a descendant, an ancestor, its label, or part of the same link or button. Findings are
grouped by the covering element, so one cookie banner gives one finding.

### dead-control

With clicking enabled, every visible `button`, `[role=button]`, `input[type=button]` and `<a>`
without a real `href` is clicked at the largest width. A control is dead when, within 450 ms,
none of these happened: DOM mutation, form submit, navigation or URL change, network request,
console output, dialog, new window, download, focus moving elsewhere, a form field changing
value, the title changing, scrolling, or a call to one of the wrapped APIs (clipboard, storage,
history, `window.open`, scrolling, media play and pause, fullscreen, share, beacon, dialog and
popover methods, WebSocket send, canvas drawing, `print`). After a click that changed something
the page is reloaded before the next control.

Never clicked: labels that look destructive (`DESTRUCTIVE` in `page_lib.py`: delete, pay, send,
log out, 削除, 購入, 送信 and so on), submit buttons inside a form, `aria-disabled` controls,
reset buttons, and controls wired by an HTML attribute (`popovertarget`, `commandfor`,
`download`, Bootstrap `data-bs-toggle`). Skipped controls are counted in the stats.

For `href="#"` the browser's own jump (adding `#` to the URL, scrolling to the top) does not
count as an effect.

### placeholder-link and placeholder-text

Links to `example.com`, `.org`, `.net` warn. Links to `localhost` warn on a page that is not
itself local. Images from placeholder services (`via.placeholder.com`, `placehold.co`,
`picsum.photos` and others) are reported under this rule with `detail.kind = "image"`.
`href="#"`, an empty `href` and `javascript:` links are judged by the click check: alive means
no finding, dead means `dead-control`. When they were not clicked they are info, because a
script may handle them. `#main` style links are real links.

Visible text is matched against a short list: Lorem ipsum, `John Doe` and `Jane Doe`,
`Your Company` and `Acme Inc`, `TODO:` and `FIXME`, and Japanese template text such as
`ダミーテキスト` and `ここにテキストが入ります`. Text inside `code`, `pre`, `kbd` and `samp` is
ignored. Japanese sample names such as 山田太郎 are not matched: they are normal in form
examples and would mostly be false alarms. Lower-case "todo" and "Todo:" are not matched, so a
to-do app is not flagged.

### default-title

Empty, or equal (ignoring case) to a template title: Create Next App, Vite + React, React App,
Vite App, Welcome to Nuxt, Document, Untitled and others in `DEFAULT_TITLES`.

### ime-enter-submit

For up to 3 visible text inputs, textareas and contenteditable elements, the check starts a
composition through the DevTools protocol (`Input.imeSetComposition`) and then sends the keys a
browser sends when the user confirms a conversion with Enter.

What Chromium delivers was measured first (macOS, Playwright 1.65.2 headless shell): after
`imeSetComposition`, a `rawKeyDown` with key `Enter` and virtual key code 229 arrives in the page
as `keydown` with `key: "Enter"`, `keyCode: 229`, `isComposing: true`, with no `keypress` and no
native form submission. After the text is committed, `keyup` arrives with `keyCode: 13` and
`isComposing: false`. This is the event order of Chrome on macOS with a real IME.

Three phases are tried and the first one that misbehaves is reported:

1. keydown during composition (fail): catches `if (e.key === 'Enter') send()`.
2. keyup after the commit (fail): catches handlers on `keyup`, which an `isComposing` test cannot protect.
3. Safari order (warn): commit first, then keydown with `keyCode: 229` and `isComposing: false`. This catches a guard that tests only `isComposing`.

A reaction is a form submit event, a navigation, a network request, or the field being emptied
(fail), or new DOM nodes outside the field (warn). A control window before the key press
measures what the page does on its own, so a suggestion list that is already updating or
fetching is not blamed on Enter. A plain `<form>` is not flagged: Chromium does not submit it
during composition.

## Known false positives

- `text-clipped`: a design that cuts text at a fixed height without an ellipsis is reported even when it is deliberate (a news teaser limited by `max-height`). The rule cannot tell intent without `text-overflow` or `line-clamp`.
- `dead-control`: a control whose only effect is outside the list above (a WebGL scene, audio through Web Audio, a timer longer than 450 ms, a write to IndexedDB or a cookie) looks dead. So does a control that correctly does nothing in the current state, such as the already selected tab, when it changes no attribute.
- `overlap`: a deliberate overlay over a control (a "coming soon" veil) is reported.
- `horizontal-overflow`: a page that scrolls sideways on purpose is reported.
- `placeholder-text`: a page that discusses placeholders ("we replaced the Lorem ipsum") is reported.
- `console-error`: errors logged by browser extensions do not occur here, but errors from third-party scripts do and are reported like the page's own.

## Known false negatives

- Only the state right after load is inspected: no login, no menus opened, no second step of a flow. Pages behind authentication need a URL that is reachable without it.
- `dead-control`: submit buttons inside forms and destructive-looking labels are never clicked, so a form whose submit does nothing is not found. UI libraries that mutate the DOM on every press (ripple spans and `data-pressed` are filtered, other effects are not) can make a dead button look alive.
- `text-clipped`: text that sticks out of its box with `overflow: visible`, and text clipped by an ancestor that holds block children, are not reported.
- `overlap`: only the centre point of a control is tested, and only up to 150 controls.
- `horizontal-overflow` inside `body { overflow-x: hidden }` is invisible to this rule, because the document does not scroll.
- `ime-enter-submit`: the simulation reproduces Chromium's event sequence, not a real IME. The Safari phase replays Safari's event order inside Chromium; it has not been checked against Safari. Chrome on Windows reports `key: "Process"` during composition, where `e.key === 'Enter'` handlers do not fire; the finding describes macOS behaviour. Handlers that submit on `keypress` or on `beforeinput` with `insertLineBreak` are not exercised.
- `default-title`: the favicon is not checked. The entry "v0 App" is from memory and has not been confirmed against a fresh v0 project.
- Iframes and shadow DOM content are not inspected.

## Evidence

- Gate tests: `services/page/tests/test_page_lib.py`, pure analysis, no browser.
- Browser eval: `evals/page_eval.py` runs 18 known-answer cases from `evals/page_fixtures/` over a local HTTP server. Each rule has at least one page that must be flagged and one that must stay silent. The traps on the silent page: a carousel scrolling inside its box, intended ellipsis, a toggle that only flips `aria-pressed`, a button wired by event delegation, copy to clipboard, scroll to top while already at the top, a skip link, and an IME-guarded chat input.
- `evals/page_eval.py --real URL...` runs read-only against public pages to look at noise.

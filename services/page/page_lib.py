"""Turn what a coding agent cannot see on a rendered page into text.

Opens each URL in Chromium at several viewport widths and reports, with a selector for every
finding: script errors, failed requests, sideways scrolling, clipped text, covered controls,
controls that do nothing when clicked, placeholder links and text, a default framework title,
and inputs that submit while an IME composition is still open.

The module has two halves. The browser half (`record`) drives Playwright and returns a plain
dict, the recording. The analysis half (`analyse`) is pure: recording in, findings out, so the
gate tests need no browser. Playwright is imported only inside the functions that drive it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

NAME = "page"
SUMMARY = "open pages in a browser and report script errors, layout breaks, dead controls and IME submit bugs"
RULES = {
    "page-error": "fail",
    "console-error": "fail",
    "request-failed": "fail",
    "horizontal-overflow": "fail",
    "text-clipped": "warn",
    "overlap": "warn",
    "dead-control": "fail",
    "placeholder-link": "warn",
    "placeholder-text": "warn",
    "default-title": "fail",
    "ime-enter-submit": "fail",
}

DEFAULT_WIDTHS = (360, 768, 1280)
DEFAULT_MAX_CONTROLS = 25
MAX_INPUTS = 3            # text fields tried for the IME check, per page
MAX_PER_RULE = 8          # findings kept per rule and page; the rest are counted in stats
VIEW_HEIGHT = 900
CLICK_WINDOW_MS = 450     # how long a click may take to show an effect
IME_SETTLE_MS = 700       # lets debounced input handlers finish before the control window
IME_WINDOW_MS = 350
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0")
IME_TEXT = "にほんご"

# Labels of controls that are never clicked: the click could delete, pay, send or sign out.
DESTRUCTIVE = (
    "delete", "remove", "destroy", "erase", "reset", "clear all", "pay", "purchase", "buy", "checkout", "order",
    "subscribe", "unsubscribe", "logout", "log out", "sign out", "signout", "send", "submit", "publish", "deploy",
    "confirm", "cancel plan", "deactivate", "revoke", "transfer",
    "削除", "消去", "購入", "支払", "決済", "注文", "送信", "退会", "解約", "ログアウト", "サインアウト", "公開", "確定", "リセット",
)
DEFAULT_TITLES = (
    "create next app", "vite + react", "vite + react + ts", "vite + vue", "vite + vue + ts", "vite + svelte",
    "vite + svelte + ts", "vite + preact", "vite + lit", "vite + solid", "vite app", "react app", "welcome to nuxt",
    "welcome to nuxt!", "nuxt app", "welcome to sveltekit", "new remix app", "remix app", "create t3 app",
    "welcome to astro", "astro", "document", "untitled", "untitled document", "my app", "v0 app", "expo app",
    "angular", "ionic app", "title", "page title", "home page", "index",
)
PLACEHOLDER_IMAGE_HOSTS = ("via.placeholder.com", "placehold.co", "placehold.it", "placekitten.com", "picsum.photos",
                           "dummyimage.com", "placeholder.com", "loremflickr.com", "fakeimg.pl")
EXAMPLE_HOSTS = ("example.com", "example.org", "example.net")
# (kind, pattern). Kept narrow on purpose: every pattern here should be wrong on a published page.
PLACEHOLDER_TEXT = (
    ("lorem ipsum", re.compile(r"lorem ipsum|dolor sit amet|consectetur adipisc", re.I)),
    ("sample person", re.compile(r"\b(?:John|Jane) (?:Doe|Smith)\b")),
    ("sample company", re.compile(r"\bYour (?:Company|Business|Brand|Name|Logo|Product)(?: Name)?\b"
                                  r"|\bAcme (?:Inc|Corp|Co|Corporation|Company|Ltd)\b|\bCompany Name\b")),
    ("todo marker", re.compile(r"\bTODO\s*:|\bFIXME\b|\bXXX\s*:|\bTBD\b")),
    ("sample text", re.compile(r"ダミーテキスト|サンプルテキスト|ここに(?:テキスト|文章|説明|本文)(?:が|を)?入|テキストテキスト"
                               r"|\bPlaceholder text\b|\bYour text here\b|\bInsert text here\b", re.I)),
)
# Attribute changes a pressed or focused control gets from a UI library whether or not it does anything.
_COSMETIC = ("data-focus", "data-focused", "data-focus-visible", "data-focus-visible-added", "data-pressed",
             "data-hover", "data-hovered", "data-active", "data-agent-sense-control", "data-agent-sense-input")

INIT = r"""
(() => {
  if (window.__agentSenseCalls) return;
  const calls = window.__agentSenseCalls = [];
  const wrap = (obj, name, label) => {
    try {
      const orig = obj && obj[name];
      if (typeof orig !== 'function') return;
      obj[name] = function (...args) { calls.push(label); return orig.apply(this, args); };
    } catch (e) {}
  };
  wrap(navigator.clipboard, 'writeText', 'clipboard'); wrap(navigator.clipboard, 'write', 'clipboard');
  wrap(Document.prototype, 'execCommand', 'execCommand');
  wrap(Storage.prototype, 'setItem', 'storage'); wrap(Storage.prototype, 'removeItem', 'storage');
  wrap(Storage.prototype, 'clear', 'storage');
  wrap(window, 'open', 'window.open'); wrap(window, 'print', 'print');
  wrap(History.prototype, 'pushState', 'history'); wrap(History.prototype, 'replaceState', 'history');
  wrap(History.prototype, 'back', 'history'); wrap(History.prototype, 'go', 'history');
  wrap(window, 'scrollTo', 'scroll'); wrap(window, 'scrollBy', 'scroll'); wrap(window, 'scroll', 'scroll');
  wrap(Element.prototype, 'scrollIntoView', 'scroll'); wrap(Element.prototype, 'scrollTo', 'scroll');
  wrap(Element.prototype, 'requestFullscreen', 'fullscreen'); wrap(Element.prototype, 'animate', 'animate');
  wrap(HTMLMediaElement.prototype, 'play', 'media'); wrap(HTMLMediaElement.prototype, 'pause', 'media');
  wrap(navigator, 'share', 'share'); wrap(navigator, 'sendBeacon', 'beacon');
  wrap(HTMLDialogElement.prototype, 'showModal', 'dialog'); wrap(HTMLDialogElement.prototype, 'show', 'dialog');
  wrap(HTMLDialogElement.prototype, 'close', 'dialog');
  wrap(HTMLElement.prototype, 'showPopover', 'popover'); wrap(HTMLElement.prototype, 'togglePopover', 'popover');
  wrap(HTMLElement.prototype, 'hidePopover', 'popover');
  wrap(window.WebSocket && WebSocket.prototype, 'send', 'websocket');
  wrap(window.Notification, 'requestPermission', 'notification');
  wrap(window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype, 'fillRect', 'canvas');
  wrap(window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype, 'drawImage', 'canvas');
  wrap(window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype, 'clearRect', 'canvas');
})()
"""

# Shared helpers, prepended to every script below. ASCII only.
_HELPERS = r"""
  const snippet = (el, n) => ((el.innerText || el.textContent || el.value || el.getAttribute('aria-label')
    || el.getAttribute('title') || '') + '').trim().replace(/\s+/g, ' ').slice(0, n || 60);
  const seg = el => {
    let s = el.tagName.toLowerCase();
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id)) return s + '#' + el.id;
    const cls = typeof el.className === 'string'
      ? el.className.trim().split(/\s+/).filter(c => /^[A-Za-z_][\w-]*$/.test(c)).slice(0, 2) : [];
    if (cls.length) s += '.' + cls.join('.');
    const p = el.parentElement;
    if (p) {
      const same = [...p.children].filter(c => c.tagName === el.tagName);
      if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
    }
    return s;
  };
  const locator = el => {
    const parts = [];
    for (let cur = el; cur && cur.nodeType === 1 && cur !== document.documentElement; cur = cur.parentElement) {
      const s = seg(cur);
      parts.unshift(s);
      if (s.includes('#') || cur === document.body || parts.length >= 5) break;
    }
    return parts.join(' > ');
  };
  const dimmed = new WeakMap();
  const faded = el => {
    if (!el || el.nodeType !== 1) return false;
    if (dimmed.has(el)) return dimmed.get(el);
    const out = parseFloat(getComputedStyle(el).opacity) < 0.02 || faded(el.parentElement);
    dimmed.set(el, out);
    return out;
  };
  const shown = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden' && !faded(el);
"""

COLLECT = r"""
([maxElements, maxTexts, maxInteractive]) => {
""" + _HELPERS + r"""
  const root = document.documentElement, body = document.body;
  const out = {title: document.title, client_width: root.clientWidth, scroll_width: 0, overflow: [], clipped: [],
               covered: [], links: [], images: [], texts: []};
  if (!body) return out;
  const vw = root.clientWidth;
  const scroller = document.scrollingElement || root;
  out.scroll_width = scroller.scrollWidth;
  const all = [...body.querySelectorAll('*')].slice(0, maxElements);

  if (scroller.scrollWidth > vw + 1) {
    const clippedAbove = el => {
      for (let p = el.parentElement; p && p !== body && p !== root; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (s.overflowX !== 'visible' || s.position === 'fixed') return true;
      }
      return false;
    };
    const found = [];
    for (const el of all) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const right = r.right + window.scrollX;
      if (right <= vw + 1) continue;
      if (getComputedStyle(el).position === 'fixed') continue;
      const p = el.parentElement;
      if (p && p !== body && p.getBoundingClientRect().right + window.scrollX >= right - 1) continue;
      if (clippedAbove(el)) continue;
      found.push({locator: locator(el), right: Math.round(right), width: Math.round(r.width), text: snippet(el, 40)});
    }
    found.sort((a, b) => b.right - a.right);
    out.overflow = found.slice(0, 5);
  }

  const SKIP_CLIP = new Set(['INPUT', 'TEXTAREA', 'SELECT', 'SVG', 'svg', 'CANVAS', 'VIDEO', 'IFRAME', 'BODY', 'HTML']);
  for (const el of all) {
    if (out.clipped.length >= 40) break;
    if (SKIP_CLIP.has(el.tagName)) continue;
    const s = getComputedStyle(el);
    const hx = s.overflowX === 'hidden' || s.overflowX === 'clip';
    const hy = s.overflowY === 'hidden' || s.overflowY === 'clip';
    if (!hx && !hy) continue;
    if (el.clientWidth <= 2 || el.clientHeight <= 2) continue;
    if (typeof el.className === 'string' && /sr-only|visually-hidden|screen-reader|a11y-hidden|assistive/i.test(el.className)) continue;
    if (s.position === 'absolute' && (s.clip !== 'auto' || (s.clipPath && s.clipPath !== 'none'))) continue;
    const dx = el.scrollWidth - el.clientWidth, dy = el.scrollHeight - el.clientHeight;
    const size = parseFloat(s.fontSize) || 16;
    const cutX = hx && dx > 4, cutY = hy && dy > Math.max(4, size * 0.5);
    if (!cutX && !cutY) continue;
    let direct = false;
    for (const node of el.childNodes) if (node.nodeType === 3 && node.nodeValue.trim().length >= 2) { direct = true; break; }
    if (!direct) {
      const kids = [...el.children];
      const inline = kids.length > 0 && kids.every(k => getComputedStyle(k).display === 'inline' && !k.querySelector('img,svg,video'));
      if (!inline || !el.textContent.trim()) continue;
    }
    if (!shown(el)) continue;
    const clamp = s.webkitLineClamp && s.webkitLineClamp !== 'none';
    const intent = cutX && s.textOverflow === 'ellipsis' ? 'ellipsis' : (clamp ? 'clamp' : null);
    out.clipped.push({locator: locator(el), text: snippet(el, 60), axis: cutX ? 'x' : 'y',
                      hidden_px: Math.round(cutX ? dx : dy), intent});
  }

  const controls = [...body.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], summary')];
  let seen = 0;
  for (const el of controls) {
    if (seen >= maxInteractive) break;
    if (el.disabled || !shown(el)) continue;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (el.tagName === 'INPUT' && ['checkbox', 'radio', 'file', 'range', 'color'].includes(type)) continue;
    seen++;
    el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx >= window.innerWidth || cy >= window.innerHeight) continue;
    const top = document.elementFromPoint(cx, cy);
    if (!top || top === el || el.contains(top) || top.contains(el)) continue;
    const label = top.closest('label');
    if (label && (label.control === el || label.contains(el))) continue;
    const wrapper = el.closest('a, button, label');
    if (wrapper && wrapper.contains(top)) continue;
    let by = top, position = getComputedStyle(top).position;
    for (let cur = top; cur && cur !== body; cur = cur.parentElement) {
      const pos = getComputedStyle(cur).position;
      if (pos === 'fixed' || pos === 'sticky') { by = cur; position = pos; break; }
    }
    out.covered.push({locator: locator(el), label: snippet(el, 40), by: locator(by), by_position: position});
  }
  window.scrollTo(0, 0);

  for (const el of body.querySelectorAll('a')) {
    if (out.links.length >= 400) break;
    if (!shown(el)) continue;
    const raw = el.getAttribute('href');
    let host = '', protocol = '';
    try { const u = new URL(el.href); host = u.hostname; protocol = u.protocol; } catch (e) {}
    out.links.push({locator: locator(el), href: raw, host, protocol, text: snippet(el, 40),
                    role: el.getAttribute('role') || ''});
  }
  for (const el of body.querySelectorAll('img[src]')) {
    if (out.images.length >= 200) break;
    let host = '';
    try { host = new URL(el.src).hostname; } catch (e) {}
    if (host) out.images.push({locator: locator(el), host});
  }

  const SKIP_TEXT = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TEXTAREA', 'OPTION', 'TITLE']);
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode()) && out.texts.length < maxTexts) {
    const text = node.nodeValue.trim(), el = node.parentElement;
    if (text.length < 3 || !el || SKIP_TEXT.has(el.tagName)) continue;
    if (el.closest('code, pre, kbd, samp, var')) continue;
    if (!shown(el)) continue;
    out.texts.push({locator: locator(el), text: text.replace(/\s+/g, ' ').slice(0, 200)});
  }
  return out;
}
"""

TAG_CONTROLS = r"""
() => {
""" + _HELPERS + r"""
  const out = [];
  const dead = href => href === null || /^\s*(#|#!|#0)?\s*$/.test(href) || /^\s*javascript:/i.test(href);
  let next = 0;
  for (const el of document.querySelectorAll('button, [role=button], input[type=button], input[type=submit], a')) {
    if (el.tagName === 'A' && !dead(el.getAttribute('href'))) continue;
    if (el.disabled || !shown(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const id = String(next++);
    el.setAttribute('data-agent-sense-control', id);
    const type = (el.getAttribute('type') || (el.tagName === 'BUTTON' ? 'submit' : '')).toLowerCase();
    const form = el.form || el.closest('form');
    out.push({
      id, locator: locator(el), label: snippet(el, 60), tag: el.tagName.toLowerCase(), type,
      hash_link: el.tagName === 'A' && /^\s*(#|#!|#0)\s*$/.test(el.getAttribute('href') || ''),
      aria_disabled: el.getAttribute('aria-disabled') === 'true',
      in_form: !!form, form_invalid: !!form && !form.checkValidity(),
      native: el.hasAttribute('popovertarget') || el.hasAttribute('commandfor') || el.hasAttribute('command')
        || el.hasAttribute('download') || !!el.closest('summary') || el.hasAttribute('data-bs-toggle')
        || el.hasAttribute('data-toggle'),
    });
  }
  return out;
}
"""

ARM = r"""
([id, cosmetic]) => {
  const el = document.querySelector('[data-agent-sense-control="' + id + '"]');
  if (!el) return false;
  el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
  const values = () => [...document.querySelectorAll('input, textarea, select')]
    .map(f => f.value + '|' + (f.checked ? 1 : 0)).join('|~|');
  const st = window.__agentSenseState = {mutations: 0, submits: 0, el};
  st.observer = new MutationObserver(list => {
    for (const m of list) {
      if (m.type === 'attributes') {
        const name = m.attributeName || '';
        if (name.startsWith('data-agent-sense')) continue;
        if (m.target === el && cosmetic.includes(name)) continue;
      }
      if (m.type === 'childList' && el.contains(m.target)) {
        const nodes = [...m.addedNodes, ...m.removedNodes];
        if (nodes.length && nodes.every(n => n.nodeType === 1 && /ripple/i.test(typeof n.className === 'string' ? n.className : ''))) continue;
      }
      st.mutations++;
    }
  });
  st.observer.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  document.addEventListener('submit', () => { st.submits++; }, true);
  st.values = values; st.before = values(); st.active = document.activeElement; st.title = document.title;
  st.scroll = [window.scrollX, window.scrollY]; st.url = location.href;
  if (window.__agentSenseCalls) window.__agentSenseCalls.length = 0;
  return true;
}
"""

PROBE = r"""
() => {
  const st = window.__agentSenseState;
  if (!st) return null;
  const active = document.activeElement;
  return {
    mutations: st.mutations, submits: st.submits,
    focus_moved: active !== st.active && active !== st.el && active !== document.body && !!active && !st.el.contains(active),
    values_changed: st.values() !== st.before, title_changed: document.title !== st.title,
    scrolled: Math.abs(window.scrollX - st.scroll[0]) + Math.abs(window.scrollY - st.scroll[1]) > 2,
    url_changed: location.href.replace(/#$/, '') !== st.url.replace(/#$/, ''), calls: [...new Set(window.__agentSenseCalls || [])],
  };
}
"""

TAG_INPUTS = r"""
(max) => {
""" + _HELPERS + r"""
  const out = [];
  const ok = new Set(['', 'text', 'search']);
  for (const el of document.querySelectorAll('input, textarea, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]')) {
    if (out.length >= max) break;
    if (el.tagName === 'INPUT' && !ok.has((el.getAttribute('type') || '').toLowerCase())) continue;
    if (el.disabled || el.readOnly || !shown(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const id = String(out.length);
    el.setAttribute('data-agent-sense-input', id);
    out.push({id, locator: locator(el), tag: el.tagName.toLowerCase(), in_form: !!el.closest('form'),
              label: (el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('name') || '').slice(0, 40)});
  }
  return out;
}
"""

IME_ARM = r"""
(id) => {
  const el = document.querySelector('[data-agent-sense-input="' + id + '"]');
  if (!el) return false;
  const st = window.__agentSenseIme = {added: 0, submits: 0, el, url: location.href};
  st.length = () => (('value' in el ? el.value : el.textContent) || '').length;
  st.observer = new MutationObserver(list => {
    for (const m of list) {
      if (el.contains(m.target)) continue;
      for (const n of m.addedNodes) if (n.nodeType === 1 || (n.nodeType === 3 && n.nodeValue.trim())) st.added++;
    }
  });
  st.observer.observe(document, {subtree: true, childList: true});
  document.addEventListener('submit', () => { st.submits++; }, true);
  return true;
}
"""

IME_PROBE = r"""
() => {
  const st = window.__agentSenseIme;
  if (!st) return null;
  const out = {added: st.added, submits: st.submits, length: st.length(), url_changed: location.href !== st.url};
  st.added = 0; st.submits = 0;
  return out;
}
"""


# ---------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------

def is_local(url: str) -> bool:
    """True for pages on this machine, where clicking and typing cannot hurt anyone else."""
    parts = urlsplit(url)
    if parts.scheme == "file":
        return True
    host = (parts.hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def origin(url: str) -> str:
    parts = urlsplit(url)
    return "file://" if parts.scheme == "file" else f"{parts.scheme}://{parts.netloc}".lower()


def parse_widths(value) -> tuple[int, ...]:
    if value is None or value == "":
        return DEFAULT_WIDTHS
    items = value.split(",") if isinstance(value, str) else list(value)
    try:
        widths = tuple(dict.fromkeys(int(str(item).strip()) for item in items if str(item).strip()))
    except ValueError as error:
        raise RuntimeError(f"--widths needs numbers like 360,768,1280: {error}") from None
    if not widths or any(width < 200 or width > 4000 for width in widths):
        raise RuntimeError("--widths needs values between 200 and 4000")
    return widths


def enabled(mode: str, url: str) -> bool:
    """Resolve auto|on|off for a page: auto touches only pages on this machine."""
    return mode == "on" or (mode == "auto" and is_local(url))


def to_url(item: str, root: Path) -> str:
    """A URL as given, or a file URL for a path (relative paths are taken from the project root)."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", item) and not re.match(r"^[A-Za-z]:[\\/]", item):
        return item
    path = Path(item)
    return (path if path.is_absolute() else Path(root) / path).resolve().as_uri()


def default_title(title: str | None) -> str | None:
    """Why a title looks untouched, or None when it is fine."""
    cleaned = (title or "").strip()
    if not cleaned:
        return "the page has no title"
    if cleaned.lower() in DEFAULT_TITLES:
        return f'the title is still the template default "{cleaned}"'
    return None


def link_kind(link: dict, page_local: bool) -> str | None:
    """Which kind of placeholder a link is, or None for a real link."""
    href, host = link.get("href"), (link.get("host") or "").lower()
    if href is None:
        return None  # an <a> without href is not a link; the click check covers it
    stripped = href.strip()
    if stripped in ("", "#", "#!", "#0"):
        return "empty" if not stripped else "hash"
    if stripped.lower().startswith("javascript:"):
        return "javascript"
    if host in EXAMPLE_HOSTS or any(host.endswith("." + name) for name in EXAMPLE_HOSTS):
        return "example"
    if not page_local and link.get("protocol") in ("http:", "https:") and (host in LOCAL_HOSTS or host.endswith(".localhost")):
        return "localhost"
    return None


def text_kind(text: str) -> tuple[str, str] | None:
    """(kind, matched text) when visible text is a leftover placeholder."""
    for kind, pattern in PLACEHOLDER_TEXT:
        match = pattern.search(text)
        if match:
            return kind, match.group(0)
    return None


def destructive(label: str) -> bool:
    lowered = (label or "").lower()
    return any(re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered) if word.isascii() else word in lowered
               for word in DESTRUCTIVE)


def control_skip_reason(control: dict) -> str | None:
    """Why a control is not clicked, or None when it is safe and meaningful to click."""
    if control.get("aria_disabled"):
        return "aria-disabled"
    if control.get("native"):
        return "wired by an HTML attribute"
    if control.get("type") == "reset":
        return "reset button"
    if destructive(control.get("label", "")):
        return "label looks destructive"
    if control.get("in_form") and control.get("type") == "submit" and control.get("form_invalid"):
        return "submit button of a form with required fields still empty"
    if control.get("in_form") and control.get("type") == "submit":
        return "submit button of a form"
    return None


def click_effects(probe: dict | None, events: dict, hash_link: bool = False) -> list[str]:
    """Everything a click visibly did. `probe` is None when the page navigated away.

    For a link with href="#" the browser itself adds "#" to the URL and jumps to the top; that is
    the placeholder at work, not an effect, so only a real URL change counts for such a link.
    """
    effects = []
    if probe is None or (probe or {}).get("url_changed") or (events.get("navigations") and not hash_link):
        effects.append("navigation")
    probe = dict(probe or {})
    if hash_link:
        probe["scrolled"] = False
    for key, label in (("mutations", "dom change"), ("submits", "form submit"), ("focus_moved", "focus moved"),
                       ("values_changed", "field value changed"), ("title_changed", "title changed"),
                       ("scrolled", "scrolled")):
        if probe.get(key):
            effects.append(label)
    effects += [f"called {name}" for name in probe.get("calls", [])]
    for key, label in (("requests", "network request"), ("console", "console output"), ("exceptions", "exception"),
                       ("dialogs", "dialog"), ("popups", "new window"), ("downloads", "download")):
        if events.get(key):
            effects.append(label)
    return effects


def ime_signals(control: dict | None, phase: dict | None, requests: int, control_requests: int,
                length_before: int) -> list[str]:
    """Signs that the page treated Enter as a submit during one phase of a composition."""
    if phase is None:
        return ["navigation"]
    signals = []
    if phase.get("url_changed"):
        signals.append("navigation")
    if phase.get("submits"):
        signals.append("form submit")
    if requests and not control_requests:
        signals.append("network request")
    if length_before > 0 and phase.get("length") == 0:
        signals.append("field cleared")
    if phase.get("added") and not (control or {}).get("added"):
        signals.append("dom change")
    return signals


STRONG_IME = {"navigation", "form submit", "network request", "field cleared"}


# ---------------------------------------------------------------------------------------------
# Analysis: recording -> findings
# ---------------------------------------------------------------------------------------------

def _widths_text(widths: list[int], all_widths: list[int]) -> str:
    if len(widths) == len(all_widths) and len(all_widths) > 1:
        return "at every width checked"
    return "at " + ", ".join(f"{width}px" for width in widths)


def _finding(rule: str, message: str, fix: str, url: str, severity: str | None = None, **detail) -> dict:
    return {"rule": rule, "severity": severity or RULES[rule], "message": message, "fix": fix,
            "detail": {"url": url, **{key: value for key, value in detail.items() if value is not None}}}


def _merge(views: list[dict], key: str, identity) -> list[tuple[dict, list[int]]]:
    """Items of one kind across all widths, deduplicated, each with the widths it showed up at."""
    merged: dict = {}
    for view in views:
        for item in view.get(key, []):
            first, widths = merged.setdefault(identity(item), (item, []))
            if view["width"] not in widths:
                widths.append(view["width"])
    return list(merged.values())


def analyse(recording: dict) -> tuple[list[dict], dict]:
    """Findings for one recorded page, plus counts of what was checked and what was left out."""
    url = recording["url"]
    stats = {"clicked": 0, "skipped_controls": 0, "inputs_checked": 0, "dropped": {}}
    if recording.get("error"):
        return [_finding("page-error", f"could not load {url}: {recording['error']}",
                         "check the URL and that the dev server is running", url)], stats
    views = recording.get("views", [])
    all_widths = [view["width"] for view in views]
    local = recording.get("local", is_local(url))
    page_origin = origin(recording.get("final_url") or url)
    findings: list[dict] = []

    reason = default_title(recording.get("title"))
    if reason:
        findings.append(_finding("default-title", reason,
                                 "set a real <title> (in Next.js: metadata.title; in Vite: index.html)", url,
                                 title=recording.get("title") or ""))

    for item, widths in _merge(views, "console", lambda item: (item["kind"], item["text"])):
        uncaught = item["kind"] == "exception"
        findings.append(_finding(
            "console-error", f"{'uncaught exception' if uncaught else 'console.error'} while loading: {item['text']}",
            "open the stack trace in the browser console and fix the script error" if uncaught
            else "fix the cause or stop logging it as an error",
            url, severity="fail" if uncaught else "warn", kind=item["kind"], widths=widths, where=item.get("where")))

    for item, widths in _merge(views, "requests", lambda item: (item["url"], item.get("status"), item.get("failure"))):
        same = origin(item["url"]) == page_origin
        what = f"HTTP {item['status']}" if item.get("status") else (item.get("failure") or "failed")
        findings.append(_finding(
            "request-failed", f"{item.get('method', 'GET')} {item['url']} -> {what}",
            "fix the path or the route that should serve it" if same else "check the third-party URL, or remove the request",
            url, severity="fail" if same else "warn", request_url=item["url"], status=item.get("status"),
            failure=item.get("failure"), same_origin=same, widths=widths))

    for item, widths in _merge(views, "overflow", lambda item: item["locator"]):
        view = next(view for view in views if view["width"] == widths[0])
        findings.append(_finding(
            "horizontal-overflow",
            f"the page scrolls sideways {_widths_text(widths, all_widths)}: <{item['locator']}> reaches {item['right']}px "
            f"in a {view['client_width']}px viewport",
            "let it shrink or wrap (max-width: 100%, min-width: 0 on flex children, flex-wrap, overflow-wrap: anywhere), "
            "or give its container overflow-x: auto",
            url, locator=item["locator"], widths=widths, right=item["right"], element_width=item["width"],
            text=item.get("text")))
    for view in views:  # the document scrolls but no single element could be named
        if view.get("scroll_width", 0) > view.get("client_width", 0) + 1 and not view.get("overflow"):
            findings.append(_finding(
                "horizontal-overflow",
                f"the page scrolls sideways at {view['width']}px: content is {view['scroll_width']}px wide in a "
                f"{view['client_width']}px viewport (no single element found)",
                "look for negative margins, 100vw widths or transforms that push content past the right edge",
                url, widths=[view["width"]]))

    for item, widths in _merge(views, "clipped", lambda item: item["locator"]):
        axis = "width" if item["axis"] == "x" else "height"
        if item.get("intent"):
            findings.append(_finding(
                "text-clipped",
                f"text truncated on purpose ({item['intent']}) {_widths_text(widths, all_widths)}: <{item['locator']}> "
                f"\"{item['text']}\"",
                "nothing to do if the full text is available elsewhere (title attribute, detail view)",
                url, severity="info", locator=item["locator"], widths=widths, intent=item["intent"], text=item["text"]))
            continue
        findings.append(_finding(
            "text-clipped",
            f"text cut off {_widths_text(widths, all_widths)}: <{item['locator']}> hides {item['hidden_px']}px of its "
            f"{axis} (\"{item['text']}\")",
            "let the box grow (remove the fixed height or width), allow wrapping, or add text-overflow: ellipsis if "
            "truncation is intended",
            url, locator=item["locator"], widths=widths, axis=item["axis"], hidden_px=item["hidden_px"], text=item["text"]))

    by_cover: dict = {}
    for item, widths in _merge(views, "covered", lambda item: (item["locator"], item["by"])):
        entry = by_cover.setdefault(item["by"], {"controls": [], "widths": [], "position": item.get("by_position")})
        entry["controls"].append(item)
        entry["widths"] = sorted(set(entry["widths"]) | set(widths))
    for by, entry in by_cover.items():
        first = entry["controls"][0]
        count = len(entry["controls"])
        subject = (f"<{first['locator']}> (\"{first['label']}\")" if count == 1
                   else f"{count} controls, first <{first['locator']}> (\"{first['label']}\")")
        findings.append(_finding(
            "overlap", f"{subject} covered by <{by}> {_widths_text(entry['widths'], all_widths)}: a click on the centre "
                       f"lands on the covering element",
            "move or resize the covering element, fix its z-index, or give it pointer-events: none if it is decoration",
            url, locator=first["locator"], covered_by=by, widths=entry["widths"], controls=[c["locator"] for c in entry["controls"]],
            covering_position=entry["position"]))

    alive_links = {control["locator"] for control in recording.get("controls", []) if control.get("effects")}
    clicked = bool(recording.get("clicked"))
    for item, widths in _merge(views, "links", lambda item: (item["locator"], item.get("href"))):
        kind = link_kind(item, local)
        if not kind:
            continue
        if kind in ("hash", "empty", "javascript") and (item["locator"] in alive_links or item.get("role") == "button"):
            continue  # a script-driven link that does something when clicked
        if kind in ("hash", "empty", "javascript") and clicked and any(
                control["locator"] == item["locator"] and not control.get("skipped") for control in recording["controls"]):
            continue  # clicked and found dead: reported as dead-control instead
        if kind in ("hash", "empty", "javascript"):
            # Not clicked (remote page, or a control that was skipped): a script may still drive it.
            what = {"hash": 'href="#"', "empty": "an empty href", "javascript": "a javascript: href"}[kind]
            findings.append(_finding(
                "placeholder-link", f"link <{item['locator']}> (\"{item['text']}\") has {what}; it was not clicked, so a "
                                    f"script may still handle it",
                "point it at the real destination, or use a <button> if it runs a script", url, severity="info",
                locator=item["locator"], href=item.get("href"), kind=kind, widths=widths))
            continue
        what = (f"points to {item['host']}" if kind == "example"
                else f"points to {item['host']}, which only exists on your machine")
        findings.append(_finding(
            "placeholder-link", f"link <{item['locator']}> (\"{item['text']}\") {what}",
            "point it at the real destination", url, locator=item["locator"], href=item.get("href"), kind=kind, widths=widths))
    for item, widths in _merge(views, "images", lambda item: (item["locator"], item["host"])):
        if item["host"].lower() in PLACEHOLDER_IMAGE_HOSTS:
            findings.append(_finding(
                "placeholder-link", f"image <{item['locator']}> is loaded from the placeholder service {item['host']}",
                "replace it with the real image", url, locator=item["locator"], kind="image", host=item["host"], widths=widths))

    for item, widths in _merge(views, "texts", lambda item: (item["locator"], item["text"])):
        found = text_kind(item["text"])
        if found:
            findings.append(_finding(
                "placeholder-text", f"{found[0]} left in visible text at <{item['locator']}>: \"{item['text'][:80]}\"",
                "replace it with real content or remove the element", url, locator=item["locator"], kind=found[0],
                matched=found[1], widths=widths))

    for control in recording.get("controls", []):
        if control.get("skipped"):
            stats["skipped_controls"] += 1
            continue
        stats["clicked"] += 1
        effects = control.get("effects", [])
        if "exception" in effects:
            findings.append(_finding(
                "console-error", f"uncaught exception after clicking <{control['locator']}> (\"{control['label']}\"): "
                                 f"{control.get('error') or 'see the browser console'}",
                "fix the click handler", url, locator=control["locator"], kind="exception", after_click=True))
        if not effects:
            findings.append(_finding(
                "dead-control", f"<{control['locator']}> (\"{control['label']}\") does nothing when clicked: no DOM change, "
                                f"request, navigation, dialog, focus move or console output within {CLICK_WINDOW_MS} ms",
                "wire the handler (in a Next.js server component the file needs 'use client'), or remove the control",
                url, locator=control["locator"], label=control["label"], tag=control.get("tag"), width=recording.get("click_width")))

    for field in recording.get("ime", []):
        if field.get("skipped"):
            continue
        stats["inputs_checked"] += 1
        for phase, when, who in (("keydown", "on the Enter keydown that confirms an IME conversion", "Chrome and Firefox on macOS"),
                                 ("keyup", "on the Enter keyup right after an IME conversion is confirmed", "every browser"),
                                 ("safari", "on the Enter keydown that Safari delivers after compositionend", "Safari (keydown arrives with isComposing false and keyCode 229)")):
            signals = field.get(phase) or []
            if not signals:
                continue
            strong = bool(STRONG_IME & set(signals))
            severity = "warn" if phase == "safari" or not strong else "fail"
            fix = {"keydown": "ignore the key while composing: if (event.isComposing || event.keyCode === 229) return;",
                   "keyup": "handle Enter on keydown, not keyup, and return early when event.isComposing || event.keyCode === 229",
                   "safari": "also check event.keyCode === 229, because Safari reports isComposing false here"}[phase]
            findings.append(_finding(
                "ime-enter-submit",
                f"<{field['locator']}> reacts {when} ({', '.join(signals)}), so Japanese, Chinese and Korean text is sent "
                f"half-typed; affects {who}",
                fix, url, severity=severity, locator=field["locator"], phase=phase, signals=signals))
            break  # one finding per field: the earliest phase that misbehaves

    kept, counts = [], {}
    for finding in findings:
        counts[finding["rule"]] = counts.get(finding["rule"], 0) + 1
        if counts[finding["rule"]] <= MAX_PER_RULE:
            kept.append(finding)
    stats["dropped"] = {rule: count - MAX_PER_RULE for rule, count in counts.items() if count > MAX_PER_RULE}
    return kept, stats


# ---------------------------------------------------------------------------------------------
# Browser half
# ---------------------------------------------------------------------------------------------

class _Events:
    """Counts what a page does, so a click or key press can be judged by what followed it."""

    KEYS = ("requests", "console", "exceptions", "dialogs", "popups", "downloads", "navigations")

    def __init__(self, page):
        self.counts = dict.fromkeys(self.KEYS, 0)
        self.console, self.failed, self.last_error = [], [], ""
        self.armed = False  # main-frame navigations count only after the first load
        page.on("request", lambda request: self._bump("requests"))
        page.on("console", self._on_console)
        page.on("pageerror", self._on_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_failed)
        page.on("dialog", self._on_dialog)
        page.on("popup", self._on_popup)
        page.on("download", lambda download: self._bump("downloads"))
        page.on("framenavigated", lambda frame: self._bump("navigations") if self.armed and frame == page.main_frame else None)

    def _bump(self, key):
        self.counts[key] += 1

    def _on_console(self, message):
        self._bump("console")
        if message.type == "error" and not message.text.startswith("Failed to load resource"):
            self.console.append({"kind": "error", "text": message.text[:300]})

    def _on_error(self, error):
        self._bump("exceptions")
        self.last_error = str(error).splitlines()[0][:300]
        self.console.append({"kind": "exception", "text": self.last_error})

    def _on_response(self, response):
        if response.status >= 400:
            self.failed.append({"url": response.url[:300], "status": response.status, "method": response.request.method})

    def _on_failed(self, request):
        failure = request.failure or ""
        if "ERR_ABORTED" in failure:
            return  # cancelled by the page itself (navigation, replaced media)
        self.failed.append({"url": request.url[:300], "status": None, "failure": failure[:120], "method": request.method})

    def _on_dialog(self, dialog):
        self._bump("dialogs")
        import asyncio
        asyncio.ensure_future(dialog.dismiss())

    def _on_popup(self, popup):
        self._bump("popups")
        import asyncio
        asyncio.ensure_future(popup.close())

    def mark(self) -> dict:
        return dict(self.counts)

    def since(self, mark: dict) -> dict:
        return {key: self.counts[key] - mark[key] for key in self.KEYS}


async def _load(page, url: str, timeout: int, wait: int) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # pages that never go idle are inspected as they stand
    if wait:
        await page.wait_for_timeout(wait)


async def _open(context, url: str, width: int, options: dict):
    page = await context.new_page()
    await page.set_viewport_size({"width": width, "height": VIEW_HEIGHT})
    events = _Events(page)
    await _load(page, url, options["timeout"], options["wait"])
    events.armed = True
    return page, events


async def _click_controls(context, url: str, width: int, options: dict) -> list[dict]:
    page, events = await _open(context, url, width, options)
    results = []
    try:
        controls = await page.evaluate(TAG_CONTROLS)
        dirty = False
        for index, control in enumerate(controls):
            entry = {key: control[key] for key in ("locator", "label", "tag", "type")}
            reason = control_skip_reason(control)
            if not reason and sum(1 for item in results if not item.get("skipped")) >= options["max_controls"]:
                reason = "over --max-controls"
            if reason:
                results.append({**entry, "skipped": reason, "effects": []})
                continue
            if dirty:  # the last click changed the page: start again from a fresh load
                events.armed = False
                await _load(page, url, options["timeout"], options["wait"])
                events.armed = True
                again = await page.evaluate(TAG_CONTROLS)
                if index >= len(again) or again[index]["locator"] != control["locator"]:
                    results.append({**entry, "skipped": "the page did not come back the same after a reload", "effects": []})
                    continue
                dirty = False
            if not await page.evaluate(ARM, [control["id"], list(_COSMETIC)]):
                results.append({**entry, "skipped": "gone before it could be clicked", "effects": []})
                continue
            mark = events.mark()
            try:
                await page.locator(f'[data-agent-sense-control="{control["id"]}"]').click(timeout=1500, no_wait_after=True)
            except Exception:
                results.append({**entry, "skipped": "not clickable (covered, off-screen or moving)", "effects": []})
                continue
            await page.wait_for_timeout(CLICK_WINDOW_MS)
            try:
                probe = await page.evaluate(PROBE)
            except Exception:
                probe = None  # the page navigated away
            delta = events.since(mark)
            effects = click_effects(probe, delta, control.get("hash_link", False))
            results.append({**entry, "effects": effects, **({"error": events.last_error} if delta["exceptions"] else {})})
            dirty = bool(effects)
    finally:
        await page.close()
    return results


async def _ime_pass(context, url: str, width: int, options: dict, index: int, safari: bool) -> dict | None:
    """One composition on one field. Returns {"keydown": [...], "keyup": [...]} or {"safari": [...]}."""
    page, events = await _open(context, url, width, options)
    try:
        fields = await page.evaluate(TAG_INPUTS, MAX_INPUTS)
        if index >= len(fields):
            return None
        field = fields[index]
        selector = f'[data-agent-sense-input="{field["id"]}"]'
        session = await context.new_cdp_session(page)
        await page.locator(selector).focus(timeout=1500)
        await session.send("Input.imeSetComposition", {"text": IME_TEXT, "selectionStart": len(IME_TEXT),
                                                       "selectionEnd": len(IME_TEXT)})
        await page.wait_for_timeout(IME_SETTLE_MS)
        if not await page.evaluate(IME_ARM, field["id"]):
            return {**field, "skipped": "gone before the check"}

        async def window(action) -> tuple[dict | None, int]:
            mark = events.mark()
            await action()
            await page.wait_for_timeout(IME_WINDOW_MS)
            try:
                probe = await page.evaluate(IME_PROBE)
            except Exception:
                probe = None
            delta = events.since(mark)
            return (None if delta["navigations"] else probe), delta["requests"]

        async def nothing():
            return None

        async def commit():
            await session.send("Input.insertText", {"text": IME_TEXT})

        async def keydown():  # what Chromium on macOS delivers for the Enter that confirms a conversion
            await session.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Enter", "code": "Enter",
                                                          "windowsVirtualKeyCode": 229, "nativeVirtualKeyCode": 229})

        async def keyup():
            await session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                                                          "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})

        control, control_requests = await window(nothing)
        if control is None or not control["length"]:
            return {**field, "skipped": "the field did not take the composition"}
        if safari:  # Safari ends the composition first, then sends keydown with keyCode 229
            committed, _ = await window(commit)
            if committed is None:
                return {**field, "safari": []}
            phase, requests = await window(keydown)
            return {**field, "safari": ime_signals(control, phase, requests, control_requests, committed["length"])}
        down, requests = await window(keydown)
        result = {**field, "keydown": ime_signals(control, down, requests, control_requests, control["length"]), "keyup": []}
        if down is None:
            return result

        async def commit_and_release():
            await commit()
            await keyup()

        # Length before keyup is the committed text; a cleared field after it means keyup submitted.
        up, requests = await window(commit_and_release)
        result["keyup"] = ime_signals(control, up, requests, control_requests, len(IME_TEXT))
        return result
    except Exception as error:
        return {"locator": f"input {index}", "skipped": f"{type(error).__name__}: {str(error).splitlines()[0][:120]}"}
    finally:
        await page.close()


async def _ime_fields(context, url: str, width: int, options: dict) -> list[dict]:
    results = []
    for index in range(MAX_INPUTS):
        first = await _ime_pass(context, url, width, options, index, safari=False)
        if first is None:
            break
        if not first.get("skipped") and not first.get("keydown") and not first.get("keyup"):
            second = await _ime_pass(context, url, width, options, index, safari=True)
            first["safari"] = (second or {}).get("safari", [])
        results.append(first)
    return results


async def record_url(browser, url: str, options: dict) -> dict:
    """Everything `analyse` needs about one page, as plain data."""
    recording = {"url": url, "local": is_local(url), "views": [], "controls": [], "ime": [],
                 "clicked": False, "ime_checked": False}
    context = await browser.new_context(viewport={"width": options["widths"][0], "height": VIEW_HEIGHT})
    await context.add_init_script(INIT)
    try:
        for width in options["widths"]:
            page, events = await _open(context, url, width, options)
            try:
                view = await page.evaluate(COLLECT, [20000, 4000, 150])
                recording["final_url"], recording["title"] = page.url, view.pop("title")
                recording["views"].append({**view, "width": width, "console": events.console, "requests": events.failed})
            finally:
                await page.close()
        width = max(options["widths"])
        if enabled(options["click"], url):
            recording["controls"] = await _click_controls(context, url, width, options)
            recording["clicked"], recording["click_width"] = True, width
        if enabled(options["ime"], url):
            recording["ime"] = await _ime_fields(context, url, width, options)
            recording["ime_checked"] = True
    except Exception as error:
        if not recording["views"]:
            recording["error"] = f"{type(error).__name__}: {str(error).splitlines()[0][:200]}"
        else:
            recording["partial"] = f"{type(error).__name__}: {str(error).splitlines()[0][:200]}"
    finally:
        await context.close()
    return recording


async def record(urls: list[str], options: dict) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("playwright is not installed: pip install playwright") from None
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(channel=options.get("channel"))
        except Exception as error:
            raise RuntimeError(f"could not start the browser: {str(error).splitlines()[0][:200]}. Install one with "
                               f"'playwright install chromium-headless-shell' or pass --channel chrome") from None
        try:
            return [await record_url(browser, url, options) for url in urls]
        finally:
            await browser.close()


# ---------------------------------------------------------------------------------------------
# Service interface
# ---------------------------------------------------------------------------------------------

def add_arguments(parser) -> None:
    parser.add_argument("urls", nargs="*", help="page URLs or HTML files (default: urls in the [page] config table)")
    parser.add_argument("--widths", help="viewport widths to check (default: 360,768,1280)")
    parser.add_argument("--click", choices=("auto", "on", "off"),
                        help="click buttons to find ones that do nothing; auto (default) clicks only on localhost and file pages")
    parser.add_argument("--ime", choices=("auto", "on", "off"),
                        help="type through an IME composition and press Enter; auto (default) as for --click")
    parser.add_argument("--channel", help="drive an installed browser (chrome, msedge) instead of the bundled one")
    parser.add_argument("--timeout", type=int, help="page load timeout in ms (default 30000)")
    parser.add_argument("--wait", type=int, help="extra ms to wait after load, for client-side rendering (default 0)")
    parser.add_argument("--max-controls", type=int, help=f"most controls clicked per page (default {DEFAULT_MAX_CONTROLS})")


def options_from(args) -> tuple[list[str], dict]:
    """Command-line values win over the config table, which wins over the defaults."""
    config = getattr(args, "config", None) or {}

    def pick(name: str, default):
        value = getattr(args, name, None)
        return value if value not in (None, [], "") else config.get(name, config.get(name.replace("_", "-"), default))

    root = Path(getattr(args, "root", None) or ".")
    given = pick("urls", [])
    urls = [to_url(item, root) for item in ([given] if isinstance(given, str) else list(given))]
    if not urls:
        raise RuntimeError('no page to check: pass a URL, or set urls = ["http://localhost:3000"] under [page] in .agent-sense.toml')
    options = {"widths": parse_widths(pick("widths", None)), "click": pick("click", "auto"), "ime": pick("ime", "auto"),
               "channel": pick("channel", None), "timeout": int(pick("timeout", 30000)), "wait": int(pick("wait", 0)),
               "max_controls": int(pick("max_controls", DEFAULT_MAX_CONTROLS))}
    for name in ("click", "ime"):
        if options[name] not in ("auto", "on", "off"):
            raise RuntimeError(f"{name} must be auto, on or off")
    return list(dict.fromkeys(urls)), options


def run(args, recorder=None) -> dict:
    """Check the pages. `recorder(urls, options)` replaces the browser in tests."""
    urls, options = options_from(args)
    if recorder is None:
        import asyncio
        recordings = asyncio.run(record(urls, options))
    else:
        recordings = recorder(urls, options)
    findings, pages = [], []
    for recording in recordings:
        found, stats = analyse(recording)
        findings += found
        pages.append({"url": recording["url"], "clicked": stats["clicked"], "skipped_controls": stats["skipped_controls"],
                      "inputs_checked": stats["inputs_checked"], "click_check": bool(recording.get("clicked")),
                      "ime_check": bool(recording.get("ime_checked")), "dropped": stats["dropped"],
                      **({"partial": recording["partial"]} if recording.get("partial") else {})})
    return {"findings": findings, "stats": {"pages": pages, "widths": list(options["widths"])}}

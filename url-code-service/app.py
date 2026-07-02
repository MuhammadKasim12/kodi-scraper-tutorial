import os
import random
import re
import threading
import time
from urllib.parse import urljoin

from flask import Flask, request, jsonify, redirect, abort, render_template
from flask_cors import CORS
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = Flask(__name__)
CORS(app)  # lets the phone page call /shorten directly from the browser

# _store lives in this process's memory, not a shared backend -- must run as
# exactly one gunicorn worker / one Fly machine, or a code minted by one
# process is invisible to whichever process serves the redirect.

CODE_LENGTH = 5
CODE_MAX = 10 ** CODE_LENGTH - 1
RECYCLE_AFTER_SECONDS = 7 * 24 * 60 * 60  # a code frees up for reuse after a week

_lock = threading.Lock()
_store = {}  # "01234" -> {"url": str, "created_at": epoch seconds}


def _is_expired(entry):
    return time.time() - entry["created_at"] > RECYCLE_AFTER_SECONDS


def _purge_expired():
    for code in [c for c, e in _store.items() if _is_expired(e)]:
        del _store[code]


def _allocate_code():
    """Random unused 5-digit code. 100,000 slots with a 7-day TTL is far
    more headroom than a personal single-user tool needs, so a handful of
    retries is enough to dodge the rare collision."""
    _purge_expired()
    for _ in range(50):
        candidate = f"{random.randint(0, CODE_MAX):05d}"
        if candidate not in _store:
            return candidate
    raise RuntimeError("code space exhausted")


@app.post("/shorten")
def shorten():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "missing 'url'"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    with _lock:
        code = _allocate_code()
        _store[code] = {"url": url, "created_at": time.time()}

    return jsonify({"code": code, "url": url, "recycles_in_days": 7})


@app.get("/<code>")
def resolve(code):
    """Plain HTTP redirect -- this is the endpoint the Kodi add-on hits.
    urllib follows 302s automatically, so no add-on-side code is needed
    beyond building this URL from the typed 5-digit code."""
    if not (code.isdigit() and len(code) == CODE_LENGTH):
        abort(404)
    with _lock:
        entry = _store.get(code)
        if entry is None or _is_expired(entry):
            _store.pop(code, None)
            abort(404)
        url = entry["url"]
    return redirect(url, code=302)


@app.get("/api/resolve/<code>")
def resolve_json(code):
    with _lock:
        entry = _store.get(code)
        if entry is None or _is_expired(entry):
            return jsonify({"error": "not found or expired"}), 404
        return jsonify({"code": code, "url": entry["url"]})


# ---------------------------------------------------------------------------
# JS-rendering fallback for addon.py's resolve_url().
#
# addon.py's own regex-based find_video_url() has no JS engine, so it can't
# see a video that only appears after a page's JS runs (a lot of embedded
# players work this way). Fire TV/Android can't run a desktop-grade headless
# browser either, so that JS execution has to happen somewhere with real
# CPU/RAM -- this always-on Fly machine, which already exists for the
# code-redirect feature. addon.py calls /api/resolve_js as a *last* resort,
# after its own fast static-HTML pass and iframe-follow both fail; Kodi's own
# player still does the actual playback (hardware-accelerated, seekable) --
# this only replaces "guess the URL from raw HTML" with "watch what a real
# browser's JS actually requests," not "stream a browser tab into Kodi."
# ---------------------------------------------------------------------------

_pw_lock = threading.Lock()
_playwright = None
_browser = None


def _get_browser():
    """Lazily launch one shared headless Chromium and reuse it across
    requests -- launching Chromium from scratch (~1-2s) on every call would
    make an already-slow fallback slower. Safe under gunicorn's required
    single worker (see _store comment above): one process, one browser."""
    global _playwright, _browser
    with _pw_lock:
        if _browser is None:
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
        return _browser


_VIDEO_EXT_RE = re.compile(r"\.(?:mp4|m3u8|mpd)(?:[?#]|$)", re.IGNORECASE)
_VIDEO_CONTENT_TYPES = (
    "video/",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
)

# Same "last resort" static patterns as addon.py's find_video_url(), kept for
# the case where the video shows up in the post-JS DOM rather than in a
# network request the page fires.
_STATIC_VIDEO_PATTERNS = [
    r'<video[^>]*>\s*<source[^>]+src=["\']([^"\']+)["\']',
    r'<video[^>]+src=["\']([^"\']+)["\']',
    r'<meta[^>]+property=["\']og:video(?::(?:url|secure_url))?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']',
    r'\bdata-(?:src|video|url)=["\']([^"\']+?\.(?:mp4|m3u8|mpd)[^"\']*)["\']',
    r'["\']?(?:file|src)["\']?\s*:\s*["\']([^"\']+?\.(?:mp4|m3u8|mpd)[^"\']*)["\']',
    r'["\'](https?://[^"\'\s<>]+?\.m3u8[^"\'\s<>]*)["\']',
    r'["\'](https?://[^"\'\s<>]+?\.mpd[^"\'\s<>]*)["\']',
    r'["\'](https?://[^"\'\s<>]+?\.mp4[^"\'\s<>]*)["\']',
]


def _find_video_url_in_html(html, page_url):
    for pattern in _STATIC_VIDEO_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return urljoin(page_url, match.group(1).replace("\\/", "/"))
    return None


def resolve_video_url_with_browser(url, nav_timeout_ms=15000, settle_ms=2000):
    """Load `url` in real headless Chromium, run its JS, and return the
    first direct video URL that surfaces -- either as a request/response for
    a media file, or in the post-JS-rendered DOM. Returns (stream_url,
    final_page_url); stream_url is None if nothing turned up."""
    browser = _get_browser()
    context = browser.new_context(
        user_agent="Mozilla/5.0 (KodiScraperTutorial/0.1; +browser-fallback)"
    )
    found = {"url": None}

    def on_request(req):
        if not found["url"] and _VIDEO_EXT_RE.search(req.url):
            found["url"] = req.url

    def on_response(resp):
        if found["url"]:
            return
        ctype = resp.headers.get("content-type", "")
        if any(ctype.startswith(t) for t in _VIDEO_CONTENT_TYPES) or _VIDEO_EXT_RE.search(resp.url):
            found["url"] = resp.url

    page = context.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    final_url = url
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        final_url = page.url
        page.wait_for_timeout(settle_ms)  # let lazy-loaded players/XHRs fire
        if not found["url"]:
            found["url"] = _find_video_url_in_html(page.content(), final_url)
    except PlaywrightTimeoutError:
        pass  # partial navigation is fine -- use whatever we captured so far
    finally:
        context.close()

    return found["url"], final_url


@app.get("/api/resolve_js")
def resolve_js():
    target = (request.args.get("url") or "").strip()
    if not target:
        return jsonify({"error": "missing 'url'"}), 400
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    try:
        stream_url, final_url = resolve_video_url_with_browser(target)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    if not stream_url:
        return jsonify({"error": "no video found", "page_url": final_url}), 404
    return jsonify({"stream_url": stream_url, "page_url": final_url})


@app.get("/")
def index():
    """No-install phone control page -- open this URL directly on the
    phone's browser, nothing to download or sideload."""
    return render_template("index.html")


@app.get("/health")
def health():
    with _lock:
        _purge_expired()
        active = len(_store)
    return jsonify({"status": "ok", "active_codes": active})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

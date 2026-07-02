import os
import random
import threading
import time

from flask import Flask, request, jsonify, redirect, abort
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # lets mobile-remote.html call /shorten directly from the phone browser

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


@app.get("/")
def health():
    with _lock:
        _purge_expired()
        active = len(_store)
    return jsonify({"status": "ok", "active_codes": active})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

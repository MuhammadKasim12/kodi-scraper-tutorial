import re
import sys
import json
import urllib.request
import urllib.parse
from urllib.parse import parse_qsl, urljoin

import xbmcgui
import xbmcplugin

# ---------------------------------------------------------------------------
# EDUCATIONAL SCRAPER ADDON -- FOR EDUCATIONAL PURPOSES ONLY.
# Do not point resolve_url() at content you don't have the legal right to
# access; see README.md for the full disclaimer.
#
# This is the standard skeleton every Kodi "video plugin" uses, whether it's
# pulling from a JSON API (like here) or scraping raw HTML with regex/
# BeautifulSoup. The four moving parts are always the same:
#
#   1. ROUTING    - Kodi calls your script over and over with a different
#                    ?action=...&... query string each time the user clicks
#                    something. There's no persistent state between calls.
#   2. FETCH       - you make an HTTP request to the source site.
#   3. PARSE       - you turn the response (HTML/JSON) into a list of
#                    {title, id/url, thumbnail} dicts.
#   4. LIST/RESOLVE- you either hand Kodi a directory of clickable items
#                    (xbmcplugin.addDirectoryItem) or, for a playable item,
#                    hand back the final stream URL (setResolvedUrl).
#
# This example points at archive.org's public API instead of a piracy site,
# so it's actually legal to run and the content never disappears.
# ---------------------------------------------------------------------------

HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]

USER_AGENT = "Mozilla/5.0 (KodiScraperTutorial/0.1)"

# Base URL of your deployed url-code-service (see url-code-service/ folder).
# A typed 5-digit code is resolved as f"{CODE_SERVICE_BASE}/{code}".
CODE_SERVICE_BASE = "https://kasim-url-code-svc.fly.dev"

# A few curated archive.org collections to browse as "categories".
COLLECTIONS = [
    ("Feature Films", "feature_films"),
    ("Prelinger Archives (ads/industrial/educational)", "prelinger"),
    ("Silent Films", "silent_films"),
]


def fetch_json(url, timeout=15):
    """Generic HTTP GET -> parsed JSON. Swap this for html.parser/BeautifulSoup
    if the site you're targeting only gives you HTML back."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_url(action, **kwargs):
    kwargs["action"] = action
    return BASE_URL + "?" + urllib.parse.urlencode(kwargs)


def list_collections():
    xbmcplugin.setPluginCategory(HANDLE, "Collections")
    xbmcplugin.setContent(HANDLE, "videos")

    for label, collection_id in COLLECTIONS:
        item = xbmcgui.ListItem(label=label)
        item.setInfo("video", {"title": label, "mediatype": "video"})
        url = build_url("list_items", collection=collection_id)
        xbmcplugin.addDirectoryItem(HANDLE, url, item, isFolder=True)

    # Manual entry: type/paste a URL using the remote's on-screen keyboard
    # instead of sending one from the phone page.
    manual_item = xbmcgui.ListItem(label="[Enter URL manually]")
    manual_item.setProperty("IsPlayable", "true")
    xbmcplugin.addDirectoryItem(HANDLE, build_url("enter_url"), manual_item, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


def list_items(collection):
    """
    STEP 2+3 in action: hit archive.org's search API and parse the JSON
    response into a list of playable items. If you were scraping a plain
    HTML page instead, this is where you'd run something like:

        html = urllib.request.urlopen(req).read().decode()
        titles = re.findall(r'<h3 class="title">(.*?)</h3>', html)
        links  = re.findall(r'<a class="watch" href="(.*?)"', html)

    ...or, more robustly, feed `html` into BeautifulSoup(html, "html.parser")
    and use .select()/.find_all() instead of brittle regex.
    """
    xbmcplugin.setPluginCategory(HANDLE, collection)
    xbmcplugin.setContent(HANDLE, "videos")

    query = urllib.parse.urlencode({
        "q": f"collection:{collection} AND mediatype:movies",
        "fl[]": ["identifier", "title", "description"],
        "rows": 25,
        "output": "json",
    }, doseq=True)
    search_url = f"https://archive.org/advancedsearch.php?{query}"

    try:
        data = fetch_json(search_url)
        docs = data.get("response", {}).get("docs", [])
    except Exception as e:
        xbmcgui.Dialog().notification("Scraper Tutorial", f"Fetch failed: {e}")
        docs = []

    for doc in docs:
        identifier = doc.get("identifier")
        title = doc.get("title", identifier)
        if not identifier:
            continue

        item = xbmcgui.ListItem(label=title)
        item.setInfo("video", {
            "title": title,
            "plot": doc.get("description", ""),
            "mediatype": "movie",
        })
        item.setArt({
            "thumb": f"https://archive.org/services/img/{identifier}",
            "icon": f"https://archive.org/services/img/{identifier}",
        })
        item.setProperty("IsPlayable", "true")

        url = build_url("play", identifier=identifier)
        xbmcplugin.addDirectoryItem(HANDLE, url, item, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


def play_item(identifier):
    """
    STEP 4 (resolve): every item's metadata lives at a predictable API
    endpoint. We parse it to find an actual playable video file, then hand
    Kodi the *direct* URL via setResolvedUrl. This is the exact same move
    a scraper for any other site makes -- follow a detail page/API call
    until you find a concrete .mp4/.m3u8 URL, then resolve to it.
    """
    meta_url = f"https://archive.org/metadata/{identifier}"
    try:
        data = fetch_json(meta_url)
    except Exception as e:
        xbmcgui.Dialog().notification("Scraper Tutorial", f"Metadata failed: {e}")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    server = data.get("server")
    d1 = data.get("d1")
    dir_ = data.get("dir", "")
    files = data.get("files", [])

    host = server or d1
    stream_file = None
    for f in files:
        name = f.get("name", "")
        fmt = f.get("format", "")
        if name.lower().endswith(".mp4") or "MPEG4" in fmt or "h.264" in fmt.lower():
            stream_file = name
            break

    if not host or not stream_file:
        xbmcgui.Dialog().notification("Scraper Tutorial", "No playable file found")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    stream_url = f"https://{host}{dir_}/{urllib.parse.quote(stream_file)}"
    play_item_obj = xbmcgui.ListItem(path=stream_url)
    xbmcplugin.setResolvedUrl(HANDLE, True, play_item_obj)


def fetch_html(url):
    """
    Returns (html, final_url). urllib follows HTTP redirects automatically,
    so passing in a short link (tinyurl.com/xxxxx, bit.ly/xxxx, etc.) "just
    works" -- resp.geturl() gives back wherever the redirect chain actually
    landed, which is what relative links on the page need to be resolved
    against (not the short link itself).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        html = resp.read().decode(charset, errors="replace")
        return html, resp.geturl()


def _flatten_jsonld(data):
    """Yield every dict found in a JSON-LD payload, including @graph nodes
    and array entries -- schema.org data can nest either way."""
    if isinstance(data, list):
        for item in data:
            yield from _flatten_jsonld(item)
    elif isinstance(data, dict):
        yield data
        if isinstance(data.get("@graph"), list):
            yield from _flatten_jsonld(data["@graph"])


def find_jsonld_video_url(html):
    """
    schema.org VideoObject structured data (JSON-LD) is a standard,
    widely-used SEO pattern on mainstream/legitimate video sites (news,
    education, corporate) -- since it's real JSON, parse it properly
    instead of regex-guessing.
    """
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _flatten_jsonld(data):
            url = obj.get("contentUrl") or obj.get("embedUrl")
            if url:
                return url
    return None


def find_video_url(html, page_url):
    """
    Generic (site-agnostic) direct-video-file finder. This is the "last
    resort" scraping technique: no site-specific selectors, just standard
    web patterns for exposing a video: schema.org JSON-LD, plain
    <video>/<source> tags, og:video/twitter:player:stream meta tags,
    data-src-style lazy-load attributes, the "file"/"src"/"sources" JSON
    keys common JS player libraries (JW Player, video.js, Plyr) embed in a
    <script> block, and bare .mp4/.m3u8/.mpd links anywhere in the
    HTML/JS. It will NOT defeat DRM or sites that only expose video after
    running arbitrary JS (no JS engine here) -- those need a site-specific
    resolver, same as any real scraper add-on.
    """
    jsonld_url = find_jsonld_video_url(html)
    if jsonld_url:
        return urljoin(page_url, jsonld_url.replace("\\/", "/"))

    ext = r"(?:mp4|m3u8|mpd)"
    patterns = [
        r'<video[^>]*>\s*<source[^>]+src=["\']([^"\']+)["\']',
        r'<video[^>]+src=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:video(?::(?:url|secure_url))?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']',
        r'\bdata-(?:src|video|url)=["\']([^"\']+?\.' + ext + r'[^"\']*)["\']',
        r'["\']?(?:file|src)["\']?\s*:\s*["\']([^"\']+?\.' + ext + r'[^"\']*)["\']',
        r'["\']?sources["\']?\s*:\s*\[\s*\{[^}]*?["\']?(?:file|src)["\']?\s*:\s*["\']([^"\']+)["\']',
        r'["\'](https?://[^"\'\s<>]+?\.m3u8[^"\'\s<>]*)["\']',
        r'["\'](https?://[^"\'\s<>]+?\.mpd[^"\'\s<>]*)["\']',
        r'["\'](https?://[^"\'\s<>]+?\.mp4[^"\'\s<>]*)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            candidate = match.group(1).replace("\\/", "/")  # undo JSON slash-escaping
            return urljoin(page_url, candidate)
    return None


def find_iframe_url(html, page_url):
    """First <iframe src> on the page, if any -- many sites embed a
    third-party player this way instead of a direct <video> tag."""
    match = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return urljoin(page_url, match.group(1))
    return None


def resolve_url_with_browser(target):
    """
    Last-resort fallback for pages whose video only appears after running
    JS -- find_video_url() above has no JS engine, so it can never see
    those (its own docstring says so). Fire TV/Android can't run a
    desktop-grade headless browser either, so this asks url-code-service
    (already deployed, already always-on for the code-redirect feature) to
    do it: it loads the page in real headless Chromium, runs its JS, and
    watches what the page actually requests. Kodi still plays the resulting
    direct URL with its own native player -- this doesn't stream a browser
    tab into Kodi, it just extracts what a browser would have played.

    Slower than the static pass (page load + JS settle time), so it's only
    tried after find_video_url()/iframe-following both come up empty.
    """
    api_url = CODE_SERVICE_BASE + "/api/resolve_js?url=" + urllib.parse.quote(target, safe="")
    try:
        data = fetch_json(api_url, timeout=25)
    except Exception:
        return None
    return data.get("stream_url")


def resolve_url(target):
    """
    Entry point for "arbitrary URL sent from phone" flow: fetch whatever
    page the user pointed at (following any short-link redirect), try to
    find a direct video URL in it, and hand it straight to Kodi's player.
    Triggered remotely via Kodi's JSON-RPC Player.Open with a plugin://
    URL -- see mobile-remote.html.
    """
    try:
        html, final_url = fetch_html(target)
    except Exception as e:
        xbmcgui.Dialog().notification("Scraper Tutorial", f"Could not fetch page: {e}")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    stream_url = find_video_url(html, final_url)

    if not stream_url:
        iframe_url = find_iframe_url(html, final_url)
        if iframe_url:
            try:
                iframe_html, iframe_final_url = fetch_html(iframe_url)
                stream_url = find_video_url(iframe_html, iframe_final_url)
            except Exception:
                pass  # iframe fetch failing just means we fall through to the JS fallback below

    if not stream_url:
        xbmcgui.Dialog().notification("Scraper Tutorial", "Rendering page in a browser, this can take a few seconds...")
        stream_url = resolve_url_with_browser(final_url)

    if not stream_url:
        xbmcgui.Dialog().notification("Scraper Tutorial", "No direct video found on that page")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    item = xbmcgui.ListItem(path=stream_url)
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def enter_url():
    """
    On-device alternative to the phone remote: pops Kodi's own keyboard
    (works with the Fire TV remote's D-pad-driven on-screen keyboard, or
    any paired Bluetooth keyboard/voice-to-text).

    A bare 5-digit code (e.g. "01234", generated by url-code-service's
    /shorten endpoint) is by far the fastest thing to type with a D-pad --
    it's resolved as CODE_SERVICE_BASE/<code>, and fetch_html()'s existing
    redirect-following does the rest, same as any other short link.
    """
    typed = xbmcgui.Dialog().input(
        "Enter a 5-digit code (from the phone page) or a full URL",
        type=xbmcgui.INPUT_ALPHANUM,
    )
    if not typed:
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    typed = typed.strip()
    if typed.isdigit() and len(typed) == 5:
        target = f"{CODE_SERVICE_BASE}/{typed}"
    elif not typed.startswith(("http://", "https://")):
        target = "https://" + typed
    else:
        target = typed

    resolve_url(target)


def router():
    params = dict(parse_qsl(sys.argv[2][1:]))
    action = params.get("action")

    if action is None:
        list_collections()
    elif action == "list_items":
        list_items(params["collection"])
    elif action == "play":
        play_item(params["identifier"])
    elif action == "resolve_url":
        resolve_url(params["target"])
    elif action == "enter_url":
        enter_url()
    else:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    router()

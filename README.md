# Kodi Scraper Add-on Tutorial

> **DISCLAIMER: FOR EDUCATIONAL PURPOSES ONLY.**
> This project exists to teach the mechanics of Kodi scraper/video add-ons
> (routing, fetching, parsing, resolving, remote control via JSON-RPC). It
> is wired up against archive.org's public-domain catalog specifically so
> it's legal to run as-is. The generic `resolve_url` action and
> `mobile-remote.html` are general-purpose tools — pointing them at content
> you don't have the legal right to access (copyrighted streams, sites that
> prohibit scraping in their ToS, etc.) is your responsibility and may be
> illegal in your jurisdiction. Do not use this to infringe copyright.

A minimal, working Kodi video-plugin add-on demonstrating the standard
list -> parse -> resolve -> play pattern used by every Kodi scraper add-on.

Points at archive.org's public-domain film API instead of a piracy site, so
it's legal to run and won't rot.

## Install (on Fire TV)

Fire TV doesn't ship Kodi in its app store, and Fire OS blocks installs from
unknown sources by default, so:

1. On the Fire TV: Settings -> My Fire TV -> Developer options -> turn on
   **ADB debugging** and **Apps from Unknown Sources**.
2. Install the **Downloader** app from the Fire TV app store (search for it).
3. In Downloader, enter the URL for the official Kodi Android APK
   (kodi.tv/download -> Android -> ARM, since Fire TV is ARM) and install it.
4. Launch Kodi once from the Fire TV apps list so its config/addons folders
   get created.
5. Get this add-on onto the Fire TV. Easiest path is `adb`:
   - Enable ADB debugging (step 1) and find the Fire TV's IP:
     Settings -> My Fire TV -> About -> Network.
   - From your computer: `adb connect <firetv-ip>:5555`
   - `adb push plugin.video.scrapertutorial /sdcard/Android/data/org.xbmc.kodi/files/.kodi/addons/plugin.video.scrapertutorial`
   - Restart Kodi (or Settings -> Add-ons -> "My add-ons" should now show it;
     if not, use Kodi's own "Install from zip file" pointed at a zipped copy
     on a USB/network share instead).
6. Settings -> Services -> Control -> enable **"Allow remote control via
   HTTP"** — same as any other Kodi install, this is what `mobile-remote.html`
   talks to.
7. Find the Fire TV's IP again (My Fire TV -> About -> Network) and use
   `<that-ip>:8080` as the host in `mobile-remote.html`. Phone and Fire TV
   just need to be on the same Wi-Fi network — no Fire TV-specific pairing
   or remote-app protocol involved.

## Install (general / other platforms)

1. Zip the `plugin.video.scrapertutorial` folder (zip the folder itself, not just its contents).
2. In Kodi: Settings -> Add-ons -> Install from zip file -> select the zip.
3. Find it under Video Add-ons -> "Scraper Tutorial (Archive.org)".

Or for active development, symlink/copy the folder straight into Kodi's
`addons` directory and enable Settings -> Add-ons -> "Unknown sources".

## How it generalizes to any site

| Step | This example | Generic scraping equivalent |
|---|---|---|
| Routing | `sys.argv` query string switch in `router()` | identical for any addon |
| Fetch | `urllib.request` GET to a JSON API | GET/POST to any page, with headers/cookies/session as needed |
| Parse | `json.loads(...)` | `re.findall(...)` or `BeautifulSoup(html, "html.parser")` for HTML |
| Resolve | follow metadata endpoint to find a direct `.mp4` URL | inspect the target page's network requests (browser devtools) to find the real stream URL, which is often buried in embedded JS or a separate XHR call |

The only genuinely site-specific work in any scraper add-on is steps 2-3:
figuring out the source's URL structure and what its HTML/JSON looks like.
Browser devtools' Network tab is the standard way to find this — load the
page, filter by XHR/Fetch, and see what request actually returns the video
URL.

## Part 2: send an arbitrary URL from your phone

The add-on also has a generic `resolve_url` action (`addon.py`) that fetches
any page you point it at and looks for a direct video file in its HTML
(`<video>`/`<source>` tags, `og:video` meta tags, bare `.mp4`/`.m3u8` links).
It's a last-resort, site-agnostic technique — it won't defeat DRM, obfuscated
JS players, or third-party embeds (YouTube/Vimeo iframes etc.); those need a
site-specific resolver, same as any real scraper add-on.

`mobile-remote.html` is a single static page — open it in your phone's
browser (AirDrop/email it to the phone, or serve it from any static host) —
that lets you type a URL and push it to Kodi over your **local network**
using Kodi's built-in JSON-RPC API. No server of your own required.

Setup:

1. On the Kodi device: Settings -> Services -> Control -> enable **"Allow
   remote control via HTTP"**. Note the port (default `8080`) and, if you
   set a username/password there, remember them.
2. Find the Kodi device's local IP (Settings -> System info -> Network, or
   check your router's client list).
3. On your phone, open `mobile-remote.html`, enter `<ip>:8080` as the host
   (and credentials if you set any), paste a page URL, tap **Send to TV**.
4. Kodi calls `Player.Open` on `plugin://plugin.video.scrapertutorial/?action=resolve_url&target=...`,
   which runs `resolve_url()` in `addon.py` and plays whatever direct video
   URL it finds.

This is exactly the "mobile enters a URL, TV scrapes and plays it" flow —
built on Kodi's own remote-control API rather than a custom relay server.

## Part 3: enter the URL directly on the TV (no phone needed)

The add-on's root menu has an **"[Enter URL manually]"** item. Selecting it
with the Fire TV remote pops Kodi's own on-screen keyboard (`enter_url()` in
`addon.py`) — type/paste a URL there and it resolves and plays exactly like
the phone flow, no second device required.

**Recommendation: type a 5-digit code, not the full URL.** Typing a long URL
with a D-pad-driven on-screen keyboard is slow and error-prone — even a
TinyURL-style alphanumeric short link is ~18 mixed-case characters. Numbers
only, 5 digits, is about as fast as a D-pad keyboard gets.

## Part 4: url-code-service — your own URL-to-code resolver

`url-code-service/` is a small self-hosted Flask service (same stack as your
`quick-resume-gen` project: Flask + flask-cors + gunicorn, deployable via the
included `Dockerfile`/`fly.toml`/`render.yaml`/`Procfile` — copy whichever
deploy path you already use). It owns a 5-digit numeric code space:

- `POST /shorten {"url": "..."}` → `{"code": "01234", ...}` — stores the URL
  in memory, keyed by a random unused code.
- `GET /<code>` → `302` redirect to the stored URL. This is the endpoint the
  Kodi add-on hits — `fetch_html()` already follows redirects automatically,
  so no add-on-side parsing is needed beyond building this URL.
- Any code untouched for **7 days becomes eligible for reuse** — allocation
  purges expired entries before picking a new random code, so the 100,000-code
  space (`00000`–`99999`) never fills up for personal use.

**Already deployed** at `https://kasim-url-code-svc.fly.dev` (Fly.io, `sjc`
region) — `addon.py`'s `CODE_SERVICE_BASE` already points at it, so you can
skip straight to entering that same URL in `mobile-remote.html`'s
"url-code-service URL" field. To redeploy your own copy instead:

```bash
cd url-code-service
fly launch --copy-config --now --name your-app-name
```

Then wire the other two pieces to it:

1. In `plugin.video.scrapertutorial/addon.py`, set `CODE_SERVICE_BASE` to
   your deployed URL.
2. In `mobile-remote.html`, enter that same URL in the **"url-code-service
   URL"** field (persisted locally on your phone).

Flow end-to-end: paste a long URL into `mobile-remote.html` → tap **"Get code
for Page URL"** → it POSTs to `/shorten` and shows a 5-digit code → walk to
the TV, open the add-on's **"[Enter URL manually]"** item, type the 5 digits
→ Kodi hits `CODE_SERVICE_BASE/<code>`, gets redirected, scrapes, plays.

**Two constraints from the in-memory store — don't change without fixing
this first:**
- **Exactly one process must serve the app.** `Dockerfile`/`Procfile` run
  gunicorn with `--workers 1` on purpose — with 2+ workers, `/shorten` and
  the redirect lookup can land on different OS processes with independent
  memory, so a freshly-minted code 404s about half the time. Same logic
  applies to Fly machine count: `fly scale count 1`, not 2+.
- **The machine must stay running.** `fly.toml` sets
  `auto_stop_machines = 'off'` / `min_machines_running = 1` on purpose —
  Fly's default scale-to-zero behavior powers the machine off between
  requests to save cost, which wipes the in-memory dict just as thoroughly
  as a worker mismatch does. The tradeoff: the machine runs continuously
  instead of scaling to zero, which is the right call for a personal tool
  this light but is worth knowing if you're watching free-tier hours.
- If you want codes to survive restarts/redeploys entirely, swap the
  `_store` dict for a SQLite file (or a Fly volume) — a small change, but
  out of scope for this tutorial version.

## Legal note

This pattern works identically against pirated-content sites, which is why
Kodi scraper add-ons have a bad reputation — but the technique itself is
neutral. Only point this at sites you have the legal right to pull from
(your own media server, public-domain archives, services with an API/ToS
that permits it).

# Secure Video Chat — Flet edition

A video-chat web app written in Python with [Flet](https://flet.dev): sign up, sign in, pick a room, and talk.
The UI is Flet (Flutter on the web, driven by Python code on the server). The video itself is the
[Agora Web SDK](https://docs.agora.io), because Flet has no
WebRTC/Agora control: each room is a small web page (`assets/room.html`) embedded in the Flet UI through
Flet's `WebView`.

This folder is a **separate implementation** of the project in the repository root (Django). The two do not
import each other and the Django code is unchanged; they use different databases and different accounts.

> **Before you deploy:** the Django project's git history contains a real Agora App ID/Certificate and a
> Django `SECRET_KEY`. Treat them as compromised: rotate them in the Agora console and generate new
> secrets. Nothing in this folder uses or needs them.

## How it fits together

```
 Browser
 ├─ Flutter web client (Flet) ──websocket──▶  Flet session: Python UI code (ui/)
 │                                              └─ services (rules) ─▶ repositories (SQL) ─▶ SQLite
 └─ <iframe> /room.html#p=<signed ticket>
      ├─ POST /api/rtc/credentials  (Authorization: Bearer <ticket>) ─▶ short-lived Agora token
      └─ vendored AgoraRTC 4.8.0 ───────────────────────────────────▶ Agora network (media)
```

One FastAPI process serves everything: the `/api/*` endpoints, the static room page, and the Flet app
(`flet.fastapi`), so the room page is same-origin with the app and no third-party host is needed to load it.

## Quick start

Requires Python 3.11+.

```bash
cd flet_app
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then edit it
python main.py                                         # http://127.0.0.1:8550
```

Set at least `SECRET_KEY` (see `.env.example` for a one-liner that generates one) and your own
`AGORA_APP_ID` / `AGORA_APP_CERTIFICATE` from the [Agora console](https://console.agora.io). Without the
Agora values the app still runs, but the lobby disables **Join room** and says video is not configured.

Open the app in two browsers (or a normal and a private window), register two users, and join the same room
name. Browsers only allow camera and microphone on `https://` pages or on `localhost`.

## Configuration

All settings are environment variables, optionally loaded from `.env`. `.env.example` documents every one;
the ones that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | random per process | Signs room tickets. ≥ 32 characters; short or `django-insecure…` keys are refused at startup. |
| `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` | empty | Your Agora project. The certificate stays on the server. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Host (and WebSocket Origin) allow-list; anything else gets a 400. |
| `PUBLIC_BASE_URL` | derived from the request | Public origin, needed behind a proxy. `https://` also turns on HSTS. |
| `TRUSTED_PROXIES` | empty | Proxies whose `X-Forwarded-*` headers uvicorn trusts. Needed for per-client throttling behind a proxy. |
| `CSP_CONNECT_SRC_EXTRA` | empty | Extra `connect-src` origins for the room page (see Troubleshooting). |
| `DATABASE_URL` | `sqlite:///data/videochat.sqlite3` | SQLAlchemy URL. Only SQLite has been tested. |
| `SESSION_TTL_SECONDS` / `SESSION_IDLE_TTL_SECONDS` | 7 days / 24 h | Absolute and idle login lifetime. |

## Security model

What is implemented, and checked by the tests described below:

- **Passwords** — scrypt (N=2¹⁵, r=8, p=1, 16-byte random salt), constant-time comparison, parameters bounded
  when a stored hash is parsed. Policy: 12–128 characters, not on a common-password list, must not contain
  the username or e-mail name.
- **Login sessions** — opaque random tokens; only their SHA-256 is stored. Absolute and idle expiry, "sign out
  of all devices", and changing the password revokes every other session.
- **Throttling** — in-memory sliding windows: sign-in 30 / 15 min per IP, 10 per username, 5 per
  IP + username pair; registration 10 / hour per IP; password changes 5 / 15 min per user; 120 API calls /
  min per user. Failed sign-ins give one generic message for unknown user and wrong password.
- **Room entry** — joining mints an HMAC-signed **ticket** bound to the user, the login session, the room and a
  random per-membership Agora uid. It travels in the URL *fragment* (never sent over the network or in
  `Referer`), the room page removes it from the address bar immediately, and it is then sent only as an
  `Authorization: Bearer` header. The API uses no cookies, so there is no ambient credential for CSRF.
  Every call re-checks the signature, expiry, that the login is still alive, and that the membership exists.
- **Agora tokens** — minted server-side (publisher role, 1 h, never longer than the ticket) and renewed before
  they expire. The room page asks the server who a remote uid is, so participant names come from the server,
  not from anything another client claims.
- **Input handling** — strict validation (NFKC, control characters rejected, ASCII checked before case folding
  so `ß` cannot collide with `SS`); the room page renders only with `textContent`.
- **Browser hardening** — a strict CSP on the room page (`script-src 'self'`, no inline script, no `eval`,
  `connect-src` limited to this server and Agora's domains, framing only by the same origin); `no-store` on
  the API and room page; `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `X-Frame-Options`;
  HSTS when `HTTPS` is on; no `Server` banner; Host and WebSocket-Origin allow-listing.

### What it does not do

Please read these before describing the app to anyone as "secure":

- **No end-to-end encryption.** Media goes through Agora's network using the SDK's normal transport. This app
  neither adds an encryption layer nor enables Agora's media-encryption feature.
- **Rooms are not access-controlled.** Any signed-in user can join any room name. Use long, random names if a
  room should be hard to find; the name is not a secret.
- **Signing out does not eject a live call.** The ticket and renewals stop working at once, but an Agora token
  that was already issued stays valid at Agora until it expires (default 1 h, `RTC_TOKEN_TTL_SECONDS`).
- **No e-mail verification, password reset or multi-factor sign-in.**
- **"Keep me signed in" is a trade-off.** It keeps the session token in the browser's local storage so a reload
  can restore the login (opt-in, revocable server-side). The Flet shell page needs inline scripts, so its CSP
  is necessarily looser than the room page's.
- **Single process only.** Throttles and Flet sessions live in memory; a restart resets them and several
  workers would not share them.
- **Old SDK.** The vendored Agora SDK is 4.8.0 (byte-identical to the copy the Django project already ships);
  update it deliberately (replace the file in `assets/vendor/` and the `<script>` tag in `room.html`), and
  re-run the E2E suite.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                      # 215 tests, about 3 seconds
```

The unit/integration tests cover validation, password hashing, tickets, the rate limiter, accounts, rooms,
Agora token minting (with obviously fake credentials), the HTTP API, and the middleware (CSP, Host and Origin
guard).

`tests/e2e/` drives a real server with a real Chrome (Puppeteer) through the Flutter UI's accessibility tree.
Only the Agora network is replaced by a stub, so calls can be exercised without Agora. Its 20 steps cover:
the sign-in redirect, weak-password rejection, registration, joining a room (ticket removed from the URL), a
second user whose verified name appears, token renewal, mute/camera, "ticket only ever sent in an
`Authorization` header", leave, profile, password change, sign-out/in, throttling, "sign out everywhere",
"keep me signed in" across a reload, the **real** Agora SDK loading under the strict CSP and playing Chrome's
fake camera, other origins being unable to frame the room page, and no unexpected console errors.
How to run it is at the top of `tests/e2e/run.js`.

### What has *not* been verified

- A real call: joining a real Agora channel with real credentials and real remote media. The E2E uses a stub for
  the network; the real SDK was only loaded and run against a fake camera.
- Whether the CSP `connect-src` list (`*.agora.io`, `*.sd-rtn.com`, `*.agoraio.cn`) covers every host your
  Agora region uses. `CSP_CONNECT_SRC_EXTRA` is the escape hatch.
- Browsers other than headless Chrome (Safari, Firefox, mobile), real devices, and Flet desktop/mobile targets.
- Running behind a TLS-terminating reverse proxy, PostgreSQL or any database other than SQLite, Python 3.10,
  and more than one process.

## Troubleshooting

- **HTTP 400 on every request** — the `Host` is not in `ALLOWED_HOSTS`.
- **Room area blank behind a proxy** — set `PUBLIC_BASE_URL` to the public origin the browser uses.
- **Camera/microphone refused** — the page is neither `https://` nor `localhost`.
- **Video fails to connect and the console shows a CSP violation** — add the blocked origin to
  `CSP_CONNECT_SRC_EXTRA` (comma-separated, e.g. `https://edge.example.com,wss://edge.example.com`).
- **"Too many attempts"** — the throttle window is 15 minutes; restarting the process clears it in development.
- **Console noise from Flet itself** — the client tries to fetch an animation runtime from `cdn.jsdelivr.net`
  (blocked by the CSP on purpose, harmless), and typing `-` in a text field logged a WASM
  `Exception: Exception` in headless Chrome while the character was still inserted.

## Layout

```
main.py                    entry point (uvicorn)
assets/                    room.html, js/room.js, css/room.css, vendor/AgoraRTC_N-4.8.0.js
videochat/
  config.py                environment → Settings
  container.py             wires database, limiter and services together
  security/                validation, scrypt passwords, signed tickets, rate limiter
  data/                    SQLAlchemy models, session handling, and the repositories (every SQL statement)
  services/                accounts, rooms, Agora token minting, throttle (rules only: no SQL, UI or HTTP)
  web/                     FastAPI app, /api router and its dependencies (ticket, throttle, authed), middleware
  ui/                      Flet app, theme, components, and one module per view
tests/                     pytest suite; tests/e2e is the Puppeteer browser suite
```

"""Pure-ASGI middleware: host/origin guard and security response headers."""
from __future__ import annotations

import re
from typing import Iterable, Sequence
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import Settings

ROOM_PAGE_PATH = "/room.html"
AGORA_CONNECT_SOURCES = (
    "https://*.agora.io", "wss://*.agora.io",
    "https://*.sd-rtn.com", "wss://*.sd-rtn.com",
    "https://*.agoraio.cn", "wss://*.agoraio.cn",
)


def split_host(value: str) -> str:
    """Hostname without port: ``example.com:8550`` -> ``example.com``, ``[::1]:80`` -> ``[::1]``."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.split(":", 1)[0]


def host_allowed(hostname: str, patterns: Iterable[str]) -> bool:
    hostname = split_host(hostname)
    if not hostname:
        return False
    for raw in patterns:
        pattern = split_host(raw)
        if pattern == "*" or pattern == hostname:
            return True
        if pattern.startswith("*.") and hostname.endswith(pattern[1:]) and hostname != pattern[2:]:
            return True
    return False


class HostGuardMiddleware:
    """Reject requests whose ``Host`` (and, for WebSockets, ``Origin``) is not on the allow-list.

    Blocks DNS-rebinding and cross-site WebSocket hijacking against the Flet socket.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        patterns = list(settings.allowed_hosts)
        if settings.public_base_url:
            patterns.append(urlsplit(settings.public_base_url).netloc)
        self.patterns = patterns

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not host_allowed(headers.get("host", ""), self.patterns):
            await self._deny(scope, send, 400, b"Invalid host header")
            return
        if scope["type"] == "websocket":
            origin = headers.get("origin")
            if origin is not None and not host_allowed(urlsplit(origin).netloc, self.patterns):
                await self._deny(scope, send, 403, b"Origin not allowed")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope: Scope, send: Send, status: int, body: bytes) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


def _csp(directives: Sequence[tuple[str, Sequence[str]]]) -> str:
    return "; ".join(" ".join([name, *values]) if values else name for name, values in directives)


def room_page_csp(settings: Settings) -> str:
    """Strict policy for the page that runs the Agora SDK: no inline script, no eval, no framing by others."""
    connect = ["'self'", *AGORA_CONNECT_SOURCES, *settings.csp_connect_extra]
    return _csp([
        ("default-src", ["'none'"]),
        ("script-src", ["'self'", "'wasm-unsafe-eval'"]),
        ("style-src", ["'self'"]),
        # The Agora player positions its <video> with inline style *attributes*; allow those only (no <style>
        # blocks, no script), which is the narrowest relaxation that keeps remote video laid out correctly.
        ("style-src-attr", ["'unsafe-inline'"]),
        ("img-src", ["'self'", "data:", "blob:"]),
        ("media-src", ["'self'", "blob:", "mediastream:"]),
        ("connect-src", connect),
        ("worker-src", ["blob:"]),
        ("font-src", ["'self'"]),
        ("object-src", ["'none'"]),
        ("base-uri", ["'none'"]),
        ("form-action", ["'none'"]),
        ("frame-ancestors", ["'self'"]),
    ])


_SAFE_HOST = re.compile(r"[a-z0-9.\-:\[\]]{1,260}")


def shell_csp(host: str) -> str:
    """Policy for the Flet web client (Flutter needs inline bootstrap script and wasm).

    The Host header is echoed into ``connect-src`` (some browsers do not treat ``'self'`` as covering
    ws/wss), so it is only used if it is a plain host[:port] - never anything that could add directives.
    """
    host = host.strip().lower()
    sockets = [f"ws://{host}", f"wss://{host}"] if _SAFE_HOST.fullmatch(host) else []
    return _csp([
        ("default-src", ["'self'"]),
        ("script-src", ["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'"]),
        ("style-src", ["'self'", "'unsafe-inline'"]),
        ("img-src", ["'self'", "data:", "blob:"]),
        ("font-src", ["'self'", "data:"]),
        ("connect-src", ["'self'", "data:", "blob:", *sockets]),
        ("worker-src", ["'self'", "blob:"]),
        ("frame-src", ["'self'"]),
        ("media-src", ["'self'", "blob:"]),
        ("object-src", ["'none'"]),
        ("base-uri", ["'self'"]),
        ("form-action", ["'self'"]),
        ("frame-ancestors", ["'none'"]),
    ])


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings
        self._room_csp = room_page_csp(settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        host = Headers(scope=scope).get("host", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._apply(MutableHeaders(scope=message), path, host)
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _apply(self, headers: MutableHeaders, path: str, host: str) -> None:
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Referrer-Policy"] = "no-referrer"
        headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=(), payment=(), usb=(), display-capture=()"
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
        headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if self.settings.https:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        if path.startswith("/api/") or path == "/api":
            headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            headers["X-Frame-Options"] = "DENY"
            headers["Cache-Control"] = "no-store"
        elif path == ROOM_PAGE_PATH:
            headers["Content-Security-Policy"] = self._room_csp
            headers["X-Frame-Options"] = "SAMEORIGIN"
            headers["Cache-Control"] = "no-store"
        else:
            headers["Content-Security-Policy"] = shell_csp(host)
            headers["X-Frame-Options"] = "DENY"

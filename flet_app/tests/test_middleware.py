import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from videochat.web.middleware import HostGuardMiddleware, SecurityHeadersMiddleware, host_allowed, room_page_csp, shell_csp, split_host

from conftest import make_settings


def build(settings):
    async def ok(request):
        return PlainTextResponse("ok")

    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    app = Starlette(routes=[WebSocketRoute("/ws", ws), Route("/{path:path}", ok)])
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(HostGuardMiddleware, settings=settings)
    return TestClient(app, base_url="http://localhost:8550")


def test_split_host():
    assert split_host("Example.com:8550") == "example.com"
    assert split_host("[::1]:8550") == "[::1]"
    assert split_host("[::1]") == "[::1]"
    assert split_host(" localhost ") == "localhost"


@pytest.mark.parametrize(
    "host,patterns,expected",
    [
        ("localhost:8550", ["localhost"], True),
        ("LOCALHOST", ["localhost"], True),
        ("evil.com", ["localhost"], False),
        ("", ["localhost"], False),
        ("a.example.com", ["*.example.com"], True),
        ("example.com", ["*.example.com"], False),  # wildcard means subdomains only
        ("evilexample.com", ["*.example.com"], False),
        ("anything.test", ["*"], True),
        ("[::1]:80", ["[::1]"], True),
    ],
)
def test_host_allowed(host, patterns, expected):
    assert host_allowed(host, patterns) is expected


WS_HOST = {"Host": "localhost:8550"}  # TestClient.websocket_connect otherwise sends "Host: testserver"


class TestHostGuard:
    def test_allowed_host_passes_and_foreign_host_is_refused(self):
        client = build(make_settings(allowed_hosts=("localhost",)))
        assert client.get("/x").status_code == 200
        assert client.get("/x", headers={"Host": "evil.example"}).status_code == 400

    def test_public_base_url_host_is_trusted(self):
        client = build(make_settings(allowed_hosts=("localhost",), public_base_url="https://chat.example.com"))
        assert client.get("/x", headers={"Host": "chat.example.com"}).status_code == 200

    def test_websocket_from_another_origin_is_refused(self):
        client = build(make_settings(allowed_hosts=("localhost",)))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={**WS_HOST, "Origin": "https://evil.example"}):
                pass

    def test_websocket_same_origin_or_without_origin_is_accepted(self):
        client = build(make_settings(allowed_hosts=("localhost",)))
        with client.websocket_connect("/ws", headers={**WS_HOST, "Origin": "http://localhost:8550"}) as ws:
            assert ws.receive_text() == "hi"
        with client.websocket_connect("/ws", headers=WS_HOST) as ws:
            assert ws.receive_text() == "hi"

    def test_websocket_with_a_foreign_host_header_is_refused(self):
        client = build(make_settings(allowed_hosts=("localhost",)))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"Host": "evil.example"}):
                pass


class TestHeaders:
    def test_every_response_gets_the_baseline_headers(self):
        h = build(make_settings()).get("/anything").headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert h["cross-origin-resource-policy"] == "same-origin"
        assert "camera=(self)" in h["permissions-policy"] and "geolocation=()" in h["permissions-policy"]
        assert h["x-frame-options"] == "DENY"
        assert "strict-transport-security" not in h  # plain HTTP: HSTS would be ignored/harmful

    def test_hsts_only_when_https_is_configured(self):
        h = build(make_settings(https=True)).get("/anything").headers
        assert h["strict-transport-security"] == "max-age=31536000; includeSubDomains"

    def test_room_page_gets_the_strict_policy_and_may_only_be_framed_by_itself(self):
        h = build(make_settings()).get("/room.html").headers
        csp = h["content-security-policy"]
        script_src = next(d for d in csp.split("; ") if d.startswith("script-src"))
        assert script_src == "script-src 'self' 'wasm-unsafe-eval'"  # no inline, no eval
        directives = dict(d.split(" ", 1) for d in csp.split("; "))
        assert directives["style-src"] == "'self'"  # no <style> blocks...
        assert directives["style-src-attr"] == "'unsafe-inline'"  # ...only inline style attributes (Agora's player)
        assert "default-src 'none'" in csp and "frame-ancestors 'self'" in csp and "object-src 'none'" in csp
        assert "base-uri 'none'" in csp and "form-action 'none'" in csp
        assert h["x-frame-options"] == "SAMEORIGIN" and h["cache-control"] == "no-store"

    def test_room_page_connect_sources_are_limited_to_self_and_agora(self):
        csp = room_page_csp(make_settings())
        connect = next(d for d in csp.split("; ") if d.startswith("connect-src")).split()[1:]
        assert "'self'" in connect and "https://*.agora.io" in connect and "wss://*.sd-rtn.com" in connect
        assert "https:" not in connect and "wss:" not in connect and "*" not in connect

    def test_extra_connect_sources_are_configurable(self):
        csp = room_page_csp(make_settings(csp_connect_extra=("https://edge.example.net",)))
        assert "https://edge.example.net" in csp

    def test_shell_policy_cannot_be_framed_and_allows_the_same_origin_iframe(self):
        csp = build(make_settings()).get("/").headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "frame-src 'self'" in csp
        assert "ws://localhost:8550" in csp and "wss://localhost:8550" in csp

    @pytest.mark.parametrize("evil", ["x; script-src *", "a b", "a,b", "a\"b", "a;b"])
    def test_host_header_cannot_inject_csp_directives(self, evil):
        assert "script-src *" not in shell_csp(evil)
        assert evil not in shell_csp(evil)

    def test_wildcard_allow_list_still_cannot_inject_directives(self):
        client = build(make_settings(allowed_hosts=("*",)))
        h = client.get("/", headers={"Host": "x; script-src *"}).headers
        assert "script-src *" not in h["content-security-policy"]

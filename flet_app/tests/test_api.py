import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from videochat.container import build_container
from videochat.data.models import RoomMember
from videochat.security import tickets
from videochat.services import rtc
from videochat.web.app import create_api_app

from conftest import FAKE_APP_ID, GOOD_PASSWORD, make_settings


@pytest.fixture
def client(container):
    with TestClient(create_api_app(container), raise_server_exceptions=False) as c:
        yield c


def login(container, name):
    container.accounts.register(name, f"{name}@example.com", GOOD_PASSWORD)
    return container.accounts.login(name, GOOD_PASSWORD)


def join(container, who, room="lobby", name=None):
    p = who.principal
    return container.rooms.join(p.user.id, p.session_id, room, name or p.user.username.title())


def bearer(grant_or_token):
    token = getattr(grant_or_token, "ticket", grant_or_token)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def alice(container):
    return login(container, "alice")


@pytest.fixture
def bob(container):
    return login(container, "bob")


def test_health_and_hardening_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in r.headers


def test_no_cors_headers_even_for_a_cross_origin_preflight(client, container, alice):
    r = client.post("/api/rtc/credentials", headers={**bearer(join(container, alice)), "Origin": "https://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    pre = client.options("/api/rtc/credentials", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers


class TestCredentials:
    def test_issues_a_token_for_exactly_the_ticket_room_and_uid(self, client, container, alice):
        grant = join(container, alice, "team-1", "Alice A")
        r = client.post("/api/rtc/credentials", headers=bearer(grant))
        assert r.status_code == 200
        body = r.json()
        assert body["app_id"] == FAKE_APP_ID and body["channel"] == "TEAM-1" and body["uid"] == grant.uid
        assert body["token"].startswith("006" + FAKE_APP_ID)
        assert (body["display_name"], body["username"]) == ("Alice A", "alice")
        assert "certificate" not in str(body).lower() and container.settings.agora_app_certificate not in str(body)

    def test_token_never_outlives_the_ticket(self, db):
        settings = make_settings(ticket_ttl=120, rtc_token_ttl=3600)
        c = build_container(settings, db=db)
        c.accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        who = c.accounts.login("alice", GOOD_PASSWORD)
        grant = c.rooms.join(who.principal.user.id, who.principal.session_id, "lobby", "Alice")
        with TestClient(create_api_app(c)) as client:
            r = client.post("/api/rtc/credentials", headers=bearer(grant))
        assert r.json()["expires_at"] <= grant.expires_at <= int(time.time()) + 121

    def test_not_configured_is_a_clean_503(self, db):
        c = build_container(make_settings(agora_app_certificate=""), db=db)
        c.accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        who = c.accounts.login("alice", GOOD_PASSWORD)
        grant = c.rooms.join(who.principal.user.id, who.principal.session_id, "lobby", "Alice")
        with TestClient(create_api_app(c)) as client:
            r = client.post("/api/rtc/credentials", headers=bearer(grant))
        assert r.status_code == 503 and r.json()["code"] == "rtc_unavailable"

    def test_is_rate_limited(self, client, container, alice):
        headers = bearer(join(container, alice))
        assert [client.post("/api/rtc/credentials", headers=headers).status_code for _ in range(20)] == [200] * 20
        blocked = client.post("/api/rtc/credentials", headers=headers)
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1

    def test_renew_returns_a_fresh_token_for_the_same_binding(self, client, container, alice):
        grant = join(container, alice)
        first = client.post("/api/rtc/credentials", headers=bearer(grant)).json()
        renewed = client.post("/api/rtc/renew", headers=bearer(grant))
        assert renewed.status_code == 200 and renewed.json()["token"].startswith("006" + FAKE_APP_ID)
        assert renewed.json()["expires_at"] >= first["expires_at"]


class TestAuthentication:
    @pytest.mark.parametrize(
        "headers",
        [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "}, {"Authorization": "Bearer garbage"}, {"Authorization": "bearer a.b.c"}],
    )
    def test_missing_or_malformed_credentials_are_401_with_a_challenge(self, client, headers):
        r = client.post("/api/rtc/credentials", headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer" and "error" in r.json()

    def test_ticket_signed_with_another_key_is_rejected(self, client, container, alice):
        p = alice.principal
        forged = tickets.issue_ticket("z" * 48, user_id=p.user.id, session_id=p.session_id, room="LOBBY", name="Alice", uid=5, ttl=600)
        assert client.post("/api/rtc/credentials", headers=bearer(forged)).status_code == 401

    def test_expired_ticket_is_rejected(self, client, container, alice):
        p = alice.principal
        old = tickets.issue_ticket(container.settings.secret_key, user_id=p.user.id, session_id=p.session_id, room="LOBBY", name="Alice", uid=5, ttl=60, now=time.time() - 3600)
        assert client.post("/api/rtc/credentials", headers=bearer(old)).status_code == 401

    def test_logging_out_kills_the_ticket(self, client, container, alice):
        grant = join(container, alice)
        assert client.post("/api/rtc/renew", headers=bearer(grant)).status_code == 200
        container.accounts.logout(alice.token)
        r = client.post("/api/rtc/renew", headers=bearer(grant))
        assert r.status_code == 401 and "session has ended" in r.json()["error"]

    def test_ticket_for_a_deleted_membership_is_rejected(self, client, container, alice):
        grant = join(container, alice)
        with container.db.session() as s:
            for m in s.scalars(select(RoomMember)):
                s.delete(m)
        assert client.post("/api/rtc/credentials", headers=bearer(grant)).status_code == 404

    def test_errors_never_echo_internals(self, client, container, alice, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("super secret internal detail")

        monkeypatch.setattr(rtc, "build_rtc_token", boom)
        r = client.post("/api/rtc/credentials", headers=bearer(join(container, alice)))
        assert r.status_code == 500 and r.json() == {"error": "Internal server error."}
        assert "super secret" not in r.text


class TestMembers:
    def test_returns_name_and_verified_username_within_the_callers_room(self, client, container, alice, bob):
        a = join(container, alice, "lobby", "The Boss")
        b = join(container, bob, "lobby", "Bobby")
        r = client.get(f"/api/rtc/members/{a.uid}", headers=bearer(b))
        assert r.status_code == 200
        assert r.json() == {"uid": a.uid, "name": "The Boss", "username": "alice"}

    def test_cannot_look_up_members_of_other_rooms(self, client, container, alice, bob):
        a = join(container, alice, "secret-room", "Alice")
        b = join(container, bob, "other-room", "Bob")
        assert client.get(f"/api/rtc/members/{a.uid}", headers=bearer(b)).status_code == 404

    @pytest.mark.parametrize("uid", ["0", "abc", "-5", str(2**32), "1.5"])
    def test_rejects_bad_uids(self, client, container, alice, uid):
        r = client.get(f"/api/rtc/members/{uid}", headers=bearer(join(container, alice)))
        assert r.status_code == 422 and r.json() == {"error": "Invalid request."}


def test_leave_marks_the_member_gone_and_is_idempotent(client, container, alice):
    grant = join(container, alice)
    for _ in range(2):
        assert client.post("/api/rtc/leave", headers=bearer(grant)).json() == {"status": "ok"}
    with container.db.session() as s:
        assert s.scalar(select(RoomMember)).left_at is not None

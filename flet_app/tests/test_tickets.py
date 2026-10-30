import base64
import json

import pytest

from videochat.security import tickets
from videochat.security.tickets import TicketError

KEY = "k" * 48
NOW = 1_800_000_000


def make(**over):
    args = dict(user_id=7, session_id=3, room="LOBBY", name="Alice", uid=4242, ttl=600, now=NOW)
    args.update(over)
    return tickets.issue_ticket(KEY, **args)


def b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()


def test_roundtrip():
    t = tickets.verify_ticket(KEY, make(), now=NOW + 5)
    assert (t.user_id, t.session_id, t.room, t.name, t.uid) == (7, 3, "LOBBY", "Alice", 4242)
    assert t.expires_at == NOW + 600


def test_expiry_is_enforced():
    token = make()
    tickets.verify_ticket(KEY, token, now=NOW + 599)
    with pytest.raises(TicketError, match="expired"):
        tickets.verify_ticket(KEY, token, now=NOW + 600)


def test_wrong_key_is_rejected():
    with pytest.raises(TicketError):
        tickets.verify_ticket("z" * 48, make(), now=NOW)


def test_tampered_payload_is_rejected():
    version, body, sig = make().split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=="))
    forged["room"] = "OTHER"
    with pytest.raises(TicketError, match="signature"):
        tickets.verify_ticket(KEY, f"{version}.{b64(forged)}.{sig}", now=NOW)


def test_tampered_signature_is_rejected():
    version, body, sig = make().split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(TicketError):
        tickets.verify_ticket(KEY, f"{version}.{body}.{flipped}", now=NOW)


@pytest.mark.parametrize("junk", ["", "a", "a.b", "a.b.c.d", "v1..", "v2.x.y", None, 5, "v1." + "x" * 5000])
def test_malformed_tokens(junk):
    with pytest.raises(TicketError):
        tickets.verify_ticket(KEY, junk, now=NOW)


def test_future_issued_ticket_is_rejected():
    with pytest.raises(TicketError, match="future"):
        tickets.verify_ticket(KEY, make(now=NOW + 3600), now=NOW)


def test_signed_but_wrongly_typed_claims_are_rejected():
    # even a correctly signed token must have well-typed claims
    body = b64({"usr": "7", "sid": 1, "room": "R", "name": "n", "uid": 1, "iat": NOW, "exp": NOW + 99, "jti": "x"})
    sig = tickets._b64e(tickets._sign(KEY, f"v1.{body}"))
    with pytest.raises(TicketError, match="malformed"):
        tickets.verify_ticket(KEY, f"v1.{body}.{sig}", now=NOW)


def test_tickets_are_unique_per_issue():
    assert make() != make()

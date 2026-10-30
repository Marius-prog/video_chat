"""Signed, short-lived "room tickets".

Flet keeps the login in a server-side session; the embedded Agora page runs in
a browser iframe that cannot see it. A ticket bridges the two: the server signs
"user U may join room R as uid X until time T" and the page presents it as a
Bearer token to the credentials API. Tickets are HMAC-SHA256 signed with a key
derived from ``SECRET_KEY``, are bound to one room, one Agora uid and one login
session (so logging out invalidates renewals), and expire on their own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional

VERSION = "v1"
MAX_TICKET_LENGTH = 2048
_CLOCK_SKEW = 60


class TicketError(Exception):
    """The ticket is malformed, forged, expired or otherwise unusable."""


@dataclass(frozen=True)
class RoomTicket:
    user_id: int
    session_id: int
    room: str
    name: str
    uid: int
    issued_at: int
    expires_at: int
    nonce: str


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key(secret_key: str) -> bytes:
    return hmac.new(secret_key.encode("utf-8"), b"videochat/room-ticket/" + VERSION.encode(), hashlib.sha256).digest()


def _sign(secret_key: str, signing_input: str) -> bytes:
    return hmac.new(_key(secret_key), signing_input.encode("ascii"), hashlib.sha256).digest()


def issue_ticket(
    secret_key: str,
    *,
    user_id: int,
    session_id: int,
    room: str,
    name: str,
    uid: int,
    ttl: int,
    now: Optional[float] = None,
) -> str:
    issued = int(time.time() if now is None else now)
    payload = {
        "uid": uid,
        "usr": user_id,
        "sid": session_id,
        "room": room,
        "name": name,
        "iat": issued,
        "exp": issued + ttl,
        "jti": secrets.token_hex(8),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{VERSION}.{body}"
    return f"{signing_input}.{_b64e(_sign(secret_key, signing_input))}"


def verify_ticket(secret_key: str, token: str, *, now: Optional[float] = None) -> RoomTicket:
    if not isinstance(token, str) or not token or len(token) > MAX_TICKET_LENGTH:
        raise TicketError("malformed ticket")
    try:
        version, body, signature = token.split(".")
    except ValueError:
        raise TicketError("malformed ticket") from None
    if version != VERSION:
        raise TicketError("unsupported ticket version")

    try:
        presented = _b64d(signature)
        expected = _sign(secret_key, f"{version}.{body}")
    except (ValueError, UnicodeEncodeError):
        raise TicketError("malformed ticket") from None
    if not hmac.compare_digest(presented, expected):
        raise TicketError("bad signature")

    try:
        data = json.loads(_b64d(body))
        ticket = RoomTicket(
            user_id=_int(data["usr"]),
            session_id=_int(data["sid"]),
            room=_str(data["room"]),
            name=_str(data["name"]),
            uid=_int(data["uid"]),
            issued_at=_int(data["iat"]),
            expires_at=_int(data["exp"]),
            nonce=_str(data["jti"]),
        )
    except (ValueError, KeyError, TypeError):
        raise TicketError("malformed ticket") from None

    current = time.time() if now is None else now
    if ticket.expires_at <= current:
        raise TicketError("ticket expired")
    if ticket.issued_at > current + _CLOCK_SKEW:
        raise TicketError("ticket issued in the future")
    return ticket


def _int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected int")
    return value


def _str(value) -> str:
    if not isinstance(value, str):
        raise TypeError("expected str")
    return value

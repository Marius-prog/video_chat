"""JSON API used by the embedded room page.

Authentication is a signed room ticket in ``Authorization: Bearer <ticket>`` (see
``security/tickets.py``) - never a cookie, so these endpoints are not CSRF-prone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from ..container import Container
from ..security import tickets
from ..security.ratelimit import RateLimitExceeded
from ..services import rtc
from ..services.errors import ServiceError
from ..services.rooms import MemberInfo

log = logging.getLogger("videochat.api")

router = APIRouter(prefix="/api")


@dataclass(frozen=True)
class Authed:
    ticket: tickets.RoomTicket
    member: MemberInfo


def _container(request: Request) -> Container:
    return request.app.state.container


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def authed(request: Request, authorization: Optional[str] = Header(default=None)) -> Authed:
    """Verify the ticket, that its login is still alive, and that the membership still exists."""
    c = _container(request)
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Missing room ticket.", headers={"WWW-Authenticate": "Bearer"})
    try:
        ticket = tickets.verify_ticket(c.settings.secret_key, token.strip())
    except tickets.TicketError:
        log.warning("rejected room ticket from %s", _client(request))
        raise HTTPException(401, "Invalid or expired room ticket.", headers={"WWW-Authenticate": "Bearer"}) from None
    try:
        c.limiter.enforce(f"api:user:{ticket.user_id}", 120, 60)
    except RateLimitExceeded as exc:
        raise HTTPException(429, "Too many requests.", headers={"Retry-After": str(exc.retry_after)}) from None
    if not c.accounts.session_active(ticket.session_id, ticket.user_id):
        raise HTTPException(401, "Your session has ended. Please sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return Authed(ticket=ticket, member=c.rooms.authorize(ticket))


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/rtc/credentials")
def credentials(request: Request, auth: Authed = Depends(authed)) -> dict:
    c = _container(request)
    try:
        c.limiter.enforce(f"rtc:credentials:{auth.ticket.user_id}", 20, 60)
    except RateLimitExceeded as exc:
        raise HTTPException(429, "Too many requests.", headers={"Retry-After": str(exc.retry_after)}) from None
    issued = rtc.build_rtc_token(c.settings, auth.ticket.room, auth.ticket.uid, not_after=auth.ticket.expires_at)
    return {
        "app_id": issued.app_id,
        "channel": auth.ticket.room,
        "uid": auth.ticket.uid,
        "token": issued.token,
        "expires_at": issued.expires_at,
        "display_name": auth.member.name,
        "username": auth.member.username,
    }


@router.post("/rtc/renew")
def renew(request: Request, auth: Authed = Depends(authed)) -> dict:
    c = _container(request)
    try:
        c.limiter.enforce(f"rtc:renew:{auth.ticket.user_id}", 20, 60)
    except RateLimitExceeded as exc:
        raise HTTPException(429, "Too many requests.", headers={"Retry-After": str(exc.retry_after)}) from None
    issued = rtc.build_rtc_token(c.settings, auth.ticket.room, auth.ticket.uid, not_after=auth.ticket.expires_at)
    return {"token": issued.token, "expires_at": issued.expires_at}


@router.get("/rtc/members/{uid}")
def member(request: Request, uid: int = Path(ge=1, le=2**32 - 1), auth: Authed = Depends(authed)) -> dict:
    """Display name for a uid, only inside the room the caller's own ticket is bound to."""
    found = _container(request).rooms.member(auth.ticket.room, uid)
    if found is None:
        raise HTTPException(404, "Member not found.")
    return {"uid": found.uid, "name": found.name, "username": found.username}


@router.post("/rtc/leave")
def leave(request: Request, auth: Authed = Depends(authed)) -> dict:
    _container(request).rooms.leave(auth.ticket.room, auth.ticket.uid, auth.ticket.user_id)
    return {"status": "ok"}


# -- error shape: {"error": "<message>"} everywhere, no stack traces or internals --------------------

def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status)


def http_error_handler(_request: Request, exc) -> JSONResponse:
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


def validation_error_handler(_request: Request, _exc) -> JSONResponse:
    return JSONResponse({"error": "Invalid request."}, status_code=422)


def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error in API", exc_info=exc)
    return JSONResponse({"error": "Internal server error."}, status_code=500)

"""JSON API used by the embedded room page.

Authentication is a signed room ticket in ``Authorization: Bearer <ticket>`` (see
``security/tickets.py``) - never a cookie, so these endpoints are not CSRF-prone.

Cross-cutting concerns are FastAPI dependencies, so the endpoints only do their own work:
``verified_ticket`` parses and checks the ticket, ``throttle_per_user`` rate-limits per ticket
holder, and ``authed`` adds the login and membership checks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from ..container import Container
from ..security import tickets
from ..services import rtc, throttle
from ..services.errors import RateLimited, ServiceError
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


# -- dependencies -----------------------------------------------------------------------------------

def verified_ticket(request: Request, authorization: Optional[str] = Header(default=None)) -> tickets.RoomTicket:
    """The room ticket from ``Authorization: Bearer``, signature and expiry checked; 401 with a challenge otherwise."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Missing room ticket.", headers={"WWW-Authenticate": "Bearer"})
    try:
        return tickets.verify_ticket(_container(request).settings.secret_key, token.strip())
    except tickets.TicketError:
        log.warning("rejected room ticket from %s", _client(request))
        raise HTTPException(401, "Invalid or expired room ticket.", headers={"WWW-Authenticate": "Bearer"}) from None


def throttle_per_user(scope: str, limit: int, window: float) -> Callable[..., None]:
    """Build a dependency that rate-limits an endpoint per ticket holder.

    Use it as ``Depends(throttle_per_user("rtc:renew", 20, 60))``. It runs only after the ticket is
    verified, so unauthenticated noise never spends a user's budget, and it raises the same
    ``RateLimited`` error the services raise, which ``service_error_handler`` turns into a 429.
    """

    def dependency(request: Request, ticket: tickets.RoomTicket = Depends(verified_ticket)) -> None:
        throttle.enforce(_container(request).limiter, f"{scope}:{ticket.user_id}", limit, window)

    return dependency


def authed(
    request: Request,
    ticket: tickets.RoomTicket = Depends(verified_ticket),
    _: None = Depends(throttle_per_user("api:user", 120, 60)),
) -> Authed:
    """A verified ticket whose login is still alive and whose membership still exists."""
    c = _container(request)
    if not c.accounts.session_active(ticket.session_id, ticket.user_id):
        raise HTTPException(401, "Your session has ended. Please sign in again.", headers={"WWW-Authenticate": "Bearer"})
    return Authed(ticket=ticket, member=c.rooms.authorize(ticket))


# -- endpoints --------------------------------------------------------------------------------------
# Dependencies resolve in parameter order: ``auth`` first, then the endpoint's own throttle, so a
# dead login is refused before it can spend the endpoint budget.

@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.post("/rtc/credentials")
def credentials(
    request: Request,
    auth: Authed = Depends(authed),
    _: None = Depends(throttle_per_user("rtc:credentials", 20, 60)),
) -> dict:
    issued = rtc.build_rtc_token(
        _container(request).settings, auth.ticket.room, auth.ticket.uid, not_after=auth.ticket.expires_at
    )
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
def renew(
    request: Request,
    auth: Authed = Depends(authed),
    _: None = Depends(throttle_per_user("rtc:renew", 20, 60)),
) -> dict:
    issued = rtc.build_rtc_token(
        _container(request).settings, auth.ticket.room, auth.ticket.uid, not_after=auth.ticket.expires_at
    )
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
    headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
    return JSONResponse({"error": exc.message, "code": exc.code}, status_code=exc.status, headers=headers)


def http_error_handler(_request: Request, exc) -> JSONResponse:
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


def validation_error_handler(_request: Request, _exc) -> JSONResponse:
    return JSONResponse({"error": "Invalid request."}, status_code=422)


def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error in API", exc_info=exc)
    return JSONResponse({"error": "Internal server error."}, status_code=500)

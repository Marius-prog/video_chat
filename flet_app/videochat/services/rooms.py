"""Rooms, joining, and the uid -> name directory the room page needs.

The rules live here; the SQL lives in :class:`~videochat.data.repositories.RoomRepository`.
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from ..config import Settings
from ..data.repositories import DuplicateRecord, RoomRepository
from ..security import tickets, validation
from ..security.ratelimit import RateLimiter
from . import throttle
from .errors import Forbidden, NotFound

log = logging.getLogger("videochat.rooms")

_MAX_UID_ATTEMPTS = 8


@dataclass(frozen=True)
class JoinGrant:
    room: str
    uid: int
    display_name: str
    ticket: str
    expires_at: int
    created_room: bool


@dataclass(frozen=True)
class RoomSummary:
    name: str
    is_owner: bool
    last_joined_at: int


@dataclass(frozen=True)
class MemberInfo:
    uid: int
    name: str
    username: str


class RoomService:
    def __init__(
        self,
        repo: RoomRepository,
        settings: Settings,
        limiter: RateLimiter,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._repo = repo
        self._settings = settings
        self._limiter = limiter
        self._clock = clock

    # -- joining ------------------------------------------------------------

    def join(self, user_id: int, session_id: int, room_name: str, display_name: str) -> JoinGrant:
        """Validate the request, create the room on first use, and issue a signed room ticket."""
        room_name = validation.validate_room_name(room_name)
        display_name = validation.validate_display_name(display_name)
        throttle.enforce(self._limiter, f"join:user:{user_id}", 15, 60)

        room_id, created = self._ensure_room(room_name, user_id)
        now = int(self._clock())
        uid = self._add_member(room_id, user_id, display_name, now)
        ticket = tickets.issue_ticket(
            self._settings.secret_key,
            user_id=user_id,
            session_id=session_id,
            room=room_name,
            name=display_name,
            uid=uid,
            ttl=self._settings.ticket_ttl,
            now=now,
        )
        log.info("user id=%s joined room=%s uid=%s%s", user_id, room_name, uid, " (created)" if created else "")
        return JoinGrant(
            room=room_name,
            uid=uid,
            display_name=display_name,
            ticket=ticket,
            expires_at=now + self._settings.ticket_ttl,
            created_room=created,
        )

    def authorize(self, ticket: tickets.RoomTicket) -> MemberInfo:
        """Confirm a verified ticket still maps to a live membership, and mark the member present."""
        found = self._repo.find_membership(ticket.room, ticket.uid, ticket.user_id)
        if found is None:
            raise NotFound("This room session no longer exists. Please rejoin.")
        member, room, user = found
        if not room.is_active:
            raise Forbidden("This room is closed.")
        if member.left_at is not None:
            self._repo.mark_present(member.id)
        return MemberInfo(uid=member.uid, name=member.display_name, username=user.username)

    def leave(self, room_name: str, uid: int, user_id: int) -> None:
        self._repo.mark_left(room_name, uid, user_id, int(self._clock()))

    # -- queries ------------------------------------------------------------

    def member(self, room_name: str, uid: int) -> Optional[MemberInfo]:
        found = self._repo.find_member_name(room_name, uid)
        return MemberInfo(uid=uid, name=found[0], username=found[1]) if found else None

    def recent_rooms(self, user_id: int, limit: int = 5) -> List[RoomSummary]:
        return [
            RoomSummary(name=name, is_owner=owner_id == user_id, last_joined_at=last)
            for name, owner_id, last in self._repo.recent_rooms(user_id, limit)
        ]

    def owned_rooms(self, user_id: int) -> List[RoomSummary]:
        return [
            RoomSummary(name=name, is_owner=True, last_joined_at=created_at)
            for name, created_at in self._repo.owned_rooms(user_id)
        ]

    def purge_stale_members(self, *, keep_for: int = 30 * 24 * 3600) -> int:
        now = int(self._clock())
        return self._repo.delete_members(
            left_before=now - keep_for,
            abandoned_before=now - (self._settings.ticket_ttl + 24 * 3600),
        )

    # -- internals ----------------------------------------------------------

    def _ensure_room(self, name: str, owner_id: int) -> tuple[int, bool]:
        room = self._repo.find_room(name)
        if room is None:
            try:
                return self._repo.add_room(name=name, owner_id=owner_id, created_at=int(self._clock())).id, True
            except DuplicateRecord:  # someone else created it between our SELECT and INSERT
                room = self._repo.find_room(name)
        if not room.is_active:
            raise Forbidden("This room is closed.")
        return room.id, False

    def _add_member(self, room_id: int, user_id: int, display_name: str, now: int) -> int:
        for _ in range(_MAX_UID_ATTEMPTS):
            uid = secrets.randbelow(validation.MAX_UID) + 1  # 1 .. 2**32-1, Agora's uid range
            try:
                self._repo.add_member(room_id=room_id, user_id=user_id, uid=uid, display_name=display_name, joined_at=now)
                return uid
            except DuplicateRecord:  # that uid is already taken in this room: draw again
                continue
        raise RuntimeError("could not allocate a unique uid")  # astronomically unlikely

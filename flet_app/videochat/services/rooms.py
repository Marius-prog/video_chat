"""Rooms, joining, and the uid -> name directory the room page needs."""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from sqlalchemy import delete, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..config import Settings
from ..data.db import Database
from ..data.models import Room, RoomMember, User
from ..security import tickets, validation
from ..security.ratelimit import RateLimiter, RateLimitExceeded
from .errors import Forbidden, NotFound, RateLimited

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
        db: Database,
        settings: Settings,
        limiter: RateLimiter,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._db = db
        self._settings = settings
        self._limiter = limiter
        self._clock = clock

    # -- joining ------------------------------------------------------------

    def join(self, user_id: int, session_id: int, room_name: str, display_name: str) -> JoinGrant:
        """Validate the request, create the room on first use, and issue a signed room ticket."""
        room_name = validation.validate_room_name(room_name)
        display_name = validation.validate_display_name(display_name)
        try:
            self._limiter.enforce(f"join:user:{user_id}", 15, 60)
        except RateLimitExceeded as exc:
            raise RateLimited(exc.retry_after) from None

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
        with self._db.session() as s:
            row = s.execute(
                select(RoomMember, Room, User)
                .join(Room, Room.id == RoomMember.room_id)
                .join(User, User.id == RoomMember.user_id)
                .where(
                    Room.name == ticket.room,
                    RoomMember.uid == ticket.uid,
                    RoomMember.user_id == ticket.user_id,
                )
            ).first()
            if row is None:
                raise NotFound("This room session no longer exists. Please rejoin.")
            member, room, user = row
            if not room.is_active:
                raise Forbidden("This room is closed.")
            member.left_at = None
            return MemberInfo(uid=member.uid, name=member.display_name, username=user.username)

    def leave(self, room_name: str, uid: int, user_id: int) -> None:
        with self._db.session() as s:
            room_ids = select(Room.id).where(Room.name == room_name)
            s.execute(
                update(RoomMember)
                .where(
                    RoomMember.room_id.in_(room_ids),
                    RoomMember.uid == uid,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                )
                .values(left_at=int(self._clock()))
            )

    # -- queries ------------------------------------------------------------

    def member(self, room_name: str, uid: int) -> Optional[MemberInfo]:
        with self._db.session() as s:
            row = s.execute(
                select(RoomMember.display_name, User.username)
                .join(Room, Room.id == RoomMember.room_id)
                .join(User, User.id == RoomMember.user_id)
                .where(Room.name == room_name, RoomMember.uid == uid)
            ).first()
        return MemberInfo(uid=uid, name=row[0], username=row[1]) if row else None

    def recent_rooms(self, user_id: int, limit: int = 5) -> List[RoomSummary]:
        last = func.max(RoomMember.joined_at).label("last")
        with self._db.session() as s:
            rows = s.execute(
                select(Room.name, Room.owner_id, last)
                .join(RoomMember, RoomMember.room_id == Room.id)
                .where(RoomMember.user_id == user_id, Room.is_active.is_(True))
                .group_by(Room.id, Room.name, Room.owner_id)
                .order_by(desc("last"))
                .limit(limit)
            ).all()
        return [RoomSummary(name=r[0], is_owner=r[1] == user_id, last_joined_at=r[2]) for r in rows]

    def owned_rooms(self, user_id: int) -> List[RoomSummary]:
        with self._db.session() as s:
            rows = s.execute(
                select(Room.name, Room.created_at).where(Room.owner_id == user_id).order_by(desc(Room.created_at))
            ).all()
        return [RoomSummary(name=r[0], is_owner=True, last_joined_at=r[1]) for r in rows]

    def purge_stale_members(self, *, keep_for: int = 30 * 24 * 3600) -> int:
        now = int(self._clock())
        abandoned = now - (self._settings.ticket_ttl + 24 * 3600)
        with self._db.session() as s:
            result = s.execute(
                delete(RoomMember).where(
                    or_(
                        RoomMember.left_at < now - keep_for,
                        (RoomMember.left_at.is_(None)) & (RoomMember.joined_at < abandoned),
                    )
                )
            )
            return result.rowcount or 0

    # -- internals ----------------------------------------------------------

    def _ensure_room(self, name: str, owner_id: int) -> tuple[int, bool]:
        with self._db.session() as s:
            room = s.scalar(select(Room).where(Room.name == name))
            if room is not None:
                if not room.is_active:
                    raise Forbidden("This room is closed.")
                return room.id, False
        try:
            with self._db.session() as s:
                room = Room(name=name, owner_id=owner_id, created_at=int(self._clock()))
                s.add(room)
                s.flush()
                return room.id, True
        except IntegrityError:  # someone else created it between our SELECT and INSERT
            with self._db.session() as s:
                return s.scalar(select(Room.id).where(Room.name == name)), False

    def _add_member(self, room_id: int, user_id: int, display_name: str, now: int) -> int:
        for _ in range(_MAX_UID_ATTEMPTS):
            uid = secrets.randbelow(validation.MAX_UID) + 1  # 1 .. 2**32-1, Agora's uid range
            try:
                with self._db.session() as s:
                    s.add(RoomMember(room_id=room_id, user_id=user_id, uid=uid, display_name=display_name, joined_at=now))
                return uid
            except IntegrityError:
                continue
        raise RuntimeError("could not allocate a unique uid")  # astronomically unlikely

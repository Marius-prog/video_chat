"""Repositories: every SQL statement of the application lives here.

Services ask these objects for rows and tell them what to change; they never see a
session or a SQLAlchemy statement. Each method is one transaction (``Database.session``):
commit on success, roll back on any exception. Rows come back detached
(``expire_on_commit=False``), so reading them is fine but assigning to them changes
nothing: every change goes through a method of the repository.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import delete, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import Database
from .models import AuthSession, Room, RoomMember, User


class DuplicateRecord(Exception):
    """A unique constraint rejected the insert. The caller decides what that means."""


class AccountRepository:
    """Users and their logins (``users`` and ``auth_sessions``)."""

    def __init__(self, db: Database):
        self._db = db

    # -- users --------------------------------------------------------------

    def add_user(self, *, username: str, email: str, password_hash: str, created_at: int) -> User:
        try:
            with self._db.session() as s:
                user = User(username=username, email=email, password_hash=password_hash, created_at=created_at)
                s.add(user)
                s.flush()
                return user
        except IntegrityError:
            raise DuplicateRecord() from None

    def get_user(self, user_id: int) -> Optional[User]:
        with self._db.session() as s:
            return s.get(User, user_id)

    def find_user_by_username(self, username: str) -> Optional[User]:
        with self._db.session() as s:
            return s.scalar(select(User).where(User.username == username))

    def find_user_by_email(self, email: str) -> Optional[User]:
        with self._db.session() as s:
            return s.scalar(select(User).where(User.email == email))

    # -- logins -------------------------------------------------------------

    def record_login(
        self,
        user_id: int,
        *,
        token_hash: str,
        now: int,
        expires_at: int,
        ip: str,
        user_agent: str,
        password_hash: Optional[str] = None,
    ) -> AuthSession:
        """Create a login row and stamp ``last_login_at``; ``password_hash`` replaces the stored hash when given."""
        values: dict = {"last_login_at": now}
        if password_hash is not None:
            values["password_hash"] = password_hash
        with self._db.session() as s:
            s.execute(update(User).where(User.id == user_id).values(**values))
            record = AuthSession(
                token_hash=token_hash,
                user_id=user_id,
                created_at=now,
                expires_at=expires_at,
                last_seen_at=now,
                ip=ip,
                user_agent=user_agent,
            )
            s.add(record)
            s.flush()
            return record

    def find_session_by_token_hash(self, token_hash: str) -> Optional[Tuple[AuthSession, User]]:
        with self._db.session() as s:
            row = s.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.token_hash == token_hash)
            ).first()
            return (row[0], row[1]) if row else None

    def find_session(self, session_id: int, user_id: int) -> Optional[Tuple[AuthSession, User]]:
        with self._db.session() as s:
            row = s.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
            ).first()
            return (row[0], row[1]) if row else None

    def touch_session(self, session_id: int, now: int) -> None:
        with self._db.session() as s:
            s.execute(update(AuthSession).where(AuthSession.id == session_id).values(last_seen_at=now))

    def revoke_session(self, token_hash: str, now: int) -> None:
        with self._db.session() as s:
            s.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )

    def revoke_user_sessions(self, user_id: int, now: int, *, keep_session_id: Optional[int] = None) -> None:
        with self._db.session() as s:
            self._revoke(s, user_id, now, keep_session_id)

    def replace_password(self, user_id: int, password_hash: str, now: int, *, keep_session_id: Optional[int] = None) -> None:
        """Store a new hash and, in the same transaction, revoke every other login of the user."""
        with self._db.session() as s:
            s.execute(update(User).where(User.id == user_id).values(password_hash=password_hash))
            self._revoke(s, user_id, now, keep_session_id)

    def delete_dead_sessions(self, cutoff: int) -> int:
        """Delete logins that expired or were revoked before ``cutoff``; returns how many."""
        with self._db.session() as s:
            result = s.execute(
                delete(AuthSession).where(or_(AuthSession.expires_at < cutoff, AuthSession.revoked_at < cutoff))
            )
            return result.rowcount or 0

    @staticmethod
    def _revoke(s: Session, user_id: int, now: int, keep_session_id: Optional[int]) -> None:
        stmt = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        if keep_session_id is not None:
            stmt = stmt.where(AuthSession.id != keep_session_id)
        s.execute(stmt.values(revoked_at=now))


class RoomRepository:
    """Rooms and who is in them (``rooms`` and ``room_members``)."""

    def __init__(self, db: Database):
        self._db = db

    # -- rooms --------------------------------------------------------------

    def find_room(self, name: str) -> Optional[Room]:
        with self._db.session() as s:
            return s.scalar(select(Room).where(Room.name == name))

    def add_room(self, *, name: str, owner_id: int, created_at: int) -> Room:
        try:
            with self._db.session() as s:
                room = Room(name=name, owner_id=owner_id, created_at=created_at)
                s.add(room)
                s.flush()
                return room
        except IntegrityError:
            raise DuplicateRecord() from None

    def recent_rooms(self, user_id: int, limit: int) -> List[Tuple[str, int, int]]:
        """``(name, owner_id, last joined_at)`` of the open rooms a user has joined, most recent first."""
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
            return [(r[0], r[1], r[2]) for r in rows]

    def owned_rooms(self, user_id: int) -> List[Tuple[str, int]]:
        """``(name, created_at)`` of the rooms a user created, newest first."""
        with self._db.session() as s:
            rows = s.execute(
                select(Room.name, Room.created_at).where(Room.owner_id == user_id).order_by(desc(Room.created_at))
            ).all()
            return [(r[0], r[1]) for r in rows]

    # -- members ------------------------------------------------------------

    def add_member(self, *, room_id: int, user_id: int, uid: int, display_name: str, joined_at: int) -> RoomMember:
        try:
            with self._db.session() as s:
                member = RoomMember(room_id=room_id, user_id=user_id, uid=uid, display_name=display_name, joined_at=joined_at)
                s.add(member)
                s.flush()
                return member
        except IntegrityError:
            raise DuplicateRecord() from None

    def find_membership(self, room_name: str, uid: int, user_id: int) -> Optional[Tuple[RoomMember, Room, User]]:
        with self._db.session() as s:
            row = s.execute(
                select(RoomMember, Room, User)
                .join(Room, Room.id == RoomMember.room_id)
                .join(User, User.id == RoomMember.user_id)
                .where(Room.name == room_name, RoomMember.uid == uid, RoomMember.user_id == user_id)
            ).first()
            return (row[0], row[1], row[2]) if row else None

    def find_member_name(self, room_name: str, uid: int) -> Optional[Tuple[str, str]]:
        """``(display name, account username)`` of a uid inside a room."""
        with self._db.session() as s:
            row = s.execute(
                select(RoomMember.display_name, User.username)
                .join(Room, Room.id == RoomMember.room_id)
                .join(User, User.id == RoomMember.user_id)
                .where(Room.name == room_name, RoomMember.uid == uid)
            ).first()
            return (row[0], row[1]) if row else None

    def mark_present(self, member_id: int) -> None:
        with self._db.session() as s:
            s.execute(update(RoomMember).where(RoomMember.id == member_id).values(left_at=None))

    def mark_left(self, room_name: str, uid: int, user_id: int, now: int) -> None:
        """Record that a user's own uid left a room; a no-op for anyone else's uid or if already gone."""
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
                .values(left_at=now)
            )

    def delete_members(self, *, left_before: int, abandoned_before: int) -> int:
        """Delete members who left before ``left_before`` or never left but joined before ``abandoned_before``."""
        with self._db.session() as s:
            result = s.execute(
                delete(RoomMember).where(
                    or_(
                        RoomMember.left_at < left_before,
                        (RoomMember.left_at.is_(None)) & (RoomMember.joined_at < abandoned_before),
                    )
                )
            )
            return result.rowcount or 0

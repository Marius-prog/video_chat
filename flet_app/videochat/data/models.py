"""Database schema (SQLAlchemy 2.x). All timestamps are integer Unix seconds (UTC)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    last_login_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class AuthSession(Base):
    """A login. Only the SHA-256 of the bearer token is stored, never the token."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    last_seen_at: Mapped[int] = mapped_column(BigInteger)
    revoked_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RoomMember(Base):
    """One row per join: maps an Agora uid to a display name inside a room."""

    __tablename__ = "room_members"
    __table_args__ = (UniqueConstraint("room_id", "uid", name="uq_room_member_uid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    uid: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(50))
    joined_at: Mapped[int] = mapped_column(BigInteger)
    left_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

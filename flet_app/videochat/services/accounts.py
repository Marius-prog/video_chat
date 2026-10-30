"""Registration, login, sessions and password changes."""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..config import Settings
from ..data.db import Database
from ..data.models import AuthSession, User
from ..security import passwords, validation
from ..security.ratelimit import RateLimiter, RateLimitExceeded
from .errors import AccountExists, InvalidCredentials, NotAuthenticated, RateLimited

log = logging.getLogger("videochat.accounts")

_LOGIN_WINDOW = 15 * 60
_TOUCH_INTERVAL = 60
_MAX_TOKEN_LENGTH = 128


@dataclass(frozen=True)
class UserInfo:
    id: int
    username: str
    email: str
    created_at: int


@dataclass(frozen=True)
class Principal:
    user: UserInfo
    session_id: int


@dataclass(frozen=True)
class LoginResult:
    principal: Principal
    token: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _info(user: User) -> UserInfo:
    return UserInfo(id=user.id, username=user.username, email=user.email, created_at=user.created_at)


class AccountService:
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

    # -- registration -------------------------------------------------------

    def register(self, username: str, email: str, password: str, *, ip: str = "") -> UserInfo:
        username = validation.validate_username(username)
        email = validation.validate_email(email)
        password = validation.validate_password(password, username=username, email=email)
        self._enforce(f"register:ip:{ip}", 10, 3600)

        password_hash = passwords.hash_password(password, n=self._settings.scrypt_n)
        try:
            with self._db.session() as s:
                user = User(
                    username=username,
                    email=email,
                    password_hash=password_hash,
                    created_at=int(self._clock()),
                )
                s.add(user)
                s.flush()
                info = _info(user)
        except IntegrityError:
            raise AccountExists("That username or email is already registered.") from None
        log.info("registered user id=%s", info.id)
        return info

    # -- login / sessions ---------------------------------------------------

    def login(self, identifier: str, password: str, *, ip: str = "", user_agent: str = "") -> LoginResult:
        ident = (identifier or "").strip().lower() if isinstance(identifier, str) else ""
        pair_key = f"login:pair:{ip}:{ident}"
        self._enforce(f"login:ip:{ip}", 30, _LOGIN_WINDOW)
        self._enforce(f"login:user:{ident}", 10, _LOGIN_WINDOW)
        self._enforce(pair_key, 5, _LOGIN_WINDOW)

        too_long = not isinstance(password, str) or len(password) > validation.PASSWORD_MAX_LENGTH
        with self._db.session() as s:
            column = User.email if "@" in ident else User.username
            user = s.scalar(select(User).where(column == ident[:254])) if ident else None
            stored = user.password_hash if user and user.is_active else passwords.dummy_hash(self._settings.scrypt_n)
            user_id = user.id if user else None
            active = bool(user and user.is_active)

        # Always run one hash verification so unknown accounts cost the same time as wrong passwords.
        matches = (not too_long) and passwords.verify_password(password, stored)
        if not (active and matches):
            log.warning("failed login for %r from %s", ident[:40], ip or "?")
            raise InvalidCredentials("Invalid username or password.")

        now = int(self._clock())
        token = secrets.token_urlsafe(32)
        with self._db.session() as s:
            user = s.get(User, user_id)
            if passwords.needs_rehash(user.password_hash, n=self._settings.scrypt_n):
                user.password_hash = passwords.hash_password(password, n=self._settings.scrypt_n)
            user.last_login_at = now
            record = AuthSession(
                token_hash=_hash_token(token),
                user_id=user.id,
                created_at=now,
                expires_at=now + self._settings.session_ttl,
                last_seen_at=now,
                ip=(ip or "")[:64],
                user_agent=(user_agent or "")[:200],
            )
            s.add(record)
            s.flush()
            principal = Principal(user=_info(user), session_id=record.id)
        self._limiter.reset(pair_key)
        log.info("login user id=%s session=%s", principal.user.id, principal.session_id)
        return LoginResult(principal=principal, token=token)

    def resolve(self, token: Optional[str]) -> Optional[Principal]:
        """Return who a bearer token belongs to, or ``None`` if it is unknown/expired/revoked."""
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
            return None
        now = int(self._clock())
        with self._db.session() as s:
            row = s.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.token_hash == _hash_token(token))
            ).first()
            if row is None:
                return None
            record, user = row
            if not self._usable(record, user, now):
                return None
            self._touch(record, now)
            return Principal(user=_info(user), session_id=record.id)

    def session_active(self, session_id: int, user_id: int) -> bool:
        """Is the login that issued a room ticket still valid? (used on every credentials call)"""
        now = int(self._clock())
        with self._db.session() as s:
            row = s.execute(
                select(AuthSession, User)
                .join(User, User.id == AuthSession.user_id)
                .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
            ).first()
            if row is None or not self._usable(row[0], row[1], now):
                return False
            self._touch(row[0], now)  # a long call keeps the login alive through token renewals
            return True

    def logout(self, token: Optional[str]) -> None:
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
            return
        with self._db.session() as s:
            s.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == _hash_token(token), AuthSession.revoked_at.is_(None))
                .values(revoked_at=int(self._clock()))
            )

    def logout_all(self, user_id: int) -> None:
        with self._db.session() as s:
            s.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=int(self._clock()))
            )

    # -- password change ----------------------------------------------------

    def change_password(self, user_id: int, current: str, new: str, *, keep_session_id: Optional[int] = None) -> None:
        self._enforce(f"pwchange:{user_id}", 5, _LOGIN_WINDOW)
        with self._db.session() as s:
            user = s.get(User, user_id)
            if user is None:
                raise NotAuthenticated("Please sign in again.")
            stored, username, email = user.password_hash, user.username, user.email
        too_long = not isinstance(current, str) or len(current) > validation.PASSWORD_MAX_LENGTH
        if too_long or not passwords.verify_password(current, stored):
            raise InvalidCredentials("Current password is incorrect.")
        new = validation.validate_password(new, username=username, email=email)
        new_hash = passwords.hash_password(new, n=self._settings.scrypt_n)
        now = int(self._clock())
        with self._db.session() as s:
            s.execute(update(User).where(User.id == user_id).values(password_hash=new_hash))
            others = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            if keep_session_id is not None:
                others = others.where(AuthSession.id != keep_session_id)
            s.execute(others.values(revoked_at=now))
        log.info("password changed user id=%s", user_id)

    # -- housekeeping -------------------------------------------------------

    def purge_expired(self, *, grace: int = 24 * 3600) -> int:
        cutoff = int(self._clock()) - grace
        with self._db.session() as s:
            result = s.execute(
                delete(AuthSession).where(
                    or_(AuthSession.expires_at < cutoff, AuthSession.revoked_at < cutoff)
                )
            )
            return result.rowcount or 0

    # -- internals ----------------------------------------------------------

    def _usable(self, record: AuthSession, user: User, now: int) -> bool:
        return (
            record.revoked_at is None
            and record.expires_at > now
            and record.last_seen_at > now - self._settings.session_idle_ttl
            and user.is_active
        )

    @staticmethod
    def _touch(record: AuthSession, now: int) -> None:
        if now - record.last_seen_at >= _TOUCH_INTERVAL:
            record.last_seen_at = now

    def _enforce(self, key: str, limit: int, window: float) -> None:
        try:
            self._limiter.enforce(key, limit, window)
        except RateLimitExceeded as exc:
            raise RateLimited(exc.retry_after) from None

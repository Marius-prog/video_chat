"""Registration, login, sessions and password changes.

The rules live here; the SQL lives in :class:`~videochat.data.repositories.AccountRepository`.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from ..config import Settings
from ..data.models import AuthSession, User
from ..data.repositories import AccountRepository, DuplicateRecord
from ..security import passwords, validation
from ..security.ratelimit import RateLimiter
from . import throttle
from .errors import AccountExists, InvalidCredentials, NotAuthenticated

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


def _hash_presented_token(token: Optional[str]) -> Optional[str]:
    """Hash of a token a client presented, or ``None`` for anything that cannot be one of ours."""
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    return _hash_token(token)


def _info(user: User) -> UserInfo:
    return UserInfo(id=user.id, username=user.username, email=user.email, created_at=user.created_at)


class AccountService:
    def __init__(
        self,
        repo: AccountRepository,
        settings: Settings,
        limiter: RateLimiter,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._repo = repo
        self._settings = settings
        self._limiter = limiter
        self._clock = clock

    # -- registration -------------------------------------------------------

    def register(self, username: str, email: str, password: str, *, ip: str = "") -> UserInfo:
        username = validation.validate_username(username)
        email = validation.validate_email(email)
        password = validation.validate_password(password, username=username, email=email)
        throttle.enforce(self._limiter, f"register:ip:{ip}", 10, 3600)

        password_hash = passwords.hash_password(password, n=self._settings.scrypt_n)
        try:
            user = self._repo.add_user(
                username=username, email=email, password_hash=password_hash, created_at=int(self._clock())
            )
        except DuplicateRecord:
            raise AccountExists("That username or email is already registered.") from None
        log.info("registered user id=%s", user.id)
        return _info(user)

    # -- login / sessions ---------------------------------------------------

    def login(self, identifier: str, password: str, *, ip: str = "", user_agent: str = "") -> LoginResult:
        ident = (identifier or "").strip().lower() if isinstance(identifier, str) else ""
        pair_key = f"login:pair:{ip}:{ident}"
        throttle.enforce(self._limiter, f"login:ip:{ip}", 30, _LOGIN_WINDOW)
        throttle.enforce(self._limiter, f"login:user:{ident}", 10, _LOGIN_WINDOW)
        throttle.enforce(self._limiter, pair_key, 5, _LOGIN_WINDOW)

        too_long = not isinstance(password, str) or len(password) > validation.PASSWORD_MAX_LENGTH
        user = self._find_user(ident)
        active = bool(user and user.is_active)
        stored = user.password_hash if active else passwords.dummy_hash(self._settings.scrypt_n)

        # Always run one hash verification so unknown accounts cost the same time as wrong passwords.
        matches = (not too_long) and passwords.verify_password(password, stored)
        if not (active and matches):
            log.warning("failed login for %r from %s", ident[:40], ip or "?")
            raise InvalidCredentials("Invalid username or password.")

        now = int(self._clock())
        token = secrets.token_urlsafe(32)
        upgraded_hash = None
        if passwords.needs_rehash(user.password_hash, n=self._settings.scrypt_n):
            upgraded_hash = passwords.hash_password(password, n=self._settings.scrypt_n)
        record = self._repo.record_login(
            user.id,
            token_hash=_hash_token(token),
            now=now,
            expires_at=now + self._settings.session_ttl,
            ip=(ip or "")[:64],
            user_agent=(user_agent or "")[:200],
            password_hash=upgraded_hash,
        )
        principal = Principal(user=_info(user), session_id=record.id)
        self._limiter.reset(pair_key)
        log.info("login user id=%s session=%s", principal.user.id, principal.session_id)
        return LoginResult(principal=principal, token=token)

    def resolve(self, token: Optional[str]) -> Optional[Principal]:
        """Return who a bearer token belongs to, or ``None`` if it is unknown/expired/revoked."""
        token_hash = _hash_presented_token(token)
        if token_hash is None:
            return None
        return self._principal_if_usable(self._repo.find_session_by_token_hash(token_hash))

    def session_active(self, session_id: int, user_id: int) -> bool:
        """Is the login that issued a room ticket still valid? (used on every credentials call)"""
        return self._principal_if_usable(self._repo.find_session(session_id, user_id)) is not None

    def logout(self, token: Optional[str]) -> None:
        token_hash = _hash_presented_token(token)
        if token_hash is not None:
            self._repo.revoke_session(token_hash, int(self._clock()))

    def logout_all(self, user_id: int) -> None:
        self._repo.revoke_user_sessions(user_id, int(self._clock()))

    # -- password change ----------------------------------------------------

    def change_password(self, user_id: int, current: str, new: str, *, keep_session_id: Optional[int] = None) -> None:
        throttle.enforce(self._limiter, f"pwchange:{user_id}", 5, _LOGIN_WINDOW)
        user = self._repo.get_user(user_id)
        if user is None:
            raise NotAuthenticated("Please sign in again.")
        too_long = not isinstance(current, str) or len(current) > validation.PASSWORD_MAX_LENGTH
        if too_long or not passwords.verify_password(current, user.password_hash):
            raise InvalidCredentials("Current password is incorrect.")
        new = validation.validate_password(new, username=user.username, email=user.email)
        new_hash = passwords.hash_password(new, n=self._settings.scrypt_n)
        self._repo.replace_password(user_id, new_hash, int(self._clock()), keep_session_id=keep_session_id)
        log.info("password changed user id=%s", user_id)

    # -- housekeeping -------------------------------------------------------

    def purge_expired(self, *, grace: int = 24 * 3600) -> int:
        return self._repo.delete_dead_sessions(int(self._clock()) - grace)

    # -- internals ----------------------------------------------------------

    def _find_user(self, ident: str) -> Optional[User]:
        if not ident:
            return None
        ident = ident[:254]
        return self._repo.find_user_by_email(ident) if "@" in ident else self._repo.find_user_by_username(ident)

    def _principal_if_usable(self, found: Optional[Tuple[AuthSession, User]]) -> Optional[Principal]:
        if found is None:
            return None
        record, user = found
        now = int(self._clock())
        if not self._usable(record, user, now):
            return None
        if now - record.last_seen_at >= _TOUCH_INTERVAL:  # activity keeps the login alive (idle timeout)
            self._repo.touch_session(record.id, now)
        return Principal(user=_info(user), session_id=record.id)

    def _usable(self, record: AuthSession, user: User, now: int) -> bool:
        return (
            record.revoked_at is None
            and record.expires_at > now
            and record.last_seen_at > now - self._settings.session_idle_ttl
            and user.is_active
        )

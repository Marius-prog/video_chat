"""Wires settings, database, limiter and services together."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .config import Settings
from .data.db import Database
from .data.repositories import AccountRepository, RoomRepository
from .security.ratelimit import RateLimiter
from .services.accounts import AccountService
from .services.rooms import RoomService


@dataclass
class Container:
    settings: Settings
    db: Database
    limiter: RateLimiter
    accounts: AccountService
    rooms: RoomService

    _last_housekeeping: float = field(default=0.0, repr=False)

    def housekeeping(self, min_interval: float = 3600.0) -> bool:
        """Drop expired logins and stale room memberships, at most once per ``min_interval`` seconds."""
        now = time.monotonic()
        if self._last_housekeeping and now - self._last_housekeeping < min_interval:
            return False
        self._last_housekeeping = now
        self.accounts.purge_expired()
        self.rooms.purge_stale_members()
        return True


def build_container(settings: Settings, *, db: Optional[Database] = None) -> Container:
    db = db or Database(settings.database_url)
    db.create_all()
    limiter = RateLimiter()
    return Container(
        settings=settings,
        db=db,
        limiter=limiter,
        accounts=AccountService(AccountRepository(db), settings, limiter),
        rooms=RoomService(RoomRepository(db), settings, limiter),
    )

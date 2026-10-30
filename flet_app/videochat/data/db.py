"""Engine and session handling."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        kwargs: dict = {"pool_pre_ping": True}
        if parsed.get_backend_name() == "sqlite":
            kwargs["connect_args"] = {"check_same_thread": False}
            if parsed.database in (None, "", ":memory:"):
                kwargs["poolclass"] = StaticPool  # one shared connection, otherwise each checkout sees an empty DB
        self.engine: Engine = create_engine(url, **kwargs)
        if parsed.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope: commit on success, roll back on any exception."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except Exception:  # in-memory databases cannot switch journal mode
        pass
    cursor.close()
